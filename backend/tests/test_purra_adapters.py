from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRuntimeResult,
    DomainContext,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelTokenUsage,
    ReasoningMode,
    RuntimeOutcome,
    ToolCall,
    ToolChoiceMode,
    ToolSchema,
)
from purra.errors import ModelGatewayError, UnsupportedModelFeatureError
from purra.model_protocol import (
    generic_capability_snapshot,
    InvocationOutputLimit,
    InvocationOutputLimitSource,
    ModelProtocolCapabilities,
    ReasoningControl,
    ReasoningReplayPolicy,
    resolve_invocation_output_limit,
)
from purra.ports import ModelGateway
from purra.runtime import AgentRuntime
from purra.testing import assert_model_gateway_conforms
from infrastructure.models import provider_model_gateway
from infrastructure.models.provider_model_gateway import ProviderModelGateway
from infrastructure.models.profiles.registry import resolve_model_profile


def _snapshot(*, profile_id="generic", protocol=None):
    return replace(
        generic_capability_snapshot(),
        profile_id=profile_id,
        max_call_output_tokens=393_216,
        protocol=protocol or ModelProtocolCapabilities(),
    )


def _limit(max_tokens: int) -> InvocationOutputLimit:
    return InvocationOutputLimit(
        max_tokens=max_tokens,
        source=InvocationOutputLimitSource.USER_OVERRIDE,
        profile_max_tokens=393_216,
    )


@pytest.mark.parametrize(
    ("native_reason", "normalized"),
    [
        ("end_turn", ModelFinishReason.STOP),
        ("stop_sequence", ModelFinishReason.STOP),
        ("max_tokens", ModelFinishReason.LENGTH),
        ("max_output_tokens", ModelFinishReason.LENGTH),
        ("content_filter", ModelFinishReason.FILTERED),
        ("blocked", ModelFinishReason.FILTERED),
        ("provider_specific_unknown", ModelFinishReason.OTHER),
    ],
)
def test_provider_finish_reasons_are_normalized_fail_closed(
    native_reason,
    normalized,
):
    assert provider_model_gateway._normalize_finish_reason(
        native_reason
    ) is normalized


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


@pytest.mark.parametrize(
    ("explicit_limit", "expected_limit"),
    [(None, 393_216), (256_000, 256_000)],
)
@pytest.mark.parametrize(
    "reasoning_mode",
    [ReasoningMode.DEFAULT, ReasoningMode.DISABLED],
)
def test_provider_receives_the_exact_profile_or_user_output_limit(
    explicit_limit,
    expected_limit,
    reasoning_mode,
):
    snapshot = resolve_model_profile(
        "deepseek:deepseek-v4-flash",
        "deepseek-v4-flash",
        "https://api.deepseek.com",
    ).capability_snapshot(context_window_tokens=1_000_000)
    invocation = ModelInvocation(
        request=ModelRequest(
            provider="openai",
            model="deepseek-v4-flash",
            capability_snapshot=snapshot,
            options={
                "baseURL": "https://api.deepseek.com",
                **(
                    {"thinking": {"type": "disabled"}}
                    if reasoning_mode is ReasoningMode.DISABLED
                    else {}
                ),
            },
        ),
        output_limit=resolve_invocation_output_limit(
            snapshot,
            explicit_user_override=explicit_limit,
        ),
        reasoning_mode=reasoning_mode,
    )

    assert provider_model_gateway._provider_options(invocation)[
        "max_tokens"
    ] == expected_limit


def test_incompatible_reasoning_selection_fails_before_provider_invocation():
    invocation = ModelInvocation(
        request=ModelRequest(
            provider="openai",
            model="custom-model",
            capability_snapshot=_snapshot(
                protocol=ModelProtocolCapabilities(
                    reasoning_control=ReasoningControl.UNAVAILABLE,
                ),
            ),
            options={"thinking": {"type": "enabled"}},
        ),
        reasoning_mode=ReasoningMode.DEFAULT,
    )

    with pytest.raises(UnsupportedModelFeatureError):
        provider_model_gateway._provider_options(invocation)


def test_always_enabled_reasoning_profile_rejects_disabled_invocation():
    invocation = ModelInvocation(
        request=ModelRequest(
            provider="openai",
            model="kimi-k3",
            capability_snapshot=resolve_model_profile(
                "moonshot:kimi-k3",
                "kimi-k3",
                "https://api.moonshot.cn/v1",
            ).capability_snapshot(context_window_tokens=1_000_000),
            options={"baseURL": "https://api.moonshot.cn/v1"},
        ),
        output_limit=_limit(1_200),
        reasoning_mode=ReasoningMode.DISABLED,
    )

    with pytest.raises(UnsupportedModelFeatureError):
        provider_model_gateway._provider_options(invocation)


def test_provider_tool_wire_keeps_display_names_host_only():
    invocation = ModelInvocation(
        request=ModelRequest(provider="openai", model="model"),
        tools=(ToolSchema(
            name="readSource",
            description="Read source data. User-facing name: 读取原作。",
            parameters={"type": "object", "properties": {}},
            display_names={
                "zh-CN": "读取原作",
                "en-US": "Read Source",
            },
        ),),
        tool_choice=ToolChoiceMode.AUTO,
    )

    options = provider_model_gateway._provider_options(invocation)

    function = options["tools"][0]["function"]
    assert function["name"] == "readSource"
    assert "读取原作" in function["description"]
    assert "displayNames" not in function
    assert "display_names" not in function


def test_model_call_parameters_are_provider_normalized_and_redacted():
    gateway = ProviderModelGateway("private-provider-key")
    invocation = ModelInvocation(
        request=ModelRequest(
            provider="openai",
            model="model",
            capability_snapshot=_snapshot(profile_id="profile"),
            options={
                "baseURL": (
                    "https://name:password@example.invalid/v1"
                    "?api_key=private-query-key&region=cn"
                ),
                "apiKey": "private-option-key",
                "authorization": "Bearer private-token",
                "metadata": {
                    "access_token": "private-access-token",
                    "label": "writing",
                },
                "thinking": {"type": "disabled"},
                "tools": [{"function": {"name": "caller-owned"}}],
            },
        ),
        tools=(ToolSchema(
            name="readThing",
            description="Read a thing",
            parameters={"type": "object"},
        ),),
        tool_choice=ToolChoiceMode.AUTO,
        output_limit=_limit(2_048),
        reasoning_mode=ReasoningMode.DISABLED,
    )

    parameters = gateway.describe_invocation(
        [AgentMessage(role="user", content="private message")],
        invocation,
    )

    assert parameters == {
        "provider": "openai",
        "model": "model",
        "options": {
            "baseURL": (
                "https://example.invalid/v1"
                "?api_key=%3Credacted%3E&region=cn"
            ),
            "apiKey": "<redacted>",
            "authorization": "<redacted>",
            "metadata": {
                "access_token": "<redacted>",
                "label": "writing",
            },
            "model": "model",
            "model_profile": "profile",
            "max_tokens": 2_048,
            "thinking": {"type": "disabled"},
        },
        "maxCallOutputTokens": 2_048,
        "reasoningMode": "disabled",
        "toolChoice": "auto",
        "toolNames": ["readThing"],
        "messageCount": 1,
        "messageRoles": ["user"],
            "profileId": "profile",
            "modelOutputCapabilities": {
                "maxCallOutputTokens": 393_216,
                "thinkingTokenAccounting": "unknown",
            },
            "outputLimit": {
                "maxTokens": 2_048,
                "source": "user_override",
                "profileMaxTokens": 393_216,
            },
        }
    assert "private message" not in json.dumps(parameters)
    assert "private-provider-key" not in json.dumps(parameters)
    assert "caller-owned" not in json.dumps(parameters)


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
        (
            400,
            "reasoning_content in thinking mode must be passed back",
            "provider_reasoning_context_invalid",
        ),
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
            }],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 8,
                "total_tokens": 128,
                "prompt_tokens_details": {"cached_tokens": 20},
                "completion_tokens_details": {"reasoning_tokens": 3},
            },
        }

    async def _stream(key, messages, options, provider, signal):
        captured.update({
            "key": key,
            "messages": messages,
            "options": options,
            "provider": provider,
            "signal": signal,
        })
        return {"applied_output_limit": options.get("max_tokens"), "stream": _chunks(), "model": "resolved-model"}

    async def _complete(key, messages, options, provider, signal):
        assert key == "secret"
        assert messages[0]["content"] == "hello"
        assert options["model"] == "requested-model"
        assert provider == "anthropic"
        assert signal is None
        return {
            "applied_output_limit": options.get("max_tokens"),
            "message": {"role": "assistant", "content": "complete"},
            "model": "resolved-model",
            "finish_reason": "stop",
        }

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_stream", _stream)
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _complete,
    )
    gateway = ProviderModelGateway("secret")
    request = ModelRequest(
        provider="anthropic",
        model="requested-model",
        capability_snapshot=resolve_model_profile(
            "deepseek:deepseek-v4-flash",
            "deepseek-v4-flash",
            "https://api.deepseek.com",
        ).capability_snapshot(context_window_tokens=1_000_000),
        options={
            "baseURL": "https://example.invalid",
            "tool_choice": "required",
            "max_tokens": 999_999,
            "thinking": {"type": "disabled"},
            "temperature": 1.0,
            "metadata": {"tags": ["writing"]},
        },
    )
    messages = (AgentMessage(
            role="user",
            content="hello",
            attributes={
                "name": "caller-name",
                "context_name": "writing_retrieval",
                "untrusted": True,
                "purra_plan": True,
            },
            host_metadata={
                "writing_outline_sources": [{
                    "outlineId": "outline-1",
                    "text": "private receipt",
                }],
            },
        ),)
    invocation = ModelInvocation(
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
            output_limit=_limit(2_048),
            reasoning_mode=ReasoningMode.DISABLED,
        )
    chunks, completion = await assert_model_gateway_conforms(
        gateway=gateway,
        messages=messages,
        invocation=invocation,
        expected_model="resolved-model",
        expected_tool_names=("readThing",),
    )

    assert isinstance(gateway, ModelGateway)
    assert completion.message.content == "complete"
    assert chunks[0].content_delta == "ok"
    assert chunks[0].reasoning_delta == "brief"
    assert chunks[0].finish_reason == "tool_calls"
    assert chunks[0].tool_call_deltas[0].id == "call-1"
    assert chunks[0].tool_call_deltas[0].name == "readThing"
    assert chunks[0].tool_call_deltas[0].arguments_fragment == "{}"
    assert chunks[0].usage == ModelTokenUsage(
        input_tokens=120,
        output_tokens=8,
        total_tokens=128,
        cached_input_tokens=20,
        reasoning_output_tokens=3,
    )
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
    assert captured["options"]["thinking"] == {"type": "disabled"}
    assert captured["options"]["model_profile"] == "deepseek:deepseek-v4-flash"
    assert captured["options"]["temperature"] == 1.0
    assert type(captured["options"]) is dict
    assert type(captured["options"]["metadata"]) is dict
    assert type(captured["options"]["metadata"]["tags"]) is list
    parameters = captured["options"]["tools"][0]["function"]["parameters"]
    assert type(parameters) is dict
    assert type(parameters["properties"]) is dict
    assert json.loads(json.dumps(captured["options"])) == captured["options"]


def test_provider_options_keep_required_tool_choice_provider_neutral():
    request = ModelRequest(provider="openai", model="requested-model")
    first = ToolSchema(
        name="readFirst",
        description="Read the first input",
        parameters={"type": "object", "properties": {}},
    )
    second = ToolSchema(
        name="readSecond",
        description="Read the second input",
        parameters={"type": "object", "properties": {}},
    )

    single = provider_model_gateway._provider_options(ModelInvocation(
        request=request,
        tools=(first,),
        tool_choice=ToolChoiceMode.REQUIRED,
    ))
    multiple = provider_model_gateway._provider_options(ModelInvocation(
        request=request,
        tools=(first, second),
        tool_choice=ToolChoiceMode.REQUIRED,
    ))

    assert single["tool_choice"] == "required"
    assert multiple["tool_choice"] == "required"


@pytest.mark.asyncio
async def test_provider_model_gateway_normalizes_non_stream_completion(monkeypatch):
    async def _complete(_key, _messages, options, _provider, _signal):
        assert "tools" not in options
        assert "tool_choice" not in options
        return {
            "applied_output_limit": options.get("max_tokens"),
            "message": {"role": "assistant", "content": "planned", "reasoning_content": "brief"},
            "model": "resolved-model",
            "usage": {
                "input_tokens": 90,
                "output_tokens": 10,
                "cache_read_input_tokens": 5,
            },
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
    assert completion.message.reasoning == "brief"
    assert completion.usage == ModelTokenUsage(
        input_tokens=95,
        output_tokens=10,
        cached_input_tokens=5,
    )


@pytest.mark.asyncio
async def test_provider_model_gateway_maps_typed_tool_continuation_messages(monkeypatch):
    captured: list[dict] = []

    async def _stream(_key, messages, _options, _provider, _signal):
        captured.extend(messages)

        async def _chunks():
            yield {"choices": [{"message": {"content": "fallback"}, "finish_reason": "stop"}]}

        return {"applied_output_limit": _options.get("max_tokens"), "stream": _chunks(), "model": "model"}

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_stream", _stream)
    stream = await ProviderModelGateway("secret").stream(
        [
            AgentMessage(
                role="assistant",
                content="",
                reasoning="brief",
                tool_calls=(ToolCall(id="call-1", name="readThing", arguments_json="{}"),),
            ),
            AgentMessage(role="tool", content="result", tool_call_id="call-1"),
        ],
        ModelInvocation(
            request=ModelRequest(
                provider="openai",
                model="model",
                capability_snapshot=_snapshot(
                    protocol=ModelProtocolCapabilities(
                        reasoning_replay=ReasoningReplayPolicy.REQUIRED,
                    ),
                ),
            ),
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

        return {"applied_output_limit": _args[2].get("max_tokens"), "stream": _chunks(), "model": "model"}

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
async def test_provider_model_gateway_standardizes_completion_failures(monkeypatch):
    async def _complete(*_args, **_kwargs):
        raise httpx.ReadError("completion contained private transport details")

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _complete,
    )

    with pytest.raises(ModelGatewayError) as captured:
        await ProviderModelGateway("secret").complete(
            [AgentMessage(role="user", content="hello")],
            ModelInvocation(
                request=ModelRequest(provider="openai", model="model"),
                tool_choice=ToolChoiceMode.NONE,
            ),
        )

    assert captured.value.code == "upstream_stream_interrupted"
    assert captured.value.retryable is True
    assert "private" not in str(captured.value)


@pytest.mark.asyncio
async def test_provider_model_gateway_treats_wrapped_connection_errors_as_retryable(
    monkeypatch,
):
    async def _complete(*_args, **_kwargs):
        try:
            raise httpx.ConnectError("private DNS or socket detail")
        except httpx.ConnectError as cause:
            raise RuntimeError("SDK connection wrapper") from cause

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _complete,
    )

    with pytest.raises(ModelGatewayError) as captured:
        await ProviderModelGateway("secret").complete(
            [AgentMessage(role="user", content="hello")],
            ModelInvocation(
                request=ModelRequest(provider="openai", model="model"),
                tool_choice=ToolChoiceMode.NONE,
            ),
        )

    assert captured.value.code == "upstream_stream_interrupted"
    assert captured.value.retryable is True
    assert "private" not in str(captured.value)
    assert "SDK" not in str(captured.value)


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

        return {"applied_output_limit": _args[2].get("max_tokens"), "stream": _chunks(), "model": "model"}

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

        return {"applied_output_limit": _args[2].get("max_tokens"), "stream": _chunks(), "model": "model"}

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
        # PurrA 0.3 clips the caller signal with its invocation deadline and
        # passes that reason-aware child signal to the Provider boundary.
        assert received_signal is not signal
        assert not received_signal.is_set()
        signal.set()
        assert received_signal.is_set()
        return {"applied_output_limit": _options.get("max_tokens"), "stream": raw_stream, "model": "resolved-model"}

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
            output_limit=_limit(2_048),
            signal=signal,
        )
    ]

    result = updates[-1]
    assert isinstance(result, AgentRuntimeResult)
    assert result.outcome is RuntimeOutcome.CANCELED
    assert result.error_code == "request_canceled"
    assert raw_stream.next_calls == 0
    assert raw_stream.close_calls == 1


@pytest.mark.asyncio
async def test_runtime_rejects_unknown_output_limit_before_provider_request(
    monkeypatch,
):
    provider_called = False

    async def _stream(*_args, **_kwargs):
        nonlocal provider_called
        provider_called = True
        raise AssertionError("unknown output limit must not reach the Provider")

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _stream,
    )
    runtime = AgentRuntime(model_gateway=ProviderModelGateway("secret"))
    updates = [
        update
        async for update in runtime.run(
            AgentRunRequest(
                messages=(AgentMessage(role="user", content="hello"),),
                model=ModelRequest(provider="openai", model="requested-model"),
                domain_context=DomainContext(namespace="test"),
            ),
        )
    ]

    result = updates[-1]
    assert isinstance(result, AgentRuntimeResult)
    assert result.outcome is RuntimeOutcome.FAILED
    assert result.error_code == "model_output_limit_unknown"
    assert provider_called is False


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("applied,usage", [(None, 1), (4096, 1), (2048, 2049)])
async def test_managed_provider_rejects_invalid_output_receipt_or_usage(
    monkeypatch, streaming, applied, usage,
):
    from purra.errors import ContractViolationError
    from purra.model_invocation import (
        AgentModelCall, AgentModelInvocationManager, ModelInvocationContext,
    )
    from purra.output import AgentOutputIntent, OutputCommitMode

    closed = False

    class RawStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            return {
                "choices": [{"delta": {"content": "answer"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": usage},
            }

        async def aclose(self):
            nonlocal closed
            closed = True

    async def provider(*args):
        assert args[2]["max_tokens"] == 2048
        result = {
            "model": "model", "message": {"role": "assistant", "content": "answer"},
            "finish_reason": "stop", "usage": {"prompt_tokens": 1, "completion_tokens": usage},
            "stream": RawStream(),
        }
        if applied is not None:
            result["applied_output_limit"] = applied
        return result

    monkeypatch.setattr(provider_model_gateway.provider_router, "create_chat_stream", provider)
    monkeypatch.setattr(provider_model_gateway.provider_router, "create_chat_no_stream", provider)
    manager = AgentModelInvocationManager(ProviderModelGateway("test-key"))
    call = AgentModelCall(
        request=ModelRequest(provider="openai", model="model", capability_snapshot=_snapshot()),
        output_limit=_limit(2048),
        output_intent=AgentOutputIntent.STRUCTURED_PRIVATE,
        commit_mode=OutputCommitMode.PRIVATE,
    )
    context = ModelInvocationContext(run_id="output-contract-test")
    messages = (AgentMessage(role="user", content="hello"),)
    with pytest.raises(ContractViolationError) as error:
        if streaming:
            result = await manager.stream(messages, call, context)
            async for _ in result.chunks:
                pass
        else:
            await manager.complete(messages, call, context)
    assert error.value.code == "model_gateway_contract_violation"
    if streaming:
        assert closed
