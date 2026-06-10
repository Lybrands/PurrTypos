"""
Chapter diff history CRUD.

A diff session is committed as a single row when the user finishes accepting/
rejecting paragraphs. ``before_text`` and ``after_text`` are full plain-text
snapshots (Lexical 序列化由前端处理后再 commit)，便于「diff 历史面板」回滚。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


async def insert_diff_history(
    db: DatabaseConnection,
    *,
    chapter_id: str,
    before_text: str,
    after_text: str,
    source: str = "ai_rewrite",
    accepted_segments: int = 0,
    rejected_segments: int = 0,
) -> int | None:
    return await db.execute_and_get_id(
        "INSERT INTO chapter_diff_history "
        "(chapter_id, before_text, after_text, source, "
        "accepted_segments, rejected_segments) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            chapter_id,
            before_text,
            after_text,
            source,
            int(accepted_segments),
            int(rejected_segments),
        ],
    )


async def list_diff_history(
    db: DatabaseConnection, chapter_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT id, chapter_id, before_text, after_text, source, "
        "accepted_segments, rejected_segments, create_time "
        "FROM chapter_diff_history WHERE chapter_id = ? "
        "ORDER BY create_time DESC LIMIT ?",
        [chapter_id, int(limit)],
    )


async def get_diff_history(
    db: DatabaseConnection, diff_id: int
) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT id, chapter_id, before_text, after_text, source, "
        "accepted_segments, rejected_segments, create_time "
        "FROM chapter_diff_history WHERE id = ?",
        [int(diff_id)],
    )
