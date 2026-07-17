from __future__ import annotations

from fastapi import APIRouter

from database.crud import chapter_diff as diff_crud
from database.crud.articles import save_article
from dependencies import get_db
from schemas.chapter_diff import CommitDiffRequest

router = APIRouter(tags=["chapter-diff"])


@router.post("/chapter-diff/{chapterId}/commit")
async def commit_chapter_diff(chapterId: str, body: CommitDiffRequest):
    """落盘：把 ``content`` 写入 articles，并将本次 diff 写入历史表。"""
    db = get_db()
    await save_article(db, chapterId, body.content)
    diff_id = await diff_crud.insert_diff_history(
        db,
        chapter_id=chapterId,
        before_text=body.before_text,
        after_text=body.after_text,
        source=body.source,
        accepted_segments=body.accepted_segments,
        rejected_segments=body.rejected_segments,
    )
    try:
        from services import memory_deposition_service
        await memory_deposition_service.deposit_chapter_diff_candidate(
            db,
            chapter_id=chapterId,
            diff_id=diff_id,
            before_text=body.before_text,
            after_text=body.after_text,
            source=body.source,
            accepted_segments=body.accepted_segments,
        )
    except Exception:
        # 记忆候选沉淀失败不应阻断正文落库。
        pass
    return {"success": True, "data": {"id": diff_id}}


@router.get("/chapter-diff/{chapterId}")
async def list_chapter_diff(chapterId: str, limit: int = 50):
    db = get_db()
    rows = await diff_crud.list_diff_history(db, chapterId, limit=limit)
    return {"success": True, "data": rows}


@router.get("/chapter-diff/by-id/{diffId}")
async def get_chapter_diff(diffId: int):
    db = get_db()
    row = await diff_crud.get_diff_history(db, diffId)
    return {"success": True, "data": row}


@router.post("/chapter-diff/by-id/{diffId}/rollback")
async def rollback_chapter_diff(diffId: int):
    """把指定历史的 ``before_text`` 写回 articles，并记录一条 rollback 历史。

    实现说明：当前 ``content`` 是纯文本（articles.content schema），所以可以直接把
    历史里的 ``before_text`` 当作 articles.content 写回；若以后 article schema 变成
    Lexical JSON，这里要改成另存 before_text 的对应 JSON 镜像。
    """
    db = get_db()
    target = await diff_crud.get_diff_history(db, diffId)
    if not target:
        return {"success": False, "error": "diff not found"}

    chapter_id = target["chapter_id"]
    rollback_to = target.get("before_text") or ""
    rollback_from = target.get("after_text") or ""

    await save_article(db, chapter_id, rollback_to)
    new_id = await diff_crud.insert_diff_history(
        db,
        chapter_id=chapter_id,
        before_text=rollback_from,
        after_text=rollback_to,
        source=f"rollback_of:{diffId}",
        accepted_segments=0,
        rejected_segments=0,
    )
    return {"success": True, "data": {"id": new_id, "chapterId": chapter_id}}
