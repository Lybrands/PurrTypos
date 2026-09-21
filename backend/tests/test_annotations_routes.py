from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db
from routers.annotations import (
    AddAnnotationRequest,
    UpdateAnnotationRequest,
    create_annotation,
    get_annotations_by_book,
    modify_annotation,
    remove_annotation,
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


async def _add(book_id: str = "book1", chapter_id: str = "chapter1"):
    return await create_annotation(
        AddAnnotationRequest(
            bookId=book_id,
            chapterId=chapter_id,
            startOffset=10,
            endOffset=24,
            quotedText="被选中的句子",
            contextBefore="前文",
            contextAfter="后文",
            note="这里的伏笔要改",
        )
    )


async def test_create_and_list_roundtrip(temp_db: DatabaseConnection):
    created = await _add()
    assert created["success"] is True
    row = created["data"]
    assert row["status"] == "open"
    assert row["source"] == "manual"
    assert row["quoted_text"] == "被选中的句子"

    listed = await get_annotations_by_book(bookId="book1", chapterId=None)
    assert listed["success"] is True
    assert [r["id"] for r in listed["data"]] == [row["id"]]

    filtered = await get_annotations_by_book(bookId="book1", chapterId="other")
    assert filtered["data"] == []


async def test_list_orders_by_offset(temp_db: DatabaseConnection):
    second = await create_annotation(
        AddAnnotationRequest(
            bookId="book1",
            chapterId="chapter1",
            startOffset=2,
            endOffset=5,
            quotedText="b",
            note="早",
        )
    )
    first = await _add()
    listed = await get_annotations_by_book(bookId="book1", chapterId=None)
    assert [r["id"] for r in listed["data"]] == [
        second["data"]["id"],
        first["data"]["id"],
    ]


async def test_update_note_and_status(temp_db: DatabaseConnection):
    created = await _add()
    ann_id = created["data"]["id"]

    updated = await modify_annotation(
        ann_id,
        UpdateAnnotationRequest(bookId="book1", status="resolved"),
    )
    assert updated["success"] is True
    assert updated["data"]["status"] == "resolved"

    missing = await modify_annotation(
        9999,
        UpdateAnnotationRequest(bookId="book1", note="x"),
    )
    assert missing["success"] is False
    assert missing["error"] == "annotation_not_found"


async def test_update_requires_change(temp_db: DatabaseConnection):
    await _add()
    with pytest.raises(ValueError):
        UpdateAnnotationRequest(bookId="book1")


async def test_delete(temp_db: DatabaseConnection):
    created = await _add()
    ann_id = created["data"]["id"]

    removed = await remove_annotation(ann_id, bookId="book1")
    assert removed["success"] is True

    again = await remove_annotation(ann_id, bookId="book1")
    assert again["success"] is False
    assert again["error"] == "annotation_not_found"

    listed = await get_annotations_by_book(bookId="book1", chapterId=None)
    assert listed["data"] == []


async def test_wrong_book_is_isolated(temp_db: DatabaseConnection):
    await _add(book_id="book1")
    missing = await remove_annotation(1, bookId="book2")
    assert missing["success"] is False
