from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from exceptions import AppError

from database.connection import DatabaseConnection
from dependencies import set_db
from routers.books import delete_book
from routers.files import export_database
from routers.story_background import (
    AddAttachmentsRequest,
    AttachmentInput,
    add_attachment,
    delete_attachment,
)
from application.writing_chat_request_lifecycle import (
    WritingChatRequestLifecycle,
    WritingChatRequestStartCanceled,
)
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


async def _count(db: DatabaseConnection, table: str) -> int:
    row = await db.fetch_one(f"SELECT COUNT(*) AS c FROM {table}")
    return int(row["c"])


async def test_database_export_returns_sqlite_bytes(temp_db: DatabaseConnection):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book1", "Book One"],
    )

    res = await export_database()

    assert res.media_type == "application/octet-stream"
    assert res.body.startswith(b"SQLite format 3\x00")
    assert int(res.headers["X-PurrTypos-Db-Size"]) == len(res.body)


async def test_story_background_attachment_registers_and_deletes_file(
    temp_db: DatabaseConnection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from routers import story_background

    monkeypatch.setattr(story_background, "DATA_DIR", tmp_path)
    attachment_dir = tmp_path / "story-background-attachments" / "book1"
    attachment_dir.mkdir(parents=True)
    attachment_path = attachment_dir / "note.txt"
    attachment_path.write_text("hello", encoding="utf-8")

    res = await add_attachment(
        "book1",
        AddAttachmentsRequest(
            attachments=[
                AttachmentInput(
                    name="note.txt",
                    storedPath="story-background-attachments/book1/note.txt",
                )
            ]
        ),
    )

    assert res["success"] is True
    assert res["addedCount"] == 1
    assert len(res["data"]) == 1

    delete_res = await delete_attachment(res["data"][0]["id"])

    assert delete_res["success"] is True
    assert not attachment_path.exists()
    assert await _count(temp_db, "story_background_attachments") == 0


async def test_delete_book_cascades_related_tables_and_attachment_file(
    temp_db: DatabaseConnection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import routers.books as books_router

    monkeypatch.setattr(books_router, "DATA_DIR", tmp_path)
    attachment_dir = tmp_path / "story-background-attachments" / "book1"
    attachment_dir.mkdir(parents=True)
    attachment_path = attachment_dir / "note.txt"
    attachment_path.write_text("hello", encoding="utf-8")

    await temp_db.execute("INSERT INTO books (id, title) VALUES (?, ?)", ["book1", "Book One"])
    await temp_db.execute(
        "INSERT INTO outlines (id, title, type, book_id) VALUES (?, ?, ?, ?)",
        ["outline1", "Outline", "writing", "book1"],
    )
    await temp_db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title) VALUES (?, ?, ?)",
        ["chapter1", "outline1", "Chapter One"],
    )
    await temp_db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        ["chapter1", "{}"],
    )
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, chapter_id, title, book_id) VALUES (?, ?, ?, ?)",
        [10, "chapter1", "Session", "book1"],
    )
    await temp_db.execute(
        "INSERT INTO ai_conversations (session_id, chapter_id, prompt, response) VALUES (?, ?, ?, ?)",
        [10, "chapter1", "p", "r"],
    )
    await temp_db.execute(
        "UPDATE ai_conversations SET client_turn_id = 'book-delete-turn' "
        "WHERE session_id = 10"
    )
    await temp_db.execute(
        "INSERT INTO ai_local_conversation_turn_receipts "
        "(session_id, client_turn_id, payload_digest, status, conversation_id) "
        "VALUES (10, 'book-delete-turn', 'sha256:fixture', 'persisted', 1)"
    )
    await temp_db.execute(
        "INSERT INTO ai_conversation_summaries "
        "(session_id, version, covered_through_conversation_id, "
        "covered_turn_count, source_digest, summary_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [10, 1, 1, 1, "a" * 64, "{}"],
    )
    await temp_db.execute(
        "INSERT INTO ai_favorites (session_id, session_title, prompt, content) VALUES (?, ?, ?, ?)",
        [10, "Session", "p", "c"],
    )
    await temp_db.execute(
        "INSERT INTO ai_memories (book_id, layer, content, chapter_id) VALUES (?, ?, ?, ?)",
        ["book1", "chapter", "memory", "chapter1"],
    )
    await temp_db.execute(
        "INSERT INTO ai_foreshadowing (book_id, chapter_id, content) VALUES (?, ?, ?)",
        ["book1", "chapter1", "foreshadowing"],
    )
    await temp_db.execute(
        "INSERT INTO memory_items (book_id, kind, content, fingerprint) VALUES (?, ?, ?, ?)",
        ["book1", "plot", "memory item", "book1|plot|memory item"],
    )
    await temp_db.execute(
        "INSERT INTO memory_items (book_id, kind, content, fingerprint) VALUES (?, ?, ?, ?)",
        ["book1", "plot", "linked memory item", "book1|plot|linked memory item"],
    )
    await temp_db.execute(
        "INSERT INTO memory_links (book_id, from_memory_id, to_memory_id, relation) VALUES (?, ?, ?, ?)",
        ["book1", 1, 2, "relates_to"],
    )
    await temp_db.execute(
        "INSERT INTO story_memory_deltas "
        "(id, book_id, chapter_id, source_revision, status) "
        "VALUES (?, ?, ?, ?, ?)",
        ["delta1", "book1", "chapter1", "rev1", "applied"],
    )
    await temp_db.execute(
        "INSERT INTO story_memory_sources "
        "(id, delta_id, operation_index, book_id, chapter_id, excerpt) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["source1", "delta1", 0, "book1", "chapter1", "Hero entered the city"],
    )
    await temp_db.execute(
        "INSERT INTO story_memory_analysis_runs "
        "(id, book_id, chapter_id, source_revision, status, delta_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["analysis1", "book1", "chapter1", "rev1", "completed", "delta1"],
    )
    await temp_db.execute(
        "INSERT INTO story_memory_evolution_reviews "
        "(book_id, chapter_id, delta_id, target_key, kind, classification, "
        "recommendation, risk) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "book1",
            "chapter1",
            "delta1",
            "character:hero:location",
            "character_state",
            "addition",
            "apply",
            "low",
        ],
    )
    await temp_db.execute(
        "INSERT INTO story_memory_delta_operations "
        "(delta_id, operation_index, operation, target_key, kind, "
        "after_payload_json, source_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            "delta1",
            0,
            "upsert",
            "character:hero:location",
            "character_state",
            '{"value":"city"}',
            "source1",
        ],
    )
    await temp_db.execute(
        "INSERT INTO story_memory_records "
        "(id, book_id, memory_key, kind, payload_json, last_delta_id, last_source_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            "record1",
            "book1",
            "character:hero:location",
            "character_state",
            '{"value":"city"}',
            "delta1",
            "source1",
        ],
    )
    await temp_db.execute(
        "INSERT INTO story_memory_versions "
        "(record_id, book_id, memory_key, version, delta_id, action, kind, "
        "payload_json, status, lifecycle, provenance_status, source_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "record1",
            "book1",
            "character:hero:location",
            1,
            "delta1",
            "apply",
            "character_state",
            '{"value":"city"}',
            "confirmed",
            "active",
            "valid",
            "source1",
        ],
    )
    await temp_db.execute(
        "INSERT INTO story_background (book_id, content) VALUES (?, ?)",
        ["book1", "background"],
    )
    await temp_db.execute(
        "INSERT INTO story_background_attachments (book_id, name, stored_path) VALUES (?, ?, ?)",
        ["book1", "note.txt", "story-background-attachments/book1/note.txt"],
    )
    await temp_db.execute(
        "INSERT INTO characters (book_id, name) VALUES (?, ?)",
        ["book1", "Hero"],
    )
    await temp_db.execute(
        "INSERT INTO chapter_canvas (chapter_id, content) VALUES (?, ?)",
        ["chapter1", "draft"],
    )
    await temp_db.execute(
        "INSERT INTO outline_history (outline_id, before_title) VALUES (?, ?)",
        ["outline1", "Old"],
    )
    await temp_db.execute(
        "INSERT INTO chapter_diff_history (chapter_id, before_text, after_text) VALUES (?, ?, ?)",
        ["chapter1", "before", "after"],
    )

    res = await delete_book("book1")

    assert res["success"] is True
    assert res["data"]["attachmentPaths"] == ["story-background-attachments/book1/note.txt"]
    assert not attachment_path.exists()

    for table in [
        "books",
        "outlines",
        "outline_chapters",
        "articles",
        "ai_sessions",
        "ai_conversations",
        "ai_conversation_summaries",
        "ai_local_conversation_turn_receipts",
        "ai_favorites",
        "ai_memories",
        "ai_foreshadowing",
        "memory_items",
        "memory_links",
        "story_memory_records",
        "story_memory_deltas",
        "story_memory_analysis_runs",
        "story_memory_evolution_reviews",
        "story_memory_sources",
        "story_memory_delta_operations",
        "story_memory_versions",
        "story_background",
        "story_background_attachments",
        "characters",
        "chapter_canvas",
        "outline_history",
        "chapter_diff_history",
    ]:
        assert await _count(temp_db, table) == 0, table


async def test_delete_book_rejects_active_run_ownership(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES ('book-active', 'Active')"
    )
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id, scope) "
        "VALUES (81, 'book-active', 'setting')"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_runs (id, session_id, status, prompt) "
        "VALUES ('run-book-active', 81, 'running', '写作')"
    )

    with pytest.raises(AppError) as conflict:
        await delete_book("book-active")

    assert conflict.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT id FROM books WHERE id = 'book-active'"
    )


async def test_claimed_writing_request_blocks_book_delete_before_submit(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES ('book-claim', 'Claim')"
    )
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id, scope) "
        "VALUES (82, 'book-claim', 'setting')"
    )
    store = SqliteWritingChatRequestStore(temp_db)
    await store.reserve(
        request_id="request-book-claim",
        session_id=82,
        request_digest="sha256:claim",
        book_id="book-claim",
        chapter_id=None,
        expected_conversation_ids=[],
        expected_run_ids=[],
    )
    await store.claim(
        request_id="request-book-claim",
        session_id=82,
        request_digest="sha256:claim",
        book_id="book-claim",
        chapter_id=None,
        expected_conversation_ids=[],
        expected_run_ids=[],
    )

    with pytest.raises(AppError) as conflict:
        await delete_book("book-claim")
    assert conflict.value.status_code == 409

    await WritingChatRequestLifecycle(
        store,
        "request-book-claim",
    ).before_submit()
    assert await temp_db.fetch_one(
        "SELECT id FROM ai_sessions WHERE id = 82"
    )


async def test_before_submit_rechecks_owner_after_claim_delete_race(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES ('book-race', 'Race')"
    )
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id, scope) "
        "VALUES (84, 'book-race', 'setting')"
    )
    store = SqliteWritingChatRequestStore(temp_db)
    await store.reserve(
        request_id="request-owner-race",
        session_id=84,
        request_digest="sha256:race",
        book_id="book-race",
        chapter_id=None,
        expected_conversation_ids=[],
        expected_run_ids=[],
    )
    await store.claim(
        request_id="request-owner-race",
        session_id=84,
        request_digest="sha256:race",
        book_id="book-race",
        chapter_id=None,
        expected_conversation_ids=[],
        expected_run_ids=[],
    )

    # The product delete transaction normally loses to the claimed receipt.
    # This raw delete models an owner disappearing after claim but before the
    # final pre-submit check, which must still prevent Core Run creation.
    await temp_db.execute("DELETE FROM ai_sessions WHERE id = 84")

    with pytest.raises(WritingChatRequestStartCanceled):
        await WritingChatRequestLifecycle(
            store,
            "request-owner-race",
        ).before_submit()
    assert await temp_db.fetch_one(
        "SELECT status, rejection_code FROM ai_writing_chat_requests "
        "WHERE request_id = 'request-owner-race'"
    ) == {
        "status": "rejected",
        "rejection_code": "request_not_startable",
    }


async def test_delete_book_rejects_owner_long_task_without_session_metadata(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES ('book-task', 'Task')"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, "
        "status, total_units, metadata_json) VALUES "
        "('book-task-active', 'purrtypos.writing', 'draft', "
        "'book-task', 'missing-run', 'paused', 1, '{}')"
    )

    with pytest.raises(AppError) as conflict:
        await delete_book('book-task')

    assert conflict.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT id FROM books WHERE id = 'book-task'"
    )


async def test_delete_book_unlinks_terminal_run_and_removes_owned_runtime_rows(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES ('book-terminal', 'Terminal')"
    )
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id, scope) "
        "VALUES (83, 'book-terminal', 'setting')"
    )
    conversation_id = await temp_db.execute_and_get_id(
        "INSERT INTO ai_conversations (session_id, prompt, response) "
        "VALUES (83, 'done', 'done')"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, conversation_id, status, prompt) "
        "VALUES ('run-book-terminal', 83, ?, 'done', 'done')",
        [conversation_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_error_reports "
        "(id, stream_id, agent_run_id, session_id, conversation_id, error_message) "
        "VALUES ('report-book-terminal', 'stream-terminal', "
        "'run-book-terminal', 83, ?, 'x')",
        [conversation_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, "
        "status, total_units) VALUES ('task-book-terminal', "
        "'purrtypos.writing', 'draft', 'book-terminal', "
        "'run-book-terminal', 'completed', 1)"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_usage "
        "(task_id, run_id, invocation_count, input_tokens, output_tokens) "
        "VALUES ('task-book-terminal', 'run-book-terminal', 1, 2, 3)"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_artifacts "
        "(id, namespace, kind, owner_id, owner_ref_kind, owner_ref_id, "
        "created_by_run_id) VALUES "
        "('artifact-book-terminal', 'purrtypos.writing', 'draft', "
        "'book-terminal', 'run', 'run-book-terminal', 'run-book-terminal')"
    )
    await temp_db.execute(
        "INSERT INTO ai_writing_chat_requests "
        "(request_id, session_id, request_digest, status, rejection_code) "
        "VALUES ('request-book-terminal', 83, 'sha256:done', "
        "'rejected', 'done')"
    )

    await delete_book('book-terminal')

    assert await temp_db.fetch_one(
        "SELECT session_id, conversation_id FROM ai_agent_runs "
        "WHERE id = 'run-book-terminal'"
    ) == {"session_id": None, "conversation_id": None}
    assert await temp_db.fetch_one(
        "SELECT session_id, conversation_id FROM ai_error_reports "
        "WHERE id = 'report-book-terminal'"
    ) == {"session_id": None, "conversation_id": None}
    assert await temp_db.fetch_one(
        "SELECT id FROM ai_agent_long_tasks WHERE id = 'task-book-terminal'"
    ) is None
    assert await temp_db.fetch_one(
        "SELECT task_id FROM ai_agent_long_task_usage "
        "WHERE task_id = 'task-book-terminal'"
    ) is None
    assert await temp_db.fetch_one(
        "SELECT id FROM ai_agent_artifacts "
        "WHERE id = 'artifact-book-terminal'"
    ) is None
    assert await temp_db.fetch_one(
        "SELECT request_id FROM ai_writing_chat_requests "
        "WHERE request_id = 'request-book-terminal'"
    ) is None
