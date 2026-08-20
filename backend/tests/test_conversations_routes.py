from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException

from database.connection import DatabaseConnection
from dependencies import set_db
from routers.conversations import (
    delete_after_turn,
    get_conversations,
    router as conversations_router,
    save_conversation,
)
from schemas.conversations import SaveConversationRequest
from tests.support.asgi_sse import request_json

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) "
        "VALUES (1, 'book-1', 'chapter-1')"
    )
    try:
        yield db
    finally:
        await db.close()


async def test_save_conversation_returns_created_id(temp_db: DatabaseConnection):
    res = await save_conversation(SaveConversationRequest(
        sessionId=1,
        bookId="book-1",
        chapterId="chapter-1",
        prompt="p",
        response="r",
    ))

    assert res["success"] is True
    assert isinstance(res["data"]["id"], int)


async def test_local_turn_receipt_schema_is_initialized(
    temp_db: DatabaseConnection,
):
    columns = await temp_db.fetch_all(
        "PRAGMA table_info(ai_local_conversation_turn_receipts)"
    )

    assert [column["name"] for column in columns] == [
        "session_id",
        "client_turn_id",
        "payload_digest",
        "status",
        "conversation_id",
        "revision",
        "create_time",
        "update_time",
    ]


async def test_local_save_uses_the_authoritative_session_scope(
    temp_db: DatabaseConnection,
):
    with pytest.raises(HTTPException) as wrong_scope:
        await save_conversation(SaveConversationRequest(
            sessionId=1,
            bookId="book-other",
            chapterId="chapter-other",
            prompt="wrong owner",
            response="must not persist",
            clientTurnId="turn-wrong-scope",
            expectedConversationIds=[],
        ))
    assert wrong_scope.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_conversations WHERE session_id = 1"
    ) == {"count": 0}


async def test_local_conversation_save_replays_by_client_turn_identity(
    temp_db: DatabaseConnection,
):
    request = SaveConversationRequest(
        sessionId=1,
        bookId="book-1",
        chapterId="chapter-1",
        prompt="本地问题",
        response="本地回答",
        clientTurnId="turn-local-replay",
    )

    first = await save_conversation(request)
    first_receipt = await temp_db.fetch_one(
        "SELECT status, payload_digest, conversation_id, revision "
        "FROM ai_local_conversation_turn_receipts "
        "WHERE session_id = 1 AND client_turn_id = 'turn-local-replay'"
    )
    replay = await save_conversation(request)

    assert first_receipt is not None
    assert first_receipt == {
        "status": "persisted",
        "payload_digest": first_receipt["payload_digest"],
        "conversation_id": first["data"]["id"],
        "revision": 1,
    }
    assert first_receipt["payload_digest"].startswith("sha256:")
    assert replay["data"]["id"] == first["data"]["id"]
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_conversations "
        "WHERE client_turn_id = 'turn-local-replay'"
    ) == {"count": 1}

    with pytest.raises(HTTPException) as conflict:
        await save_conversation(request.model_copy(update={"response": "不同回答"}))
    assert conflict.value.status_code == 409

    with pytest.raises(HTTPException) as metadata_conflict:
        await save_conversation(request.model_copy(update={"model": "different-model"}))
    assert metadata_conflict.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT status, payload_digest, conversation_id, revision "
        "FROM ai_local_conversation_turn_receipts "
        "WHERE session_id = 1 AND client_turn_id = 'turn-local-replay'"
    ) == first_receipt


async def test_local_conversation_replay_repairs_explicit_memory_deposition(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    from services import memory_deposition_service

    original_deposit = (
        memory_deposition_service.deposit_explicit_memory_from_conversation
    )
    calls = 0

    async def fail_once_then_deposit(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated post-commit memory failure")
        return await original_deposit(**kwargs)

    monkeypatch.setattr(
        memory_deposition_service,
        "deposit_explicit_memory_from_conversation",
        fail_once_then_deposit,
    )
    request = SaveConversationRequest(
        sessionId=1,
        bookId="book-1",
        chapterId="chapter-1",
        prompt="请记住：月门只能在雨夜开启",
        response="已记住",
        clientTurnId="turn-memory-repair",
        expectedConversationIds=[],
    )

    first = await save_conversation(request)
    assert await temp_db.fetch_one(
        "SELECT id FROM memory_items WHERE source_type = 'conversation' "
        "AND source_id = ?",
        [str(first["data"]["id"])],
    ) is None

    replay = await save_conversation(request)

    assert replay["data"]["id"] == first["data"]["id"]
    assert calls == 2
    assert await temp_db.fetch_one(
        "SELECT content, status FROM memory_items "
        "WHERE source_type = 'conversation' AND source_id = ?",
        [str(first["data"]["id"])],
    ) == {"content": "月门只能在雨夜开启", "status": "active"}


async def test_legacy_local_turn_replay_canonicalizes_json_before_backfill(
    temp_db: DatabaseConnection,
):
    legacy_id = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations "
        "(session_id, chapter_id, prompt, response, agent_process, "
        "client_turn_id) VALUES (1, 'chapter-1', '旧问题', '旧回答', ?, ?)",
        [
            json.dumps({"alpha": 1, "beta": 2}, ensure_ascii=False),
            "turn-legacy-json-order",
        ],
    )

    replay = await save_conversation(SaveConversationRequest(
        sessionId=1,
        bookId="book-1",
        chapterId="chapter-1",
        prompt="旧问题",
        response="旧回答",
        agentProcess={"beta": 2, "alpha": 1},
        clientTurnId="turn-legacy-json-order",
    ))

    assert replay["data"]["id"] == legacy_id
    receipt = await temp_db.fetch_one(
        "SELECT status, conversation_id, payload_digest "
        "FROM ai_local_conversation_turn_receipts "
        "WHERE session_id = 1 AND client_turn_id = 'turn-legacy-json-order'"
    )
    assert receipt is not None
    assert receipt["status"] == "persisted"
    assert receipt["conversation_id"] == legacy_id
    assert str(receipt["payload_digest"]).startswith("sha256:")


async def test_local_conversation_save_is_a_history_frontier_cas(
    temp_db: DatabaseConnection,
):
    first = SaveConversationRequest(
        sessionId=1,
        prompt="并发一",
        response="回答一",
        clientTurnId="turn-cas-1",
        expectedConversationIds=[],
    )
    second = SaveConversationRequest(
        sessionId=1,
        prompt="并发二",
        response="回答二",
        clientTurnId="turn-cas-2",
        expectedConversationIds=[],
    )

    assert (await save_conversation(first))["success"] is True
    with pytest.raises(HTTPException) as stale:
        await save_conversation(second)
    assert stale.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_conversations WHERE session_id = 1"
    ) == {"count": 1}


async def test_local_save_rejects_an_unmaterialized_agent_frontier(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, prompt, binding_namespace, "
        "binding_aggregate_id, binding_command_id) "
        "VALUES ('run-unmaterialized', 1, 'done', 'Agent 结果', "
        "'writing.chat.request', '1', 'request-unmaterialized')"
    )

    with pytest.raises(HTTPException) as pending_projection:
        await save_conversation(SaveConversationRequest(
            sessionId=1,
            prompt="Ask 不能越过 Agent",
            response="Ask 回答",
            clientTurnId="turn-cross-mode",
            expectedConversationIds=[],
        ))
    assert pending_projection.value.status_code == 409


async def test_agent_terminal_save_cannot_overwrite_server_projection(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) VALUES (?, ?, ?)",
        [71, "book-1", "chapter-1"],
    )
    conversation_id = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations "
        "(session_id, chapter_id, prompt, response, model, task_plan, agent_process) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            71,
            "chapter-1",
            "请记住：服务端事实",
            "Run 最终正文",
            "server-model",
            json.dumps({"title": "服务端计划", "steps": [{"id": "server"}]}),
            json.dumps({"serverOwned": {"kept": True}}),
        ],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, conversation_id, status, prompt, final_response) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["run-authoritative", 71, conversation_id, "done", "问题", "Run 最终正文"],
    )

    saved = await save_conversation(SaveConversationRequest(
        sessionId=71,
        chapterId="chapter-1",
        bookId="book-hostile",
        prompt="请记住：客户端伪造",
        response="客户端残缺正文",
        model="client-model",
        taskPlan=None,
        agentProcess={"delegations": [{"id": "client"}]},
        agentRunId="run-authoritative",
    ))

    row = await temp_db.fetch_one(
        "SELECT prompt, response, model, task_plan, agent_process "
        "FROM ai_conversations WHERE id = ?",
        [conversation_id],
    )
    assert saved["data"]["id"] == conversation_id
    assert row["prompt"] == "请记住：服务端事实"
    assert row["response"] == "Run 最终正文"
    assert row["model"] == "server-model"
    assert json.loads(row["task_plan"])["title"] == "服务端计划"
    assert json.loads(row["agent_process"]) == {"serverOwned": {"kept": True}}
    memories = await temp_db.fetch_all(
        "SELECT book_id, content FROM memory_items WHERE source_type = 'conversation'"
    )
    assert memories == [{"book_id": "book-1", "content": "服务端事实"}]


async def test_durable_save_rejects_running_cross_session_and_deleted_session(
    temp_db: DatabaseConnection,
):
    from infrastructure.persistence.run_store import create_run

    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id) VALUES (72, 'book-1')"
    )
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id) VALUES (73, 'book-1')"
    )
    run_id = await create_run(
        temp_db, session_id=72, prompt="服务端问题", mode="agent"
    )
    request = SaveConversationRequest(
        sessionId=72,
        prompt="服务端问题",
        response="客户端提前正文",
        agentRunId=run_id,
    )
    with pytest.raises(HTTPException) as running:
        await save_conversation(request)
    assert running.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT conversation_id FROM ai_agent_runs WHERE id = ?", [run_id]
    ) == {"conversation_id": None}

    with pytest.raises(HTTPException) as cross_session:
        await save_conversation(request.model_copy(update={"sessionId": 73}))
    assert cross_session.value.status_code == 409

    await temp_db.execute("DELETE FROM ai_sessions WHERE id = 72")
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'done', final_response = '完成' "
        "WHERE id = ?",
        [run_id],
    )
    with pytest.raises(HTTPException) as deleted:
        await save_conversation(request)
    assert deleted.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_conversations WHERE session_id = 72"
    ) == {"count": 0}


async def test_running_run_persists_setting_resolution_without_client_projection(
    temp_db: DatabaseConnection,
):
    from infrastructure.persistence.run_conversation_store import (
        ensure_terminal_run_conversation,
    )
    from infrastructure.persistence.run_store import complete_run, create_run

    run_id = await create_run(
        temp_db, session_id=1, prompt="服务端设定问题", mode="agent"
    )
    from application.writing_proposal_read_model import proposal_occurrence_id

    proposal = {
        "kind": "background",
        "bookId": "book-1",
        "before": {"content": "旧"},
        "proposed": {"content": "新"},
    }
    await temp_db.execute(
        "INSERT INTO ai_agent_tool_receipts "
        "(run_id, tool_call_id, tool_name, arguments_digest, effects_json) "
        "VALUES (?, 'call-mid-run', 'editStoryBackground', 'digest', ?)",
        [run_id, json.dumps([{
            "type": "writing.proposed_setting_diff",
            "payload": proposal,
        }], ensure_ascii=False)],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_run_events (run_id, event_type, payload_json) "
        "VALUES (?, 'writing.proposed_setting_diff', ?)",
        [run_id, json.dumps(proposal, ensure_ascii=False)],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_run_events (run_id, event_type, payload_json) "
        "VALUES (?, 'tool.call_completed', ?)",
        [run_id, json.dumps({
            "toolCallId": "call-mid-run",
            "toolName": "editStoryBackground",
        })],
    )
    proposal_id = proposal_occurrence_id(run_id, "call-mid-run", 0)
    saved = await save_conversation(SaveConversationRequest(
        sessionId=1,
        bookId="hostile-book",
        chapterId="hostile-chapter",
        prompt="客户端伪造问题",
        response="客户端伪造正文",
        model="client-model",
        agentRunId=run_id,
        agentProcess={
            "settingDiff": {
                "version": 1,
                "resolutions": {
                    proposal_id: {
                        "proposalId": proposal_id,
                        "sessionKey": "background:book-1",
                        "kind": "background",
                        "title": "故事背景",
                        "status": "rejected",
                        "acceptedSegments": 0,
                        "rejectedSegments": 1,
                    },
                },
            },
        },
    ))
    shell = await temp_db.fetch_one(
        "SELECT session_id, chapter_id, prompt, response, model, agent_process "
        "FROM ai_conversations WHERE id = ?",
        [saved["data"]["id"]],
    )

    assert shell["session_id"] == 1
    assert shell["chapter_id"] == "chapter-1"
    assert shell["prompt"] == "服务端设定问题"
    assert shell["response"] == ""
    assert shell["model"] is None
    assert json.loads(shell["agent_process"])["settingDiff"]["resolutions"][
        proposal_id
    ]["status"] == "rejected"

    await complete_run(temp_db, run_id, final_response="服务端终稿")
    assert await ensure_terminal_run_conversation(temp_db, run_id) == saved["data"]["id"]
    projected = await temp_db.fetch_one(
        "SELECT response, agent_process FROM ai_conversations WHERE id = ?",
        [saved["data"]["id"]],
    )
    assert projected["response"] == "服务端终稿"
    assert json.loads(projected["agent_process"])["settingDiff"]["resolutions"][
        proposal_id
    ]["status"] == "rejected"


async def test_setting_diff_metadata_merge_is_monotonic_across_terminal_order(
    temp_db: DatabaseConnection,
):
    from infrastructure.persistence.run_conversation_store import (
        ensure_terminal_run_conversation,
    )
    from infrastructure.persistence.run_store import complete_run, create_run

    run_id = await create_run(
        temp_db, session_id=1, prompt="修改设定", mode="agent"
    )
    from application.writing_proposal_read_model import proposal_occurrence_id

    proposal = {
        "kind": "character",
        "bookId": "book-1",
        "characterId": 1,
        "before": {"name": "甲", "tags": "", "profileMd": "旧"},
        "proposed": {"name": "甲", "tags": "", "profileMd": "新"},
    }
    await temp_db.execute(
        "INSERT INTO ai_agent_tool_receipts "
        "(run_id, tool_call_id, tool_name, arguments_digest, effects_json) "
        "VALUES (?, 'call-stable', 'updateCharacter', 'digest', ?)",
        [run_id, json.dumps([{
            "type": "writing.proposed_setting_diff",
            "payload": proposal,
        }], ensure_ascii=False)],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_run_events (run_id, event_type, payload_json) "
        "VALUES (?, 'writing.proposed_setting_diff', ?)",
        [run_id, json.dumps(proposal, ensure_ascii=False)],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_run_events (run_id, event_type, payload_json) "
        "VALUES (?, 'tool.call_completed', ?)",
        [run_id, json.dumps({
            "toolCallId": "call-stable",
            "toolName": "updateCharacter",
        })],
    )
    await complete_run(temp_db, run_id, final_response="")
    conversation_id = await ensure_terminal_run_conversation(temp_db, run_id)
    assert conversation_id is not None
    proposal_id = proposal_occurrence_id(run_id, "call-stable", 0)

    await save_conversation(SaveConversationRequest(
        sessionId=1,
        prompt="客户端不可覆盖",
        response="客户端不可覆盖",
        agentRunId=run_id,
        agentProcess={
            "settingDiff": {
                "version": 1,
                "aliases": {
                    proposal_id: {"clientTurnIds": ["turn-1"]},
                },
                "resolutions": {
                    proposal_id: {
                        "proposalId": proposal_id,
                        "sessionKey": "character:1",
                        "kind": "character",
                        "title": "人物「甲」",
                        "status": "rejected",
                        "acceptedSegments": 0,
                        "rejectedSegments": 3,
                    },
                },
            },
        },
    ))
    await save_conversation(SaveConversationRequest(
        sessionId=1,
        prompt="晚到终态快照",
        response="",
        agentRunId=run_id,
        agentProcess={
            "settingDiff": {
                "version": 1,
                "aliases": {
                    proposal_id: {"runIds": [run_id]},
                },
            },
        },
    ))

    row = await temp_db.fetch_one(
        "SELECT agent_process FROM ai_conversations WHERE id = ?",
        [conversation_id],
    )
    setting_diff = json.loads(row["agent_process"])["settingDiff"]
    assert setting_diff["resolutions"][proposal_id]["status"] == "rejected"
    assert setting_diff["aliases"][proposal_id]["clientTurnIds"] == ["turn-1"]
    assert setting_diff["aliases"][proposal_id]["runIds"] == [run_id]
    assert setting_diff["aliases"][proposal_id]["conversationIds"] == [
        conversation_id
    ]


async def test_terminal_run_rejects_forged_setting_resolution(
    temp_db: DatabaseConnection,
):
    from infrastructure.persistence.run_conversation_store import (
        ensure_terminal_run_conversation,
    )
    from infrastructure.persistence.run_store import complete_run, create_run

    run_id = await create_run(
        temp_db, session_id=1, prompt="修改设定", mode="agent"
    )
    await complete_run(temp_db, run_id, final_response="")
    conversation_id = await ensure_terminal_run_conversation(temp_db, run_id)
    with pytest.raises(HTTPException) as forged:
        await save_conversation(SaveConversationRequest(
            sessionId=1,
            prompt="不可伪造",
            response="",
            agentRunId=run_id,
            agentProcess={
                "settingDiff": {
                    "resolutions": {
                        "setting-proposal:v1:not-in-journal": {
                            "proposalId": "setting-proposal:v1:not-in-journal",
                            "sessionKey": "background:book-1",
                            "kind": "background",
                            "title": "故事背景",
                            "status": "committed",
                        },
                    },
                },
            },
        ))

    assert forged.value.status_code == 409
    row = await temp_db.fetch_one(
        "SELECT agent_process FROM ai_conversations WHERE id = ?",
        [conversation_id],
    )
    assert row["agent_process"] is None


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
        agentProcess={
            "delegations": [{"delegationId": "delegation-1", "status": "done"}],
            "subAgentActivities": [{
                "delegationId": "delegation-1",
                "message": {"role": "assistant", "content": "子任务结果"},
            }],
        },
    ))

    row = await temp_db.fetch_one(
        "SELECT context_compaction, context_budget, agent_process "
        "FROM ai_conversations WHERE id = ?",
        [created["data"]["id"]],
    )
    assert json.loads(row["context_compaction"])["compactedTurnCount"] == 4
    assert json.loads(row["context_budget"])["windowTokens"] == 200_000
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
    from infrastructure.persistence.run_store import complete_run, create_run

    run_id = await create_run(temp_db, session_id=1, prompt="p", mode="agent")
    await complete_run(temp_db, run_id, final_response="r")
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
    from purra.long_tasks import LongTaskCreateCommand, LongTaskUnitSpec
    from infrastructure.persistence.run_store import complete_run, create_run
    from infrastructure.persistence.sqlite_long_task_repository import (
        SqliteLongTaskRepository,
    )

    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id) VALUES (15, 'book-1')"
    )
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id) VALUES (16, 'book-1')"
    )

    run_id = await create_run(
        temp_db,
        session_id=15,
        prompt="连续创作剩余场景",
        mode="agent",
    )
    await complete_run(temp_db, run_id, final_response="")
    created = await save_conversation(SaveConversationRequest(
        sessionId=15,
        prompt="连续创作剩余场景",
        response="任务已开始，进度将在本轮持续更新。",
        agentRunId=run_id,
    ))
    await SqliteLongTaskRepository(temp_db).create(
        "task-1",
        LongTaskCreateCommand(
            namespace="purrtypos.screenplay",
            kind="screenplay_draft_generation",
            owner_id="project-1",
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
    await complete_run(temp_db, continuation_run_id, final_response="")
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
    ) == {"response": "后台完成的回答", "model": None}


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

    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) VALUES (?, ?, ?)",
        [31, "book-1", "chapter-31"],
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


@pytest.mark.parametrize(
    ("status", "final_response"),
    [
        ("failed", "provider_insufficient_balance"),
        ("blocked", "blocked detail"),
        ("canceled", "cancel receipt"),
        ("done", ""),
    ],
)
async def test_terminal_materializer_never_turns_non_output_metadata_into_prose(
    temp_db: DatabaseConnection,
    status: str,
    final_response: str,
):
    from infrastructure.persistence.run_conversation_store import (
        ensure_terminal_run_conversation,
    )
    from infrastructure.persistence.run_store import create_run, update_run_status

    run_id = await create_run(
        temp_db, session_id=1, prompt=f"{status} question", mode="agent"
    )
    await update_run_status(
        temp_db, run_id, status, final_response=final_response
    )

    conversation_id = await ensure_terminal_run_conversation(temp_db, run_id)

    assert conversation_id is not None
    assert await temp_db.fetch_one(
        "SELECT chapter_id, response FROM ai_conversations WHERE id = ?",
        [conversation_id],
    ) == {"chapter_id": "chapter-1", "response": ""}


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
        "commentary",
        "tool_call_segments",
        "commentary_blocks",
        "commentary_durations_ms",
        "duration_ms",
        "task_plan",
        "context_compaction",
        "context_budget",
            "agent_process",
            "client_turn_id",
        }

    # Extra columns in an upgraded user database are retained physically but
    # are not part of the current response contract.
    await temp_db.execute(
        "ALTER TABLE ai_conversations ADD COLUMN obsolete_payload TEXT DEFAULT NULL"
    )
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id) VALUES (9, 'book-1')"
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


async def test_truncating_history_retires_tail_run_from_latest_recovery(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id) VALUES (9, 'book-1')"
    )
    first = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations (session_id, prompt, response) "
        "VALUES (9, '保留问题', '保留回答')"
    )
    tail = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations (session_id, prompt, response) "
        "VALUES (9, '被编辑问题', '旧回答')"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, conversation_id, status, prompt, binding_namespace, "
        "binding_aggregate_id, binding_command_id) "
        "VALUES ('run-kept', 9, ?, 'done', '保留问题', "
        "'writing.chat.request', '9', 'request-kept')",
        [first],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, conversation_id, status, prompt, binding_namespace, "
        "binding_aggregate_id, binding_command_id) "
        "VALUES ('run-tail', 9, ?, 'done', '被编辑问题', "
        "'writing.chat.request', '9', 'request-tail')",
        [tail],
    )
    await temp_db.execute(
        "INSERT INTO ai_writing_chat_requests "
        "(request_id, session_id, request_digest, status, run_id) "
        "VALUES ('request-tail', 9, 'digest', 'run_bound', 'run-tail')"
    )
    await temp_db.execute(
        "INSERT INTO ai_error_reports "
        "(id, stream_id, agent_run_id, session_id, conversation_id, error_message) "
        "VALUES ('report-tail', 'stream-tail', 'run-tail', 9, ?, '旧错误')",
        [tail],
    )
    await temp_db.execute(
        "INSERT INTO memory_items "
        "(book_id, kind, content, status, source_type, source_id) "
        "VALUES ('book-1', 'instruction', '被截断的显式记忆', 'active', "
        "'conversation', ?)",
        [str(tail)],
    )

    result = await delete_after_turn("9", keepTurnCount=1)

    assert result == {"success": True}
    assert await temp_db.fetch_one(
        "SELECT session_id, conversation_id FROM ai_agent_runs "
        "WHERE id = 'run-tail'"
    ) == {"session_id": None, "conversation_id": None}
    assert await temp_db.fetch_one(
        "SELECT status FROM ai_writing_chat_requests "
        "WHERE request_id = 'request-tail'"
    ) == {"status": "canceled"}
    assert await temp_db.fetch_one(
        "SELECT session_id, conversation_id FROM ai_error_reports "
        "WHERE id = 'report-tail'"
    ) == {"session_id": None, "conversation_id": None}
    assert await temp_db.fetch_one(
        "SELECT status FROM memory_items "
        "WHERE source_type = 'conversation_truncated' "
        "AND source_id = ?",
        [str(tail)],
    ) == {"status": "archived"}
    from infrastructure.persistence.run_store import get_latest_run_for_session

    latest = await get_latest_run_for_session(temp_db, 9)
    assert latest is not None
    assert latest["id"] == "run-kept"


async def test_truncating_history_rejects_active_tail_run(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id) VALUES (9, 'book-1')"
    )
    tail = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations (session_id, prompt, response) "
        "VALUES (9, '运行中问题', '')"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, conversation_id, status, prompt) "
        "VALUES ('run-active-tail', 9, ?, 'running', '运行中问题')",
        [tail],
    )

    with pytest.raises(HTTPException) as caught:
        await delete_after_turn("9", keepTurnCount=0)

    assert caught.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT id FROM ai_conversations WHERE id = ?", [tail]
    ) == {"id": tail}


async def test_exact_truncation_retires_unmaterialized_fallback_without_new_rows(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id) VALUES (9, 'book-1')"
    )
    kept = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations (session_id, prompt, response) "
        "VALUES (9, '保留', '保留回答')"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, conversation_id, status, prompt, binding_namespace, "
        "binding_aggregate_id, binding_command_id) "
        "VALUES ('run-fallback-tail', 9, NULL, 'done', 'fallback', "
        "'writing.chat.request', '9', 'request-fallback-tail')"
    )
    await temp_db.execute(
        "INSERT INTO ai_writing_chat_requests "
        "(request_id, session_id, request_digest, status, run_id) "
        "VALUES ('request-fallback-tail', 9, 'digest', 'run_bound', "
        "'run-fallback-tail')"
    )
    await temp_db.execute(
        "INSERT INTO ai_error_reports "
        "(id, stream_id, agent_run_id, session_id, conversation_id, error_message) "
        "VALUES ('report-unprojected', 'stream-unprojected', "
        "'run-fallback-tail', 9, NULL, '旧错误')"
    )

    result = await delete_after_turn(
        "9",
        keepTurnCount=1,
        retireConversationIds="",
        retireRunIds="run-fallback-tail",
        expectedConversationIds=str(kept),
    )

    assert result == {"success": True}
    assert await temp_db.fetch_one(
        "SELECT session_id FROM ai_agent_runs WHERE id = 'run-fallback-tail'"
    ) == {"session_id": None}
    assert await temp_db.fetch_one(
        "SELECT session_id FROM ai_error_reports WHERE id = 'report-unprojected'"
    ) == {"session_id": None}


async def test_exact_truncation_rejects_a_concurrently_added_conversation(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id) VALUES (9, 'book-1')"
    )
    kept = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations (session_id, prompt, response) "
        "VALUES (9, '已知行', '回答')"
    )
    concurrent = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations (session_id, prompt, response) "
        "VALUES (9, '并发新行', '不可误删')"
    )

    with pytest.raises(HTTPException) as caught:
        await delete_after_turn(
            "9",
            keepTurnCount=1,
            retireConversationIds="",
            retireRunIds="",
            expectedConversationIds=str(kept),
        )

    assert caught.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT id FROM ai_conversations WHERE id = ?", [concurrent]
    ) == {"id": concurrent}


async def test_exact_truncation_rejects_an_unmaterialized_concurrent_root_run(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id) VALUES (9, 'book-1')"
    )
    kept = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations (session_id, prompt, response) "
        "VALUES (9, '保留', '回答')"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, prompt, binding_namespace, "
        "binding_aggregate_id, binding_command_id) VALUES "
        "('run-visible-tail', 9, 'done', '待编辑', "
        "'writing.chat.request', '9', 'request-visible-tail'), "
        "('run-concurrent', 9, 'running', '并发请求', "
        "'writing.chat.request', '9', 'request-concurrent')"
    )

    with pytest.raises(HTTPException) as caught:
        await delete_after_turn(
            "9",
            keepTurnCount=1,
            retireConversationIds="",
            retireRunIds="run-visible-tail",
            expectedConversationIds=str(kept),
            expectedRunIds="run-visible-tail",
        )

    assert caught.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT session_id FROM ai_agent_runs WHERE id = 'run-visible-tail'"
    ) == {"session_id": 9}


async def test_exact_truncation_rejects_a_concurrent_accepted_receipt(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id) VALUES (9, 'book-1')"
    )
    kept = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations (session_id, prompt, response) "
        "VALUES (9, '保留', '回答')"
    )
    await temp_db.execute(
        "INSERT INTO ai_writing_chat_requests "
        "(request_id, session_id, request_digest, status) "
        "VALUES ('request-concurrent', 9, 'sha256:concurrent', 'accepted')"
    )

    with pytest.raises(HTTPException) as caught:
        await delete_after_turn(
            "9",
            keepTurnCount=1,
            retireConversationIds="",
            retireRunIds="",
            expectedConversationIds=str(kept),
            expectedRunIds="",
        )

    assert caught.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT status FROM ai_writing_chat_requests "
        "WHERE request_id = 'request-concurrent'"
    ) == {"status": "accepted"}


async def test_exact_truncation_replay_accepts_verified_post_state(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id) VALUES (9, 'book-1')"
    )
    kept = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations (session_id, prompt, response) "
        "VALUES (9, '保留', '回答')"
    )
    tail = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations (session_id, prompt, response) "
        "VALUES (9, '删除', '旧回答')"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, conversation_id, status, prompt, "
        "binding_namespace, binding_aggregate_id, binding_command_id) "
        "VALUES ('run-replay-tail', 9, ?, 'done', '删除', "
        "'writing.chat.request', '9', 'request-replay-tail')",
        [tail],
    )
    kwargs = {
        "keepTurnCount": 1,
        "retireConversationIds": str(tail),
        "retireRunIds": "run-replay-tail",
        "expectedConversationIds": f"{kept},{tail}",
        "expectedRunIds": "run-replay-tail",
    }

    assert await delete_after_turn("9", **kwargs) == {"success": True}
    assert await delete_after_turn("9", **kwargs) == {"success": True}


async def test_saved_local_turn_is_retired_with_idempotent_truncation(
    temp_db: DatabaseConnection,
):
    request = SaveConversationRequest(
        sessionId=1,
        bookId="book-1",
        chapterId="chapter-1",
        prompt="将被编辑掉的问题",
        response="旧回答",
        clientTurnId="turn-save-before-truncate",
        expectedConversationIds=[],
    )
    saved = await save_conversation(request)
    conversation_id = int(saved["data"]["id"])
    kwargs = {
        "keepTurnCount": 0,
        "retireConversationIds": str(conversation_id),
        "retireRunIds": "",
        "expectedConversationIds": str(conversation_id),
        "expectedRunIds": "",
    }

    assert await delete_after_turn("1", **kwargs) == {"success": True}
    assert await delete_after_turn("1", **kwargs) == {"success": True}
    receipt = await temp_db.fetch_one(
        "SELECT status, payload_digest, conversation_id, revision "
        "FROM ai_local_conversation_turn_receipts "
        "WHERE session_id = 1 "
        "AND client_turn_id = 'turn-save-before-truncate'"
    )
    assert receipt is not None
    assert receipt == {
        "status": "retired",
        "payload_digest": receipt["payload_digest"],
        "conversation_id": None,
        "revision": 2,
    }
    assert receipt["payload_digest"].startswith("sha256:")
    with pytest.raises(HTTPException) as late_replay:
        await save_conversation(request)
    assert late_replay.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT id FROM ai_conversations WHERE session_id = 1"
    ) is None


async def test_truncation_tombstones_an_unmaterialized_local_turn(
    temp_db: DatabaseConnection,
):
    app = FastAPI()
    app.include_router(conversations_router, prefix="/api")

    deleted = await request_json(
        app,
        method="DELETE",
        path=(
            "/api/conversations/1/after-turn?keepTurnCount=0"
            "&expectedConversationIds=&retireConversationIds="
            "&expectedRunIds=&retireRunIds="
            "&retireClientTurnIds=turn-before-materialize"
        ),
        json_body=None,
    )

    assert deleted.status_code == 200
    with pytest.raises(HTTPException) as late_save:
        await save_conversation(SaveConversationRequest(
            sessionId=1,
            bookId="book-1",
            chapterId="chapter-1",
            prompt="迟到的问题",
            response="迟到回答",
            clientTurnId="turn-before-materialize",
            expectedConversationIds=[],
        ))
    assert late_save.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT status, payload_digest, conversation_id, revision "
        "FROM ai_local_conversation_turn_receipts "
        "WHERE session_id = 1 "
        "AND client_turn_id = 'turn-before-materialize'"
    ) == {
        "status": "retired",
        "payload_digest": None,
        "conversation_id": None,
        "revision": 1,
    }


async def test_truncation_cannot_retire_a_kept_legacy_local_turn(
    temp_db: DatabaseConnection,
):
    kept = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations "
        "(session_id, chapter_id, prompt, response, client_turn_id) "
        "VALUES (1, 'chapter-1', '保留问题', '保留回答', 'legacy-kept-turn')"
    )
    tail = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations "
        "(session_id, chapter_id, prompt, response) "
        "VALUES (1, 'chapter-1', '尾部问题', '尾部回答')"
    )

    with pytest.raises(HTTPException) as protected:
        await delete_after_turn(
            "1",
            keepTurnCount=1,
            expectedConversationIds=f"{kept},{tail}",
            retireConversationIds=str(tail),
            expectedRunIds="",
            retireRunIds="",
            retireClientTurnIds="legacy-kept-turn",
        )

    assert protected.value.status_code == 409
    assert await temp_db.fetch_all(
        "SELECT id FROM ai_conversations WHERE session_id = 1 ORDER BY id"
    ) == [{"id": kept}, {"id": tail}]
    assert await temp_db.fetch_one(
        "SELECT status FROM ai_local_conversation_turn_receipts "
        "WHERE session_id = 1 AND client_turn_id = 'legacy-kept-turn'"
    ) is None


async def test_local_turn_tombstone_is_scoped_to_its_session(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) "
        "VALUES (2, 'book-2', 'chapter-2')"
    )
    session_two = await save_conversation(SaveConversationRequest(
        sessionId=2,
        bookId="book-2",
        chapterId="chapter-2",
        prompt="另一本书的问题",
        response="另一本书的回答",
        clientTurnId="turn-shared-token",
        expectedConversationIds=[],
    ))
    app = FastAPI()
    app.include_router(conversations_router, prefix="/api")

    deleted = await request_json(
        app,
        method="DELETE",
        path=(
            "/api/conversations/1/after-turn?keepTurnCount=0"
            "&expectedConversationIds=&retireConversationIds="
            "&expectedRunIds=&retireRunIds="
            "&retireClientTurnIds=turn-shared-token"
        ),
        json_body=None,
    )

    assert deleted.status_code == 200
    assert await temp_db.fetch_one(
        "SELECT status, conversation_id FROM ai_local_conversation_turn_receipts "
        "WHERE session_id = 2 AND client_turn_id = 'turn-shared-token'"
    ) == {
        "status": "persisted",
        "conversation_id": session_two["data"]["id"],
    }
    with pytest.raises(HTTPException) as session_one_late_save:
        await save_conversation(SaveConversationRequest(
            sessionId=1,
            bookId="book-1",
            chapterId="chapter-1",
            prompt="会话一迟到",
            response="不应保存",
            clientTurnId="turn-shared-token",
            expectedConversationIds=[],
        ))
    assert session_one_late_save.value.status_code == 409


async def test_exact_truncation_rejects_missing_targets(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id) VALUES (9, 'book-1')"
    )

    with pytest.raises(HTTPException) as missing_conversation:
        await delete_after_turn(
            "9",
            keepTurnCount=0,
            retireConversationIds="999",
            retireRunIds="",
            expectedConversationIds="",
        )
    assert missing_conversation.value.status_code == 409

    with pytest.raises(HTTPException) as missing_run:
        await delete_after_turn(
            "9",
            keepTurnCount=0,
            retireConversationIds="",
            retireRunIds="run-missing",
            expectedConversationIds="",
        )
    assert missing_run.value.status_code == 409


async def test_memory_deposition_and_truncation_are_transactionally_ordered(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id) VALUES (9, 'book-1')"
    )
    deposit_started = asyncio.Event()
    release_deposit = asyncio.Event()

    async def delayed_deposit(*, book_id, conversation_id, prompt):
        deposit_started.set()
        await release_deposit.wait()
        await temp_db.execute(
            "INSERT INTO memory_items "
            "(book_id, kind, content, status, source_type, source_id) "
            "VALUES (?, 'canon', ?, 'active', 'conversation', ?)",
            [book_id, prompt, str(conversation_id)],
        )

    monkeypatch.setattr(
        "services.memory_deposition_service.deposit_explicit_memory_from_conversation",
        delayed_deposit,
    )
    saving = asyncio.create_task(save_conversation(SaveConversationRequest(
        sessionId=9,
        prompt="请记住：不会复活",
        response="",
    )))
    await deposit_started.wait()
    deleting = asyncio.create_task(delete_after_turn("9", keepTurnCount=0))
    await asyncio.sleep(0)
    assert deleting.done() is False

    release_deposit.set()
    saved, deleted = await asyncio.gather(saving, deleting)

    assert saved["success"] is True
    assert deleted["success"] is True
    assert await temp_db.fetch_one(
        "SELECT status FROM memory_items "
        "WHERE source_type = 'conversation_truncated'"
    ) == {"status": "archived"}
