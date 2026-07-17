from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from infrastructure.persistence.writing.sqlite_writing_tool_memory_repository import (
    SqliteWritingToolMemoryRepository,
)


@pytest_asyncio.fixture
async def memory_repository(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db, SqliteWritingToolMemoryRepository(db)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_spark_mutations_are_book_scoped_and_keep_the_mirror_in_sync(
    memory_repository,
):
    db, repository = memory_repository
    spark = await repository.add_spark_idea(
        "book-a",
        "全局",
        "龙族惧怕盐",
    )

    mirror = await db.fetch_one(
        "SELECT * FROM memory_items WHERE book_id = ? "
        "AND source_type = 'spark_idea' AND source_id = ?",
        ["book-a", str(spark["id"])],
    )
    assert mirror is not None
    assert mirror["content"] == "龙族惧怕盐"

    assert await repository.update_spark_idea(
        "book-b",
        str(spark["id"]),
        {"content": "越权修改"},
    ) is None
    updated = await repository.update_spark_idea(
        "book-a",
        str(spark["id"]),
        {"content": "龙族惧怕海盐", "chapter_id": "chapter-1"},
    )
    assert updated is not None
    assert updated["content"] == "龙族惧怕海盐"

    mirror = await db.fetch_one(
        "SELECT * FROM memory_items WHERE book_id = ? "
        "AND source_type = 'spark_idea' AND source_id = ?",
        ["book-a", str(spark["id"])],
    )
    assert mirror is not None
    assert mirror["content"] == "龙族惧怕海盐"
    assert mirror["scope_type"] == "chapter"
    assert mirror["scope_id"] == "chapter-1"

    assert await repository.delete_spark_idea(
        "book-b",
        str(spark["id"]),
    ) is None
    deleted = await repository.delete_spark_idea(
        "book-a",
        str(spark["id"]),
    )
    assert deleted is not None
    assert deleted["content"] == "龙族惧怕海盐"
    mirror = await db.fetch_one(
        "SELECT status FROM memory_items WHERE book_id = ? "
        "AND source_type = 'spark_idea' AND source_id = ?",
        ["book-a", str(spark["id"])],
    )
    assert mirror == {"status": "archived"}


@pytest.mark.asyncio
async def test_foreshadowing_resolution_is_scoped_and_archives_its_mirror(
    memory_repository,
):
    db, repository = memory_repository
    foreshadowing = await repository.add_foreshadowing(
        "book-a",
        "chapter-1",
        "黑猫避开神龛",
        "悬念",
    )

    assert await repository.update_foreshadowing(
        "book-b",
        str(foreshadowing["id"]),
        {"status": "已回收"},
    ) is None
    resolved = await repository.update_foreshadowing(
        "book-a",
        str(foreshadowing["id"]),
        {"status": "已回收", "resolved_chapter_id": "chapter-3"},
    )
    assert resolved is not None
    assert resolved["status"] == "已回收"
    assert resolved["resolved_chapter_id"] == "chapter-3"

    mirror = await db.fetch_one(
        "SELECT status, keywords FROM memory_items WHERE book_id = ? "
        "AND source_type = 'foreshadowing' AND source_id = ?",
        ["book-a", str(foreshadowing["id"])],
    )
    assert mirror == {"status": "archived", "keywords": "悬念 已回收"}


@pytest.mark.asyncio
async def test_long_term_memory_crud_search_and_links_are_book_scoped(
    memory_repository,
):
    _db, repository = memory_repository
    first = await repository.create_memory_item(
        book_id="book-a",
        kind="plot",
        content="玉佩在雨夜发光",
    )
    duplicate = await repository.create_memory_item(
        book_id="book-a",
        kind="plot",
        content="玉佩在雨夜发光",
    )
    other_book = await repository.create_memory_item(
        book_id="book-b",
        kind="plot",
        content="玉佩在雨夜发光",
    )
    second = await repository.create_memory_item(
        book_id="book-a",
        kind="plot",
        content="古门在月食时开启",
    )

    assert duplicate["id"] == first["id"]
    assert duplicate["deduped"] is True
    rows = await repository.search_memory_items("book-a", "玉佩")
    assert [row["id"] for row in rows] == [first["id"]]

    assert await repository.update_memory_item(
        "book-b",
        first["id"],
        {"content": "越权修改"},
    ) is None
    assert await repository.archive_memory_item(
        "book-b",
        first["id"],
    ) is None

    link = await repository.link_memory_items(
        book_id="book-a",
        from_memory_id=first["id"],
        to_memory_id=second["id"],
        relation="supports",
    )
    assert link["book_id"] == "book-a"

    with pytest.raises(ValueError, match="current book"):
        await repository.link_memory_items(
            book_id="book-a",
            from_memory_id=first["id"],
            to_memory_id=other_book["id"],
            relation="relates_to",
        )

    archived = await repository.archive_memory_item("book-a", first["id"])
    assert archived is not None
    assert archived["status"] == "archived"
    assert await repository.search_memory_items("book-a", "玉佩") == []


@pytest.mark.asyncio
async def test_primary_spark_write_survives_best_effort_mirror_failure(
    memory_repository,
    monkeypatch,
):
    db, repository = memory_repository

    async def _fail_mirror(_row):
        raise RuntimeError("mirror unavailable")

    monkeypatch.setattr(repository, "_mirror_spark_idea", _fail_mirror)
    spark = await repository.add_spark_idea("book-a", "全局", "保留主记录")

    row = await db.fetch_one(
        "SELECT content FROM ai_memories WHERE id = ? AND book_id = ?",
        [spark["id"], "book-a"],
    )
    assert row == {"content": "保留主记录"}
    assert await db.fetch_all(
        "SELECT * FROM memory_items WHERE book_id = ?",
        ["book-a"],
    ) == []


@pytest.mark.asyncio
async def test_concurrent_identical_memory_creates_return_one_deduped_row(
    memory_repository,
):
    db, repository = memory_repository

    first, second = await asyncio.gather(
        repository.create_memory_item(
            book_id="book-a",
            kind="plot",
            content="同一条并发记忆",
        ),
        repository.create_memory_item(
            book_id="book-a",
            kind="plot",
            content="同一条并发记忆",
        ),
    )

    assert first["id"] == second["id"]
    assert {first["deduped"], second["deduped"]} == {False, True}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM memory_items WHERE book_id = ?",
        ["book-a"],
    ) == {"count": 1}


@pytest.mark.asyncio
async def test_cross_book_memory_link_error_is_non_disclosing(
    memory_repository,
):
    _db, repository = memory_repository
    first = await repository.create_memory_item(
        book_id="book-a",
        kind="plot",
        content="本书记忆",
    )
    other = await repository.create_memory_item(
        book_id="book-b",
        kind="plot",
        content="他书记忆",
    )

    with pytest.raises(ValueError) as error:
        await repository.link_memory_items(
            book_id="book-a",
            from_memory_id=first["id"],
            to_memory_id=other["id"],
            relation="relates_to",
        )

    assert str(error.value) == "both memory items must exist in the current book"
