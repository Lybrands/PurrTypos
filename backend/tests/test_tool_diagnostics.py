from __future__ import annotations

import json

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db
from infrastructure.persistence.run_store import append_event, create_run
from infrastructure.persistence.tool_diagnostics import read_tool_diagnostics
from routers.ai import get_agent_run_tool_diagnostics

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db(tmp_path):
    database = DatabaseConnection(tmp_path)
    await database.init()
    set_db(database)
    try:
        yield database
    finally:
        await database.close()


async def test_tool_io_is_correlated_and_redacted_without_reasoning_or_other_runs(db, monkeypatch):
    monkeypatch.setattr("config.DEV_DIAGNOSTICS_ENABLED", True)
    run = await create_run(db, session_id=1, prompt="test", mode="agent")
    other = await create_run(db, session_id=1, prompt="test", mode="agent")
    await append_event(db, run, "tool.call_completed", {
        "toolCallId": "read", "toolName": "readSourceChapters", "outcome": "completed",
    })
    await append_event(db, run, "tool.calls_started", {"calls": [
        {"id": "read", "name": "readSourceChapters", "arguments_json": json.dumps({
            "chapterId": "chapter-1", "api_key": "ARG_SECRET",
            "nested": json.dumps({"headers": {"Authorization": "Bearer NESTED_SECRET"}}),
        })},
        {"id": "write", "name": "writeCandidate", "arguments_json": "{}"},
        {"id": "empty", "name": "readEmpty", "arguments_json": "null"},
        {"id": "unfinished", "name": "readPending", "arguments_json": "{invalid"},
    ]})
    await append_event(db, run, "tool.results", {"results": [
        {"tool_call_id": "read", "tool_name": "readSourceChapters", "from_cache": True,
         "content": json.dumps({"text": "正文", "access_token": "RESULT_SECRET",
                                "reasoning": "PRIVATE_REASONING"})},
        {"tool_call_id": "write", "tool_name": "writeCandidate", "error": "invalid_schema",
         "content": "invalid parameters; token=ERROR_SECRET; Basic BASIC_SECRET; "
                    "Bearer BEARER_SECRET; https://user:URL_SECRET@example.test"},
        {"tool_call_id": "empty", "content": ""},
    ]})
    await append_event(db, other, "tool.results", {"results": [
        {"tool_call_id": "read", "content": "OTHER_RUN_MARKER"},
    ]})
    await append_event(db, run, "provider.reasoning_delta", {"delta": "HIDDEN_REASONING"})
    response = await get_agent_run_tool_diagnostics(run)
    assert response["success"]
    calls = {call["toolCallId"]: call for call in response["data"]["calls"]}
    assert set(calls) == {"read", "write", "empty", "unfinished"}
    assert calls["read"]["cached"] is True
    assert calls["read"]["status"] == "completed"
    assert '"chapterId": "chapter-1"' in calls["read"]["arguments"]["text"]
    assert isinstance(json.loads(calls["read"]["arguments"]["text"])["nested"], str)
    assert "正文" in calls["read"]["result"]["text"]
    assert calls["write"]["status"] == "failed"
    assert calls["write"]["error"]["text"] == "invalid_schema"
    assert calls["empty"]["arguments"]["text"] == "null"
    assert calls["empty"]["result"]["text"] == ""
    assert calls["unfinished"]["arguments"]["text"] == "{invalid"
    assert "result" not in calls["unfinished"]
    assert "status" not in calls["unfinished"]
    serialized = json.dumps(response)
    for secret in ("ARG_SECRET", "NESTED_SECRET", "RESULT_SECRET", "ERROR_SECRET",
                   "PRIVATE_REASONING", "OTHER_RUN_MARKER", "HIDDEN_REASONING",
                   "BASIC_SECRET", "BEARER_SECRET", "URL_SECRET"):
        assert secret not in serialized
    assert "<redacted>" in serialized


async def test_tool_diagnostics_pages_and_reads_late_results_and_envelopes(db, monkeypatch):
    monkeypatch.setattr("infrastructure.persistence.tool_diagnostics._PAGE_SIZE", 1)
    run = await create_run(db, session_id=1, prompt="test", mode="agent")
    await append_event(db, run, "runtime.event", {
        "eventType": "tool.calls_started", "data": {"calls": [
            {"id": "call", "name": "read", "arguments_json": "{}"},
        ]},
    })
    await append_event(db, run, "tool.call_completed", {
        "toolCallId": "call", "outcome": "failed", "errorCode": "canceled",
    })
    first = await read_tool_diagnostics(db, run)
    assert first["hasMore"]
    assert "arguments" in first["calls"][0]
    second = await read_tool_diagnostics(db, run, after=first["nextCursor"])
    assert not second["hasMore"]
    assert second["calls"][0]["status"] == "failed"
    assert "arguments" not in second["calls"][0]
    assert "result" not in second["calls"][0]
    await append_event(db, run, "tool.results", {"results": [
        {"tool_call_id": "call", "error": "canceled", "content": "停止"},
    ]})
    third = await read_tool_diagnostics(db, run, after=second["nextCursor"])
    assert third["calls"][0]["result"]["text"] == "停止"
    assert third["nextCursor"] > second["nextCursor"]


async def test_tool_diagnostics_reports_truncation_and_ignores_malformed_items(db):
    run = await create_run(db, session_id=1, prompt="test", mode="agent")
    await append_event(db, run, "tool.calls_started", {"calls": [None, {}, "bad"]})
    await append_event(db, run, "tool.results", {"results": [
        {"tool_call_id": "long", "content": "正文" * 32_001},
    ]})
    page = await read_tool_diagnostics(db, run)
    assert len(page["calls"]) == 1
    preview = page["calls"][0]["result"]
    assert preview["truncated"] is True
    assert preview["characters"] == 64_002
    assert len(preview["text"]) == 32_000


async def test_tool_diagnostics_endpoint_is_dev_only_and_checks_run(db, monkeypatch):
    monkeypatch.setattr("config.DEV_DIAGNOSTICS_ENABLED", False)
    disabled = await get_agent_run_tool_diagnostics("missing")
    assert not disabled["success"]
    assert "开发环境" in disabled["error"]
    monkeypatch.setattr("config.DEV_DIAGNOSTICS_ENABLED", True)
    missing = await get_agent_run_tool_diagnostics("missing")
    assert missing == {"success": False, "error": "Agent Run 不存在"}
    assert not (await get_agent_run_tool_diagnostics("missing", after=-1))["success"]
