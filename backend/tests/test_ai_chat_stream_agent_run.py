from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db
from routers.ai import chat_stream
from schemas.ai import ChatStreamRequest

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        yield db
    finally:
        await db.close()


class _Request:
    async def is_disconnected(self) -> bool:
        return False


async def _collect_sse_events(response, limit: int = 20) -> list[dict]:
    events: list[dict] = []
    async for raw in response.body_iterator:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        stripped = text.strip()
        if stripped.startswith("{"):
            events.append(json.loads(stripped))
            if len(events) >= limit or any(evt.get("done") for evt in events):
                break
            continue
        for line in text.splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[len("data: "):].strip()
            if payload and payload != "[DONE]":
                events.append(json.loads(payload))
        if len(events) >= limit or any(evt.get("done") for evt in events):
            break
    return events


async def test_chat_stream_emits_agent_run_events_before_done(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _none_plan(**_kwargs):
        return None

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _none_plan)

    def _skills():
        return [
            {
                "name": "getChapterContent",
                "description": "读取章节",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }
        ]

    monkeypatch.setattr("services.tool_router.get_api_skill_items", _skills)

    async def _stream():
        yield {
            "choices": [
                {
                    "delta": {"content": "完成"},
                    "finish_reason": "stop",
                }
            ]
        }

    async def _create_chat_stream(*_args, **_kwargs):
        return {"stream": _stream(), "model": "mock-model"}

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "帮我检查前三章节奏并给出修改建议"}],
            apiKey="k",
            options={"model": "mock-model"},
            enableAgentTools=True,
            bookId="book1",
            chatAgentMode="agent",
        ),
        _Request(),
    )

    events = await _collect_sse_events(response)
    event_keys = [next(iter(evt.keys())) for evt in events]

    assert "agentRunStarted" in event_keys
    assert "agentRunTodosUpdated" in event_keys
    assert event_keys.index("agentRunStarted") < event_keys.index("done")
    assert event_keys.index("agentRunTodosUpdated") < event_keys.index("done")
