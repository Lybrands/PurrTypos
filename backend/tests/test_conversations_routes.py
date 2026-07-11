from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db
from routers.conversations import save_conversation
from schemas.conversations import SaveConversationRequest

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


async def test_save_conversation_returns_created_id(temp_db: DatabaseConnection):
    res = await save_conversation(SaveConversationRequest(
        sessionId=1,
        chapterId="chapter1",
        prompt="p",
        response="r",
    ))

    assert res["success"] is True
    assert isinstance(res["data"]["id"], int)


async def test_save_conversation_persists_task_plan_json(temp_db: DatabaseConnection):
    created = await save_conversation(SaveConversationRequest(
        sessionId=1,
        chapterId="chapter1",
        prompt="p",
        response="r",
        taskPlan={"title": "运行计划", "status": "done", "steps": []},
    ))
    conversation_id = created["data"]["id"]

    row = await temp_db.fetch_one(
        "SELECT task_plan FROM ai_conversations WHERE id = ?",
        [conversation_id],
    )
    assert json.loads(row["task_plan"])["status"] == "done"


async def test_save_conversation_persists_turn_duration(temp_db: DatabaseConnection):
    created = await save_conversation(SaveConversationRequest(
        sessionId=1,
        chapterId="chapter1",
        prompt="p",
        response="r",
        durationMs=12_345,
    ))

    row = await temp_db.fetch_one(
        "SELECT duration_ms FROM ai_conversations WHERE id = ?",
        [created["data"]["id"]],
    )
    assert row["duration_ms"] == 12_345


async def test_save_conversation_links_agent_run(temp_db: DatabaseConnection):
    from services.agent_run_store import create_run

    run_id = await create_run(temp_db, session_id=1, prompt="p", mode="agent")
    created = await save_conversation(SaveConversationRequest(
        sessionId=1,
        chapterId="chapter1",
        prompt="p",
        response="r",
        agentRunId=run_id,
    ))
    conversation_id = created["data"]["id"]

    row = await temp_db.fetch_one(
        "SELECT conversation_id FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )
    assert row["conversation_id"] == conversation_id
