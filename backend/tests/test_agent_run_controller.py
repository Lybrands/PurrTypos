from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db

pytestmark = pytest.mark.asyncio


def _make_controller(db: DatabaseConnection, send_chunk):
    from application.event_sinks import LegacyChunkEventSink
    from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
    from services.agent_run_controller import AgentRunController

    return AgentRunController(
        repository=SqliteRunRepository(db),
        event_sink=LegacyChunkEventSink(send_chunk),
    )


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        yield db
    finally:
        await db.close()


async def test_store_persists_run_todos_and_events(temp_db: DatabaseConnection):
    from services.agent_run_store import (
        append_event,
        create_run,
        get_run_todos,
        update_todo_status,
        upsert_todos,
    )

    run_id = await create_run(temp_db, session_id=7, prompt="优化前三章", mode="agent")
    await upsert_todos(
        temp_db,
        run_id,
        [
            {
                "id": "read-context",
                "title": "读取上下文",
                "type": "read",
                "status": "pending",
                "executor": "tool",
                "suggestedTools": ["getChapterContent"],
            }
        ],
    )
    await update_todo_status(
        temp_db,
        run_id,
        "read-context",
        "done",
        result_summary="已读取章节。",
    )
    await append_event(temp_db, run_id, "agentRunTodoUpdated", {"stepId": "read-context"})

    run = await temp_db.fetch_one("SELECT * FROM ai_agent_runs WHERE id = ?", [run_id])
    todos = await get_run_todos(temp_db, run_id)
    events = await temp_db.fetch_all(
        "SELECT event_type FROM ai_agent_run_events WHERE run_id = ?",
        [run_id],
    )

    assert run["status"] == "running"
    assert todos[0]["id"] == "read-context"
    assert todos[0]["status"] == "done"
    assert todos[0]["resultSummary"] == "已读取章节。"
    assert [e["event_type"] for e in events] == ["agentRunTodoUpdated"]


async def test_controller_fails_closed_when_planner_returns_none(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _none_plan(**_kwargs):
        return None

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _none_plan)

    from services.agent_run_controller import AgentRunController

    events: list[dict] = []
    controller = _make_controller(temp_db, events.append)

    await controller.start(
        session_id=3,
        prompt="帮我检查前三章节奏并给出修改建议",
        mode="agent",
        key="k",
        api_provider="openai",
        planner_options={"model": "m"},
        chat_agent_mode="agent",
        available_tool_names={"getChapterContent"},
        signal=None,
    )

    assert controller.run_id
    assert events[0]["agentRunStarted"]["runId"] == controller.run_id
    assert events[1]["agentRunFailed"]["runId"] == controller.run_id
    assert controller.status == "failed"
    assert controller.allowed_tool_names() == set()

    from services.agent_run_store import get_run_events
    traces = [
        event["payload"]
        for event in await get_run_events(temp_db, controller.run_id)
        if event["eventType"] == "agentRunTrace"
    ]
    assert next(trace for trace in traces if trace["stage"] == "planner")["outcome"] == "invalid_plan"


async def test_controller_fails_closed_when_planner_raises(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _broken_plan(**_kwargs):
        raise RuntimeError("planner api rejected temperature")

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _broken_plan)

    from services.agent_run_controller import AgentRunController

    events: list[dict] = []
    controller = _make_controller(temp_db, events.append)

    await controller.start(
        session_id=3,
        prompt="帮我检查前三章节奏并给出修改建议",
        mode="agent",
        key="k",
        api_provider="openai",
        planner_options={"model": "m"},
        chat_agent_mode="agent",
        available_tool_names={"getChapterContent"},
        signal=None,
    )

    assert controller.run_id
    assert "agentRunStarted" in events[0]
    assert "agentRunFailed" in events[1]
    assert controller.status == "failed"
    assert controller.allowed_tool_names() == set()

    from services.agent_run_store import get_run_events
    traces = [
        event["payload"]
        for event in await get_run_events(temp_db, controller.run_id)
        if event["eventType"] == "agentRunTrace"
    ]
    planner_trace = next(trace for trace in traces if trace["stage"] == "planner")
    assert planner_trace["outcome"] == "exception"
    assert planner_trace["details"]["errorType"] == "RuntimeError"


async def test_controller_progresses_tool_model_and_done(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _model_plan(**_kwargs):
        return {
            "title": "优化章节",
            "status": "planned",
            "steps": [
                {
                    "id": "read",
                    "title": "读取章节",
                    "type": "read",
                    "status": "pending",
                    "executor": "tool",
                    "suggestedTools": ["getChapterContent"],
                },
                {
                    "id": "answer",
                    "title": "生成建议",
                    "type": "analyze",
                    "status": "pending",
                    "executor": "model",
                },
            ],
        }

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _model_plan)

    from services.agent_run_controller import AgentRunController

    events: list[dict] = []
    controller = _make_controller(temp_db, events.append)
    await controller.start(
        session_id=3,
        prompt="帮我检查前三章节奏并给出修改建议",
        mode="agent",
        key="k",
        api_provider="openai",
        planner_options={"model": "m"},
        chat_agent_mode="agent",
        available_tool_names={"getChapterContent"},
        signal=None,
    )

    assert controller.allowed_tool_names_for_current_transition() == {"getChapterContent"}
    await controller.on_tool_calls_started(["getChapterContent"])
    await controller.on_tool_round_completed()
    assert controller.allowed_tool_names_for_current_transition() == set()
    await controller.on_model_delta()
    await controller.complete(final_response="完成")

    todos = await controller.current_steps()
    assert [s["status"] for s in todos] == ["done", "done"]
    assert controller.allowed_tool_names() == {"getChapterContent"}
    assert "getChapterContent" in controller.execution_prompt()
    assert events[-1]["agentRunCompleted"]["status"] == "done"


async def test_controller_only_authorizes_the_next_tool_transition(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _model_plan(**_kwargs):
        return {
            "title": "ordered plan",
            "steps": [
                {"id": "think-1", "title": "think", "type": "analyze", "executor": "model"},
                {
                    "id": "read-1",
                    "title": "read chapter",
                    "type": "read",
                    "executor": "tool",
                    "suggestedTools": ["getChapterContent"],
                },
                {"id": "think-2", "title": "analyze", "type": "analyze", "executor": "model"},
                {
                    "id": "read-2",
                    "title": "search memory",
                    "type": "read",
                    "executor": "tool",
                    "suggestedTools": ["searchMemories"],
                },
            ],
        }

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _model_plan)
    from services.agent_run_controller import AgentRunController

    controller = _make_controller(temp_db, lambda _event: None)
    await controller.start(
        session_id=3,
        prompt="ordered work",
        mode="agent",
        key="k",
        api_provider="openai",
        planner_options={"model": "m"},
        chat_agent_mode="agent",
        available_tool_names={"getChapterContent", "searchMemories"},
        signal=None,
    )

    assert controller.allowed_tool_names() == {"getChapterContent", "searchMemories"}
    assert controller.allowed_tool_names_for_current_transition() == {"getChapterContent"}


async def test_final_response_completes_consecutive_model_only_steps(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _model_plan(**_kwargs):
        return {
            "title": "read and analyze",
            "steps": [
                {"id": "read", "title": "read", "type": "read", "executor": "tool", "suggestedTools": ["getChapterContent"]},
                {"id": "analyze", "title": "analyze", "type": "analyze", "executor": "model"},
                {"id": "review", "title": "review", "type": "review", "executor": "model"},
                {"id": "answer", "title": "answer", "type": "review", "executor": "model"},
            ],
        }

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _model_plan)
    from services.agent_run_controller import AgentRunController

    controller = _make_controller(temp_db, lambda _event: None)
    await controller.start(
        session_id=1,
        prompt="read and analyze",
        mode="agent",
        key="k",
        api_provider="openai",
        planner_options={"model": "m"},
        chat_agent_mode="agent",
        available_tool_names={"getChapterContent"},
    )
    await controller.on_tool_calls_started(["getChapterContent"])
    await controller.on_tool_round_completed()
    await controller.on_model_delta()
    await controller.complete(final_response="final")

    assert controller.status == "done"
    assert [step["status"] for step in await controller.current_steps()] == [
        "done", "done", "done", "done",
    ]


async def test_final_response_does_not_skip_a_pending_tool_step(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _model_plan(**_kwargs):
        return {
            "title": "analyze then read",
            "steps": [
                {"id": "analyze", "title": "analyze", "type": "analyze", "executor": "model"},
                {"id": "read", "title": "read", "type": "read", "executor": "tool", "suggestedTools": ["getChapterContent"]},
                {"id": "answer", "title": "answer", "type": "review", "executor": "model"},
            ],
        }

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _model_plan)
    from services.agent_run_controller import AgentRunController

    controller = _make_controller(temp_db, lambda _event: None)
    await controller.start(
        session_id=1,
        prompt="analyze then read",
        mode="agent",
        key="k",
        api_provider="openai",
        planner_options={"model": "m"},
        chat_agent_mode="agent",
        available_tool_names={"getChapterContent"},
    )
    await controller.on_model_delta()
    await controller.complete(final_response="premature")

    assert controller.status == "blocked"
    assert [step["status"] for step in await controller.current_steps()] == [
        "done", "blocked", "blocked",
    ]


async def test_cancel_persists_terminal_state_and_blocks_unfinished_steps(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _model_plan(**_kwargs):
        return {
            "title": "cancel me",
            "steps": [
                {"id": "read", "title": "read", "type": "read", "executor": "tool", "suggestedTools": ["getChapterContent"]},
                {"id": "answer", "title": "answer", "type": "review", "executor": "model"},
            ],
        }

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _model_plan)
    from services.agent_run_controller import AgentRunController
    from services.agent_run_store import get_run, get_run_events

    events: list[dict] = []
    controller = _make_controller(temp_db, events.append)
    await controller.start(
        session_id=1,
        prompt="cancel me",
        mode="agent",
        key="k",
        api_provider="openai",
        planner_options={"model": "m"},
        chat_agent_mode="agent",
        available_tool_names={"getChapterContent"},
    )
    await controller.cancel(reason="client_disconnected")

    run = await get_run(temp_db, controller.run_id or "")
    assert run and run["status"] == "canceled"
    assert [step["status"] for step in await controller.current_steps()] == [
        "blocked", "blocked",
    ]
    traces = [
        event["payload"]
        for event in await get_run_events(temp_db, controller.run_id or "")
        if event["eventType"] == "agentRunTrace"
    ]
    assert traces[-1]["stage"] == "terminal"
    assert traces[-1]["outcome"] == "canceled"
    assert "agentRunCanceled" in events[-1]

    await controller.fail(error="late error")
    await controller.complete(final_response="late answer")
    assert (await get_run(temp_db, controller.run_id or "") or {})["status"] == "canceled"

