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
        screenplayProposal={
            "kind": "creative_brief",
            "title": "第一版创作简报",
            "contentJson": {"theme": "重逢"},
            "contentText": "# 第一版创作简报",
            "derivedFromIds": [],
        },
        agentProcess={
            "delegations": [{"delegationId": "delegation-1", "status": "done"}],
            "subAgentActivities": [{
                "delegationId": "delegation-1",
                "message": {"role": "assistant", "content": "子任务结果"},
            }],
        },
    ))

    row = await temp_db.fetch_one(
        "SELECT context_compaction, context_budget, screenplay_proposal, "
        "agent_process "
        "FROM ai_conversations WHERE id = ?",
        [created["data"]["id"]],
    )
    assert json.loads(row["context_compaction"])["compactedTurnCount"] == 4
    assert json.loads(row["context_budget"])["windowTokens"] == 200_000
    assert json.loads(row["screenplay_proposal"])["title"] == "第一版创作简报"
    assert json.loads(row["agent_process"])["subAgentActivities"][0][
        "message"
    ]["content"] == "子任务结果"


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


async def test_history_projects_durable_task_only_onto_its_originating_session(
    temp_db: DatabaseConnection,
):
    from agent_core.long_tasks import LongTaskCreateCommand, LongTaskUnitSpec
    from agent_core.work_items import (
        WorkItemCreateCommand,
        WorkItemRunLinkCommand,
        WorkItemRunRelation,
    )
    from infrastructure.persistence.run_store import create_run
    from infrastructure.persistence.sqlite_long_task_repository import (
        SqliteLongTaskRepository,
    )
    from infrastructure.persistence.sqlite_work_item_repository import (
        SqliteWorkItemRepository,
    )

    run_id = await create_run(
        temp_db,
        session_id=15,
        prompt="连续创作剩余场景",
        mode="agent",
    )
    created = await save_conversation(SaveConversationRequest(
        sessionId=15,
        prompt="连续创作剩余场景",
        response="任务已开始，进度将在本轮持续更新。",
        agentRunId=run_id,
    ))
    work_items = SqliteWorkItemRepository(temp_db)
    work_item = await work_items.create(
        "work-1",
        WorkItemCreateCommand(
            namespace="purrtypos.screenplay",
            kind="screenplay_draft_generation",
            owner_id="project-1",
            created_by_run_id=run_id,
        ),
    )
    await SqliteLongTaskRepository(temp_db).create(
        "task-1",
        LongTaskCreateCommand(
            namespace="purrtypos.screenplay",
            kind="screenplay_draft_generation",
            owner_id="project-1",
            work_item_id=work_item.id,
            created_by_run_id=run_id,
            units=(LongTaskUnitSpec(id="batch-1", position=0),),
        ),
    )

    history = await get_conversations("15")

    assert len(history["data"]) == 1
    assert history["data"][0]["id"] == created["data"]["id"]
    assert history["data"][0]["agent_run_id"] == run_id
    assert history["data"][0]["long_task_id"] == "task-1"

    continuation_run_id = await create_run(
        temp_db,
        session_id=16,
        prompt="在新对话继续同一任务",
        mode="agent",
    )
    await work_items.link_run(WorkItemRunLinkCommand(
        work_item_id=work_item.id,
        run_id=continuation_run_id,
        relation=WorkItemRunRelation.REFERENCE,
        expected_revision=work_item.revision,
    ))
    await save_conversation(SaveConversationRequest(
        sessionId=16,
        prompt="在新对话继续同一任务",
        response="已关联到当前任务。",
        agentRunId=continuation_run_id,
    ))

    continued_history = await get_conversations("16")

    assert continued_history["data"][0]["agent_run_id"] == continuation_run_id
    assert continued_history["data"][0]["long_task_id"] is None


async def test_detached_run_materialization_and_frontend_save_are_idempotent(
    temp_db: DatabaseConnection,
):
    from infrastructure.persistence.run_conversation_store import (
        ensure_terminal_run_conversation,
    )
    from infrastructure.persistence.run_store import complete_run, create_run

    run_id = await create_run(temp_db, session_id=1, prompt="原始问题", mode="agent")
    await complete_run(temp_db, run_id, final_response="后台完成的回答")

    materialized_id = await ensure_terminal_run_conversation(temp_db, run_id)
    saved = await save_conversation(SaveConversationRequest(
        sessionId=1,
        prompt="原始问题",
        response="前端补全后的回答",
        model="model-a",
        agentRunId=run_id,
    ))

    assert saved["data"]["id"] == materialized_id
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_conversations WHERE session_id = 1",
    ) == {"count": 1}
    assert await temp_db.fetch_one(
        "SELECT response, model FROM ai_conversations WHERE id = ?",
        [materialized_id],
    ) == {"response": "前端补全后的回答", "model": "model-a"}


async def test_durable_dispatch_materializes_without_host_receipt_as_answer(
    temp_db: DatabaseConnection,
):
    from infrastructure.persistence.run_conversation_store import (
        ensure_terminal_run_conversation,
    )
    from infrastructure.persistence.run_store import (
        append_event,
        complete_run,
        create_run,
    )

    run_id = await create_run(
        temp_db,
        session_id=31,
        prompt="连续创作剩余场景",
        mode="agent",
    )
    await append_event(
        temp_db,
        run_id,
        "long_task.dispatched",
        {"taskId": "task-31", "message": "已恢复原有长篇正文任务。"},
    )
    await complete_run(
        temp_db,
        run_id,
        final_response="已恢复原有长篇正文任务。",
    )

    conversation_id = await ensure_terminal_run_conversation(temp_db, run_id)

    assert conversation_id is not None
    assert await temp_db.fetch_one(
        "SELECT prompt, response FROM ai_conversations WHERE id = ?",
        [conversation_id],
    ) == {"prompt": "连续创作剩余场景", "response": ""}


async def test_history_recovers_legacy_screenplay_proposal_from_run_event(
    temp_db: DatabaseConnection,
):
    from infrastructure.persistence import run_store

    run_id = await run_store.create_run(
        temp_db,
        session_id=12,
        prompt="生成创作简报",
        mode="agent",
    )
    proposal = {
        "kind": "creative_brief",
        "title": "可恢复的创作简报",
        "contentJson": {"theme": "重逢"},
        "contentText": "# 可恢复的创作简报",
        "derivedFromIds": [],
    }
    await run_store.append_event(
        temp_db,
        run_id,
        "screenplay.document_proposal",
        proposal,
    )
    await save_conversation(SaveConversationRequest(
        sessionId=12,
        prompt="生成创作简报",
        response="已经生成，请确认。",
        agentRunId=run_id,
    ))

    history = await get_conversations("12")

    assert json.loads(history["data"][0]["screenplay_proposal"]) == proposal


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
        "screenplay_proposal",
        "agent_process",
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

    assert set(result["data"][0]) == columns | {
        "agent_run_id",
        "long_task_id",
    }


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
