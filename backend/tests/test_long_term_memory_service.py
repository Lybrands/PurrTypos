from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db

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


async def test_create_memory_item_dedupes_by_fingerprint(temp_db):
    from services import long_term_memory_service

    first = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="canon",
        content="主角不能使用火系法术",
        scope_type="book",
    )
    second = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="canon",
        content="主角不能使用火系法术",
        scope_type="book",
    )

    assert second["id"] == first["id"]
    assert second["deduped"] is True


async def test_search_memory_items_excludes_pending_archived_and_superseded_by_default(temp_db):
    from services import long_term_memory_service

    active = await long_term_memory_service.create_memory_item(
        book_id="b1", kind="plot", content="玉佩在雨夜发光", status="active"
    )
    await long_term_memory_service.create_memory_item(
        book_id="b1", kind="plot", content="玉佩在雨夜发光", status="pending"
    )
    await long_term_memory_service.create_memory_item(
        book_id="b1", kind="plot", content="玉佩在雨夜发光", status="archived"
    )
    await long_term_memory_service.create_memory_item(
        book_id="b1", kind="plot", content="玉佩在雨夜发光", status="superseded"
    )

    rows = await long_term_memory_service.search_memory_items("b1", "玉佩")

    assert [r["id"] for r in rows] == [active["id"]]


async def test_search_memory_items_can_include_pending_when_requested(temp_db):
    from services import long_term_memory_service

    pending = await long_term_memory_service.create_memory_item(
        book_id="b1", kind="plot", content="黑猫避开神龛", status="pending"
    )

    rows = await long_term_memory_service.search_memory_items(
        "b1",
        "黑猫",
        options={"statuses": ["pending"]},
    )

    assert [r["id"] for r in rows] == [pending["id"]]


async def test_spark_idea_write_mirrors_to_memory_items(temp_db):
    from services import long_term_memory_service, memory_service

    spark = await memory_service.add_spark_idea("b1", "全局", "龙族惧怕盐")
    rows = await long_term_memory_service.search_memory_items("b1", "龙族")

    assert rows[0]["kind"] == "canon"
    assert rows[0]["source_type"] == "spark_idea"
    assert rows[0]["source_id"] == str(spark["id"])


async def test_foreshadowing_write_mirrors_to_memory_items(temp_db):
    from services import long_term_memory_service, memory_service

    fs = await memory_service.add_foreshadowing(
        "b1", "ch1", "黑猫避开神龛", type_="悬念"
    )
    rows = await long_term_memory_service.search_memory_items("b1", "黑猫")

    assert rows[0]["kind"] == "foreshadowing"
    assert rows[0]["source_type"] == "foreshadowing"
    assert rows[0]["source_id"] == str(fs["id"])


async def test_archive_memory_item_excludes_item_from_default_search(temp_db):
    from services import long_term_memory_service

    item = await long_term_memory_service.create_memory_item(
        book_id="b1", kind="world", content="昆仑山门只在月食时开启"
    )

    await long_term_memory_service.archive_memory_item(item["id"])

    assert await long_term_memory_service.search_memory_items("b1", "昆仑") == []


async def test_link_memory_items_requires_existing_items_in_same_book(temp_db):
    from services import long_term_memory_service

    first = await long_term_memory_service.create_memory_item(
        book_id="b1", kind="plot", content="玉佩在雨夜发光"
    )
    other_book = await long_term_memory_service.create_memory_item(
        book_id="b2", kind="plot", content="黑猫避开神龛"
    )

    with pytest.raises(ValueError, match="same book"):
        await long_term_memory_service.link_memory_items(
            book_id="b1",
            from_memory_id=first["id"],
            to_memory_id=other_book["id"],
            relation="relates_to",
        )

    with pytest.raises(ValueError, match="exist"):
        await long_term_memory_service.link_memory_items(
            book_id="b1",
            from_memory_id=first["id"],
            to_memory_id=9999,
            relation="relates_to",
        )


async def test_memory_routes_create_search_archive_and_build_context(temp_db):
    from routers import memories
    from schemas.memories import (
        BuildMemoryContextRequest,
        CreateMemoryRequest,
        SearchMemoriesRequest,
    )

    created = await memories.create_memory(CreateMemoryRequest(
        bookId="b1",
        kind="plot",
        content="玉佩在雨夜发光",
    ))
    assert created["success"] is True
    memory_id = created["data"]["id"]

    searched = await memories.search_memories(SearchMemoriesRequest(
        bookId="b1",
        query="玉佩",
    ))
    assert searched["success"] is True
    assert searched["data"][0]["id"] == memory_id

    context = await memories.build_memory_context(BuildMemoryContextRequest(
        bookId="b1",
        userPrompt="玉佩现在如何？",
        selectedLongTermMemoryIds=[memory_id],
    ))
    assert context["success"] is True
    assert "玉佩在雨夜发光" in context["data"]["text"]

    archived = await memories.archive_memory(memory_id)
    assert archived["success"] is True
    assert archived["data"]["status"] == "archived"
