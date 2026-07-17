from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from infrastructure.writing.tools.handlers import (
    chapter_tools,
    dashboard_tools,
    outline_tools,
)
from infrastructure.writing.tools.runtime import WritingToolDependencies


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


def _dependencies(db: DatabaseConnection) -> WritingToolDependencies:
    return WritingToolDependencies(db, object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_read_tools_do_not_initialize_missing_outlines(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-a", "Book A"],
    )
    dependencies = _dependencies(temp_db)

    global_result = await outline_tools._tool_get_global_outline(
        dependencies,
        {"bookId": "book-a"},
        {},
        None,
    )
    chapters_result = await chapter_tools._tool_list_writing_chapters(
        dependencies,
        {"bookId": "book-a"},
        {},
        None,
    )

    assert json.loads(global_result.content)["success"] is False
    assert json.loads(chapters_result.content)["success"] is False
    assert await temp_db.fetch_all(
        "SELECT id, type, book_id FROM outlines ORDER BY id"
    ) == []


@pytest.mark.asyncio
async def test_dashboard_read_tools_make_zero_persistent_writes(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-dashboard", "Dashboard Book"],
    )
    dependencies = _dependencies(temp_db)
    before = await temp_db.fetch_one("SELECT total_changes() AS count")

    health = await dashboard_tools._tool_get_story_health_dashboard(
        dependencies,
        {"bookId": "book-dashboard"},
        {},
        None,
    )
    stats = await dashboard_tools._tool_get_writing_stats_dashboard(
        dependencies,
        {"bookId": "book-dashboard"},
        {},
        None,
    )

    after = await temp_db.fetch_one("SELECT total_changes() AS count")
    assert json.loads(health.content)["totalChapters"] == 0
    assert json.loads(stats.content)["totalChapters"] == 0
    assert after == before
    assert await temp_db.fetch_all(
        "SELECT id, type, book_id FROM outlines ORDER BY id"
    ) == []


@pytest.mark.asyncio
async def test_confirmed_global_edit_creates_scoped_row_without_mutating_legacy(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-a", "Book A"],
    )
    await temp_db.execute(
        "INSERT INTO outlines "
        "(id, title, type, markdown_content, book_id) "
        "VALUES (?, ?, 'global', ?, NULL)",
        ["legacy-global", "Legacy", "shared legacy text"],
    )
    dependencies = _dependencies(temp_db)

    result = await outline_tools._tool_edit_global_outline(
        dependencies,
        {"bookId": "book-a"},
        {"markdownContent": "book A text"},
        None,
    )

    payload = json.loads(result.content)
    assert payload["success"] is True
    assert payload["outlineId"] != "legacy-global"
    assert await temp_db.fetch_one(
        "SELECT markdown_content FROM outlines WHERE id = ?",
        ["legacy-global"],
    ) == {"markdown_content": "shared legacy text"}
    assert await temp_db.fetch_one(
        "SELECT markdown_content, book_id FROM outlines WHERE id = ?",
        [payload["outlineId"]],
    ) == {"markdown_content": "book A text", "book_id": "book-a"}


@pytest.mark.asyncio
async def test_update_outline_rejects_unscoped_legacy_global(
    temp_db: DatabaseConnection,
):
    await temp_db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-a", "Book A"],
    )
    await temp_db.execute(
        "INSERT INTO outlines "
        "(id, title, type, markdown_content, book_id) "
        "VALUES (?, ?, 'global', ?, NULL)",
        ["legacy-global", "Legacy", "shared legacy text"],
    )

    result = await outline_tools._tool_update_outline(
        _dependencies(temp_db),
        {"bookId": "book-a"},
        {"outlineId": "legacy-global", "markdown_content": "overwritten"},
        None,
    )

    payload = json.loads(result.content)
    assert payload["success"] is False
    assert "不属于当前书籍" in payload["error"]
    assert await temp_db.fetch_one(
        "SELECT markdown_content, book_id FROM outlines WHERE id = ?",
        ["legacy-global"],
    ) == {"markdown_content": "shared legacy text", "book_id": None}
