"""Compatibility adapter over the current model-provider functions."""

from __future__ import annotations

from typing import Callable, Sequence

import httpx

from agent_core.contracts import (
    AgentMessage,
    ModelCompletion,
    ModelFinishReason,
    ModelInvocation,
    ReasoningMode,
    ModelStream,
    ModelStreamChunk,
    ToolCallDelta,
    ToolChoiceMode,
)
from agent_core.errors import ModelGatewayError, UnsupportedModelFeatureError
from agent_core.json_values import thaw_json_mapping, thaw_json_value
from agent_core.ports import CancellationSignal
from services import ai_provider


class LegacyModelGateway:
    """Expose the existing OpenAI/Anthropic facade through the Core port."""

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

    async def stream(
        self,
        messages: Sequence[AgentMessage],
        invocation: ModelInvocation,
        signal: CancellationSignal | None = None,
    ) -> ModelStream:
        request = invocation.request
        options = _legacy_options(invocation)
        try:
            result = await ai_provider.create_chat_stream(
                self._api_key,
                [_legacy_message(message) for message in messages],
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
        result = await ai_provider.create_chat_no_stream(
            self._api_key,
            [_legacy_message(message) for message in messages],
            _legacy_options(invocation),
            request.provider,
            signal,  # type: ignore[arg-type]
        )
        raw_message = result.get("message") or {}
        return ModelCompletion(
            message=AgentMessage.from_mapping(raw_message),
            model=str(result.get("model") or request.model),
        )


def _legacy_options(
    invocation: ModelInvocation,
) -> dict:
    request = invocation.request
    options = thaw_json_mapping(request.options)
    options["model"] = request.model
    options.pop("tools", None)
    options.pop("tool_choice", None)
    if invocation.max_output_tokens is not None:
        options["max_tokens"] = invocation.max_output_tokens
    if invocation.reasoning_mode is ReasoningMode.DISABLED:
        options["thinking_enabled"] = False
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


def _legacy_message(message: AgentMessage) -> dict:
    value = thaw_json_mapping(message.attributes)
    value.update(
        {"role": message.role.value, "content": thaw_json_value(message.content)}
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


async def _normalize_openai_stream(raw_stream):
    """Keep provider-shaped chunks out of the future Core Runtime."""

    accumulated_content = ""
    try:
        async for raw in raw_stream:
            choices = raw.get("choices") or []
            if not choices:
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
            )
    except ModelGatewayError:
        raise
    except Exception as error:
        code = _provider_error_code(error)
        raise ModelGatewayError(
            "model provider stream failed",
            code=code,
            retryable=code == "upstream_stream_interrupted",
        ) from error


def _normalize_finish_reason(value) -> ModelFinishReason | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized == "stop":
        return ModelFinishReason.STOP
    if normalized == "length":
        return ModelFinishReason.LENGTH
    if normalized in {"tool_calls", "function_call"}:
        return ModelFinishReason.TOOL_CALLS
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
    if isinstance(error, (
        httpx.ReadError,
        httpx.RemoteProtocolError,
        httpx.TimeoutException,
    )):
        return "upstream_stream_interrupted"
    return "model_gateway_error"
