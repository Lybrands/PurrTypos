"""
Chapter CRUD – port of Database.*Chapter* methods from database.js.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection

from utils.id_utils import short_id8


async def get_chapters(
    db: DatabaseConnection, outline_id: str
) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT * FROM outline_chapters WHERE outline_id = ? "
        "ORDER BY COALESCE(parent_id, id) ASC, sort ASC",
        [outline_id],
    )


async def add_chapter(
    db: DatabaseConnection,
    outline_id: str,
    title: str,
    parent_id: str | None = None,
) -> dict[str, Any]:
    level = 2 if parent_id is not None else 1
    if parent_id is not None:
        max_row = await db.fetch_one(
            "SELECT COALESCE(MAX(sort), 0) as m FROM outline_chapters "
            "WHERE outline_id = ? AND parent_id = ?",
            [outline_id, parent_id],
        )
    else:
        max_row = await db.fetch_one(
            "SELECT COALESCE(MAX(sort), 0) as m FROM outline_chapters "
            "WHERE outline_id = ? AND parent_id IS NULL",
            [outline_id],
        )
    sort = (int(max_row["m"]) if max_row else 0) + 1
    new_id = short_id8()
    await db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, level, progress, sort, parent_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [new_id, outline_id, title, level, "todo", sort, parent_id],
    )
    return {
        "id": new_id,
        "outline_id": outline_id,
        "title": title,
        "level": level,
        "progress": "todo",
        "sort": sort,
        "parent_id": parent_id,
    }
