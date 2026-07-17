"""Story background revision history CRUD."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


async def insert_story_background_history(
    db: DatabaseConnection,
    *,
    book_id: str,
    before_content: str = "",
    after_content: str = "",
    source: str = "user",
    accepted_segments: int = 0,
    rejected_segments: int = 0,
) -> int | None:
    return await db.execute_and_get_id(
        "INSERT INTO story_background_history "
        "(book_id, before_content, after_content, source, "
        "accepted_segments, rejected_segments) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            book_id,
            before_content,
            after_content,
            source,
            int(accepted_segments),
            int(rejected_segments),
        ],
    )


async def list_story_background_history(
    db: DatabaseConnection, book_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT id, book_id, before_content, after_content, source, "
        "accepted_segments, rejected_segments, create_time "
        "FROM story_background_history WHERE book_id = ? "
        "ORDER BY create_time DESC LIMIT ?",
        [book_id, int(limit)],
    )


async def get_story_background_history(
    db: DatabaseConnection, history_id: int
) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT id, book_id, before_content, after_content, source, "
        "accepted_segments, rejected_segments, create_time "
        "FROM story_background_history WHERE id = ?",
        [int(history_id)],
    )
