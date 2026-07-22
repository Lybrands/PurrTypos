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
    book_id = None
    try:
        from services.memory_deposition_service import resolve_book_id_for_chapter
        from services.story_memory_analysis_service import invalidate_saved_chapter

        book_id = await resolve_book_id_for_chapter(db, chapterId)
        if book_id:
            await invalidate_saved_chapter(
                db,
                book_id=book_id,
                chapter_id=chapterId,
                content=body.content,
            )
    except Exception:
        pass
    try:
        from services import memory_deposition_service
        await memory_deposition_service.deposit_inline_article_candidate(
            db,
            chapter_id=chapterId,
            content=body.content,
            source=body.source or "",
        )
    except Exception:
        pass
    story_memory_analysis = None
    if str(body.source or "") == "inline_edit":
        try:
            from services.story_memory_analysis_service import (
                analyze_chapter,
                receipt_dict,
            )

            if book_id:
                story_memory_analysis = receipt_dict(
                    await analyze_chapter(
                        db,
                        book_id=book_id,
                        chapter_id=chapterId,
                        content=body.content,
                        automatic=True,
                    )
                )
        except Exception:
            pass
    return {
        "success": True,
        "data": {"storyMemoryAnalysis": story_memory_analysis},
    }
