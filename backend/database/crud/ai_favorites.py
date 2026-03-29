"""
AI favorites CRUD – port from database.js.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


async def save_ai_favorite(
    db: DatabaseConnection,
    session_id: int,
    session_title: str,
    prompt: str,
    content: str,
) -> dict[str, Any] | None:
    fav_id = await db.execute_and_get_id(
        "INSERT INTO ai_favorites (session_id, session_title, prompt, content) "
        "VALUES (?, ?, ?, ?)",
        [session_id, session_title, prompt or "", content],
    )
    return await db.fetch_one(
        "SELECT * FROM ai_favorites WHERE id = ?", [fav_id]
    )


async def get_ai_favorites(db: DatabaseConnection) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT * FROM ai_favorites ORDER BY create_time ASC"
    )


async def delete_ai_favorite(db: DatabaseConnection, fav_id: int) -> None:
    await db.execute("DELETE FROM ai_favorites WHERE id = ?", [fav_id])
