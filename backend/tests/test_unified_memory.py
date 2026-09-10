from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from application.story_memory_evolution import StoryMemoryEvolutionService
from application.memory_operations import MemoryApplicationService, memory_metadata
from application.unified_memory import UnifiedMemoryQueryService
from database.connection import DatabaseConnection
from dependencies import clear_db, set_db
from domains.writing.unified_memory import UnifiedMemorySource
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
from infrastructure.memory import MemoryComponentResource, MemoryResourceConfiguration
from purra_mem0 import EmbeddingResult
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


class _EmbeddingGateway:
    async def embed(self, texts, signal):
        return EmbeddingResult(
            tuple((1.0, 0.0, 0.0, 0.0) for _ in texts),
            input_tokens=sum(len(text) for text in texts),
        )

    async def close(self):
        return None


@pytest_asyncio.fixture
async def memory(db: DatabaseConnection, tmp_path: Path):
    resource = MemoryComponentResource(
        MemoryResourceConfiguration(tmp_path / "component", 4),
        embedding_gateway=_EmbeddingGateway(),
    )
    try:
        yield MemoryApplicationService(db, resource)
    finally:
        await resource.close()


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
async def test_unified_memory_page_combines_sources_and_suppresses_story_duplicate(
    db,
    memory,
):
    await _seed_book(db)
    await memory.create_manual(
        book_id="book-1",
        operation_key="style-1",
        text="保持克制的叙事语气",
        metadata=memory_metadata(kind="style"),
    )
    await memory.create_manual(
        book_id="book-1",
        operation_key="duplicate-1",
        text="林墨的location：旧城区",
        metadata=memory_metadata(kind="character"),
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

    page = await UnifiedMemoryQueryService(db, memory).list_items("book-1")

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
async def test_unified_memory_api_filters_status_kind_and_query(
    db,
    memory,
    monkeypatch,
):
    await _seed_book(db)
    await memory.create_manual(
        book_id="book-1",
        operation_key="style-1",
        text="保持克制的叙事语气",
        metadata=memory_metadata(kind="style"),
    )
    monkeypatch.setattr("routers.memories._memory_operations", lambda: memory)

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
async def test_unified_review_filters_and_limit_preserve_atomic_delta(db, memory):
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

    page = await UnifiedMemoryQueryService(db, memory).list_items(
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


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, True])
async def test_optional_semantic_component_does_not_hide_story_sources(db, monkeypatch, configured):
    await _seed_book(db)
    if configured:
        await db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                         ["memory_embedding_config", '{"model":"configured-placeholder"}'])
    ledger = StoryMemoryLedger(SqliteStoryMemoryRepository(db))
    current = await ledger.stage_settings(
        book_id="book-1", chapter_id="chapter-1", source_revision="revision-1",
        changes=(_change(CharacterState("7", "location", "旧城区")),),
    )
    await SqliteStoryMemoryRepository(db).apply_delta(current.id)
    candidate = await ledger.stage_settings(
        book_id="book-1", chapter_id="chapter-1", source_revision="revision-1",
        changes=(_change(WorldFact("moon-law", "月光会令魔法失效"), status=StoryMemoryStatus.INFERRED),),
    )
    await StoryMemoryEvolutionService(db).review_delta(candidate.id)
    monkeypatch.setattr("routers.memories._memory_operations", lambda: MemoryApplicationService(db, None))
    response = await list_unified_memories("book-1", q="", status=[], kind=[], source=[], limit=200)
    assert response["success"] is True
    assert {item["source"] for item in response["data"]["items"]} == {"story_state", "story_candidate"}
    assert response["data"]["unavailableSources"] == {
        "semantic": "memory_component_unavailable" if configured else "memory_embedding_unconfigured",
    }
    semantic = await list_unified_memories("book-1", q="", status=[], kind=[], source=[UnifiedMemorySource.SEMANTIC], limit=200)
    assert semantic["success"] is True
    assert semantic["data"]["items"] == []
    assert semantic["data"]["unavailableSources"]


@pytest.mark.asyncio
async def test_story_only_filter_does_not_read_semantic_component(db):
    await _seed_book(db)
    class UnexpectedSemantic:
        async def list_records(self, **kwargs):
            raise AssertionError("Semantic source was not requested")
    page = await UnifiedMemoryQueryService(db, UnexpectedSemantic()).list_items(
        "book-1", sources=("story_state",),
    )
    assert not page.unavailable_sources


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["memory_access_denied", "memory_book_not_found"])
async def test_unified_page_preserves_authority_failures(db, code):
    from application.memory_operations import MemoryOperationError
    await _seed_book(db)
    class DeniedSemantic:
        async def list_records(self, **kwargs):
            raise MemoryOperationError(code)
    with pytest.raises(MemoryOperationError, match=code):
        await UnifiedMemoryQueryService(db, DeniedSemantic()).list_items("book-1")


@pytest.mark.asyncio
async def test_story_only_filter_still_requires_existing_book(db):
    from application.memory_operations import MemoryOperationError
    with pytest.raises(MemoryOperationError, match="memory_book_not_found"):
        await UnifiedMemoryQueryService(db, MemoryApplicationService(db, None)).list_items(
            "missing-book", sources=("story_state",),
        )
