from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from database.crud.outlines import (
    get_associable_outlines,
    get_volume_outlines,
    save_outline,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


async def _seed_volume_outlines(db: DatabaseConnection) -> tuple[str, list[str]]:
    book_id = "book1"
    await db.execute(
        "INSERT INTO books (id, title, enable_volume) VALUES (?, ?, ?)",
        [book_id, "Test Book", 1],
    )
    volume_ids = []
    for volume_title in ("Volume One", "Volume Two", "Volume Three"):
        volume = await save_outline(db, {
            "title": volume_title,
            "type": "volume",
            "book_id": book_id,
        })
        volume_ids.append(str(volume["id"]))
        await save_outline(db, {
            "title": f"{volume_title} Chapter",
            "type": "chapter",
            "book_id": book_id,
            "parent_outline_id": volume["id"],
        })
    return book_id, volume_ids


async def test_get_volume_outlines_batch_loads_all_child_chapters(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    book_id, volume_ids = await _seed_volume_outlines(temp_db)
    original_fetch_all = temp_db.fetch_all
    queries = []

    async def tracking_fetch_all(sql, params=()):
        queries.append(sql)
        return await original_fetch_all(sql, params)

    monkeypatch.setattr(temp_db, "fetch_all", tracking_fetch_all)
    groups = await get_volume_outlines(temp_db, book_id)

    assert [str(group["id"]) for group in groups] == volume_ids
    assert [len(group["chapters"]) for group in groups] == [1, 1, 1]
    assert len(queries) == 2
    assert "parent_outline_id IN" in queries[1]


async def test_get_associable_outlines_flattens_batched_volume_groups(
    temp_db: DatabaseConnection,
):
    book_id, volume_ids = await _seed_volume_outlines(temp_db)

    rows = await get_associable_outlines(temp_db, book_id)

    assert [str(row["id"]) for row in rows[::2]] == volume_ids
    assert [row["type"] for row in rows] == [
        "volume", "chapter", "volume", "chapter", "volume", "chapter",
    ]
    assert all("chapters" not in row for row in rows)
