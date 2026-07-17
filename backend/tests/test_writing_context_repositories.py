from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from domains.writing.associated_context import AssociatedContextBuilder
from domains.writing.contracts import WritingDomainContext
from domains.writing.memory_context import (
    MemoryContextRequest,
    WritingMemoryContextBuilder,
)
from infrastructure.persistence.writing import (
    SqliteAssociatedContextRepository,
    SqliteMemoryRecallRepository,
)


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


def _lexical(text: str) -> str:
    return json.dumps({
        "root": {
            "children": [{
                "type": "paragraph",
                "children": [{"type": "text", "text": text}],
            }],
        },
    })


async def _seed_book_chapter(
    db: DatabaseConnection,
    *,
    book_id: str,
    outline_id: str,
    chapter_id: str,
    text: str,
) -> None:
    await db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        [book_id, book_id],
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES (?, ?, 'writing', ?)",
        [outline_id, outline_id, book_id],
    )
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title) VALUES (?, ?, ?)",
        [chapter_id, outline_id, chapter_id],
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        [chapter_id, _lexical(text)],
    )


async def _seed_memory(
    db: DatabaseConnection,
    *,
    book_id: str,
    content: str,
    source_id: str,
) -> int:
    memory_id = await db.execute_and_get_id(
        "INSERT INTO memory_items "
        "(book_id, kind, content, source_type, source_id, fingerprint) "
        "VALUES (?, 'canon', ?, 'spark_idea', ?, ?)",
        [book_id, content, source_id, f"{book_id}:{source_id}"],
    )
    assert memory_id is not None
    return memory_id


@pytest.mark.asyncio
async def test_associated_repository_enforces_book_scope_and_decodes_lexical(temp_db):
    await _seed_book_chapter(
        temp_db,
        book_id="book-1",
        outline_id="writing-1",
        chapter_id="chapter-1",
        text="本书正文",
    )
    await _seed_book_chapter(
        temp_db,
        book_id="book-2",
        outline_id="writing-2",
        chapter_id="chapter-2",
        text="另一书秘密正文",
    )
    repository = SqliteAssociatedContextRepository(temp_db)

    rows = await repository.load_chapters(
        "book-1",
        ["chapter-1", "chapter-2"],
    )

    assert [(row.id, row.text) for row in rows] == [("chapter-1", "本书正文")]


@pytest.mark.asyncio
async def test_associated_context_never_injects_cross_book_chapter_or_outline(temp_db):
    await _seed_book_chapter(
        temp_db,
        book_id="book-1",
        outline_id="writing-1",
        chapter_id="chapter-1",
        text="本书正文",
    )
    await _seed_book_chapter(
        temp_db,
        book_id="book-2",
        outline_id="writing-2",
        chapter_id="chapter-2",
        text="另一书秘密正文",
    )
    await temp_db.execute(
        "INSERT INTO outlines (id, title, type, book_id, markdown_content) "
        "VALUES ('outline-2', '另一书大纲', 'chapter', 'book-2', '秘密大纲')",
    )
    builder = AssociatedContextBuilder(SqliteAssociatedContextRepository(temp_db))
    context = WritingDomainContext(
        book_id="book-1",
        associated_chapter_ids=("chapter-1", "chapter-2"),
        associated_outline_ids=("outline-2",),
        writing_chapters=(
            {"id": "chapter-1", "title": "本书章"},
            {"id": "chapter-2", "title": "伪造关联章"},
        ),
        available_outlines=({"id": "outline-2", "title": "伪造关联大纲"},),
    )

    result = await builder.build(context, 20_000)

    assert "本书正文" in result.text
    assert "另一书秘密正文" not in result.text
    assert "秘密大纲" not in result.text
    assert [
        (fact.outline_id, fact.status, fact.read_tool)
        for fact in result.outline_facts
    ] == [("outline-2", "not_injected", "queryOutline")]
    assert {
        fact.chapter_id: fact.status for fact in result.chapter_facts
    } == {
        "chapter-1": "complete",
        "chapter-2": "not_injected",
    }


@pytest.mark.asyncio
async def test_associated_context_marks_a_fully_injected_outline_complete(temp_db):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES ('book-1', '测试书')",
    )
    await temp_db.execute(
        "INSERT INTO outlines (id, title, type, book_id, markdown_content) "
        "VALUES ('outline-1', '关联大纲', 'chapter', 'book-1', '完整的大纲正文')",
    )
    builder = AssociatedContextBuilder(SqliteAssociatedContextRepository(temp_db))

    result = await builder.build(
        WritingDomainContext(
            book_id="book-1",
            associated_outline_ids=("outline-1",),
        ),
        20_000,
    )

    assert "完整的大纲正文" in result.text
    assert [
        (fact.outline_id, fact.status, fact.read_tool)
        for fact in result.outline_facts
    ] == [("outline-1", "complete", "queryOutline")]


@pytest.mark.asyncio
async def test_associated_context_never_marks_a_per_item_truncation_complete(temp_db):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES ('book-1', '测试书')",
    )
    await temp_db.execute(
        "INSERT INTO outlines (id, title, type, book_id, markdown_content) "
        "VALUES (?, '长大纲', 'chapter', 'book-1', ?)",
        ["outline-long", "长" * 6_000],
    )
    builder = AssociatedContextBuilder(SqliteAssociatedContextRepository(temp_db))

    result = await builder.build(
        WritingDomainContext(
            book_id="book-1",
            associated_outline_ids=("outline-long",),
            context_window_label="200k",
        ),
        10_000,
    )

    assert "已截断" in result.text
    assert [fact.status for fact in result.outline_facts] == ["truncated"]


@pytest.mark.asyncio
async def test_associated_chapter_fact_tracks_per_item_truncation(temp_db):
    await _seed_book_chapter(
        temp_db,
        book_id="book-1",
        outline_id="writing-1",
        chapter_id="chapter-long",
        text="长" * 9_000,
    )
    builder = AssociatedContextBuilder(SqliteAssociatedContextRepository(temp_db))

    result = await builder.build(
        WritingDomainContext(
            book_id="book-1",
            associated_chapter_ids=("chapter-long",),
            context_window_label="200k",
        ),
        10_000,
    )

    assert [fact.status for fact in result.chapter_facts] == ["truncated"]
    assert result.chapter_facts[0].locator_available_to_execution is True


@pytest.mark.asyncio
async def test_associated_context_distinguishes_empty_from_missing_outlines(temp_db):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES ('book-1', '测试书')",
    )
    await temp_db.execute(
        "INSERT INTO outlines (id, title, type, book_id, markdown_content) "
        "VALUES ('outline-empty', '空大纲', 'chapter', 'book-1', '')",
    )
    builder = AssociatedContextBuilder(SqliteAssociatedContextRepository(temp_db))

    result = await builder.build(
        WritingDomainContext(
            book_id="book-1",
            associated_outline_ids=("outline-empty", "outline-missing"),
        ),
        20_000,
    )

    assert {
        fact.outline_id: fact.status for fact in result.outline_facts
    } == {
        "outline-empty": "complete",
        "outline-missing": "not_injected",
    }


@pytest.mark.asyncio
async def test_memory_repository_scopes_ids_sources_search_and_mark_used(temp_db):
    own_id = await _seed_memory(
        temp_db,
        book_id="book-1",
        content="本书记忆",
        source_id="source-1",
    )
    other_id = await _seed_memory(
        temp_db,
        book_id="book-2",
        content="另一书秘密记忆",
        source_id="source-2",
    )
    repository = SqliteMemoryRecallRepository(temp_db)

    by_id = await repository.get_by_ids(
        "book-1",
        [own_id, other_id],
        statuses=("active",),
    )
    by_source = await repository.get_by_source_ids(
        "book-1",
        "spark_idea",
        ["source-1", "source-2"],
        statuses=("active",),
    )
    recalled = await repository.search("book-1", "", limit=20)
    await repository.mark_used("book-1", [own_id, other_id])

    assert [item.id for item in by_id] == [own_id]
    assert [item.id for item in by_source] == [own_id]
    assert [item.id for item in recalled] == [own_id]
    own_row = await temp_db.fetch_one(
        "SELECT last_used_at FROM memory_items WHERE id = ?",
        [own_id],
    )
    other_row = await temp_db.fetch_one(
        "SELECT last_used_at FROM memory_items WHERE id = ?",
        [other_id],
    )
    assert own_row and own_row["last_used_at"] is not None
    assert other_row and other_row["last_used_at"] is None


@pytest.mark.asyncio
async def test_memory_context_legacy_source_ids_cannot_cross_book(temp_db):
    own_id = await _seed_memory(
        temp_db,
        book_id="book-1",
        content="本书规则",
        source_id="source-1",
    )
    await _seed_memory(
        temp_db,
        book_id="book-2",
        content="另一书秘密规则",
        source_id="source-2",
    )
    builder = WritingMemoryContextBuilder(SqliteMemoryRecallRepository(temp_db))

    block = await builder.build(MemoryContextRequest(
        book_id="book-1",
        user_prompt="",
        selected_spark_idea_ids=("source-1", "source-2"),
    ))

    assert "本书规则" in block.text
    assert "另一书秘密规则" not in block.text
    assert own_id in block.included_ids
    assert block.selected_fact is not None
    assert block.selected_fact.requested_count == 2
    assert block.selected_fact.complete_count == 1
    assert block.selected_fact.not_injected_count == 1
    assert block.selected_fact.status == "truncated"


def test_new_context_path_has_no_process_global_dependencies():
    root = Path(__file__).resolve().parent.parent
    files = [
        root / "domains" / "writing" / "context_source.py",
        root / "domains" / "writing" / "memory_context.py",
        root / "infrastructure" / "persistence" / "writing" / "sqlite_context_repository.py",
        root / "infrastructure" / "persistence" / "writing" / "sqlite_memory_repository.py",
    ]
    forbidden = (
        "services.long_term_memory_service",
        "dependencies import get_db",
    )
    for path in files:
        source = path.read_text(encoding="utf-8")
        assert not any(value in source for value in forbidden), path
