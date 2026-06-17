from __future__ import annotations

from fastapi import APIRouter

from database.crud.articles import save_article as crud_save_article
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
    """统一走 crud.save_article：写正文 + 维护当日字数快照。"""
    db = get_db()
    await crud_save_article(db, chapterId, body.content)
    return {"success": True}
