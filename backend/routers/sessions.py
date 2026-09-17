from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from dependencies import get_db
from application.product_owner_deletion import (
    ProductOwnerActiveError,
    prepare_session_owner_deletion,
)
from database.crud.screenplay_session_deletion import (
    delete_screenplay_session_rows,
)
from schemas.sessions import (
    CreateSessionRequest,
    ReorderSessionsRequest,
    UpdateSessionPinnedRequest,
    UpdateSessionTitleRequest,
)

router = APIRouter(tags=["sessions"])


@router.post("/sessions")
async def create_session(body: CreateSessionRequest):
    db = get_db()
    scope = "setting" if body.scope == "setting" else "chapter"
    async with db.transaction():
        session_id = await db.execute_and_get_id(
            "INSERT INTO ai_sessions (book_id, chapter_id, scope) VALUES (?, ?, ?)",
            [body.bookId, None if scope == "setting" else body.chapterId, scope],
        )
        from application.writing_technique_service import WritingTechniqueService
        await WritingTechniqueService(db).initialize_session(str(session_id), str(body.bookId))
    row = await db.fetch_one("SELECT * FROM ai_sessions WHERE id = ?", [session_id])
    return {"success": True, "data": row}


@router.get("/sessions")
async def get_sessions(
    bookId: Optional[str] = Query(None),
    chapterId: Optional[str] = Query(None),
    includeClosed: Optional[bool] = Query(False),
    scope: Optional[str] = Query(None),
):
    """scope=setting 时仅返回显式标记的「全局会话」；章节查询排除全局会话。"""
    db = get_db()
    conditions = []
    params: list = []
    if bookId:
        conditions.append("book_id = ?")
        params.append(bookId)
    if scope == "setting":
        conditions.append("scope = 'setting'")
    else:
        conditions.append("(scope IS NULL OR scope != 'setting')")
        if chapterId:
            conditions.append("chapter_id = ?")
            params.append(chapterId)
    if not includeClosed:
        conditions.append("closed = 0")
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = await db.fetch_all(
        f"SELECT * FROM ai_sessions{where} ORDER BY create_time ASC",
        params,
    )
    return {"success": True, "data": rows}


@router.put("/sessions/{sessionId}/close")
async def close_session(sessionId: int):
    db = get_db()
    await db.execute("UPDATE ai_sessions SET closed = 1 WHERE id = ?", [sessionId])
    return {"success": True}


@router.put("/sessions/{sessionId}/reopen")
async def reopen_session(sessionId: int):
    db = get_db()
    await db.execute("UPDATE ai_sessions SET closed = 0 WHERE id = ?", [sessionId])
    return {"success": True}


@router.delete("/sessions/{sessionId}")
async def delete_session(sessionId: int):
    db = get_db()
    owner_rows = None
    async with db.transaction(cancellation_linearizable=True):
        try:
            owner_rows = await prepare_session_owner_deletion(db, [sessionId])
        except ProductOwnerActiveError as error:
            raise HTTPException(status_code=409, detail=error.message) from error
        await delete_screenplay_session_rows(db, sessionId)
        await db.execute(
            "DELETE FROM ai_conversations WHERE session_id = ?",
            [sessionId],
        )
        await db.execute("DELETE FROM writing_technique_selections WHERE scope_kind='session' AND scope_id=?", [str(sessionId)])
        await db.execute("DELETE FROM ai_sessions WHERE id = ?", [sessionId])
    from services.memory_deposition_service import deliver_recorded

    deliveries = await deliver_recorded(
        db,
        owner_rows.memory_delivery_keys if owner_rows is not None else (),
    )
    return {
        "success": True,
        "memoryDelivery": [item.to_dict() for item in deliveries],
    }


@router.put("/sessions/{sessionId}/title")
async def update_session_title(sessionId: int, body: UpdateSessionTitleRequest):
    db = get_db()
    await db.execute(
        "UPDATE ai_sessions SET title = ? WHERE id = ?",
        [body.title, sessionId],
    )
    return {"success": True}


@router.put("/sessions/{sessionId}/pinned")
async def update_session_pinned(sessionId: int, body: UpdateSessionPinnedRequest):
    db = get_db()
    await db.execute(
        "UPDATE ai_sessions SET pinned = ? WHERE id = ?",
        [1 if body.pinned else 0, sessionId],
    )
    return {"success": True}


@router.put("/sessions/reorder")
async def reorder_sessions(body: ReorderSessionsRequest):
    db = get_db()
    async with db.transaction():
        for index, session_id in enumerate(body.orderedIds):
            await db.execute(
                "UPDATE ai_sessions SET sort_order = ? WHERE id = ?",
                [index, session_id],
            )
    return {"success": True}
