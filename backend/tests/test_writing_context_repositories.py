from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from domains.writing.associated_context import AssociatedContextBuilder
from domains.writing.contracts import WritingDomainContext
from infrastructure.persistence.writing import (
    SqliteAssociatedContextRepository,
    SqliteWritingSourceRepository,
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
async def test_writing_sources_share_one_book_scoped_repository(temp_db):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES ('book-1', '甲'), ('book-2', '乙')"
    )
    repository = SqliteWritingSourceRepository(temp_db)
    spark = await repository.add_spark_idea("book-1", "全局", "本书设定")
    clue = await repository.add_foreshadowing(
        "book-1", "chapter-1", "本书伏笔"
    )

    assert await repository.update_spark_idea(
        "book-2", str(spark["id"]), {"content": "越权修改"}
    ) is None
    assert await repository.delete_foreshadowing(
        "book-2", str(clue["id"])
    ) is None
    assert [row["content"] for row in await repository.list_spark_ideas(
        "book-1"
    )] == ["本书设定"]
    assert [row["content"] for row in await repository.list_foreshadowing(
        "book-1"
    )] == ["本书伏笔"]


def test_new_context_path_has_no_process_global_dependencies():
    root = Path(__file__).resolve().parent.parent
    files = [
        root / "application" / "writing_context_source.py",
        root / "domains" / "writing" / "memory_context.py",
        root / "infrastructure" / "persistence" / "writing" / "sqlite_context_repository.py",
        root / "application" / "component_memory_context.py",
        root / "application" / "memory_operations.py",
    ]
    forbidden = (
        "services.long_term_memory_service",
        "dependencies import get_db",
    )
    for path in files:
        source = path.read_text(encoding="utf-8")
        assert not any(value in source for value in forbidden), path
