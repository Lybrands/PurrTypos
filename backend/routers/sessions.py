from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

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
    async with db.transaction():
        target_conversations = await db.fetch_all(
            "SELECT id FROM ai_conversations WHERE session_id = ?",
            [sessionId],
        )
        conversation_ids = [int(row["id"]) for row in target_conversations]
        placeholders = ",".join("?" for _ in conversation_ids)
        conversation_clause = (
            f" OR conversation_id IN ({placeholders})"
            if conversation_ids
            else ""
        )
        ownership_params = [sessionId, *conversation_ids]
        active_run = await db.fetch_one(
            "SELECT id FROM ai_agent_runs "
            f"WHERE (session_id = ?{conversation_clause}) "
            "AND status IN ('pending', 'queued', 'running', 'paused') "
            "LIMIT 1",
            ownership_params,
        )
        active_writing_request = await db.fetch_one(
            "SELECT request_id FROM ai_writing_chat_requests "
            "WHERE session_id = ? AND status IN ('accepted', 'starting') LIMIT 1",
            [sessionId],
        )
        active_long_task = await db.fetch_one(
            "SELECT lt.id FROM ai_agent_long_tasks AS lt "
            "WHERE lt.status IN ('pending', 'queued', 'running', 'paused') "
            "AND ("
            " CAST(json_extract(lt.metadata_json, '$.sessionId') AS INTEGER) = ? "
            " OR EXISTS ("
            "   SELECT 1 FROM ai_agent_runs AS owner_run "
            "   WHERE owner_run.id = lt.created_by_run_id "
            f"     AND (owner_run.session_id = ?{conversation_clause})"
            " ) "
            " OR EXISTS ("
            "   SELECT 1 FROM ai_agent_work_item_runs AS wir "
            "   JOIN ai_agent_runs AS linked_run ON linked_run.id = wir.run_id "
            "   WHERE wir.work_item_id = lt.work_item_id "
            f"     AND (linked_run.session_id = ?{conversation_clause})"
            " )"
            ") "
            "LIMIT 1",
            [
                sessionId,
                sessionId,
                *conversation_ids,
                sessionId,
                *conversation_ids,
            ],
        )
        if active_run or active_long_task or active_writing_request:
            raise HTTPException(
                status_code=409,
                detail="运行中、排队中或已暂停的对话不能删除，请先完成或终止任务。",
            )
        await db.execute(
            "UPDATE ai_agent_long_tasks SET "
            "metadata_json = json_remove(metadata_json, '$.sessionId') "
            "WHERE CAST(json_extract(metadata_json, '$.sessionId') AS INTEGER) = ? "
            "OR EXISTS ("
            " SELECT 1 FROM ai_agent_runs AS owner_run "
            " WHERE owner_run.id = ai_agent_long_tasks.created_by_run_id "
            f" AND (owner_run.session_id = ?{conversation_clause})"
            ") OR EXISTS ("
            " SELECT 1 FROM ai_agent_work_item_runs AS wir "
            " JOIN ai_agent_runs AS linked_run ON linked_run.id = wir.run_id "
            " WHERE wir.work_item_id = ai_agent_long_tasks.work_item_id "
            f" AND (linked_run.session_id = ?{conversation_clause})"
            ")",
            [
                sessionId,
                sessionId,
                *conversation_ids,
                sessionId,
                *conversation_ids,
            ],
        )
        await db.execute(
            "UPDATE ai_error_reports SET session_id = NULL, conversation_id = NULL "
            f"WHERE session_id = ?{conversation_clause}",
            ownership_params,
        )
        await db.execute(
            "UPDATE ai_agent_runs SET "
            "session_id = CASE WHEN session_id = ? THEN NULL ELSE session_id END, "
            "conversation_id = CASE "
            + (
                f"WHEN conversation_id IN ({placeholders}) THEN NULL "
                if conversation_ids
                else ""
            )
            + "ELSE conversation_id END "
            f"WHERE session_id = ?{conversation_clause}",
            [
                sessionId,
                *conversation_ids,
                sessionId,
                *conversation_ids,
            ],
        )
        await db.execute(
            "DELETE FROM ai_favorites WHERE session_id = ?",
            [sessionId],
        )
        await db.execute(
            "DELETE FROM ai_conversation_summaries WHERE session_id = ?",
            [sessionId],
        )
        await db.execute(
            "DELETE FROM ai_writing_chat_requests WHERE session_id = ?",
            [sessionId],
        )
        await db.execute(
            "DELETE FROM ai_local_conversation_turn_receipts WHERE session_id = ?",
            [sessionId],
        )
        if conversation_ids:
            await db.execute(
                "UPDATE memory_items SET status = 'archived', "
                "update_time = CURRENT_TIMESTAMP "
                "WHERE source_type = 'conversation' "
                f"AND source_id IN ({placeholders}) AND status <> 'archived'",
                [str(conversation_id) for conversation_id in conversation_ids],
            )
        await db.execute(
            "DELETE FROM ai_conversations WHERE session_id = ?",
            [sessionId],
        )
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
