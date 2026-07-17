from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

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
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="text_delta", text="answer"),
        ),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="end_turn"),
        ),
        SimpleNamespace(type="message_stop"),
    ))

    assert chunks[0]["choices"][0]["delta"]["content"] == "answer"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


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

    chunks = await _collect_chunks(
        monkeypatch,
        (SimpleNamespace(type="message_stop"),),
        signal=signal,
    )

    assert chunks == []


async def _collect_stream(stream):
    return [chunk async for chunk in stream]
