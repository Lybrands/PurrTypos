from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from dependencies import get_db
from schemas.sessions import CreateSessionRequest, UpdateSessionTitleRequest

router = APIRouter(tags=["sessions"])


@router.post("/sessions")
async def create_session(body: CreateSessionRequest):
    db = get_db()
    scope = "setting" if body.scope == "setting" else "chapter"
    session_id = await db.execute_and_get_id(
        "INSERT INTO ai_sessions (book_id, chapter_id, scope) VALUES (?, ?, ?)",
        [body.bookId, None if scope == "setting" else body.chapterId, scope],
    )
    row = await db.fetch_one("SELECT * FROM ai_sessions WHERE id = ?", [session_id])
    return {"success": True, "data": row}


@router.get("/sessions")
async def get_sessions(
    bookId: Optional[str] = Query(None),
    chapterId: Optional[str] = Query(None),
    includeClosed: Optional[bool] = Query(False),
    scope: Optional[str] = Query(None),
):
    """scope=setting 时仅返回显式标记的「设定会话」；章节查询排除设定会话。"""
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
    await db.execute("DELETE FROM ai_sessions WHERE id = ?", [sessionId])
    return {"success": True}


@router.put("/sessions/{sessionId}/title")
async def update_session_title(sessionId: int, body: UpdateSessionTitleRequest):
    db = get_db()
    await db.execute(
        "UPDATE ai_sessions SET title = ? WHERE id = ?",
        [body.title, sessionId],
    )
    return {"success": True}
