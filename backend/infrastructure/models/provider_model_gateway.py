"""Provider-neutral ModelGateway backed by the configured SDK adapter."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

import httpx

from agent_core.contracts import (
    AgentMessage,
    ModelCompletion,
    ModelFinishReason,
    ModelInvocation,
    ModelTokenUsage,
    ReasoningMode,
    ModelStream,
    ModelStreamChunk,
    ToolCallDelta,
    ToolChoiceMode,
)
from agent_core.errors import ModelGatewayError, UnsupportedModelFeatureError
from agent_core.json_values import thaw_json_mapping, thaw_json_value
from agent_core.model_call_parameters import build_model_call_parameters
from agent_core.ports import CancellationSignal
from infrastructure.models import provider_router
from infrastructure.models.profiles import resolve_model_profile
from utils.async_stream import OwnedAsyncIterator


class ProviderModelGateway:
    """Expose OpenAI/Anthropic providers through the Agent Core port."""

    def __init__(
        self,
        api_key: str,
        *,
        on_required_tool_choice_unsupported: Callable[[], None] | None = None,
    ):
        self._api_key = str(api_key or "").strip()
        if not self._api_key:
            raise ValueError("API key is required")
        self._on_required_tool_choice_unsupported = (
            on_required_tool_choice_unsupported
        )

    def describe_invocation(
        self,
        messages: Sequence[AgentMessage],
        invocation: ModelInvocation,
    ) -> dict[str, Any]:
        return build_model_call_parameters(
            messages,
            invocation,
            provider_options=_provider_options(invocation),
        )

    async def stream(
        self,
        messages: Sequence[AgentMessage],
        invocation: ModelInvocation,
        signal: CancellationSignal | None = None,
    ) -> ModelStream:
        request = invocation.request
        options = _provider_options(invocation)
        try:
            result = await provider_router.create_chat_stream(
                self._api_key,
                [_provider_message(message) for message in messages],
                options,
                request.provider,
                signal,  # type: ignore[arg-type]
            )
        except Exception as error:
            if (
                invocation.tool_choice is ToolChoiceMode.REQUIRED
                and _is_required_tool_choice_compatibility_error(error)
            ):
                if self._on_required_tool_choice_unsupported is not None:
                    self._on_required_tool_choice_unsupported()
                raise UnsupportedModelFeatureError(
                    "required tool choice is unsupported"
                ) from error
            raise ModelGatewayError(
                "model provider request failed",
                code=_provider_error_code(error),
                retryable=_provider_error_code(error) == "upstream_stream_interrupted",
            ) from error
        return ModelStream(
            chunks=_normalize_openai_stream(result["stream"]),
            model=str(result.get("model") or request.model),
        )

    async def complete(
        self,
        messages: Sequence[AgentMessage],
        invocation: ModelInvocation,
        signal: CancellationSignal | None = None,
    ) -> ModelCompletion:
        request = invocation.request
        result = await provider_router.create_chat_no_stream(
            self._api_key,
            [_provider_message(message) for message in messages],
            _provider_options(invocation),
            request.provider,
            signal,  # type: ignore[arg-type]
        )
        raw_message = result.get("message") or {}
        return ModelCompletion(
            message=AgentMessage.from_mapping(raw_message),
            model=str(result.get("model") or request.model),
            usage=_normalize_model_usage(result.get("usage")),
        )


def _provider_options(
    invocation: ModelInvocation,
) -> dict:
    request = invocation.request
    options = thaw_json_mapping(request.options)
    options["model"] = request.model
    options.pop("model_profile", None)
    if request.profile_id is not None:
        options["model_profile"] = request.profile_id
    options.pop("tools", None)
    options.pop("tool_choice", None)
    if invocation.max_output_tokens is not None:
        maximum = invocation.max_output_tokens
        if invocation.reasoning_mode is ReasoningMode.DISABLED:
            profile = resolve_model_profile(
                request.profile_id,
                request.model,
                options.get("baseURL"),
            )
            maximum = max(maximum, profile.internal_output_token_floor())
        options["max_tokens"] = maximum
    if invocation.reasoning_mode is ReasoningMode.DISABLED:
        caller_thinking = options.get("thinking")
        caller_had_thinking_enabled = bool(
            options.get("thinking_enabled") is True
            or (
                isinstance(caller_thinking, dict)
                and caller_thinking.get("type") == "enabled"
            )
        )
        # Provider adapters consume the normalized ``thinking`` shape.  The
        # former ad-hoc flag was ignored and could leave Anthropic extended
        # thinking enabled for the narrow 1,200-token planner request.
        options["thinking_enabled"] = False
        options["thinking"] = {"type": "disabled"}
        # A caller-selected thinking temperature may be invalid after Core
        # disables reasoning for planners/judges.  Kimi K2.6, for example,
        # requires 1.0 with thinking but 0.6 without it.  Omitting sampling
        # lets each provider apply the correct non-thinking default.
        if caller_had_thinking_enabled:
            options.pop("temperature", None)
    if invocation.tools and invocation.tool_choice is not ToolChoiceMode.NONE:
        options["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": schema.name,
                    "description": schema.description,
                    "parameters": thaw_json_mapping(schema.parameters),
                },
            }
            for schema in invocation.tools
        ]
        if invocation.tool_choice is ToolChoiceMode.REQUIRED:
            options["tool_choice"] = "required"
    return options


def _provider_message(message: AgentMessage) -> dict:
    value = thaw_json_mapping(message.attributes)
    # These attributes are Core/Application bookkeeping, not provider message
    # fields.  OpenAI-compatible APIs may reject unknown keys even though the
    # in-process mocks and some permissive providers accept them.
    for host_only_key in (
        "context_name",
        "untrusted",
        "agent_core_plan",
        "agent_core_tool_name",
        "writing_outline_sources",
    ):
        value.pop(host_only_key, None)
    # ``developer`` is an internal Core role used to keep host context above
    # conversation data.  Many OpenAI-compatible Chat Completions providers
    # (including Kimi K2.6) only accept the older ``system`` role and reject
    # ``developer`` before tokenization.  Anthropic already treats both roles
    # as system content, so this normalization preserves the same authority
    # while keeping the wire contract broadly compatible.
    provider_role = (
        "system" if message.role.value == "developer" else message.role.value
    )
    value.update(
        {"role": provider_role, "content": thaw_json_value(message.content)}
    )
    if message.thinking is not None:
        value["reasoning_content"] = message.thinking
    if message.tool_calls:
        value["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.arguments_json,
                },
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        value["tool_call_id"] = message.tool_call_id
    return value


def _normalize_openai_stream(raw_stream):
    """Keep provider-shaped chunks out of the future Core Runtime."""

    async def _normalize():
        accumulated_content = ""
        async for raw in raw_stream:
            usage = _normalize_model_usage(raw.get("usage"))
            choices = raw.get("choices") or []
            if not choices:
                if usage is not None:
                    yield ModelStreamChunk(usage=usage)
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            message = choice.get("message") or {}
            delta_content = str(delta.get("content") or "")
            if delta_content:
                content_delta = delta_content
                accumulated_content += delta_content
            else:
                message_content = message.get("content")
                if isinstance(message_content, str) and message_content:
                    if not accumulated_content:
                        content_delta = message_content
                        accumulated_content = message_content
                    elif message_content.startswith(accumulated_content):
                        content_delta = message_content[len(accumulated_content):]
                        accumulated_content = message_content
                    else:
                        content_delta = ""
                else:
                    content_delta = ""
            tool_deltas = tuple(
                ToolCallDelta(
                    index=int(item.get("index") or 0),
                    id=item.get("id"),
                    type=item.get("type"),
                    name=(item.get("function") or {}).get("name"),
                    arguments_fragment=str((item.get("function") or {}).get("arguments") or ""),
                )
                for item in (delta.get("tool_calls") or [])
                if isinstance(item, dict)
            )
            yield ModelStreamChunk(
                content_delta=content_delta,
                thinking_delta=str(
                    delta.get("reasoning_content")
                    or message.get("reasoning_content")
                    or ""
                ),
                tool_call_deltas=tool_deltas,
                finish_reason=_normalize_finish_reason(choice.get("finish_reason")),
                usage=usage,
            )
    async def _guarded():
        try:
            async for chunk in _normalize():
                yield chunk
        except ModelGatewayError:
            raise
        except Exception as error:
            code = _provider_error_code(error)
            raise ModelGatewayError(
                "model provider stream failed",
                code=code,
                retryable=code == "upstream_stream_interrupted",
            ) from error

    return OwnedAsyncIterator(
        _guarded(),
        raw_stream,
        terminal_predicate=lambda chunk: chunk.finish_reason is not None,
    )


def _normalize_model_usage(raw: Any) -> ModelTokenUsage | None:
    """Normalize OpenAI- and Anthropic-shaped provider usage."""

    if not isinstance(raw, Mapping):
        return None
    prompt_tokens = _usage_int(raw, "prompt_tokens")
    native_input_tokens = _usage_int(raw, "input_tokens")
    if prompt_tokens is not None:
        input_tokens = prompt_tokens
    elif native_input_tokens is not None:
        input_tokens = (
            native_input_tokens
            + (_usage_int(raw, "cache_creation_input_tokens") or 0)
            + (_usage_int(raw, "cache_read_input_tokens") or 0)
        )
    else:
        return None

    output_tokens = (
        _usage_int(raw, "completion_tokens")
        if _usage_int(raw, "completion_tokens") is not None
        else (_usage_int(raw, "output_tokens") or 0)
    )
    total_tokens = _usage_int(raw, "total_tokens")
    prompt_details = raw.get("prompt_tokens_details")
    completion_details = raw.get("completion_tokens_details")
    cached_input_tokens = (
        _usage_int(prompt_details, "cached_tokens")
        if isinstance(prompt_details, Mapping)
        else None
    )
    if cached_input_tokens is None:
        cached_input_tokens = _usage_int(raw, "cache_read_input_tokens") or 0
    reasoning_output_tokens = (
        _usage_int(completion_details, "reasoning_tokens")
        if isinstance(completion_details, Mapping)
        else None
    ) or 0
    return ModelTokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_input_tokens=cached_input_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
    )


def _usage_int(value: Mapping[str, Any], key: str) -> int | None:
    raw = value.get(key)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        result = int(raw)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _normalize_finish_reason(value) -> ModelFinishReason | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"stop", "end_turn", "stop_sequence"}:
        return ModelFinishReason.STOP
    if normalized in {
        "length",
        "max_tokens",
        "max_output_tokens",
        "max_completion_tokens",
        "token_limit",
    }:
        return ModelFinishReason.LENGTH
    if normalized in {"tool_calls", "function_call"}:
        return ModelFinishReason.TOOL_CALLS
    if normalized in {"content_filter", "safety", "blocked"}:
        return ModelFinishReason.FILTERED
    return ModelFinishReason.OTHER


def _is_required_tool_choice_compatibility_error(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    if status not in {400, 422}:
        return False
    text = str(error or "").lower()
    return any(marker in text for marker in (
        "tool_choice",
        "tool choice",
        "forced tool",
        "thinking mode",
        "extended thinking",
    ))


def _provider_error_code(error: Exception) -> str:
    current: BaseException | None = error
    seen: set[int] = set()
    statuses: list[int] = []
    messages: list[str] = []
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        messages.append(str(current or "").lower())
        status = getattr(current, "status_code", None)
        response = getattr(current, "response", None)
        if status is None and response is not None:
            status = getattr(response, "status_code", None)
        if isinstance(status, int):
            statuses.append(status)
        if isinstance(current, (
            httpx.ReadError,
            httpx.RemoteProtocolError,
            httpx.TimeoutException,
        )):
            return "upstream_stream_interrupted"
        current = current.__cause__ or current.__context__
    combined = " ".join(messages)
    if (
        402 in statuses
        or any(marker in combined for marker in (
            "insufficient balance",
            "insufficient_balance",
            "insufficient quota",
            "insufficient_quota",
            "credit balance",
            "余额不足",
        ))
    ):
        return "provider_insufficient_balance"
    if any(status in {401, 403} for status in statuses):
        return "provider_authentication_failed"
    if 429 in statuses:
        return "provider_rate_limited"
    if any(status in {400, 422} for status in statuses):
        return "provider_bad_request"
    if any(status >= 500 for status in statuses):
        return "provider_unavailable"
    return "model_gateway_error"
