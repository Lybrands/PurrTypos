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


async def test_retired_import_payload_migration_preserves_text_and_history(
    temp_db: DatabaseConnection,
):
    from database.schema import init_schema
    from database.crud.outlines import update_outline, restore_outline_from_history
    from database.crud.outline_history import list_outline_history

    outline = await save_outline(temp_db, {
        "title": "Text outline", "markdown_content": "Original text",
    })
    oid = outline["id"]
    await update_outline(temp_db, {"outlineId": oid, "markdown_content": "Updated text"})
    history = await list_outline_history(temp_db, oid)
    # Reproduce the retired schema and payloads in an isolated database.
    await temp_db.execute("ALTER TABLE outlines ADD COLUMN xmind_data TEXT")
    await temp_db.execute("ALTER TABLE outlines ADD COLUMN file_path TEXT")
    await temp_db.execute("ALTER TABLE outline_history ADD COLUMN before_xmind_data TEXT")
    await temp_db.execute(
        "UPDATE outlines SET xmind_data = ?, file_path = ? WHERE id = ?",
        ["retired payload", "/old/outline.xmind", oid],
    )
    await temp_db.execute("UPDATE outline_history SET before_xmind_data = 'retired snapshot'")

    await init_schema(temp_db)
    await init_schema(temp_db)  # Safe on subsequent startups.

    row = await temp_db.fetch_one("SELECT * FROM outlines WHERE id = ?", [oid])
    assert row["markdown_content"] == "Updated text"
    assert "xmind_data" not in row and "file_path" not in row
    columns = await temp_db.fetch_all("PRAGMA table_info(outline_history)")
    assert "before_xmind_data" not in {column["name"] for column in columns}
    assert await list_outline_history(temp_db, oid) == history
    restored = await restore_outline_from_history(temp_db, history[0]["id"])
    assert restored["markdown_content"] == "Original text"
    assert len(await list_outline_history(temp_db, oid)) == 2
    assert (await temp_db.fetch_one("PRAGMA integrity_check"))["integrity_check"] == "ok"
