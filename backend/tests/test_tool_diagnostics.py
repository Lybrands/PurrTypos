from __future__ import annotations

import json

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db
from infrastructure.persistence.run_store import append_event, create_run
from infrastructure.persistence.tool_diagnostics import read_tool_diagnostics
from routers.ai import get_agent_run_tool_diagnostics
from purra.contracts import RunBinding

pytestmark = pytest.mark.asyncio


async def test_model_inputs_cover_private_receipts_once_and_fill_historical_model_read_only(db):
    from infrastructure.persistence.model_input_diagnostics import read_model_input_diagnostics
    from infrastructure.persistence.sqlite_run_snapshot_reader import SqliteRunSnapshotReader
    from infrastructure.persistence.run_store import get_run

    run = await create_run(db, session_id=1, prompt="test", mode="agent")
    other = await create_run(db, session_id=1, prompt="other", mode="agent")
    for index in range(3):
        await append_event(db, run, "stream.opened", {
            "callParameters": [{"provider": "openai", "model": "fixture-model", "inputMessages": [{
                "role": "user", "content": f"input-{index}", "api_key": "KEY_SECRET",
                "reasoning_content": "HIDDEN_THOUGHT",
                "tool": json.dumps({"reasoning": "NESTED_THOUGHT", "password": "NESTED_SECRET"}),
            }]}],
            **({"planningScope": {"revision": 0}, "planningAttempt": index + 1} if index < 2 else {}),
        })
        if index < 2:
            await append_event(db, run, "model.call_recorded", {"count": 1})
    await append_event(db, other, "stream.opened", {"callParameters": [{"inputMessages": [{"content": "OTHER_RUN"}]}]})
    before = await get_run(db, run)
    calls = await read_model_input_diagnostics(db, run)
    snapshot = await SqliteRunSnapshotReader(db).load(run)
    assert len(calls) == 3
    assert [call["messages"][0]["content"] for call in calls] == ["input-0", "input-1", "input-2"]
    assert [call["phase"] for call in calls] == ["planning", "planning", "model"]
    assert snapshot.run["model_name"] == "fixture-model"
    assert snapshot.run["model_provider"] == "openai"
    assert await get_run(db, run) == before
    serialized = json.dumps(calls)
    for private in ("KEY_SECRET", "HIDDEN_THOUGHT", "NESTED_THOUGHT", "NESTED_SECRET", "OTHER_RUN"):
        assert private not in serialized


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
    await append_event(db, run, "operation.started", {
        "operationId": "operation-read-chapter-1",
        "kind": "tool",
        "display": {"labelParams": {
            "toolCallId": "read",
            "toolName": "readSourceChapters",
            "displayNames": {"zh-CN": "为第 1 集读取《第一章 清河桥》的原文"},
        }},
    })
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
    assert calls["read"]["name"] == "readSourceChapters"
    assert calls["read"]["displayName"] == "为第 1 集读取《第一章 清河桥》的原文"
    assert calls["read"]["operationId"] == "operation-read-chapter-1"
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


async def test_tool_diagnostics_replaces_static_schema_name_with_call_projection(db):
    run = await create_run(db, session_id=1, prompt="test", mode="agent")
    await append_event(db, run, "tool.calls_started", {"calls": [{
        "id": "dependency",
        "name": "readScreenplayTaskDependencies",
        "arguments_json": json.dumps({"partKeys": ["draft:1:ep01-s02"]}),
        "display_names": {"zh-CN": "读取任务依赖"},
    }]})
    await append_event(db, run, "operation.started", {
        "operationId": "operation-dependency",
        "kind": "tool",
        "display": {"labelParams": {
            "toolCallId": "dependency",
            "toolName": "readScreenplayTaskDependencies",
            "displayNames": {"zh-CN": "读取第 1 集第 2 场已完成剧本"},
        }},
    })

    page = await read_tool_diagnostics(db, run)

    assert page["calls"] == [{
        "runId": run,
        "toolCallId": "dependency",
        "eventRowId": page["calls"][0]["eventRowId"],
        "name": "readScreenplayTaskDependencies",
        "displayName": "读取第 1 集第 2 场已完成剧本",
        "startedAt": page["calls"][0]["startedAt"],
        "arguments": page["calls"][0]["arguments"],
        "operationId": "operation-dependency",
    }]


async def test_tool_diagnostics_reprojects_historical_screenplay_call_arguments(
    db,
    monkeypatch,
):
    monkeypatch.setattr("config.DEV_DIAGNOSTICS_ENABLED", True)
    run = await create_run(
        db,
        session_id=1,
        prompt="test",
        mode="agent",
        binding=RunBinding(
            namespace="screenplay.agent.task",
            aggregate_id="project-1",
            command_id="task-1:draft:1:scene-2",
            attributes={
                "candidateCompletionProjection": {
                    "scope": {"boundEpisodeNumber": 1},
                },
            },
        ),
    )
    await db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, run_id, metadata_json) "
        "VALUES ('task-1', 'draft:1:scene-2', 'draft:1:scene-2', 0, ?, ?)",
        [run, json.dumps({"input": {
            "episodeNumber": 1,
            "sceneIds": ["scene-1", "scene-2"],
        }})],
    )
    await append_event(db, run, "tool.calls_started", {"calls": [{
        "id": "dependency",
        "name": "readScreenplayTaskDependencies",
        "arguments_json": json.dumps({"partKeys": ["draft:1:scene-2"]}),
        "display_names": {"zh-CN": "读取任务依赖"},
    }]})
    await append_event(db, run, "operation.started", {
        "operationId": "operation-dependency",
        "kind": "tool",
        "display": {"labelParams": {
            "toolCallId": "dependency",
            "toolName": "readScreenplayTaskDependencies",
            "episodeNumber": 1,
            "targetDetail": "可读产出清单",
            "displayNames": {"zh-CN": "读取第 1 集任务依赖（可读产出清单）"},
        }},
    })

    response = await get_agent_run_tool_diagnostics(run)

    assert response["success"] is True
    assert response["data"]["calls"][0]["displayName"] == (
        "读取第 1 集第 2 场已完成剧本"
    )


async def test_tool_diagnostics_does_not_present_static_schema_name_as_call_name(db):
    run = await create_run(db, session_id=1, prompt="test", mode="agent")
    await append_event(db, run, "tool.calls_started", {"calls": [{
        "id": "not-yet-started",
        "name": "readScreenplayTaskDependencies",
        "arguments_json": "{}",
        "display_names": {"zh-CN": "读取任务依赖"},
    }]})

    page = await read_tool_diagnostics(db, run)

    assert page["calls"][0]["name"] == "readScreenplayTaskDependencies"
    assert "displayName" not in page["calls"][0]


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
