from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from purra.errors import ContractViolationError

from infrastructure.models import anthropic_chat


class _FakeMessages:
    def __init__(self, events):
        self._events = tuple(events)

    async def create(self, **_kwargs):
        async def _stream():
            for event in self._events:
                yield event

        return _stream()


class _CapturingMessages(_FakeMessages):
    def __init__(self, events):
        super().__init__(events)
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return await super().create(**kwargs)


class _TrackedTailStream:
    def __init__(self, events, *, tail_mode: str):
        self._events = list(events)
        self._tail_mode = tail_mode
        self._blocked = asyncio.Event()
        self.next_calls = 0
        self.tail_polled = False
        self.close_calls = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.next_calls += 1
        if self._events:
            return self._events.pop(0)
        self.tail_polled = True
        if self._tail_mode == "error":
            raise RuntimeError("provider tail must not be observed")
        await self._blocked.wait()
        raise StopAsyncIteration

    async def close(self):
        self.close_calls += 1


class _TrackedMessages:
    def __init__(self, stream):
        self._stream = stream
        self.create_calls = 0

    async def create(self, **_kwargs):
        self.create_calls += 1
        return self._stream


async def _collect_chunks(monkeypatch, events, *, signal=None):
    client = SimpleNamespace(messages=_FakeMessages(events))
    monkeypatch.setattr(
        anthropic_chat,
        "_create_client",
        lambda _api_key, _base_url: client,
    )
    result = await anthropic_chat.chat_stream_as_openai_format(
        "key",
        [{"role": "user", "content": "hello"}],
        {"model": "model", "max_tokens": 64},
        signal,
    )
    return [chunk async for chunk in result["stream"]]


@pytest.mark.asyncio
async def test_anthropic_stream_emits_finish_only_after_message_stop(monkeypatch):
    chunks = await _collect_chunks(monkeypatch, (
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(usage=SimpleNamespace(
                input_tokens=100,
                cache_creation_input_tokens=20,
                cache_read_input_tokens=30,
            )),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="answer"),
        ),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(output_tokens=7),
        ),
        SimpleNamespace(type="message_stop"),
    ))

    assert chunks[0]["choices"][0]["delta"]["content"] == "answer"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["usage"] == {
        "prompt_tokens": 150,
        "completion_tokens": 7,
        "total_tokens": 157,
        "prompt_tokens_details": {"cached_tokens": 30},
    }
    assert "completion_tokens_details" not in chunks[-1]["usage"]


@pytest.mark.asyncio
async def test_anthropic_stream_extracts_reported_thinking_usage(monkeypatch):
    chunks = await _collect_chunks(monkeypatch, (
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(usage=SimpleNamespace(input_tokens=100)),
        ),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
            usage=SimpleNamespace(
                output_tokens=7,
                output_tokens_details=SimpleNamespace(thinking_tokens=5),
            ),
        ),
        SimpleNamespace(type="message_stop"),
    ))

    assert chunks[-1]["usage"]["completion_tokens_details"] == {
        "reasoning_tokens": 5,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stop_reason", "finish_reason"),
    [
        ("model_context_window_exceeded", "length"),
        ("refusal", "content_filter"),
        ("pause_turn", "pause_turn"),
        ("provider_specific_unknown", "provider_specific_unknown"),
    ],
)
async def test_anthropic_non_success_stop_reasons_never_become_stop(
    monkeypatch,
    stop_reason,
    finish_reason,
):
    chunks = await _collect_chunks(monkeypatch, (
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason=stop_reason),
        ),
        SimpleNamespace(type="message_stop"),
    ))

    assert chunks[-1]["choices"][0]["finish_reason"] == finish_reason
    assert finish_reason != "stop"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stop_reason", "finish_reason"),
    [
        ("model_context_window_exceeded", "length"),
        ("refusal", "content_filter"),
        ("pause_turn", "pause_turn"),
    ],
)
async def test_anthropic_non_stream_non_success_stop_reasons_fail_closed(
    monkeypatch,
    stop_reason,
    finish_reason,
):
    async def create(**_params):
        return SimpleNamespace(
            content=[],
            model="model",
            stop_reason=stop_reason,
            usage=None,
        )

    monkeypatch.setattr(
        anthropic_chat,
        "_create_client",
        lambda *_: SimpleNamespace(messages=SimpleNamespace(create=create)),
    )

    result = await anthropic_chat.chat_no_stream_as_openai_format(
        "key",
        [{"role": "user", "content": "hello"}],
        {"model": "model", "max_tokens": 64},
    )

    assert result["finish_reason"] == finish_reason
    assert finish_reason != "stop"


@pytest.mark.asyncio
async def test_minimax_native_thinking_is_parsed_without_anthropic_budget_param(
    monkeypatch,
):
    messages = _CapturingMessages((
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="thinking_delta", thinking="reasoning"),
        ),
        SimpleNamespace(type="message_stop"),
    ))
    client = SimpleNamespace(messages=messages)
    monkeypatch.setattr(
        anthropic_chat,
        "_create_client",
        lambda _api_key, _base_url: client,
    )

    result = await anthropic_chat.chat_stream_as_openai_format(
        "key",
        [{"role": "user", "content": "hello"}],
        {
            "model": "MiniMax-M3",
            "model_profile": "minimax:MiniMax-M3", "profile_binding": "compatible",
            "baseURL": "https://api.minimaxi.com/anthropic",
            "thinking": {"type": "enabled"},
            "max_tokens": 8192,
        },
    )
    chunks = [chunk async for chunk in result["stream"]]

    assert "thinking" not in messages.kwargs
    assert chunks[0]["choices"][0]["delta"]["reasoning_content"] == "reasoning"


@pytest.mark.asyncio
@pytest.mark.parametrize("tail_mode", ("error", "hang"))
async def test_anthropic_message_stop_is_authoritative_and_closes_raw_stream(
    monkeypatch,
    tail_mode,
):
    raw_stream = _TrackedTailStream((
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
        ),
        SimpleNamespace(type="message_stop"),
    ), tail_mode=tail_mode)
    messages = _TrackedMessages(raw_stream)
    client = SimpleNamespace(messages=messages)
    monkeypatch.setattr(
        anthropic_chat,
        "_create_client",
        lambda _api_key, _base_url: client,
    )

    result = await anthropic_chat.chat_stream_as_openai_format(
        "key",
        [{"role": "user", "content": "hello"}],
        {"model": "model", "max_tokens": 64},
    )
    chunks = await asyncio.wait_for(
        _collect_stream(result["stream"]),
        timeout=1,
    )

    assert chunks == [{
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }]
    assert messages.create_calls == 1
    assert raw_stream.next_calls == 2
    assert raw_stream.tail_polled is False
    assert raw_stream.close_calls == 1


@pytest.mark.asyncio
async def test_anthropic_terminal_chunk_closes_raw_stream_before_consumer_break(
    monkeypatch,
):
    raw_stream = _TrackedTailStream((
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
        ),
        SimpleNamespace(type="message_stop"),
    ), tail_mode="error")
    messages = _TrackedMessages(raw_stream)
    client = SimpleNamespace(messages=messages)
    monkeypatch.setattr(
        anthropic_chat,
        "_create_client",
        lambda _api_key, _base_url: client,
    )

    result = await anthropic_chat.chat_stream_as_openai_format(
        "key",
        [{"role": "user", "content": "hello"}],
        {"model": "model", "max_tokens": 64},
    )
    received = []
    async for chunk in result["stream"]:
        received.append(chunk)
        break

    assert received == [{
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }]
    assert raw_stream.next_calls == 2
    assert raw_stream.tail_polled is False
    assert raw_stream.close_calls == 1


@pytest.mark.asyncio
async def test_anthropic_stream_closes_raw_stream_before_first_iteration(
    monkeypatch,
):
    raw_stream = _TrackedTailStream((), tail_mode="error")
    messages = _TrackedMessages(raw_stream)
    client = SimpleNamespace(messages=messages)
    monkeypatch.setattr(
        anthropic_chat,
        "_create_client",
        lambda _api_key, _base_url: client,
    )

    result = await anthropic_chat.chat_stream_as_openai_format(
        "key",
        [{"role": "user", "content": "hello"}],
        {"model": "model", "max_tokens": 64},
    )
    await result["stream"].aclose()

    assert messages.create_calls == 1
    assert raw_stream.next_calls == 0
    assert raw_stream.close_calls == 1


@pytest.mark.asyncio
async def test_anthropic_premature_eof_never_synthesizes_success(monkeypatch):
    chunks = await _collect_chunks(monkeypatch, (
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="partial"),
        ),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
        ),
    ))

    assert chunks[0]["choices"][0]["delta"]["content"] == "partial"
    assert all(
        chunk["choices"][0].get("finish_reason") is None
        for chunk in chunks
    )


@pytest.mark.asyncio
async def test_anthropic_cancellation_never_synthesizes_success(monkeypatch):
    signal = asyncio.Event()
    signal.set()

    from purra.cancellation import OperationCanceled
    with pytest.raises(OperationCanceled):
        await _collect_chunks(monkeypatch, (SimpleNamespace(type="message_stop"),), signal=signal)



async def _collect_stream(stream):
    return [chunk async for chunk in stream]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("requested,budget", [(2048, 1024), (8192, 3072)])
async def test_anthropic_preserves_the_explicit_budget_and_sdk_limit(
    monkeypatch, streaming, requested, budget,
):
    from infrastructure.models.profiles.base import ModelProfile

    captured = {}
    monkeypatch.setattr(anthropic_chat, "profile_for_options", lambda *_: ModelProfile())

    async def create(**params):
        captured.update(params)
        if not streaming:
            return SimpleNamespace(content=[], model="model", stop_reason="end_turn", usage=None)

        async def events():
            yield SimpleNamespace(type="message_stop")
        return events()

    monkeypatch.setattr(anthropic_chat, "_create_client", lambda *_: SimpleNamespace(
        messages=SimpleNamespace(create=create),
    ))
    chat = (anthropic_chat.chat_stream_as_openai_format if streaming
            else anthropic_chat.chat_no_stream_as_openai_format)
    result = await chat("test-key", [{"role": "user", "content": "hello"}], {
        "model": "model",
        "thinking": {"type": "enabled", "budget_tokens": budget},
        "max_tokens": requested,
    })
    assert result["applied_generation_limit"] == captured["max_tokens"] == requested
    assert captured["thinking"] == {
        "type": "enabled",
        "budget_tokens": budget,
    }
    if streaming:
        await result["stream"].aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "reasoning_options",
    [
        {"thinking": {"type": "enabled"}},
        {"thinking_enabled": True},
    ],
)
async def test_anthropic_missing_explicit_thinking_budget_fails_before_connect(
    monkeypatch,
    streaming,
    reasoning_options,
):
    from infrastructure.models.profiles.base import ModelProfile

    connect_calls = []
    monkeypatch.setattr(anthropic_chat, "profile_for_options", lambda *_: ModelProfile())
    monkeypatch.setattr(
        anthropic_chat,
        "_create_client",
        lambda *_: connect_calls.append(True),
    )
    chat = (anthropic_chat.chat_stream_as_openai_format if streaming
            else anthropic_chat.chat_no_stream_as_openai_format)

    with pytest.raises(ContractViolationError) as caught:
        await chat("test-key", [{"role": "user", "content": "hello"}], {
            "model": "model",
            "max_tokens": 8192,
            **reasoning_options,
        })

    assert caught.value.code == "anthropic_thinking_budget_required"
    assert connect_calls == []
