from __future__ import annotations

import pytest


def test_anthropic_tool_choice_translation():
    from services.anthropic_chat import openai_tool_choice_to_anthropic

    assert openai_tool_choice_to_anthropic("required") == {"type": "any"}
    assert openai_tool_choice_to_anthropic("auto") == {"type": "auto"}
    assert openai_tool_choice_to_anthropic({
        "type": "function",
        "function": {"name": "getChapterContent"},
    }) == {"type": "tool", "name": "getChapterContent"}


@pytest.mark.asyncio
async def test_openai_stream_forwards_required_tool_choice(monkeypatch: pytest.MonkeyPatch):
    from services import openai_chat

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
