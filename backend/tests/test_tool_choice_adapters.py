from __future__ import annotations

import pytest


def test_anthropic_tool_choice_translation():
    from infrastructure.models.anthropic_chat import openai_tool_choice_to_anthropic

    assert openai_tool_choice_to_anthropic("required") == {"type": "any"}
    assert openai_tool_choice_to_anthropic("auto") == {"type": "auto"}
    assert openai_tool_choice_to_anthropic({
        "type": "function",
        "function": {"name": "getChapterContent"},
    }) == {"type": "tool", "name": "getChapterContent"}


@pytest.mark.asyncio
async def test_openai_stream_forwards_required_tool_choice(monkeypatch: pytest.MonkeyPatch):
    from infrastructure.models import openai_chat

    captured: dict = {}

    async def _empty_stream():
        if False:
            yield None

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _empty_stream()

    class _Client:
        class _Chat:
            completions = _Completions()

        chat = _Chat()

    monkeypatch.setattr(openai_chat, "_create_client", lambda *_args: _Client())
    await openai_chat.chat_stream(
        "k",
        [{"role": "user", "content": "read"}],
        {
            "model": "mock",
            "baseURL": "http://example.invalid",
            "tools": [{"type": "function", "function": {"name": "read", "parameters": {}}}],
            "tool_choice": "required",
        },
    )

    assert captured["tool_choice"] == "required"
    assert captured["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
async def test_openai_stream_retries_without_unsupported_usage_option(
    monkeypatch: pytest.MonkeyPatch,
):
    from infrastructure.models import openai_chat

    calls: list[dict] = []

    async def _empty_stream():
        if False:
            yield None

    class _Completions:
        async def create(self, **kwargs):
            calls.append(dict(kwargs))
            if "stream_options" in kwargs:
                raise ValueError("stream_options include_usage is unsupported")
            return _empty_stream()

    class _Client:
        class _Chat:
            completions = _Completions()

        chat = _Chat()

    monkeypatch.setattr(openai_chat, "_create_client", lambda *_args: _Client())
    await openai_chat.chat_stream(
        "k",
        [{"role": "user", "content": "read"}],
        {"model": "mock", "baseURL": "http://example.invalid"},
    )

    assert len(calls) == 2
    assert calls[0]["stream_options"] == {"include_usage": True}
    assert "stream_options" not in calls[1]


@pytest.mark.asyncio
async def test_minimax_openai_stream_requests_split_reasoning(
    monkeypatch: pytest.MonkeyPatch,
):
    from infrastructure.models import openai_chat

    captured: dict = {}

    async def _empty_stream():
        if False:
            yield None

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _empty_stream()

    class _Client:
        class _Chat:
            completions = _Completions()

        chat = _Chat()

    monkeypatch.setattr(openai_chat, "_create_client", lambda *_args: _Client())
    await openai_chat.chat_stream(
        "k",
        [{"role": "user", "content": "reason"}],
        {
            "model": "MiniMax-M3",
            "baseURL": "https://api.minimaxi.com/v1",
            "thinking": {"type": "enabled"},
        },
    )

    assert captured["extra_body"] == {"reasoning_split": True}


@pytest.mark.asyncio
async def test_kimi_k3_stream_forces_max_reasoning_and_preserves_history(
    monkeypatch: pytest.MonkeyPatch,
):
    from infrastructure.models import openai_chat

    captured: dict = {}

    async def _empty_stream():
        if False:
            yield None

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return _empty_stream()

    class _Client:
        class _Chat:
            completions = _Completions()

        chat = _Chat()

    messages = [
        {"role": "assistant", "content": "", "reasoning_content": "prior thought"},
        {"role": "user", "content": "continue"},
    ]
    monkeypatch.setattr(openai_chat, "_create_client", lambda *_args: _Client())
    await openai_chat.chat_stream(
        "k",
        messages,
        {
            "model": "kimi-k3",
            "model_profile": "moonshot:kimi-k3",
            "baseURL": "https://api.moonshot.cn/v1",
            "thinking": {"type": "disabled"},
        },
    )

    assert captured["extra_body"] == {"reasoning_effort": "max"}
    assert captured["messages"] == messages


@pytest.mark.asyncio
async def test_openai_stream_propagates_consumer_close_to_raw_stream(
    monkeypatch: pytest.MonkeyPatch,
):
    from infrastructure.models import openai_chat

    class _Chunk:
        def model_dump(self):
            return {"choices": [{"delta": {"content": "first"}}]}

    class _TrackedRawStream:
        def __init__(self):
            self._sent = False
            self.close_calls = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._sent:
                raise StopAsyncIteration
            self._sent = True
            return _Chunk()

        async def close(self):
            self.close_calls += 1

    raw_stream = _TrackedRawStream()

    class _Completions:
        async def create(self, **_kwargs):
            return raw_stream

    class _Client:
        class _Chat:
            completions = _Completions()

        chat = _Chat()

    monkeypatch.setattr(openai_chat, "_create_client", lambda *_args: _Client())
    result = await openai_chat.chat_stream(
        "k",
        [{"role": "user", "content": "read"}],
        {"model": "mock", "baseURL": "http://example.invalid"},
    )

    stream = result["stream"]
    assert await anext(stream) == {
        "choices": [{"delta": {"content": "first"}}],
    }
    await stream.aclose()

    assert raw_stream.close_calls == 1


@pytest.mark.asyncio
async def test_openai_terminal_chunk_collects_usage_tail_before_consumer_break(
    monkeypatch: pytest.MonkeyPatch,
):
    from infrastructure.models import openai_chat

    class _Chunk:
        def __init__(self, value):
            self.value = value

        def model_dump(self):
            return self.value

    class _TrackedRawStream:
        def __init__(self):
            self.next_calls = 0
            self.close_calls = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.next_calls += 1
            if self.next_calls == 1:
                return _Chunk({
                    "choices": [{
                        "delta": {"content": "done"},
                        "finish_reason": "stop",
                    }],
                })
            if self.next_calls == 2:
                return _Chunk({
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 120,
                        "completion_tokens": 8,
                        "total_tokens": 128,
                    },
                })
            raise AssertionError("consumer must stop after the usage chunk")

        async def close(self):
            self.close_calls += 1

    raw_stream = _TrackedRawStream()

    class _Completions:
        async def create(self, **_kwargs):
            return raw_stream

    class _Client:
        class _Chat:
            completions = _Completions()

        chat = _Chat()

    monkeypatch.setattr(openai_chat, "_create_client", lambda *_args: _Client())
    result = await openai_chat.chat_stream(
        "k",
        [{"role": "user", "content": "read"}],
        {"model": "mock", "baseURL": "http://example.invalid"},
    )
    received = []
    async for chunk in result["stream"]:
        received.append(chunk)
        break

    assert received == [{
        "choices": [{
            "delta": {"content": "done"},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 8,
            "total_tokens": 128,
        },
    }]
    assert raw_stream.next_calls == 2
    assert raw_stream.close_calls == 1


@pytest.mark.asyncio
async def test_openai_stream_closes_raw_stream_before_first_iteration(
    monkeypatch: pytest.MonkeyPatch,
):
    from infrastructure.models import openai_chat

    class _TrackedRawStream:
        def __init__(self):
            self.next_calls = 0
            self.close_calls = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.next_calls += 1
            raise AssertionError("raw stream must not be read before close")

        async def close(self):
            self.close_calls += 1

    raw_stream = _TrackedRawStream()

    class _Completions:
        async def create(self, **_kwargs):
            return raw_stream

    class _Client:
        class _Chat:
            completions = _Completions()

        chat = _Chat()

    monkeypatch.setattr(openai_chat, "_create_client", lambda *_args: _Client())
    result = await openai_chat.chat_stream(
        "k",
        [{"role": "user", "content": "read"}],
        {"model": "mock", "baseURL": "http://example.invalid"},
    )
    await result["stream"].aclose()

    assert raw_stream.next_calls == 0
    assert raw_stream.close_calls == 1
