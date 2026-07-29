from __future__ import annotations

from pathlib import Path

import pytest

from database.connection import DatabaseConnection


@pytest.mark.asyncio
async def test_import_from_buffer_replaces_live_database_and_keeps_connection_usable(
    tmp_path: Path,
):
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source = DatabaseConnection(source_dir)
    target = DatabaseConnection(target_dir)
    await source.init()
    await target.init()
    try:
        await source.execute(
            "INSERT INTO books (id, title) VALUES (?, ?)",
            ["source01", "导入后的书"],
        )
        payload = await source.export_to_buffer()
        assert payload

        await target.execute(
            "INSERT INTO books (id, title) VALUES (?, ?)",
            ["target01", "导入前的书"],
        )
        await target.import_from_buffer(payload)

        books = await target.fetch_all("SELECT id, title FROM books ORDER BY id")
        assert books == [{"id": "source01", "title": "导入后的书"}]
        assert await target.is_healthy() is True
        assert list(target_dir.glob("purrtypos.db.before-import-*.bak"))
    finally:
        await source.close()
        await target.close()


@pytest.mark.asyncio
async def test_import_from_buffer_rejects_non_purrtypos_database(tmp_path: Path):
    target = DatabaseConnection(tmp_path / "target")
    await target.init()
    try:
        with pytest.raises(ValueError, match="有效的 PurrTypos"):
            await target.import_from_buffer(b"not a sqlite database")
        assert await target.is_healthy() is True
    finally:
        await target.close()
