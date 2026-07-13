from __future__ import annotations

import json

import httpx
import pytest

from agent_core.contracts import (
    AgentMessage,
    ModelInvocation,
    ModelRequest,
    ReasoningMode,
    ToolCall,
    ToolChoiceMode,
    ToolSchema,
)
from agent_core.events import AgentEvent, CoreEventType
from agent_core.errors import ModelGatewayError, UnsupportedModelFeatureError
from agent_core.ports import EventSink, ModelGateway
from application.event_sinks import CallbackEventSink, LegacyChunkEventSink
from infrastructure.models.legacy_model_gateway import LegacyModelGateway


@pytest.mark.asyncio
async def test_legacy_model_gateway_preserves_provider_messages_tools_and_model(monkeypatch):
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

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _stream)
    gateway = LegacyModelGateway("secret")
    request = ModelRequest(
        provider="anthropic",
        model="requested-model",
        options={
            "baseURL": "https://example.invalid",
            "tool_choice": "required",
            "max_tokens": 999_999,
            "thinking_enabled": True,
            "metadata": {"tags": ["writing"]},
        },
    )
    stream = await gateway.stream(
        [AgentMessage(role="user", content="hello")],
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
    assert captured["messages"] == [{"role": "user", "content": "hello"}]
    assert captured["options"]["model"] == "requested-model"
    assert captured["options"]["tools"][0]["function"]["name"] == "readThing"
    assert captured["options"]["tool_choice"] == "required"
    assert captured["options"]["max_tokens"] == 2_048
    assert captured["options"]["thinking_enabled"] is False
    assert type(captured["options"]) is dict
    assert type(captured["options"]["metadata"]) is dict
    assert type(captured["options"]["metadata"]["tags"]) is list
    parameters = captured["options"]["tools"][0]["function"]["parameters"]
    assert type(parameters) is dict
    assert type(parameters["properties"]) is dict
    assert json.loads(json.dumps(captured["options"])) == captured["options"]


@pytest.mark.asyncio
async def test_legacy_model_gateway_normalizes_non_stream_completion(monkeypatch):
    async def _complete(_key, _messages, options, _provider, _signal):
        assert "tools" not in options
        assert "tool_choice" not in options
        return {
            "message": {"role": "assistant", "content": "planned", "reasoning_content": "brief"},
            "model": "resolved-model",
        }

    monkeypatch.setattr("services.ai_provider.create_chat_no_stream", _complete)
    completion = await LegacyModelGateway("secret").complete(
        [AgentMessage(role="user", content="plan")],
        ModelInvocation(
            request=ModelRequest(
                provider="openai",
                model="requested-model",
                options={"tools": [{"legacy": True}], "tool_choice": "required"},
            ),
            tool_choice=ToolChoiceMode.NONE,
        ),
    )

    assert completion.model == "resolved-model"
    assert completion.message.content == "planned"
    assert completion.message.thinking == "brief"


@pytest.mark.asyncio
async def test_legacy_model_gateway_maps_typed_tool_continuation_messages(monkeypatch):
    captured: list[dict] = []

    async def _stream(_key, messages, _options, _provider, _signal):
        captured.extend(messages)

        async def _chunks():
            yield {"choices": [{"message": {"content": "fallback"}, "finish_reason": "stop"}]}

        return {"stream": _chunks(), "model": "model"}

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _stream)
    stream = await LegacyModelGateway("secret").stream(
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
async def test_legacy_model_gateway_standardizes_required_tool_choice_rejection(monkeypatch):
    class CompatibilityError(RuntimeError):
        status_code = 400

    async def _stream(*_args, **_kwargs):
        raise CompatibilityError("provider rejects tool_choice in thinking mode")

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _stream)
    observations: list[str] = []
    gateway = LegacyModelGateway(
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
async def test_legacy_model_gateway_standardizes_upstream_stream_interruptions(monkeypatch):
    async def _stream(*_args, **_kwargs):
        async def _chunks():
            raise httpx.ReadError("connection contained secret details")
            yield  # pragma: no cover

        return {"stream": _chunks(), "model": "model"}

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _stream)
    stream = await LegacyModelGateway("secret").stream(
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
async def test_legacy_model_gateway_turns_cumulative_message_fallback_into_deltas(monkeypatch):
    async def _stream(*_args, **_kwargs):
        async def _chunks():
            yield {"choices": [{"delta": {"content": "a"}, "finish_reason": None}]}
            yield {"choices": [{"message": {"content": "ab"}, "finish_reason": "stop"}]}

        return {"stream": _chunks(), "model": "model"}

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _stream)
    stream = await LegacyModelGateway("secret").stream(
        [AgentMessage(role="user", content="hello")],
        ModelInvocation(
            request=ModelRequest(provider="openai", model="model"),
            tool_choice=ToolChoiceMode.NONE,
        ),
    )

    chunks = [chunk async for chunk in stream.chunks]
    assert [chunk.content_delta for chunk in chunks] == ["a", "b"]


@pytest.mark.asyncio
async def test_callback_event_sink_supports_sync_and_async_callbacks():
    received: list[AgentEvent] = []
    sync_sink = CallbackEventSink(received.append)

    async def _async_callback(event: AgentEvent):
        received.append(event)

    async_sink = CallbackEventSink(_async_callback)
    event = AgentEvent(type=CoreEventType.RUN_STARTED, run_id="run-1")
    await sync_sink.emit(event)
    await async_sink.emit(event)

    assert isinstance(sync_sink, EventSink)
    assert received == [event, event]


@pytest.mark.asyncio
async def test_legacy_chunk_event_sink_preserves_existing_sse_shape():
    received: list[dict] = []
    sink = LegacyChunkEventSink(received.append)
    event = AgentEvent(
        type="agentRunStarted",
        run_id="run-1",
        payload={
            "runId": "run-1",
            "status": "running",
            "details": {"steps": [{"id": "step-1"}]},
        },
    )

    await sink.emit(event)

    assert isinstance(sink, EventSink)
    assert received == [{
        "agentRunStarted": {
            "runId": "run-1",
            "status": "running",
            "details": {"steps": [{"id": "step-1"}]},
        },
    }]
    payload = received[0]["agentRunStarted"]
    assert type(payload) is dict
    assert type(payload["details"]) is dict
    assert type(payload["details"]["steps"]) is list
    assert json.loads(json.dumps(received)) == received
