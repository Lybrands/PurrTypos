from __future__ import annotations

import pytest


class _EmptyStream:
    def __init__(self):
        self.close_calls = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def aclose(self):
        self.close_calls += 1


class _FakeClient:
    def __init__(self):
        self.create_calls: list[dict] = []
        self.streams: list[_EmptyStream] = []
        self.close_calls = 0
        self.chat = self._Chat(self)

    class _Chat:
        def __init__(self, client: "_FakeClient"):
            self.completions = _FakeClient._Completions(client)

    class _Completions:
        def __init__(self, client: "_FakeClient"):
            self._client = client

        async def create(self, **kwargs):
            self._client.create_calls.append(dict(kwargs))
            stream = _EmptyStream()
            self._client.streams.append(stream)
            return stream

    async def aclose(self):
        self.close_calls += 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "profile_id", "thinking_type"),
    [
        ("deepseek-v4-pro", "deepseek:deepseek-v4-pro", "enabled"),
        ("deepseek-v4-flash", "deepseek:deepseek-v4-flash", "disabled"),
    ],
)
async def test_deepseek_v4_uses_openai_compatible_thinking_and_tools(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    profile_id: str,
    thinking_type: str,
):
    from infrastructure.models import openai_chat

    client = _FakeClient()
    monkeypatch.setattr(openai_chat, "_create_client", lambda *_args: client)

    result = await openai_chat.chat_stream(
        "secret",
        [{"role": "user", "content": "继续写作"}],
        {
            "model": model,
            "model_profile": profile_id,
            "baseURL": "https://api.deepseek.com",
            "thinking": {"type": thinking_type},
            "max_tokens": 16_384,
            "tools": [{
                "type": "function",
                "function": {"name": "read", "parameters": {}},
            }],
            "tool_choice": "required",
        },
    )

    assert result["model"] == model
    assert client.create_calls == [{
        "model": model,
        "messages": [{"role": "user", "content": "继续写作"}],
        "stream": True,
        "extra_body": {"thinking": {"type": thinking_type}},
        "max_tokens": 16_384,
        "tools": [{
            "type": "function",
            "function": {"name": "read", "parameters": {}},
        }],
        "tool_choice": "required",
        "stream_options": {"include_usage": True},
    }]

    await result["stream"].aclose()
    assert client.streams[0].close_calls == 1
    assert client.close_calls == 1
