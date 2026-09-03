from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx2
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from database.connection import DatabaseConnection
from infrastructure.models import provider_model_gateway as gateways
from infrastructure.models.profiles.registry import BUILTIN_MODEL_PROFILES
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from purra.api import AgentExecutionCheckpoint
from purra.cancellation import OperationCanceled
from purra.contracts import (
    AgentMessage, ModelFinishReason, ModelInvocation, ModelRequest,
    ModelStreamActivity, ModelStreamChunk, ReasoningMode, RunCreateParams,
    ToolCallDelta, ToolChoiceMode, ToolSchema,
)
from purra.errors import ModelGatewayError, UnsupportedModelFeatureError
from purra.json_values import thaw_json_mapping
from purra.model_protocol import (
    ModelProtocolCapabilities, ReasoningControl, generic_capability_snapshot,
    resolve_invocation_output_limit,
)
from purra.ports import RunCommit


def _invocation(provider, *, options=None, reasoning=ReasoningMode.DEFAULT, cap=4096):
    snapshot = replace(
        generic_capability_snapshot(),
        max_call_output_tokens=8192,
        protocol=ModelProtocolCapabilities(reasoning_control=ReasoningControl.SELECTABLE),
    )
    return ModelInvocation(
        request=ModelRequest(
            provider=provider,
            model="test-model",
            capability_snapshot=snapshot,
            options={
                "baseURL": "https://api.openai.com/v1" if provider == "openai" else "https://api.anthropic.com",
                "context_window": "128k",
                **(options or {}),
            },
        ),
        output_limit=resolve_invocation_output_limit(snapshot, explicit_user_override=cap),
        reasoning_mode=reasoning,
    )


def _mock_sdk(monkeypatch, provider, handler):
    clients = []
    requests = []

    async def handle(request):
        requests.append(json.loads(request.content))
        return handler(request)

    def client(**kwargs):
        http = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
        clients.append(http)
        sdk = AsyncOpenAI if provider == "openai" else AsyncAnthropic
        return sdk(http_client=http, **kwargs)

    monkeypatch.setattr(gateways, "AsyncOpenAI" if provider == "openai" else "AsyncAnthropic", client)
    return clients, requests


def _openai_response():
    return {
        "id": "chat-1", "object": "chat.completion", "created": 1, "model": "test-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Ready"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }


def _anthropic_response(*, content=None, stop="end_turn"):
    return {
        "id": "msg-1", "type": "message", "role": "assistant", "model": "test-model",
        "content": content or [{"type": "text", "text": "Ready"}],
        "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }


@pytest.mark.asyncio
async def test_native_openai_preserves_roles_and_uses_managed_output_limit(monkeypatch):
    clients, requests = _mock_sdk(monkeypatch, "openai", lambda _: httpx2.Response(200, json=_openai_response()))
    invocation = _invocation("openai", options={"max_tokens": 99, "thinking": {"type": "enabled"}}, reasoning=ReasoningMode.ENABLED)
    result = await gateways.ProviderModelGateway("test-key").complete(
        [AgentMessage("developer", "Use tools"), AgentMessage("user", "Hello")], invocation,
    )
    assert result.message.content == "Ready"
    assert result.usage.total_tokens == 12
    assert result.applied_output_limit == 4096
    assert requests[0]["messages"][0]["role"] == "developer"
    assert requests[0]["max_completion_tokens"] == 4096
    assert requests[0]["reasoning_effort"] == "medium"
    assert requests[0]["store"] is False
    assert not {"max_tokens", "baseURL", "context_window", "thinking", "model_profile"} & requests[0].keys()
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
async def test_anthropic_signed_tool_continuation_survives_business_checkpoint(monkeypatch, tmp_path):
    blocks = [
        {"type": "thinking", "thinking": "private thought", "signature": "private-signature"},
        {"type": "tool_use", "id": "tool-1", "name": "readSource", "input": {}},
    ]
    responses = iter([_anthropic_response(content=blocks, stop="tool_use"), _anthropic_response()])
    clients, requests = _mock_sdk(monkeypatch, "anthropic", lambda _: httpx2.Response(200, json=next(responses)))
    invocation = replace(
        _invocation("anthropic", options={"thinking": {"type": "enabled"}}, reasoning=ReasoningMode.ENABLED),
        tools=(ToolSchema("readSource", "Read source", {"type": "object", "properties": {}}),),
        tool_choice=ToolChoiceMode.AUTO,
    )
    gateway = gateways.ProviderModelGateway("test-key")
    result = await gateway.complete([AgentMessage("user", "Read source")], invocation)
    assert result.message.reasoning is None
    assert thaw_json_mapping(result.message.provider_data)["anthropic_message"]["content"] == blocks
    monkeypatch.setattr(gateways, "DEV_DIAGNOSTICS_ENABLED", True)
    diagnostic = json.dumps(gateway.describe_invocation([result.message], invocation))
    assert "private thought" not in diagnostic
    assert "private-signature" not in diagnostic

    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        repository = SqliteRunRepository(db)
        run_id = await repository.create(RunCreateParams(session_id=None, prompt="Read", mode="agent"))
        checkpoint = AgentExecutionCheckpoint(
            run_id=run_id, next_round=2, round_limit=3,
            messages=(AgentMessage("user", "Read source"), result.message, AgentMessage("tool", "Source text", tool_call_id="tool-1")),
        )
        await repository.commit(run_id, RunCommit(execution_checkpoint=checkpoint))
        restored = (await repository.get(run_id)).execution_checkpoint
        assert restored == checkpoint
        final = await gateway.complete(restored.messages, invocation)
    finally:
        await db.close()
    assert final.message.content == "Ready"
    assert requests[1]["messages"][1]["content"] == blocks
    assert requests[1]["messages"][2]["content"][0]["type"] == "tool_result"
    assert requests[0]["max_tokens"] == 4096
    assert requests[0]["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert all(client.is_closed for client in clients)


def _stream_response(provider, *, terminal=True):
    if provider == "openai":
        base = {"id": "chat-1", "object": "chat.completion.chunk", "created": 1, "model": "test-model"}
        events = [
            {**base, "choices": [{"index": 0, "delta": {"content": "Ready"}, "finish_reason": None}]},
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {**base, "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}},
        ]
        if not terminal:
            events = events[:1]
        wire = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
    else:
        events = [
            {"type": "message_start", "message": {**_anthropic_response(), "content": [], "stop_reason": None}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Ready"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 2}},
            {"type": "message_stop"},
        ]
        if not terminal:
            events = events[:-2]
        wire = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=wire)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("stop", ["exhaust", "unstarted", "cancel"])
async def test_native_stream_usage_and_client_lifecycle(monkeypatch, provider, stop):
    clients, requests = _mock_sdk(monkeypatch, provider, lambda _: _stream_response(provider))
    signal = asyncio.Event()
    stream = await gateways.ProviderModelGateway("test-key").stream(
        [AgentMessage("user", "Hello")], _invocation(provider), signal,
    )
    assert requests == []
    if stop == "unstarted":
        await stream.chunks.aclose()
        assert requests == []
    elif stop == "cancel":
        await anext(stream.chunks)
        signal.set()
        with pytest.raises(OperationCanceled):
            async for _ in stream.chunks:
                pass
    else:
        chunks = [chunk async for chunk in stream.chunks]
        assert any(isinstance(chunk, ModelStreamActivity) for chunk in chunks)
        assert "".join(chunk.content_delta for chunk in chunks if isinstance(chunk, ModelStreamChunk)) == "Ready"
        assert chunks[-1].finish_reason is ModelFinishReason.STOP
        assert chunks[-1].usage.total_tokens == 12
        assert stream.applied_output_limit == 4096
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
async def test_public_progress_projects_provider_content_incrementally():
    async def source():
        yield ModelStreamChunk(content_delta="【进")
        yield ModelStreamChunk(content_delta="展】正在")
        yield ModelStreamChunk(content_delta="核对当前资料")
        yield ModelStreamChunk(
            tool_call_deltas=(ToolCallDelta(
                index=0,
                id="tool-1",
                type="function",
                name="readSource",
                arguments_fragment="{}",
            ),),
            finish_reason=ModelFinishReason.TOOL_CALLS,
        )

    chunks = [
        chunk
        async for chunk in gateways._project_public_progress(
            source(), enabled=True,
        )
    ]

    assert [chunk.progress_delta for chunk in chunks] == [
        "",
        "正在",
        "正在核对当前资料",
        "",
    ]
    assert "".join(chunk.content_delta for chunk in chunks) == "【进展】正在核对当前资料"


@pytest.mark.asyncio
async def test_public_progress_does_not_relabel_an_unmarked_direct_answer():
    async def source():
        yield ModelStreamChunk(content_delta="这是")
        yield ModelStreamChunk(
            content_delta="直接回答。",
            finish_reason=ModelFinishReason.STOP,
        )

    chunks = [
        chunk
        async for chunk in gateways._project_public_progress(
            source(), enabled=True,
        )
    ]

    assert [chunk.progress_delta for chunk in chunks] == ["", ""]
    assert "".join(chunk.content_delta for chunk in chunks) == "这是直接回答。"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("failure", ["transport", "missing_terminal"])
async def test_native_interruption_remains_eligible_for_managed_recovery(monkeypatch, provider, failure):
    def handler(request):
        if failure == "transport":
            raise httpx2.ReadTimeout("private transport error", request=request)
        return _stream_response(provider, terminal=False)

    clients, requests = _mock_sdk(monkeypatch, provider, handler)
    with pytest.raises(ModelGatewayError) as caught:
        stream = await gateways.ProviderModelGateway("test-key").stream([AgentMessage("user", "Hello")], _invocation(provider))
        async for _ in stream.chunks:
            pass
    assert caught.value.code == "upstream_stream_interrupted"
    assert caught.value.retryable is True
    assert len(requests) == 1
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize(("status", "message", "code"), [
    (401, "bad secret", "provider_authentication_failed"),
    (429, "rate limit", "provider_rate_limited"),
    (429, "insufficient_quota", "provider_insufficient_balance"),
    (503, "unavailable", "provider_unavailable"),
])
async def test_native_sdk_errors_keep_business_codes_without_hidden_retries(monkeypatch, provider, status, message, code):
    clients, requests = _mock_sdk(monkeypatch, provider, lambda _: httpx2.Response(status, json={"error": {"message": message, "type": "api_error"}}))
    with pytest.raises(ModelGatewayError) as caught:
        stream = await gateways.ProviderModelGateway("test-key").stream([AgentMessage("user", "Hello")], _invocation(provider))
        async for _ in stream.chunks:
            pass
    assert caught.value.code == code
    assert message not in str(caught.value)
    assert len(requests) == 1
    assert all(client.is_closed for client in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_native_required_tool_rejection_updates_capability_cache(monkeypatch, provider):
    _mock_sdk(monkeypatch, provider, lambda _: httpx2.Response(400, json={"error": {"message": "tool_choice is unsupported", "type": "invalid_request_error"}}))
    marked = []
    gateway = gateways.ProviderModelGateway("test-key", on_required_tool_choice_unsupported=lambda: marked.append(True))
    with pytest.raises(UnsupportedModelFeatureError):
        await gateway.complete([AgentMessage("user", "Hello")], replace(
            _invocation(provider), tool_choice=ToolChoiceMode.REQUIRED,
            tools=(ToolSchema("readSource", "Read", {"type": "object", "properties": {}}),),
        ))
    assert marked == [True]


@pytest.mark.asyncio
async def test_anthropic_thinking_never_increases_output_budget(monkeypatch):
    clients, requests = _mock_sdk(monkeypatch, "anthropic", lambda _: pytest.fail("must not send request"))
    with pytest.raises(UnsupportedModelFeatureError):
        await gateways.ProviderModelGateway("test-key").complete([AgentMessage("user", "Hello")], _invocation(
            "anthropic", options={"thinking": {"type": "enabled"}}, reasoning=ReasoningMode.ENABLED, cap=1024,
        ))
    assert clients == requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", BUILTIN_MODEL_PROFILES, ids=lambda profile: profile.profile_id)
async def test_registered_vendors_keep_business_routing(monkeypatch, profile):
    captured = []

    async def complete(key, messages, options, provider, signal):
        captured.append((options, provider))
        return {"message": {"role": "assistant", "content": "Ready"}, "finish_reason": "stop", "applied_output_limit": options["max_tokens"]}

    monkeypatch.setattr(gateways.provider_router, "create_chat_no_stream", complete)
    provider = "zai" if profile.profile_id.startswith("zai:") else "openai"
    snapshot = profile.capability_snapshot(context_window_tokens=1_000_000)
    invocation = ModelInvocation(
        request=ModelRequest(provider=provider, model=next(iter(profile.model_names)), capability_snapshot=snapshot, options={"baseURL": next(iter(profile.base_urls))}),
        output_limit=resolve_invocation_output_limit(snapshot, explicit_user_override=None),
    )
    result = await gateways.ProviderModelGateway("test-key").complete([AgentMessage("user", "Hello")], invocation)
    assert result.message.content == "Ready"
    assert captured[0][0]["model_profile"] == profile.profile_id
    assert captured[0][1] == provider
