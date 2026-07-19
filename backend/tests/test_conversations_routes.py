from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db
from routers.conversations import delete_after_turn, get_conversations, save_conversation
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


async def test_save_conversation_persists_context_ui_state(
    temp_db: DatabaseConnection,
):
    created = await save_conversation(SaveConversationRequest(
        sessionId=1,
        prompt="p",
        response="r",
        contextCompaction={
            "status": "completed",
            "compactedTurnCount": 4,
        },
        contextBudget={
            "windowTokens": 200_000,
            "estimatedInputTokens": 12_000,
            "toolSchemaTokens": 1_000,
        },
    ))

    row = await temp_db.fetch_one(
        "SELECT context_compaction, context_budget "
        "FROM ai_conversations WHERE id = ?",
        [created["data"]["id"]],
    )
    assert json.loads(row["context_compaction"])["compactedTurnCount"] == 4
    assert json.loads(row["context_budget"])["windowTokens"] == 200_000


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
    from infrastructure.persistence.run_store import create_run

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


async def test_conversation_schema_and_api_expose_only_current_turn_fields(
    temp_db: DatabaseConnection,
):
    columns = {
        row["name"]
        for row in await temp_db.fetch_all("PRAGMA table_info(ai_conversations)")
    }
    assert columns == {
        "id",
        "session_id",
        "chapter_id",
        "prompt",
        "response",
        "create_time",
        "model",
        "thinking",
        "tool_call_segments",
        "thinking_blocks",
        "thinking_durations_ms",
        "duration_ms",
        "task_plan",
        "context_compaction",
        "context_budget",
    }

    # Extra columns in an upgraded user database are retained physically but
    # are not part of the current response contract.
    await temp_db.execute(
        "ALTER TABLE ai_conversations ADD COLUMN obsolete_payload TEXT DEFAULT NULL"
    )
    await save_conversation(SaveConversationRequest(
        sessionId=9,
        prompt="p",
        response="r",
    ))
    result = await get_conversations("9")

    assert set(result["data"][0]) == columns


async def test_truncating_conversation_invalidates_persisted_summary(
    temp_db: DatabaseConnection,
):
    for index in range(3):
        await temp_db.execute(
            "INSERT INTO ai_conversations (session_id, prompt, response) "
            "VALUES (?, ?, ?)",
            [9, f"p{index}", f"r{index}"],
        )
    await temp_db.execute(
        "INSERT INTO ai_conversation_summaries "
        "(session_id, version, covered_through_conversation_id, "
        "covered_turn_count, source_digest, summary_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [9, 1, 1, 1, "a" * 64, "{}"],
    )

    result = await delete_after_turn("9", keepTurnCount=2)

    assert result["success"] is True
    row = await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_conversations WHERE session_id = ?",
        [9],
    )
    assert row["count"] == 2
    assert await temp_db.fetch_one(
        "SELECT session_id FROM ai_conversation_summaries WHERE session_id = ?",
        [9],
    ) is None
