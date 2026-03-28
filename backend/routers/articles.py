from __future__ import annotations

from fastapi import APIRouter

from dependencies import get_db
from schemas.articles import SaveArticleRequest

router = APIRouter(tags=["articles"])


@router.get("/articles/{chapterId}")
async def get_article(chapterId: str):
    db = get_db()
    row = await db.fetch_one(
        "SELECT * FROM articles WHERE chapter_id = ?", [chapterId]
    )
    return {"success": True, "data": row}


@router.put("/articles/{chapterId}")
async def save_article(chapterId: str, body: SaveArticleRequest):
    db = get_db()
    existing = await db.fetch_one(
        "SELECT id FROM articles WHERE chapter_id = ?", [chapterId]
    )
    if existing:
        await db.execute(
            "UPDATE articles SET content = ?, update_time = datetime('now') WHERE chapter_id = ?",
            [body.content, chapterId],
        )
    else:
        await db.execute(
            "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
            [chapterId, body.content],
        )
    return {"success": True}
