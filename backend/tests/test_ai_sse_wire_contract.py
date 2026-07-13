from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from routers.ai import router as ai_router


TAPE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "agent_core"
    / "legacy_runtime_tape_v1.json"
)
TAPE = json.loads(TAPE_PATH.read_text(encoding="utf-8"))
DIRECT = next(case for case in TAPE["cases"] if case["id"] == "direct-answer")


def _decode_sse_wire(body: bytes) -> tuple[str, list[dict]]:
    text = body.decode("utf-8")
    assert re.search(r"\r?\n\r?\n\Z", text), repr(text[-80:])

    payloads: list[dict] = []
    for frame in (item for item in re.split(r"\r?\n\r?\n", text) if item):
        lines = frame.splitlines()
        data_lines = [line for line in lines if line.startswith("data: ")]
        if not data_lines:
            assert lines and all(line.startswith(":") for line in lines), frame
            continue
        payload = json.loads("\n".join(
            line[len("data: "):] for line in data_lines
        ))
        assert isinstance(payload, dict)
        payloads.append(payload)
    return text, payloads


@pytest.mark.asyncio
@pytest.mark.parametrize("core_runtime", [False, True], ids=["legacy", "core"])
async def test_chat_stream_preserves_sse_wire_contract(monkeypatch, core_runtime):
    provider_calls = 0
    core_bridge_calls = 0
    model_chunks = DIRECT["modelRounds"][0]
    expected_text = DIRECT["expected"]["finalText"]
    user_messages = DIRECT["request"]["messages"]

    async def _fake_create_chat_stream(
        key,
        messages,
        options,
        api_provider,
        signal=None,
    ):
        nonlocal provider_calls
        provider_calls += 1
        assert key == "tape-key"
        assert api_provider == "openai"
        assert options["model"] == "tape-model"
        assert "tools" not in options
        assert messages[-1] == user_messages[-1]
        assert signal is not None

        async def _stream():
            for chunk in model_chunks:
                yield chunk

        return {"stream": _stream(), "model": "tape-model"}

    monkeypatch.setattr(
        "services.ai_provider.create_chat_stream",
        _fake_create_chat_stream,
    )
    from application import legacy_runtime_bridge

    original_core_bridge = legacy_runtime_bridge.stream_core_runtime_as_legacy_chunks

    async def _spy_core_bridge(**kwargs):
        nonlocal core_bridge_calls
        core_bridge_calls += 1
        async for chunk in original_core_bridge(**kwargs):
            yield chunk

    monkeypatch.setattr(
        legacy_runtime_bridge,
        "stream_core_runtime_as_legacy_chunks",
        _spy_core_bridge,
    )
    monkeypatch.setattr("config.AGENT_CORE_RUNTIME_ENABLED", core_runtime)
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await asyncio.wait_for(
            client.post(
                "/api/ai/chat/stream",
                json={
                    "messages": user_messages,
                    "apiKey": "tape-key",
                    "apiProvider": "openai",
                    "options": {"model": "tape-model"},
                    "enableAgentTools": False,
                    "chatAgentMode": "ask",
                    "contextWindow": "32k",
                },
            ),
            timeout=5,
        )

    assert provider_calls == 1
    assert core_bridge_calls == (1 if core_runtime else 0)
    assert response.status_code == 200
    assert response.headers["content-type"].split(";", 1)[0].strip() == "text/event-stream"

    raw, payloads = _decode_sse_wire(response.content)
    assert "data: " in raw
    assert len(payloads) == 3
    assert "contextBudget" in payloads[0]
    assert payloads[1] == {"delta": expected_text}
    assert payloads[2] == {"done": True, "model": "tape-model"}
    assert sum(event.get("done") is True for event in payloads) == 1
    assert not any(event.get("error") for event in payloads)


@pytest.mark.asyncio
async def test_caller_tool_choice_preserves_legacy_path_and_exact_value(monkeypatch):
    caller_tool_choice = {
        "type": "function",
        "function": {"name": "externalSearch"},
    }
    caller_tools = [{
        "type": "function",
        "function": {
            "name": "externalSearch",
            "description": "Search an external index",
            "parameters": {"type": "object", "properties": {}},
        },
    }]
    captured_options: list[dict] = []
    core_bridge_calls = 0

    async def _fake_create_chat_stream(
        _key,
        _messages,
        options,
        _api_provider,
        signal=None,
    ):
        captured_options.append(options)
        assert signal is not None

        async def _stream():
            yield {
                "choices": [{
                    "delta": {"content": "done"},
                    "finish_reason": "stop",
                }],
            }

        return {"stream": _stream(), "model": "tool-choice-model"}

    async def _spy_core_bridge(**_kwargs):
        nonlocal core_bridge_calls
        core_bridge_calls += 1
        if False:
            yield {}

    monkeypatch.setattr(
        "services.ai_provider.create_chat_stream",
        _fake_create_chat_stream,
    )
    monkeypatch.setattr(
        "application.legacy_runtime_bridge.stream_core_runtime_as_legacy_chunks",
        _spy_core_bridge,
    )
    monkeypatch.setattr("config.AGENT_CORE_RUNTIME_ENABLED", True)
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await asyncio.wait_for(
            client.post(
                "/api/ai/chat/stream",
                json={
                    "messages": [{"role": "user", "content": "search"}],
                    "apiKey": "caller-key",
                    "apiProvider": "openai",
                    "options": {
                        "model": "tool-choice-model",
                        "tool_choice": caller_tool_choice,
                    },
                    "tools": caller_tools,
                    "enableAgentTools": False,
                    "chatAgentMode": "ask",
                    "contextWindow": "32k",
                },
            ),
            timeout=5,
        )

    _, payloads = _decode_sse_wire(response.content)
    assert response.status_code == 200
    assert core_bridge_calls == 0
    assert len(captured_options) == 1
    assert captured_options[0]["tool_choice"] == caller_tool_choice
    assert captured_options[0]["tools"] == caller_tools
    assert payloads[-1] == {"done": True, "model": "tool-choice-model"}
