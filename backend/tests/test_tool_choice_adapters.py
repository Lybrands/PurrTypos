from __future__ import annotations

from types import SimpleNamespace

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
async def test_openai_stream_does_not_retry_unsupported_usage_option(
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
    with pytest.raises(ValueError, match="include_usage"):
        await openai_chat.chat_stream("k", [{"role": "user", "content": "read"}],
                                      {"model": "mock", "baseURL": "http://example.invalid"})
    assert len(calls) == 1
    assert calls[0]["stream_options"] == {"include_usage": True}


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
    result = await openai_chat.chat_stream(
        "k",
        [{"role": "user", "content": "reason"}],
        {
            "model": "MiniMax-M3",
            "model_profile": "minimax:MiniMax-M3", "profile_binding": "compatible",
            "baseURL": "https://api.minimaxi.com/v1",
            "thinking": {"type": "enabled"},
            "max_tokens": 2_048,
        },
    )

    assert captured["extra_body"] == {
        "reasoning_split": True,
        "thinking": {"type": "adaptive"},
    }
    assert result["applied_generation_limit"] == captured["max_completion_tokens"] == 2_048
    await result["stream"].aclose()
    assert "max_tokens" not in captured


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
    result = await openai_chat.chat_stream(
        "k",
        messages,
        {
            "model": "kimi-k3",
            "model_profile": "moonshot:kimi-k3", "profile_binding": "compatible",
            "baseURL": "https://api.moonshot.cn/v1",
            "thinking": {"type": "enabled"},
            "max_tokens": 2_048,
        },
    )

    assert captured["extra_body"] == {"reasoning_effort": "max"}
    assert result["applied_generation_limit"] == captured["max_completion_tokens"] == 2_048
    await result["stream"].aclose()
    assert "max_tokens" not in captured
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


class _ClosableOpenAIClient:
    def __init__(self, create):
        self.close_calls = 0
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=create),
        )

    async def close(self):
        self.close_calls += 1


@pytest.mark.asyncio
async def test_openai_stream_close_releases_owning_client(monkeypatch):
    from infrastructure.models import openai_chat

    async def _empty_stream():
        if False:
            yield None

    async def _create(**_kwargs):
        return _empty_stream()

    client = _ClosableOpenAIClient(_create)
    monkeypatch.setattr(openai_chat, "_create_client", lambda *_args: client)

    result = await openai_chat.chat_stream(
        "k",
        [{"role": "user", "content": "read"}],
        {"model": "mock", "baseURL": "http://example.invalid"},
    )
    await result["stream"].aclose()

    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_openai_stream_creation_failure_releases_client(monkeypatch):
    from infrastructure.models import openai_chat

    async def _create(**_kwargs):
        raise RuntimeError("stream setup failed")

    client = _ClosableOpenAIClient(_create)
    monkeypatch.setattr(openai_chat, "_create_client", lambda *_args: client)

    with pytest.raises(RuntimeError, match="stream setup failed"):
        await openai_chat.chat_stream(
            "k",
            [{"role": "user", "content": "read"}],
            {"model": "mock", "baseURL": "http://example.invalid"},
        )

    assert client.close_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("profile,parameter", [(None, "max_tokens"), ("moonshot:kimi-k3", "max_completion_tokens")])
async def test_openai_no_stream_releases_client_after_success(monkeypatch, profile, parameter):
    from infrastructure.models import openai_chat

    response = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(model_dump=lambda: {
                "role": "assistant",
                "content": "done",
            }),
        )],
        usage=None,
        model="mock",
    )

    async def _create(**kwargs):
        assert kwargs[parameter] == 2_048
        return response

    client = _ClosableOpenAIClient(_create)
    monkeypatch.setattr(openai_chat, "_create_client", lambda *_args: client)

    result = await openai_chat.chat_no_stream(
        "k",
        [{"role": "user", "content": "read"}],
        {"model": "mock", "baseURL": "http://example.invalid", "model_profile": profile, "max_tokens": 2_048},
    )

    assert result["message"]["content"] == "done"
    assert result["applied_generation_limit"] == 2_048
    assert client.close_calls == 1


@pytest.mark.asyncio
async def test_openai_title_generation_releases_client_after_success(monkeypatch):
    from infrastructure.models import openai_chat

    response = SimpleNamespace(
        model="mock",
        choices=[SimpleNamespace(
            message=SimpleNamespace(content="新的标题", model_dump=lambda: {"role": "assistant", "content": "新的标题"}),
            finish_reason="stop",
        )],
    )

    async def _create(**_kwargs):
        return response

    client = _ClosableOpenAIClient(_create)
    monkeypatch.setattr(openai_chat, "_create_client", lambda *_args: client)

    from application.session_title_service import generate_session_title
    title = await generate_session_title(
        api_key="k", provider="openai", prompt="conversation",
        options={"model": "mock", "baseURL": "http://example.invalid", "context_window": "128k",
                 "profile_max_generation_tokens": 8192, "supports_thinking": False, "thinking_only": False},
    )

    assert title == "新的标题"
    assert client.close_calls == 1
