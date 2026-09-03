"""Provider-neutral ModelGateway backed by the configured SDK adapter."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping, Sequence

import httpx
import httpx2
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from purra_anthropic import AnthropicMessagesGateway
from purra_openai import OpenAIChatCompletionsGateway

from purra.contracts import (
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
from purra.errors import (
    AgentCoreError,
    ContractViolationError,
    ModelGatewayError,
    UnsupportedModelFeatureError,
)
from purra.json_values import thaw_json_mapping, thaw_json_value
from purra.model_call_parameters import build_model_call_parameters
from purra.model_protocol import ReasoningControl, ReasoningReplayPolicy
from purra.output import MAX_AGENT_PROGRESS_CHARS
from purra.ports import CancellationSignal
from purra.cancellation import raise_if_stopped
from infrastructure.models import provider_router
from infrastructure.models.capabilities import reasoning_mode_from_options
from purra.stream_ownership import OwnedAsyncIterator, close_async_resource
from config import DEV_DIAGNOSTICS_ENABLED
from constants import AGENT_PUBLIC_PROGRESS_PREFIX


class ProviderModelGateway:
    """Route native services through PurrA and compatible services through business adapters."""

    def __init__(
        self,
        api_key: str,
        *,
        on_required_tool_choice_unsupported: Callable[[], None] | None = None,
        public_progress_from_content: bool = False,
    ):
        self._api_key = str(api_key or "").strip()
        if not self._api_key:
            raise ValueError("API key is required")
        self._on_required_tool_choice_unsupported = (
            on_required_tool_choice_unsupported
        )
        self._public_progress_from_content = bool(public_progress_from_content)

    def describe_invocation(
        self,
        messages: Sequence[AgentMessage],
        invocation: ModelInvocation,
    ) -> dict[str, Any]:
        native = _uses_native_adapter(invocation)
        options = _provider_options(invocation)
        if native:
            options = thaw_json_mapping(_native_invocation(invocation, options).request.options)
        parameters = build_model_call_parameters(
            messages,
            invocation,
            provider_options=options,
        )
        if DEV_DIAGNOSTICS_ENABLED:
            parameters["inputMessages"] = [
                _diagnostic_provider_message(
                    message,
                    reasoning_replay=(
                        invocation.request.protocol_capabilities.reasoning_replay
                    ),
                    preserve_role=native,
                )
                for message in messages
            ]
        return parameters

    async def stream(
        self,
        messages: Sequence[AgentMessage],
        invocation: ModelInvocation,
        signal: CancellationSignal | None = None,
    ) -> ModelStream:
        request = invocation.request
        options = _provider_options(invocation)
        if _uses_native_adapter(invocation):
            raise_if_stopped(signal)
            native_invocation = _native_invocation(invocation, options)
            gateway, client = self._native_gateway(invocation)
            try:
                result = await gateway.stream(messages, native_invocation, signal)
            except Exception as error:
                await close_async_resource(client)
                raise self._request_error(invocation, error) from None

            async def chunks():
                try:
                    async for chunk in result.chunks:
                        yield chunk
                except Exception as error:
                    raise self._request_error(invocation, error) from None

            projected = _project_public_progress(
                chunks(),
                enabled=self._public_progress_from_content and bool(invocation.tools),
            )
            return replace(result, chunks=OwnedAsyncIterator(
                projected, result.chunks, client,
                terminal_predicate=lambda chunk: (
                    isinstance(chunk, ModelStreamChunk)
                    and chunk.finish_reason is not None
                ),
            ))
        try:
            result = await provider_router.create_chat_stream(
                self._api_key,
                [
                    _provider_message(
                        message,
                        reasoning_replay=(
                            request.protocol_capabilities.reasoning_replay
                        ),
                    )
                    for message in messages
                ],
                options,
                request.provider,
                signal,  # type: ignore[arg-type]
            )
        except Exception as error:
            raise self._request_error(invocation, error) from error
        if signal is not None and signal.is_set():
            await close_async_resource(result.get("stream"))
            raise_if_stopped(signal)
        normalized = _normalize_openai_stream(result["stream"])
        return ModelStream(
            chunks=_project_public_progress(
                normalized,
                enabled=self._public_progress_from_content and bool(invocation.tools),
            ),
            model=str(result.get("model") or request.model),
            applied_output_limit=result.get("applied_output_limit"),
        )

    async def complete(
        self,
        messages: Sequence[AgentMessage],
        invocation: ModelInvocation,
        signal: CancellationSignal | None = None,
    ) -> ModelCompletion:
        request = invocation.request
        options = _provider_options(invocation)
        if _uses_native_adapter(invocation):
            raise_if_stopped(signal)
            native_invocation = _native_invocation(invocation, options)
            gateway, client = self._native_gateway(invocation)
            try:
                return await gateway.complete(messages, native_invocation, signal)
            except Exception as error:
                raise self._request_error(invocation, error) from None
            finally:
                await close_async_resource(client)
        try:
            result = await provider_router.create_chat_no_stream(
                self._api_key,
                [
                    _provider_message(
                        message,
                        reasoning_replay=(
                            request.protocol_capabilities.reasoning_replay
                        ),
                    )
                    for message in messages
                ],
                options,
                request.provider,
                signal,  # type: ignore[arg-type]
            )
        except Exception as error:
            raise self._request_error(invocation, error) from error
        raw_message = result.get("message") or {}
        return ModelCompletion(
            message=AgentMessage.from_mapping(raw_message),
            model=str(result.get("model") or request.model),
            finish_reason=_normalize_finish_reason(result.get("finish_reason")),
            usage=_normalize_model_usage(result.get("usage")),
            applied_output_limit=result.get("applied_output_limit"),
        )

    def _native_gateway(self, invocation: ModelInvocation):
        if invocation.request.provider == "anthropic":
            client = AsyncAnthropic(api_key=self._api_key, base_url="https://api.anthropic.com")
            return AnthropicMessagesGateway(client), client
        client = AsyncOpenAI(api_key=self._api_key, base_url="https://api.openai.com/v1")
        return OpenAIChatCompletionsGateway(client), client

    def _request_error(
        self,
        invocation: ModelInvocation,
        error: Exception,
    ) -> AgentCoreError:
        if (
            invocation.tool_choice is ToolChoiceMode.REQUIRED
            and _is_required_tool_choice_compatibility_error(error)
        ):
            if self._on_required_tool_choice_unsupported is not None:
                self._on_required_tool_choice_unsupported()
            return UnsupportedModelFeatureError(
                "required tool choice is unsupported"
            )
        error_code = getattr(error, "code", "")
        if isinstance(error, AgentCoreError) and error_code != "upstream_stream_interrupted" and not error_code.startswith((
            "openai_http_", "openai_transport_",
            "anthropic_http_", "anthropic_transport_",
        )):
            return error
        code = error_code if error_code == "upstream_stream_interrupted" else _provider_error_code(error)
        return ModelGatewayError(
            "model provider request failed",
            code=code,
            retryable=code == "upstream_stream_interrupted",
        )


def _uses_native_adapter(invocation: ModelInvocation) -> bool:
    base_url = str(invocation.request.options.get("baseURL") or "").strip().lower().rstrip("/")
    if invocation.request.provider == "openai":
        return base_url in {"https://api.openai.com", "https://api.openai.com/v1"}
    if invocation.request.provider == "anthropic":
        return base_url in {"", "https://api.anthropic.com", "https://api.anthropic.com/v1"}
    return False


def _native_invocation(invocation: ModelInvocation, options: dict) -> ModelInvocation:
    options = dict(options)
    for key in ("model", "model_profile", "baseURL", "context_window", "tools", "tool_choice"):
        options.pop(key, None)
    mode = invocation.reasoning_mode
    if invocation.request.provider == "openai":
        options.pop("thinking", None)
        if invocation.request.protocol_capabilities.reasoning_control is ReasoningControl.UNAVAILABLE:
            mode = ReasoningMode.DEFAULT
    elif mode is ReasoningMode.ENABLED:
        thinking = options.get("thinking") or {"type": "enabled"}
        if thinking.get("type") == "enabled" and "budget_tokens" not in thinking:
            cap = invocation.max_call_output_tokens
            if cap is None or cap <= 1024:
                raise UnsupportedModelFeatureError("Anthropic thinking requires an output limit above 1024")
            options["thinking"] = {**thinking, "budget_tokens": min(32_000, max(1024, cap // 2))}
    return replace(
        invocation,
        request=replace(invocation.request, options=options),
        reasoning_mode=mode,
    )


def _provider_options(
    invocation: ModelInvocation,
) -> dict:
    request = invocation.request
    capabilities = request.protocol_capabilities
    if not capabilities.reasoning_mode_is_supported(invocation.reasoning_mode):
        raise UnsupportedModelFeatureError(
            "selected reasoning mode is incompatible with model capabilities"
        )
    options = thaw_json_mapping(request.options)
    configured_mode = reasoning_mode_from_options(options)
    if not capabilities.reasoning_mode_is_supported(configured_mode):
        raise UnsupportedModelFeatureError(
            "configured reasoning mode is incompatible with model capabilities"
        )
    if configured_mode is not invocation.reasoning_mode:
        raise ContractViolationError(
            "provider options conflict with the Run reasoning mode",
            code="model_configuration_conflict",
        )
    options["model"] = request.model
    options.pop("model_profile", None)
    if request.profile_id is not None:
        options["model_profile"] = request.profile_id
    options.pop("tools", None)
    options.pop("tool_choice", None)
    if invocation.max_call_output_tokens is not None:
        options["max_tokens"] = invocation.max_call_output_tokens
    if capabilities.reasoning_control is ReasoningControl.UNAVAILABLE:
        options.pop("thinking_enabled", None)
        options.pop("thinking", None)
    else:
        options.pop("thinking_enabled", None)
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


def _provider_message(
    message: AgentMessage,
    *,
    reasoning_replay: ReasoningReplayPolicy = ReasoningReplayPolicy.REQUIRED,
) -> dict:
    value = thaw_json_mapping(message.attributes)
    # These attributes are Core/Application bookkeeping, not provider message
    # fields.  OpenAI-compatible APIs may reject unknown keys even though the
    # in-process mocks and some permissive providers accept them.
    for host_only_key in (
        "context_name",
        "untrusted",
        "purra_plan",
        "purra_tool_name",
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
    if (
        message.reasoning is not None
        and reasoning_replay is ReasoningReplayPolicy.REQUIRED
    ):
        value["reasoning_content"] = message.reasoning
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


def _diagnostic_provider_message(
    message: AgentMessage,
    *,
    reasoning_replay: ReasoningReplayPolicy,
    preserve_role: bool = False,
) -> dict[str, Any]:
    value = _provider_message(message, reasoning_replay=reasoning_replay)
    if preserve_role:
        value["role"] = message.role.value
    value.pop("reasoning_content", None)
    return _redact_diagnostic_fields(value)


def _redact_diagnostic_fields(value: Any, *, field_name: str = "") -> Any:
    normalized = "".join(
        character for character in field_name.lower()
        if character.isalnum()
    )
    if normalized in {
        "apikey",
        "authorization",
        "proxyauthorization",
        "password",
        "secret",
        "accesstoken",
        "refreshtoken",
        "token",
    }:
        return "<redacted>"
    if isinstance(value, Mapping):
        return {
            str(key): _redact_diagnostic_fields(item, field_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [_redact_diagnostic_fields(item) for item in value]
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
                reasoning_delta=str(
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


def _project_public_progress(chunks, *, enabled: bool):
    if not enabled:
        return chunks

    async def _project():
        prefix = ""
        prefix_matched = False
        title = ""
        settled = False
        async for chunk in chunks:
            progress = chunk.progress_delta
            if progress:
                settled = True
            elif not settled and chunk.content_delta:
                content = chunk.content_delta
                if not prefix_matched:
                    candidate = prefix + content
                    if AGENT_PUBLIC_PROGRESS_PREFIX.startswith(candidate):
                        prefix = candidate
                        content = ""
                    elif candidate.startswith(AGENT_PUBLIC_PROGRESS_PREFIX):
                        prefix_matched = True
                        content = candidate[len(AGENT_PUBLIC_PROGRESS_PREFIX):]
                    else:
                        settled = True
                        content = ""
                if prefix_matched and content:
                    candidate = (title + content).replace("\r\n", "\n").replace("\r", "\n")
                    first_line, separator, _remainder = candidate.partition("\n")
                    normalized = first_line.strip()
                    if normalized and len(normalized) <= MAX_AGENT_PROGRESS_CHARS:
                        title = first_line
                        progress = normalized
                    else:
                        settled = True
                    if separator:
                        settled = True
            if chunk.tool_call_deltas:
                settled = True
            yield replace(chunk, progress_delta=progress)

    return OwnedAsyncIterator(
        _project(),
        chunks,
        terminal_predicate=lambda chunk: (
            isinstance(chunk, ModelStreamChunk)
            and chunk.finish_reason is not None
        ),
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
    for current in _error_chain(error):
        status = getattr(current, "status_code", None)
        response = getattr(current, "response", None)
        if status is None and response is not None:
            status = getattr(response, "status_code", None)
        if status in {400, 422} and any(marker in str(current).lower() for marker in (
            "tool_choice", "tool choice", "forced tool", "thinking mode", "extended thinking",
        )):
            return True
    return False


def _error_chain(error: Exception):
    current: BaseException | None = error
    seen: set[int] = set()
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _provider_error_code(error: Exception) -> str:
    statuses: list[int] = []
    messages: list[str] = []
    for current in _error_chain(error):
        messages.append(str(current or "").lower())
        status = getattr(current, "status_code", None)
        response = getattr(current, "response", None)
        if status is None and response is not None:
            status = getattr(response, "status_code", None)
        if isinstance(status, int):
            statuses.append(status)
        if isinstance(current, (httpx.TransportError, httpx2.TransportError)):
            return "upstream_stream_interrupted"
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
    if (
        any(status in {400, 422} for status in statuses)
        and "reasoning_content" in combined
        and "thinking" in combined
    ):
        return "provider_reasoning_context_invalid"
    if any(status in {400, 422} for status in statuses):
        return "provider_bad_request"
    if any(status >= 500 for status in statuses):
        return "provider_unavailable"
    return "model_gateway_error"
