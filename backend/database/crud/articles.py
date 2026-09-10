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
    from application.continuation_identity import require_editable_identity
    await require_editable_identity(db, chapter_id)
    existing = await db.fetch_one(
        "SELECT id, content FROM articles WHERE chapter_id = ?", [chapter_id]
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

    # 旁路：把字数变化累加到当日写作统计快照（失败不影响保存）
    from database.crud.word_stats import record_article_word_delta
    await record_article_word_delta(
        db, chapter_id, (existing or {}).get("content"), content
    )


async def get_article(
    db: DatabaseConnection, chapter_id: str
) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM articles WHERE chapter_id = ?", [chapter_id]
    )
