"""
AI session CRUD – port from database.js.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


async def create_session(
    db: DatabaseConnection,
    book_id: str,
    chapter_id: str | None = None,
) -> dict[str, Any]:
    sid = await db.execute_and_get_id(
        "INSERT INTO ai_sessions (book_id, chapter_id) VALUES (?, ?)",
        [book_id, chapter_id],
    )
    if sid is None or sid <= 0:
        max_row = await db.fetch_one("SELECT MAX(id) as id FROM ai_sessions")
        sid = int(max_row["id"]) if max_row and max_row["id"] is not None else 1
    row = await db.fetch_one("SELECT * FROM ai_sessions WHERE id = ?", [sid])
    if row:
        return {
            "id": int(row["id"]),
            "book_id": str(row["book_id"]) if row["book_id"] is not None else None,
            "chapter_id": str(row["chapter_id"]) if row["chapter_id"] is not None else None,
            "title": row["title"] or "新对话",
            "create_time": row["create_time"],
            "closed": row.get("closed", 0),
        }
    from datetime import datetime, timezone
    return {
        "id": sid,
        "book_id": str(book_id) if book_id is not None else None,
        "chapter_id": str(chapter_id) if chapter_id is not None else None,
        "title": "新对话",
        "create_time": datetime.now(timezone.utc).isoformat(),
        "closed": 0,
    }


async def get_sessions(
    db: DatabaseConnection,
    book_id: str,
    chapter_id: str | None = None,
    include_closed: bool = False,
) -> list[dict[str, Any]]:
    closed_cond = "" if include_closed else " AND (closed IS NULL OR closed = 0)"
    if chapter_id is None:
        return await db.fetch_all(
            "SELECT * FROM ai_sessions WHERE book_id = ? AND chapter_id IS NULL"
            + closed_cond
            + " ORDER BY create_time ASC",
            [book_id],
        )
    return await db.fetch_all(
        "SELECT * FROM ai_sessions WHERE book_id = ? AND chapter_id = ?"
        + closed_cond
        + " ORDER BY create_time ASC",
        [book_id, chapter_id],
    )


async def set_session_closed(db: DatabaseConnection, session_id: int) -> None:
    await db.execute(
        "UPDATE ai_sessions SET closed = 1 WHERE id = ?", [session_id]
    )


async def set_session_reopened(db: DatabaseConnection, session_id: int) -> None:
    await db.execute(
        "UPDATE ai_sessions SET closed = 0 WHERE id = ?", [session_id]
    )


async def delete_session(db: DatabaseConnection, session_id: int) -> None:
    await db.execute(
        "DELETE FROM ai_conversation_summaries WHERE session_id = ?",
        [session_id],
    )
    await db.execute(
        "DELETE FROM ai_conversations WHERE session_id = ?", [session_id]
    )
    await db.execute("DELETE FROM ai_sessions WHERE id = ?", [session_id])


async def update_session_title(
    db: DatabaseConnection, session_id: int, title: str
) -> None:
    await db.execute(
        "UPDATE ai_sessions SET title = ? WHERE id = ?", [title, session_id]
    )
