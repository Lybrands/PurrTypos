"""
Story background & attachments CRUD – port from database.js.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


async def get_story_background(
    db: DatabaseConnection, book_id: str
) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM story_background WHERE book_id = ?", [book_id]
    )


async def save_story_background(
    db: DatabaseConnection, book_id: str, content: str
) -> None:
    existing = await db.fetch_one(
        "SELECT book_id FROM story_background WHERE book_id = ?", [book_id]
    )
    if existing:
        await db.execute(
            "UPDATE story_background SET content = ?, update_time = CURRENT_TIMESTAMP "
            "WHERE book_id = ?",
            [content, book_id],
        )
    else:
        await db.execute(
            "INSERT INTO story_background (book_id, content) VALUES (?, ?)",
            [book_id, content],
        )


async def get_story_background_attachments(
    db: DatabaseConnection, book_id: str
) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT id, book_id, name, stored_path, create_time "
        "FROM story_background_attachments WHERE book_id = ? "
        "ORDER BY create_time ASC",
        [book_id],
    )


async def add_story_background_attachment(
    db: DatabaseConnection, book_id: str, name: str, stored_path: str
) -> dict[str, Any] | None:
    att_id = await db.execute_and_get_id(
        "INSERT INTO story_background_attachments (book_id, name, stored_path) "
        "VALUES (?, ?, ?)",
        [book_id, name, stored_path],
    )
    if att_id is not None:
        return await db.fetch_one(
            "SELECT id, book_id, name, stored_path, create_time "
            "FROM story_background_attachments WHERE id = ?",
            [att_id],
        )
    return None
