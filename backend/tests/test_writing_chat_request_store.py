from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from infrastructure.persistence.run_store import create_run
from infrastructure.persistence.writing_chat_request_store import (
    SqliteWritingChatRequestStore,
    WritingChatRequestConflictError,
)
from purra.contracts import RunBinding


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def receipt_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) VALUES (7, 'book-1', 'chapter-1')"
    )
    try:
        yield db, SqliteWritingChatRequestStore(db)
    finally:
        await db.close()


async def test_reservation_is_idempotent_and_rejects_id_reuse(receipt_db):
    _db, store = receipt_db
    first = await store.reserve(
        request_id="chat-1", session_id=7, request_digest="sha256:a",
        book_id="book-1", chapter_id="chapter-1",
    )
    replay = await store.reserve(
        request_id="chat-1", session_id=7, request_digest="sha256:a",
        book_id="book-1", chapter_id="chapter-1",
    )

    assert first == replay
    assert replay.status == "accepted"
    with pytest.raises(WritingChatRequestConflictError):
        await store.reserve(
            request_id="chat-1", session_id=7, request_digest="sha256:b",
            book_id="book-1", chapter_id="chapter-1",
        )


async def test_cancel_before_start_is_durable_and_prevents_claim(receipt_db):
    _db, store = receipt_db
    await store.reserve(
        request_id="chat-canceled", session_id=7, request_digest="sha256:a",
        book_id="book-1", chapter_id="chapter-1",
    )
    first = await store.request_cancel("chat-canceled", timestamp_ms=100)
    second = await store.request_cancel("chat-canceled", timestamp_ms=200)
    receipt, claimed = await store.claim(
        request_id="chat-canceled", session_id=7, request_digest="sha256:a",
        book_id="book-1", chapter_id="chapter-1",
    )

    assert first == second
    assert receipt.status == "canceled"
    assert claimed is False
    assert receipt.cancel_requested_at_ms == 100


async def test_reservation_fences_the_complete_session_history_frontier(receipt_db):
    db, store = receipt_db
    conversation_id = await db.execute_and_get_id(
        "INSERT INTO ai_conversations (session_id, prompt, response) "
        "VALUES (7, '已有问题', '已有回答')"
    )
    await db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, conversation_id, status, prompt) "
        "VALUES ('run-existing', 7, ?, 'done', '已有问题')",
        [conversation_id],
    )

    receipt = await store.reserve(
        request_id="chat-frontier",
        session_id=7,
        request_digest="sha256:frontier",
        book_id="book-1",
        chapter_id="chapter-1",
        expected_conversation_ids=[conversation_id],
        expected_run_ids=["run-existing"],
    )
    assert receipt.status == "accepted"

    with pytest.raises(WritingChatRequestConflictError, match="already has active"):
        await store.reserve(
            request_id="chat-concurrent",
            session_id=7,
            request_digest="sha256:concurrent",
            book_id="book-1",
            chapter_id="chapter-1",
            expected_conversation_ids=[conversation_id],
            expected_run_ids=["run-existing"],
        )

    await store.request_cancel("chat-frontier", timestamp_ms=200)
    with pytest.raises(WritingChatRequestConflictError, match="history changed"):
        await store.reserve(
            request_id="chat-stale",
            session_id=7,
            request_digest="sha256:stale",
            book_id="book-1",
            chapter_id="chapter-1",
            expected_conversation_ids=[],
            expected_run_ids=[],
        )


async def test_claim_terminalizes_when_history_changes_after_reservation(receipt_db):
    db, store = receipt_db
    await store.reserve(
        request_id="chat-claim-fence",
        session_id=7,
        request_digest="sha256:claim-fence",
        book_id="book-1",
        chapter_id="chapter-1",
        expected_conversation_ids=[],
        expected_run_ids=[],
    )
    await db.execute(
        "INSERT INTO ai_conversations (session_id, prompt, response) "
        "VALUES (7, '并发问题', '并发回答')"
    )

    receipt, claimed = await store.claim(
        request_id="chat-claim-fence",
        session_id=7,
        request_digest="sha256:claim-fence",
        book_id="book-1",
        chapter_id="chapter-1",
        expected_conversation_ids=[],
        expected_run_ids=[],
    )

    assert claimed is False
    assert receipt.status == "rejected"
    assert receipt.rejection_code == "history_changed_before_start"


async def test_cancel_during_start_transfers_once_to_later_exact_run(receipt_db):
    db, store = receipt_db
    await store.reserve(
        request_id="chat-later", session_id=7, request_digest="sha256:a",
        book_id="book-1", chapter_id="chapter-1",
    )
    _receipt, claimed = await store.claim(
        request_id="chat-later", session_id=7, request_digest="sha256:a",
        book_id="book-1", chapter_id="chapter-1",
    )
    assert claimed is True
    await store.request_cancel("chat-later", timestamp_ms=123)
    run_id = await create_run(
        db,
        session_id=7,
        prompt="later",
        mode="agent",
        binding=RunBinding(
            namespace="writing.chat.request",
            aggregate_id="7",
            command_id="chat-later",
        ),
    )

    bound = await store.bind_run("chat-later", run_id)
    replay = await store.bind_run("chat-later", run_id)
    run = await db.fetch_one(
        "SELECT cancel_requested_at_ms FROM ai_agent_runs WHERE id = ?", [run_id]
    )

    assert bound.status == "run_bound"
    assert replay.revision == bound.revision
    assert replay.cancel_applied_run_id == run_id
    assert run == {"cancel_requested_at_ms": 123}


async def test_wrong_run_binding_cannot_steal_receipt(receipt_db):
    db, store = receipt_db
    await store.reserve(
        request_id="chat-owned", session_id=7, request_digest="sha256:a",
        book_id="book-1", chapter_id="chapter-1",
    )
    await store.claim(
        request_id="chat-owned", session_id=7, request_digest="sha256:a",
        book_id="book-1", chapter_id="chapter-1",
    )
    wrong = await create_run(
        db,
        session_id=7,
        prompt="wrong",
        mode="agent",
        binding=RunBinding(
            namespace="writing.chat.request",
            aggregate_id="7",
            command_id="another-request",
        ),
    )

    with pytest.raises(WritingChatRequestConflictError):
        await store.bind_run("chat-owned", wrong)


async def test_start_failure_repairs_an_exact_run_created_before_bind(receipt_db):
    db, store = receipt_db
    await store.reserve(
        request_id="chat-bind-window",
        session_id=7,
        request_digest="sha256:bind-window",
        book_id="book-1",
        chapter_id="chapter-1",
    )
    await store.claim(
        request_id="chat-bind-window",
        session_id=7,
        request_digest="sha256:bind-window",
        book_id="book-1",
        chapter_id="chapter-1",
    )
    run_id = await create_run(
        db,
        session_id=7,
        prompt="bind window",
        mode="agent",
        binding=RunBinding(
            namespace="writing.chat.request",
            aggregate_id="7",
            command_id="chat-bind-window",
        ),
    )

    repaired = await store.finish_before_run(
        "chat-bind-window",
        "request_start_failed",
    )

    assert repaired.status == "run_bound"
    assert repaired.run_id == run_id
    assert repaired.rejection_code is None


async def test_restart_repairs_exact_run_or_terminalizes_unbound_request(receipt_db):
    db, store = receipt_db
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) VALUES "
        "(8, 'book-1', 'chapter-1'), (9, 'book-1', 'chapter-1')"
    )
    requests = (
        ("chat-repair", 7),
        ("chat-reject", 8),
        ("chat-cancel", 9),
    )
    for request_id, session_id in requests:
        await store.reserve(
            request_id=request_id,
            session_id=session_id,
            request_digest=f"sha256:{request_id}",
            book_id="book-1",
            chapter_id="chapter-1",
        )
        await store.claim(
            request_id=request_id,
            session_id=session_id,
            request_digest=f"sha256:{request_id}",
            book_id="book-1",
            chapter_id="chapter-1",
        )
    await store.request_cancel("chat-cancel", timestamp_ms=500)
    run_id = await create_run(
        db,
        session_id=7,
        prompt="repair",
        mode="agent",
        binding=RunBinding(
            namespace="writing.chat.request",
            aggregate_id="7",
            command_id="chat-repair",
        ),
    )

    assert await store.recover_unbound() == ("chat-repair",)
    assert (await store.get("chat-repair")).run_id == run_id
    assert (await store.get("chat-reject")).status == "rejected"
    assert (await store.get("chat-cancel")).status == "canceled"


async def test_session_delete_wins_before_reservation_without_orphan(receipt_db):
    db, store = receipt_db
    delete_has_lock = asyncio.Event()
    finish_delete = asyncio.Event()

    async def delete_while_locked() -> None:
        async with db.transaction():
            delete_has_lock.set()
            await finish_delete.wait()
            await db.execute("DELETE FROM ai_sessions WHERE id = 7")

    deleting = asyncio.create_task(delete_while_locked())
    await delete_has_lock.wait()
    reserving = asyncio.create_task(store.reserve(
        request_id="chat-after-delete",
        session_id=7,
        request_digest="sha256:a",
        book_id="book-1",
        chapter_id="chapter-1",
    ))
    finish_delete.set()
    await deleting

    with pytest.raises(ValueError, match="session does not exist"):
        await reserving
    assert await store.get("chat-after-delete") is None


async def test_child_runs_do_not_fence_the_session_frontier(receipt_db):
    """子 Agent 子 Run 继承 session_id 但不会物化成对话，不能计入前沿。"""
    db, store = receipt_db
    conversation_id = await db.execute_and_get_id(
        "INSERT INTO ai_conversations (session_id, prompt, response) "
        "VALUES (7, '委派问题', '委派回答')"
    )
    await db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, conversation_id, status, prompt) "
        "VALUES ('run-root', 7, ?, 'done', '委派问题')",
        [conversation_id],
    )
    # 会话曾委派子 Agent：子 Run 带 session_id、无 conversation、已终态。
    await db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, parent_run_id, root_run_id, agent_id, status, prompt) "
        "VALUES ('agent-run-9', 7, 'run-root', 'run-root', 'agent-9', "
        "'done', '子任务')"
    )

    receipt = await store.reserve(
        request_id="chat-after-delegation",
        session_id=7,
        request_digest="sha256:after",
        book_id="book-1",
        chapter_id="chapter-1",
        expected_conversation_ids=[conversation_id],
        # 前端只可能从对话行收集到根 Run。
        expected_run_ids=["run-root"],
    )

    assert receipt.status == "accepted"
