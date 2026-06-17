from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db

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


async def test_controller_creates_fallback_todos_when_planner_returns_none(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _none_plan(**_kwargs):
        return None

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _none_plan)

    from services.agent_run_controller import AgentRunController

    controller = AgentRunController(db=temp_db, send_chunk=lambda event: None)
    events: list[dict] = []
    controller.send_chunk = events.append

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
    todos_event = events[1]["agentRunTodosUpdated"]
    assert todos_event["runId"] == controller.run_id
    assert [s["status"] for s in todos_event["steps"]] == ["running", "pending", "pending"]
    assert [s["title"] for s in todos_event["steps"]] == ["理解目标", "收集上下文", "生成回复"]


async def test_controller_creates_fallback_todos_when_planner_raises(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _broken_plan(**_kwargs):
        raise RuntimeError("planner api rejected temperature")

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _broken_plan)

    from services.agent_run_controller import AgentRunController

    events: list[dict] = []
    controller = AgentRunController(db=temp_db, send_chunk=events.append)

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
    assert [s["title"] for s in events[1]["agentRunTodosUpdated"]["steps"]] == [
        "理解目标",
        "收集上下文",
        "生成回复",
    ]


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
    controller = AgentRunController(db=temp_db, send_chunk=events.append)
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

    await controller.on_tool_calls_started(["getChapterContent"])
    await controller.on_tool_round_completed()
    await controller.on_model_delta()
    await controller.complete(final_response="完成")

    todos = await controller.current_steps()
    assert [s["status"] for s in todos] == ["done", "done"]
    assert events[-1]["agentRunCompleted"]["status"] == "done"

