from __future__ import annotations

import json
import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException

from database.connection import DatabaseConnection
from dependencies import set_db
from routers.sessions import delete_session
from infrastructure.persistence.writing_chat_request_store import (
    SqliteWritingChatRequestStore,
)

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


async def seed_session(db: DatabaseConnection, session_id: int = 41) -> int:
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id, title) "
        "VALUES (?, ?, ?, ?)",
        [session_id, "book-1", "chapter-1", "待删除"],
    )
    return await db.execute_and_get_id(
        "INSERT INTO ai_conversations "
        "(session_id, chapter_id, prompt, response) "
        "VALUES (?, ?, ?, ?)",
        [session_id, "chapter-1", "问题", "回答"],
    )


async def test_delete_rejects_session_with_active_agent_run(
    temp_db: DatabaseConnection,
):
    conversation_id = await seed_session(temp_db)
    await temp_db.execute(
        "INSERT INTO ai_agent_runs (id, session_id, conversation_id, status, prompt) "
        "VALUES (?, ?, ?, ?, ?)",
        ["run-active", 41, conversation_id, "running", "问题"],
    )

    with pytest.raises(HTTPException) as caught:
        await delete_session(41)

    assert caught.value.status_code == 409
    assert await temp_db.fetch_one("SELECT id FROM ai_sessions WHERE id = 41")
    assert await temp_db.fetch_one(
        "SELECT id FROM ai_conversations WHERE id = ?", [conversation_id]
    )


async def test_delete_session_removes_local_turn_receipts(
    temp_db: DatabaseConnection,
):
    conversation_id = await seed_session(temp_db)
    await temp_db.execute(
        "UPDATE ai_conversations SET client_turn_id = 'turn-delete-session' "
        "WHERE id = ?",
        [conversation_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_local_conversation_turn_receipts "
        "(session_id, client_turn_id, payload_digest, status, conversation_id) "
        "VALUES (41, 'turn-delete-session', 'sha256:fixture', "
        "'persisted', ?)",
        [conversation_id],
    )

    assert await delete_session(41) == {"success": True}
    assert await temp_db.fetch_one(
        "SELECT session_id FROM ai_local_conversation_turn_receipts "
        "WHERE session_id = 41"
    ) is None


async def test_delete_screenplay_session_removes_conversation_runtime_rows(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_kind, source_snapshot_json) "
        "VALUES ('project-session-delete', '剧本', 'original', '{}')"
    )
    await temp_db.execute(
        "INSERT INTO ai_sessions "
        "(id, screenplay_project_id, scope, title, closed) "
        "VALUES (43, 'project-session-delete', 'screenplay', '历史对话', 1)"
    )
    await temp_db.execute(
        "INSERT INTO screenplay_agent_turns "
        "(id, project_id, session_id, command_id, status, user_content, "
        "assistant_content, operation_id) VALUES "
        "('turn-session-delete', 'project-session-delete', 43, "
        "'command-session-delete', 'done', '问题', '回答', "
        "'operation-session-delete')"
    )
    await temp_db.execute(
        "INSERT INTO screenplay_agent_operations "
        "(id, turn_id, project_id, session_id, status, target_role, "
        "manifest_digest) VALUES ('operation-session-delete', "
        "'turn-session-delete', 'project-session-delete', 43, 'completed', "
        "'draft', 'sha256:manifest')"
    )
    await temp_db.execute(
        "INSERT INTO screenplay_agent_operation_usage "
        "(operation_id, run_id, invocation_count, input_tokens, output_tokens) "
        "VALUES ('operation-session-delete', 'run-session-delete', 1, 2, 3)"
    )
    await temp_db.execute(
        "INSERT INTO screenplay_agent_operation_commands "
        "(command_id, operation_id, command_type, request_digest, receipt_id) "
        "VALUES ('operation-command-delete', 'operation-session-delete', "
        "'cancel', 'sha256:request', 'receipt-delete')"
    )
    await temp_db.execute(
        "INSERT INTO screenplay_agent_cancel_commands "
        "(command_id, turn_id, operation_id, request_digest, receipt_id) "
        "VALUES ('cancel-command-delete', 'turn-session-delete', "
        "'operation-session-delete', 'sha256:cancel', 'cancel-receipt')"
    )
    await temp_db.execute(
        "INSERT INTO screenplay_checkpoint_plans "
        "(operation_id, task_id, checkpoint_key, root_run_id, status, "
        "input_digest) VALUES ('operation-session-delete', "
        "'task-session-delete', 'checkpoint-delete', 'run-session-delete', "
        "'completed', 'sha256:input')"
    )

    assert await delete_session(43) == {"success": True}

    assert await temp_db.fetch_one(
        "SELECT id FROM ai_sessions WHERE id = 43"
    ) is None
    for table, column, value in (
        ("screenplay_agent_turns", "id", "turn-session-delete"),
        ("screenplay_agent_operations", "id", "operation-session-delete"),
        ("screenplay_agent_operation_usage", "operation_id", "operation-session-delete"),
        ("screenplay_agent_operation_commands", "operation_id", "operation-session-delete"),
        ("screenplay_agent_cancel_commands", "turn_id", "turn-session-delete"),
        ("screenplay_checkpoint_plans", "operation_id", "operation-session-delete"),
    ):
        assert await temp_db.fetch_one(
            f"SELECT 1 AS present FROM {table} WHERE {column} = ?",
            [value],
        ) is None


async def test_delete_screenplay_session_rejects_active_turn(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_kind, source_snapshot_json) "
        "VALUES ('project-active-session', '剧本', 'original', '{}')"
    )
    await temp_db.execute(
        "INSERT INTO ai_sessions "
        "(id, screenplay_project_id, scope, title, closed) "
        "VALUES (44, 'project-active-session', 'screenplay', '运行中对话', 1)"
    )
    await temp_db.execute(
        "INSERT INTO screenplay_agent_turns "
        "(id, project_id, session_id, command_id, status, user_content) "
        "VALUES ('turn-active-session', 'project-active-session', 44, "
        "'command-active-session', 'running', '继续')"
    )

    with pytest.raises(HTTPException) as caught:
        await delete_session(44)

    assert caught.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT id FROM ai_sessions WHERE id = 44"
    ) == {"id": 44}
    assert await temp_db.fetch_one(
        "SELECT id FROM screenplay_agent_turns "
        "WHERE id = 'turn-active-session'"
    ) == {"id": "turn-active-session"}


async def test_delete_rejects_session_with_active_long_task(
    temp_db: DatabaseConnection,
):
    await seed_session(temp_db)
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, "
        "status, total_units, metadata_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "task-paused",
            "writing.book",
            "draft",
            "book-1",
            "run-terminal",
            "paused",
            1,
            '{"sessionId":41}',
        ],
    )

    with pytest.raises(HTTPException) as caught:
        await delete_session(41)

    assert caught.value.status_code == 409


async def test_delete_rejects_metadata_less_active_task_owned_by_session_run(
    temp_db: DatabaseConnection,
):
    conversation_id = await seed_session(temp_db)
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, conversation_id, status, prompt) "
        "VALUES ('run-task-owner', 41, ?, 'done', '问题')",
        [conversation_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, "
        "status, total_units, metadata_json) "
        "VALUES ('task-without-session-meta', 'writing.book', "
        "'draft', 'book-1', 'run-task-owner', 'running', 1, '{}')"
    )

    with pytest.raises(HTTPException) as caught:
        await delete_session(41)

    assert caught.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT id FROM ai_agent_long_tasks "
        "WHERE id = 'task-without-session-meta'"
    )
    assert await temp_db.fetch_one("SELECT id FROM ai_sessions WHERE id = 41")


async def test_delete_unlinks_durable_audit_rows_and_removes_product_rows(
    temp_db: DatabaseConnection,
):
    conversation_id = await seed_session(temp_db)
    await temp_db.execute(
        "INSERT INTO ai_conversation_summaries "
        "(session_id, version, covered_through_conversation_id, "
        "covered_turn_count, source_digest, summary_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [41, 1, conversation_id, 1, "a" * 64, "{}"],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, "
        "status, total_units, metadata_json) "
        "VALUES ('task-done', 'writing.book', 'draft', 'book-1', "
        "'run-done', 'completed', 1, '{\"sessionId\":41,\"kept\":true}')"
    )
    await temp_db.execute(
        "INSERT INTO ai_favorites (session_id, session_title, prompt, content) "
        "VALUES (?, ?, ?, ?)",
        [41, "待删除", "问题", "回答"],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_runs (id, session_id, conversation_id, status, prompt) "
        "VALUES (?, ?, ?, ?, ?)",
        ["run-done", 41, conversation_id, "done", "问题"],
    )
    await temp_db.execute(
        "INSERT INTO ai_error_reports "
        "(id, stream_id, agent_run_id, session_id, conversation_id, error_message) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["report-1", "stream-1", "run-done", 41, conversation_id, "失败"],
    )

    result = await delete_session(41)

    assert result == {"success": True}
    for table in (
        "ai_sessions",
        "ai_conversations",
        "ai_conversation_summaries",
        "ai_favorites",
    ):
        assert await temp_db.fetch_one(
            f"SELECT 1 AS present FROM {table} WHERE "
            + ("id = ?" if table == "ai_sessions" else "session_id = ?"),
            [41],
        ) is None
    run = await temp_db.fetch_one(
        "SELECT session_id, conversation_id FROM ai_agent_runs WHERE id = ?",
        ["run-done"],
    )
    report = await temp_db.fetch_one(
        "SELECT session_id, conversation_id FROM ai_error_reports WHERE id = ?",
        ["report-1"],
    )
    assert run == {"session_id": None, "conversation_id": None}
    assert report == {"session_id": None, "conversation_id": None}
    task = await temp_db.fetch_one(
        "SELECT metadata_json FROM ai_agent_long_tasks WHERE id = 'task-done'"
    )
    assert json.loads(task["metadata_json"]) == {"kept": True}


async def test_delete_archives_conversation_sourced_memory_before_removing_rows(
    temp_db: DatabaseConnection,
):
    conversation_id = await seed_session(temp_db, 42)
    await temp_db.execute(
        "INSERT INTO memory_items "
        "(book_id, kind, content, status, source_type, source_id) "
        "VALUES ('book-1', 'canon', '应随会话归档', 'active', "
        "'conversation', ?)",
        [str(conversation_id)],
    )

    await delete_session(42)

    assert await temp_db.fetch_one(
        "SELECT status FROM memory_items "
        "WHERE source_type = 'conversation_truncated' "
        "AND source_id = ?",
        [str(conversation_id)],
    ) == {"status": "archived"}


async def test_delete_uses_same_active_ownership_predicate_as_unlink(
    temp_db: DatabaseConnection,
):
    conversation_id = await seed_session(temp_db, 51)
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id, title) "
        "VALUES (52, 'book-1', 'other')"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, conversation_id, status, prompt) "
        "VALUES ('run-mismatched-active', 52, ?, 'running', '问题')",
        [conversation_id],
    )

    with pytest.raises(HTTPException) as caught:
        await delete_session(51)

    assert caught.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT session_id, conversation_id, status FROM ai_agent_runs "
        "WHERE id = 'run-mismatched-active'"
    ) == {
        "session_id": 52,
        "conversation_id": conversation_id,
        "status": "running",
    }
    assert await temp_db.fetch_one(
        "SELECT id FROM ai_sessions WHERE id = 51"
    ) == {"id": 51}


async def test_writing_request_cannot_be_accepted_after_session_delete_wins_lock(
    temp_db: DatabaseConnection,
):
    await seed_session(temp_db, 61)
    delete_has_lock = asyncio.Event()
    finish_delete = asyncio.Event()

    async def delete_while_locked() -> None:
        async with temp_db.transaction():
            delete_has_lock.set()
            await finish_delete.wait()
            await temp_db.execute(
                "DELETE FROM ai_conversations WHERE session_id = 61"
            )
            await temp_db.execute("DELETE FROM ai_sessions WHERE id = 61")

    deleting = asyncio.create_task(delete_while_locked())
    await delete_has_lock.wait()
    reserving = asyncio.create_task(SqliteWritingChatRequestStore(temp_db).reserve(
        request_id="chat-late-after-delete",
        session_id=61,
        request_digest="sha256:late",
        book_id="book-1",
        chapter_id="chapter-1",
    ))
    finish_delete.set()
    await deleting

    with pytest.raises(ValueError, match="session.*does not exist"):
        await reserving
    assert await temp_db.fetch_one(
        "SELECT request_id FROM ai_writing_chat_requests WHERE session_id = 61"
    ) is None


async def test_active_receipt_blocks_delete_and_terminal_receipt_is_cleaned(
    temp_db: DatabaseConnection,
):
    await seed_session(temp_db, 62)
    store = SqliteWritingChatRequestStore(temp_db)
    await store.reserve(
        request_id="chat-delete-guard",
        session_id=62,
        request_digest="sha256:guard",
        book_id="book-1",
        chapter_id="chapter-1",
    )

    with pytest.raises(HTTPException) as caught:
        await delete_session(62)
    assert caught.value.status_code == 409

    await store.request_cancel("chat-delete-guard", timestamp_ms=100)
    await delete_session(62)
    assert await store.get("chat-delete-guard") is None
