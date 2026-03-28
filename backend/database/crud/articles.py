"""
Article CRUD – port of Database.saveArticle / getArticle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


async def save_article(
    db: DatabaseConnection, chapter_id: str, content: str
) -> None:
    existing = await db.fetch_one(
        "SELECT id FROM articles WHERE chapter_id = ?", [chapter_id]
    )
    if existing:
        await db.execute(
            "UPDATE articles SET content = ?, update_time = CURRENT_TIMESTAMP "
            "WHERE chapter_id = ?",
            [content, chapter_id],
        )
    else:
        await db.execute(
            "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
            [chapter_id, content],
        )


async def get_article(
    db: DatabaseConnection, chapter_id: str
) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM articles WHERE chapter_id = ?", [chapter_id]
    )
