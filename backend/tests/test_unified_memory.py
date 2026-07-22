from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from application.story_memory_evolution import StoryMemoryEvolutionService
from application.unified_memory import UnifiedMemoryQueryService
from database.connection import DatabaseConnection
from dependencies import clear_db, set_db
from domains.writing.story_memory import SourceReference, StoryMemoryStatus
from domains.writing.story_memory_ledger import StoryMemoryLedger
from domains.writing.story_settings import (
    CharacterState,
    StorySettingChange,
    WorldFact,
)
from infrastructure.persistence.writing.sqlite_story_memory_repository import (
    SqliteStoryMemoryRepository,
)
from routers.memories import list_unified_memories


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    set_db(connection)
    try:
        yield connection
    finally:
        clear_db(connection)
        await connection.close()


async def _seed_book(db: DatabaseConnection) -> None:
    await db.execute("INSERT INTO books (id, title) VALUES (?, ?)", ["book-1", "Book"])
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) VALUES (?, ?, ?, ?)",
        ["outline-1", "写作目录", "writing", "book-1"],
    )
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title) VALUES (?, ?, ?)",
        ["chapter-1", "outline-1", "第一章"],
    )
    await db.execute(
        "INSERT INTO characters (id, book_id, name, tags) VALUES (?, ?, ?, ?)",
        [7, "book-1", "林墨", "主角"],
    )


def _change(setting, *, status=StoryMemoryStatus.CONFIRMED):
    return StorySettingChange(
        setting=setting,
        source=SourceReference(
            chapter_id="chapter-1",
            excerpt="林墨抵达旧城区。",
            source_revision="revision-1",
        ),
        confidence=0.98,
        status=status,
    )


@pytest.mark.asyncio
async def test_unified_memory_page_combines_sources_and_suppresses_story_duplicate(db):
    await _seed_book(db)
    await db.execute(
        "INSERT INTO memory_items "
        "(book_id, kind, content, fingerprint, status, source_type) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["book-1", "style", "保持克制的叙事语气", "style-1", "active", "manual"],
    )
    await db.execute(
        "INSERT INTO memory_items "
        "(book_id, kind, content, fingerprint, status, source_type) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["book-1", "character", "林墨的location：旧城区", "duplicate-1", "active", "manual"],
    )
    ledger = StoryMemoryLedger(SqliteStoryMemoryRepository(db))
    current = await ledger.stage_settings(
        book_id="book-1",
        chapter_id="chapter-1",
        source_revision="revision-1",
        changes=(_change(CharacterState("7", "location", "旧城区")),),
    )
    await SqliteStoryMemoryRepository(db).apply_delta(current.id)
    candidate = await ledger.stage_settings(
        book_id="book-1",
        chapter_id="chapter-1",
        source_revision="revision-1",
        changes=(
            _change(
                WorldFact("moon-law", "月光会令魔法失效"),
                status=StoryMemoryStatus.INFERRED,
            ),
        ),
    )
    await StoryMemoryEvolutionService(db).review_delta(candidate.id)

    page = await UnifiedMemoryQueryService(db).list_items("book-1")

    assert page.suppressed_duplicates == 1
    assert page.total == 3
    assert {item.source.value for item in page.items} == {
        "semantic",
        "story_state",
        "story_candidate",
    }
    story = next(item for item in page.items if item.source.value == "story_state")
    assert story.content == "林墨的location：旧城区"
    assert story.chapter_title == "第一章"
    assert story.version == 1
    pending = next(
        item for item in page.items if item.source.value == "story_candidate"
    )
    assert pending.status.value == "pending"
    assert pending.actions == ("accept", "reject")


@pytest.mark.asyncio
async def test_unified_memory_api_filters_status_kind_and_query(db):
    await _seed_book(db)
    await db.execute(
        "INSERT INTO memory_items "
        "(book_id, kind, content, fingerprint, status, source_type) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["book-1", "style", "保持克制的叙事语气", "style-1", "active", "manual"],
    )

    response = await list_unified_memories(
        "book-1",
        q="克制",
        status=[],
        kind=["style"],
        source=[],
        limit=20,
    )

    assert response["success"] is True
    assert response["data"]["total"] == 1
    assert response["data"]["items"][0]["source"] == "semantic"


@pytest.mark.asyncio
async def test_unified_review_filters_and_limit_preserve_atomic_delta(db):
    await _seed_book(db)
    ledger = StoryMemoryLedger(SqliteStoryMemoryRepository(db))
    candidate = await ledger.stage_settings(
        book_id="book-1",
        chapter_id="chapter-1",
        source_revision="revision-2",
        changes=(
            _change(WorldFact("moon-law", "月光会令魔法失效")),
            _change(CharacterState("7", "mood", "戒备")),
        ),
    )
    await StoryMemoryEvolutionService(db).review_delta(candidate.id)

    page = await UnifiedMemoryQueryService(db).list_items(
        "book-1",
        kinds=("world_fact",),
        sources=("story_candidate",),
        limit=1,
    )

    assert len(page.items) == 2
    assert {item.delta_id for item in page.items} == {candidate.id}
    assert {item.kind for item in page.items} == {
        "world_fact",
        "character_state",
    }
