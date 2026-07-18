from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRuntimeResult,
    DomainContext,
    ModelInvocation,
    ModelRequest,
    ReasoningMode,
    RuntimeOutcome,
    ToolCall,
    ToolChoiceMode,
    ToolSchema,
)
from agent_core.errors import ModelGatewayError, UnsupportedModelFeatureError
from agent_core.ports import ModelGateway
from agent_core.runtime import AgentRuntime
from infrastructure.models import provider_model_gateway
from infrastructure.models.provider_model_gateway import ProviderModelGateway


def test_disabled_reasoning_keeps_an_existing_non_thinking_temperature():
    invocation = ModelInvocation(
        request=ModelRequest(
            provider="openai",
            model="model",
            options={
                "temperature": 0,
                "thinking": {"type": "disabled"},
            },
        ),
        reasoning_mode=ReasoningMode.DISABLED,
    )

    options = provider_model_gateway._provider_options(invocation)

    assert options["temperature"] == 0
    assert options["thinking"] == {"type": "disabled"}


def test_provider_message_downgrades_developer_role_to_system():
    message = AgentMessage(
        role="developer",
        content="host context",
        attributes={"context_name": "writing_retrieval", "untrusted": True},
    )

    assert provider_model_gateway._provider_message(message) == {
        "role": "system",
        "content": "host context",
    }


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (402, "insufficient balance (1008)", "provider_insufficient_balance"),
        (401, "invalid api key", "provider_authentication_failed"),
        (429, "rate limit exceeded", "provider_rate_limited"),
        (400, "invalid model", "provider_bad_request"),
        (503, "service unavailable", "provider_unavailable"),
    ],
)
def test_provider_error_code_classifies_safe_http_failures(status, message, expected):
    class _ProviderHttpError(Exception):
        status_code = status

    assert provider_model_gateway._provider_error_code(
        _ProviderHttpError(message)
    ) == expected


@pytest.mark.asyncio
async def test_provider_model_gateway_preserves_provider_messages_tools_and_model(monkeypatch):
    captured: dict = {}

    async def _chunks():
        yield {
            "choices": [{
                "delta": {
                    "content": "ok",
                    "reasoning_content": "brief",
                    "tool_calls": [{
                        "index": 0,
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "readThing", "arguments": "{}"},
                    }],
                },
                "finish_reason": "tool_calls",
            }]
        }

    async def _stream(key, messages, options, provider, signal):
        captured.update({
            "key": key,
            "messages": messages,
            "options": options,
            "provider": provider,
            "signal": signal,
        })
        return {"stream": _chunks(), "model": "resolved-model"}

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_stream", _stream)
    gateway = ProviderModelGateway("secret")
    request = ModelRequest(
        provider="anthropic",
        model="requested-model",
        profile_id="minimax:MiniMax-M3",
        options={
            "baseURL": "https://example.invalid",
            "tool_choice": "required",
            "max_tokens": 999_999,
            "thinking_enabled": True,
            "temperature": 1.0,
            "metadata": {"tags": ["writing"]},
        },
    )
    stream = await gateway.stream(
        [AgentMessage(
            role="user",
            content="hello",
            attributes={
                "name": "caller-name",
                "context_name": "writing_retrieval",
                "untrusted": True,
                "agent_core_plan": True,
            },
            host_metadata={
                "writing_outline_sources": [{
                    "outlineId": "outline-1",
                    "text": "private receipt",
                }],
            },
        )],
        ModelInvocation(
            request=request,
            tools=(ToolSchema(
                name="readThing",
                description="Read a thing",
                parameters={
                    "type": "object",
                    "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
                },
            ),),
            tool_choice=ToolChoiceMode.REQUIRED,
            max_output_tokens=2_048,
            reasoning_mode=ReasoningMode.DISABLED,
        ),
    )

    assert isinstance(gateway, ModelGateway)
    assert stream.model == "resolved-model"
    chunks = [chunk async for chunk in stream.chunks]
    assert chunks[0].content_delta == "ok"
    assert chunks[0].thinking_delta == "brief"
    assert chunks[0].finish_reason == "tool_calls"
    assert chunks[0].tool_call_deltas[0].id == "call-1"
    assert chunks[0].tool_call_deltas[0].name == "readThing"
    assert chunks[0].tool_call_deltas[0].arguments_fragment == "{}"
    assert captured["key"] == "secret"
    assert captured["provider"] == "anthropic"
    assert captured["messages"] == [{
        "name": "caller-name",
        "role": "user",
        "content": "hello",
    }]
    assert captured["options"]["model"] == "requested-model"
    assert captured["options"]["tools"][0]["function"]["name"] == "readThing"
    assert captured["options"]["tool_choice"] == "required"
    assert captured["options"]["max_tokens"] == 2_048
    assert captured["options"]["thinking_enabled"] is False
    assert captured["options"]["thinking"] == {"type": "disabled"}
    assert captured["options"]["model_profile"] == "minimax:MiniMax-M3"
    assert "temperature" not in captured["options"]
    assert type(captured["options"]) is dict
    assert type(captured["options"]["metadata"]) is dict
    assert type(captured["options"]["metadata"]["tags"]) is list
    parameters = captured["options"]["tools"][0]["function"]["parameters"]
    assert type(parameters) is dict
    assert type(parameters["properties"]) is dict
    assert json.loads(json.dumps(captured["options"])) == captured["options"]


@pytest.mark.asyncio
async def test_provider_model_gateway_normalizes_non_stream_completion(monkeypatch):
    async def _complete(_key, _messages, options, _provider, _signal):
        assert "tools" not in options
        assert "tool_choice" not in options
        return {
            "message": {"role": "assistant", "content": "planned", "reasoning_content": "brief"},
            "model": "resolved-model",
        }

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_no_stream", _complete)
    completion = await ProviderModelGateway("secret").complete(
        [AgentMessage(role="user", content="plan")],
        ModelInvocation(
            request=ModelRequest(
                provider="openai",
                model="requested-model",
                options={"tools": [{"provider": True}], "tool_choice": "required"},
            ),
            tool_choice=ToolChoiceMode.NONE,
        ),
    )

    assert completion.model == "resolved-model"
    assert completion.message.content == "planned"
    assert completion.message.thinking == "brief"


@pytest.mark.asyncio
async def test_provider_model_gateway_maps_typed_tool_continuation_messages(monkeypatch):
    captured: list[dict] = []

    async def _stream(_key, messages, _options, _provider, _signal):
        captured.extend(messages)

        async def _chunks():
            yield {"choices": [{"message": {"content": "fallback"}, "finish_reason": "stop"}]}

        return {"stream": _chunks(), "model": "model"}

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_stream", _stream)
    stream = await ProviderModelGateway("secret").stream(
        [
            AgentMessage(
                role="assistant",
                content="",
                thinking="brief",
                tool_calls=(ToolCall(id="call-1", name="readThing", arguments_json="{}"),),
            ),
            AgentMessage(role="tool", content="result", tool_call_id="call-1"),
        ],
        ModelInvocation(
            request=ModelRequest(provider="openai", model="model"),
            tool_choice=ToolChoiceMode.NONE,
        ),
    )

    chunks = [chunk async for chunk in stream.chunks]
    assert captured == [
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "brief",
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "readThing", "arguments": "{}"},
            }],
        },
        {"role": "tool", "content": "result", "tool_call_id": "call-1"},
    ]
    assert chunks[0].content_delta == "fallback"


@pytest.mark.asyncio
async def test_provider_model_gateway_standardizes_required_tool_choice_rejection(monkeypatch):
    class CompatibilityError(RuntimeError):
        status_code = 400

    async def _stream(*_args, **_kwargs):
        raise CompatibilityError("provider rejects tool_choice in thinking mode")

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_stream", _stream)
    observations: list[str] = []
    gateway = ProviderModelGateway(
        "secret",
        on_required_tool_choice_unsupported=lambda: observations.append("unsupported"),
    )

    with pytest.raises(UnsupportedModelFeatureError):
        await gateway.stream(
            [AgentMessage(role="user", content="read")],
            ModelInvocation(
                request=ModelRequest(provider="openai", model="model"),
                tools=(_schema := ToolSchema(
                    name="readThing",
                    description="Read",
                    parameters={"type": "object"},
                ),),
                tool_choice=ToolChoiceMode.REQUIRED,
            ),
        )

    assert _schema.name == "readThing"
    assert observations == ["unsupported"]


@pytest.mark.asyncio
async def test_provider_model_gateway_standardizes_upstream_stream_interruptions(monkeypatch):
    async def _stream(*_args, **_kwargs):
        async def _chunks():
            raise httpx.ReadError("connection contained secret details")
            yield  # pragma: no cover

        return {"stream": _chunks(), "model": "model"}

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_stream", _stream)
    stream = await ProviderModelGateway("secret").stream(
        [AgentMessage(role="user", content="hello")],
        ModelInvocation(
            request=ModelRequest(provider="openai", model="model"),
            tool_choice=ToolChoiceMode.NONE,
        ),
    )

    with pytest.raises(ModelGatewayError) as captured:
        _ = [chunk async for chunk in stream.chunks]

    assert captured.value.code == "upstream_stream_interrupted"
    assert captured.value.retryable is True
    assert "secret" not in str(captured.value)


@pytest.mark.asyncio
async def test_provider_model_gateway_recognizes_wrapped_stream_interruptions(
    monkeypatch,
):
    async def _stream(*_args, **_kwargs):
        async def _chunks():
            try:
                raise httpx.RemoteProtocolError("private transport detail")
            except httpx.RemoteProtocolError as cause:
                raise RuntimeError("SDK wrapper detail") from cause
            yield  # pragma: no cover

        return {"stream": _chunks(), "model": "model"}

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_stream", _stream)
    stream = await ProviderModelGateway("secret").stream(
        [AgentMessage(role="user", content="hello")],
        ModelInvocation(
            request=ModelRequest(provider="openai", model="model"),
            tool_choice=ToolChoiceMode.NONE,
        ),
    )

    with pytest.raises(ModelGatewayError) as captured:
        _ = [chunk async for chunk in stream.chunks]

    assert captured.value.code == "upstream_stream_interrupted"
    assert captured.value.retryable is True
    assert "private" not in str(captured.value)
    assert "SDK" not in str(captured.value)


@pytest.mark.asyncio
async def test_provider_model_gateway_turns_cumulative_message_fallback_into_deltas(monkeypatch):
    async def _stream(*_args, **_kwargs):
        async def _chunks():
            yield {"choices": [{"delta": {"content": "a"}, "finish_reason": None}]}
            yield {"choices": [{"message": {"content": "ab"}, "finish_reason": "stop"}]}

        return {"stream": _chunks(), "model": "model"}

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_stream", _stream)
    stream = await ProviderModelGateway("secret").stream(
        [AgentMessage(role="user", content="hello")],
        ModelInvocation(
            request=ModelRequest(provider="openai", model="model"),
            tool_choice=ToolChoiceMode.NONE,
        ),
    )

    chunks = [chunk async for chunk in stream.chunks]
    assert [chunk.content_delta for chunk in chunks] == ["a", "b"]


@pytest.mark.asyncio
async def test_normalized_openai_stream_propagates_consumer_close_to_raw_stream():
    class _TrackedRawStream:
        def __init__(self):
            self.close_calls = 0
            self._sent = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            return {
                "choices": [{
                    "delta": {"content": "first"},
                    "finish_reason": None,
                }],
            }

        async def aclose(self):
            self.close_calls += 1

    raw_stream = _TrackedRawStream()
    normalized = provider_model_gateway._normalize_openai_stream(raw_stream)

    first = await anext(normalized)
    assert first.content_delta == "first"
    await normalized.aclose()

    assert raw_stream.close_calls == 1


@pytest.mark.asyncio
async def test_normalized_openai_stream_closes_raw_stream_before_first_iteration():
    class _TrackedRawStream:
        def __init__(self):
            self.next_calls = 0
            self.close_calls = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.next_calls += 1
            raise AssertionError("raw stream must not be read before close")

        async def aclose(self):
            self.close_calls += 1

    raw_stream = _TrackedRawStream()
    normalized = provider_model_gateway._normalize_openai_stream(raw_stream)

    await normalized.aclose()

    assert raw_stream.next_calls == 0
    assert raw_stream.close_calls == 1


@pytest.mark.asyncio
async def test_provider_gateway_runtime_same_tick_cancel_closes_unstarted_raw_stream(
    monkeypatch,
):
    class _TrackedRawStream:
        def __init__(self):
            self.next_calls = 0
            self.close_calls = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.next_calls += 1
            raise AssertionError("canceled runtime must not read the raw stream")

        async def aclose(self):
            self.close_calls += 1

    raw_stream = _TrackedRawStream()
    signal = asyncio.Event()

    async def _stream(_key, _messages, _options, _provider, received_signal):
        assert received_signal is signal
        signal.set()
        return {"stream": raw_stream, "model": "resolved-model"}

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_stream", _stream)
    runtime = AgentRuntime(model_gateway=ProviderModelGateway("secret"))
    updates = [
        update
        async for update in runtime.run(
            AgentRunRequest(
                messages=(AgentMessage(role="user", content="hello"),),
                model=ModelRequest(provider="openai", model="requested-model"),
                domain_context=DomainContext(namespace="test"),
            ),
            signal=signal,
        )
    ]

    result = updates[-1]
    assert isinstance(result, AgentRuntimeResult)
    assert result.outcome is RuntimeOutcome.CANCELED
    assert result.error_code == "request_canceled"
    assert raw_stream.next_calls == 0
    assert raw_stream.close_calls == 1
