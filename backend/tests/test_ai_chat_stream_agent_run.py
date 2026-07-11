from __future__ import annotations

import json
from pathlib import Path

import httpx
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


async def test_chat_stream_emits_agent_run_started_before_done(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
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
    assert event_keys.index("agentRunStarted") < event_keys.index("done")


async def test_chat_stream_applies_runtime_todo_events(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
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
                    "delta": {
                        "content": '开始<agent_todos>{"title":"检查节奏","steps":[{"id":"read-context","title":"读取章节上下文","type":"read","executor":"tool","expectedTools":["getChapterContent"]},{"id":"review","title":"分析节奏问题","type":"analyze","executor":"model"}]}</agent_todos>继续'
                    },
                    "finish_reason": None,
                }
            ]
        }
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
    deltas = "".join(evt.get("delta", "") for evt in events)
    todos_event = next(evt["agentRunTodosUpdated"] for evt in events if "agentRunTodosUpdated" in evt)

    assert "<agent_todos>" not in deltas
    assert deltas == "开始继续完成"
    assert [step["title"] for step in todos_event["steps"]] == ["读取章节上下文", "分析节奏问题"]
    assert [step["status"] for step in todos_event["steps"]] == ["running", "pending"]


async def test_chat_stream_reports_upstream_read_error_without_raw_traceback(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _stream():
        yield {
            "choices": [
                {
                    "delta": {"content": "已输出一部分"},
                    "finish_reason": None,
                }
            ]
        }
        raise httpx.ReadError("stream interrupted")

    async def _create_chat_stream(*_args, **_kwargs):
        return {"stream": _stream(), "model": "mock-model"}

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "继续写"}],
            apiKey="k",
            options={"model": "mock-model"},
            enableAgentTools=False,
            bookId="book1",
            chatAgentMode="ask",
        ),
        _Request(),
    )

    events = await _collect_sse_events(response)

    assert events[0]["delta"] == "已输出一部分"
    assert "模型服务流式响应中断" in events[-1]["error"]
    assert "ReadError" not in events[-1]["error"]
