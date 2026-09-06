from __future__ import annotations

import json

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import clear_db, set_db
from routers.chapters import save_chapters
from schemas.chapters import ChapterItem, SaveChaptersRequest
from services import memory_deposition_service
from services.memory_deposition_service import (
    SOURCE_CHUNK_CHARS,
    extract_explicit_memory,
    record_chapter_diff_candidate,
    record_deleted_conversation_sources,
    record_explicit_conversation_memory,
    record_inline_article_candidate,
)


@pytest_asyncio.fixture
async def db(tmp_path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    set_db(connection)
    await connection.execute("INSERT INTO books (id, title) VALUES ('book', 'Book')")
    await connection.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES ('outline', 'Writing', 'writing', 'book')"
    )
    await connection.execute(
        "INSERT INTO outline_chapters (id, outline_id, title) "
        "VALUES ('chapter', 'outline', 'Chapter')"
    )
    try:
        yield connection
    finally:
        clear_db(connection)
        await connection.close()


@pytest.mark.asyncio
async def test_ai_diff_records_pending_inference_delivery(db):
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) "
        "VALUES ('memory_intelligence_enabled', 'true')"
    )
    async with db.transaction(cancellation_linearizable=True):
        diff_id = await db.execute_and_get_id(
            "INSERT INTO chapter_diff_history "
            "(chapter_id, before_text, after_text, source, accepted_segments) "
            "VALUES ('chapter', '旧句', '新句写下月门规则', 'ai_rewrite', 1)"
        )
        keys = await record_chapter_diff_candidate(
            db,
            chapter_id="chapter",
            diff_id=diff_id,
            before_text="旧句",
            after_text="新句写下月门规则",
            source="ai_rewrite",
            accepted_segments=1,
        )

    assert len(keys) == 1
    row = await db.fetch_one(
        "SELECT action, payload_json FROM memory_source_deliveries "
        "WHERE operation_key = ?",
        [keys[0]],
    )
    payload = json.loads(row["payload_json"])
    assert row["action"] == "extract"
    assert payload["state"] == "active"
    assert payload["metadata"]["kind"] == "plot"
    assert payload["metadata"]["scopeType"] == "chapter"


@pytest.mark.asyncio
async def test_disabled_inference_revokes_existing_source(db):
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) "
        "VALUES ('memory_intelligence_enabled', 'true')"
    )
    async with db.transaction(cancellation_linearizable=True):
        first = await record_inline_article_candidate(
            db,
            chapter_id="chapter",
            content="月门只在雨夜开启",
            source="inline_edit",
        )
    assert first

    await db.execute(
        "UPDATE settings SET value = 'false' "
        "WHERE key = 'memory_intelligence_enabled'"
    )
    async with db.transaction(cancellation_linearizable=True):
        deleted = await record_inline_article_candidate(
            db,
            chapter_id="chapter",
            content="用户普通保存",
            source="manual",
        )

    assert deleted
    head = await db.fetch_one(
        "SELECT deleted FROM memory_source_heads "
        "WHERE book_id = 'book' AND source_id = 'article:chapter#chunk:0001'"
    )
    assert head == {"deleted": 1}


@pytest.mark.asyncio
async def test_long_source_is_chunked_without_silent_prefix_truncation(db):
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) "
        "VALUES ('memory_intelligence_enabled', 'true')"
    )
    text = "甲" * (SOURCE_CHUNK_CHARS + 137)
    async with db.transaction(cancellation_linearizable=True):
        keys = await record_inline_article_candidate(
            db,
            chapter_id="chapter",
            content=text,
            source="inline_edit",
        )
    assert len(keys) == 2
    rows = await db.fetch_all(
        "SELECT payload_json FROM memory_source_deliveries "
        "WHERE operation_key IN (?, ?) ORDER BY operation_key",
        list(keys),
    )
    payloads = [json.loads(row["payload_json"]) for row in rows]
    assert sum(len(payload["text"]) for payload in payloads) == len(text)
    assert {payload["metadata"]["sourceLength"] for payload in payloads} == {
        len(text)
    }


@pytest.mark.asyncio
async def test_explicit_conversation_memory_is_source_idempotent_and_deletable(db):
    assert extract_explicit_memory("请记住：主角不能使用火系法术") == (
        "主角不能使用火系法术"
    )
    async with db.transaction(cancellation_linearizable=True):
        first = await record_explicit_conversation_memory(
            db,
            book_id="book",
            conversation_id=7,
            prompt="请记住：主角不能使用火系法术",
        )
    async with db.transaction(cancellation_linearizable=True):
        replay = await record_explicit_conversation_memory(
            db,
            book_id="book",
            conversation_id=7,
            prompt="请记住：主角不能使用火系法术",
        )
    assert first and replay == ()

    async with db.transaction(cancellation_linearizable=True):
        deleted = await record_deleted_conversation_sources(
            db,
            [7],
        )
    assert deleted
    assert await db.fetch_one(
        "SELECT deleted FROM memory_source_heads "
        "WHERE book_id = 'book' AND source_id = 'conversation:7#chunk:0001'"
    ) == {"deleted": 1}


@pytest.mark.asyncio
async def test_bulk_chapter_replace_revokes_only_removed_chapter_sources(
    db,
    monkeypatch,
):
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title) "
        "VALUES ('removed', 'outline', 'Removed')"
    )
    await db.execute(
        "INSERT INTO memory_source_heads "
        "(book_id, source_id, revision, deleted) VALUES "
        "('book', 'article:chapter#chunk:0001', 1, 0), "
        "('book', 'article:removed#chunk:0001', 1, 0)"
    )

    async def skip_component_delivery(_db, _keys):
        return ()

    monkeypatch.setattr(
        memory_deposition_service,
        "deliver_recorded",
        skip_component_delivery,
    )
    result = await save_chapters(
        "outline",
        SaveChaptersRequest(chapters=[
            ChapterItem(id="chapter", title="Chapter"),
        ]),
    )

    assert result == {"success": True, "memoryDelivery": []}
    rows = await db.fetch_all(
        "SELECT source_id, revision, deleted FROM memory_source_heads "
        "ORDER BY source_id"
    )
    assert rows == [
        {
            "source_id": "article:chapter#chunk:0001",
            "revision": 1,
            "deleted": 0,
        },
        {
            "source_id": "article:removed#chunk:0001",
            "revision": 2,
            "deleted": 1,
        },
    ]
