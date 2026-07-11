from __future__ import annotations

import json

from fastapi import APIRouter, Query

from dependencies import get_db
from schemas.conversations import SaveConversationRequest

router = APIRouter(tags=["conversations"])


@router.post("/conversations")
async def save_conversation(body: SaveConversationRequest):
    db = get_db()
    tool_call_segments_json = (
        json.dumps(body.toolCallSegments, ensure_ascii=False)
        if body.toolCallSegments is not None
        else None
    )
    thinking_blocks_json = (
        json.dumps(body.thinkingBlocks, ensure_ascii=False)
        if body.thinkingBlocks is not None
        else None
    )
    thinking_durations_ms_json = (
        json.dumps(body.thinkingDurationsMs, ensure_ascii=False)
        if body.thinkingDurationsMs is not None
        else None
    )
    task_plan_json = (
        json.dumps(body.taskPlan, ensure_ascii=False)
        if body.taskPlan is not None
        else None
    )
    subagent_result_json = (
        json.dumps(body.subagentResult, ensure_ascii=False)
        if body.subagentResult is not None
        else None
    )
    conversation_id = await db.execute_and_get_id(
        """INSERT INTO ai_conversations
           (session_id, chapter_id, prompt, response, model, thinking,
            tool_call_segments, thinking_blocks, thinking_durations_ms, duration_ms,
            task_plan, subagent_result)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            body.sessionId,
            body.chapterId,
            body.prompt,
            body.response,
            body.model,
            body.thinking,
            tool_call_segments_json,
            thinking_blocks_json,
            thinking_durations_ms_json,
            body.durationMs,
            task_plan_json,
            subagent_result_json,
        ],
    )
    if body.agentRunId and conversation_id is not None:
        from services.agent_run_store import set_run_conversation_id

        await set_run_conversation_id(db, str(body.agentRunId), int(conversation_id))
    try:
        from services import memory_deposition_service
        book_id = body.bookId or await memory_deposition_service.resolve_book_id_for_session(
            db,
            int(body.sessionId) if body.sessionId is not None else None,
        )
        await memory_deposition_service.deposit_explicit_memory_from_conversation(
            book_id=book_id,
            conversation_id=int(conversation_id) if conversation_id is not None else None,
            prompt=body.prompt,
        )
    except Exception:
        # 对话保存是主路径；记忆沉淀失败不应影响历史记录。
        pass
    return {"success": True, "data": {"id": conversation_id}}


@router.get("/conversations/{sessionId}")
async def get_conversations(sessionId: str):
    db = get_db()
    rows = await db.fetch_all(
        "SELECT * FROM ai_conversations WHERE session_id = ? ORDER BY create_time ASC",
        [sessionId],
    )
    return {"success": True, "data": rows}


@router.delete("/conversations/{sessionId}/after-turn")
async def delete_after_turn(sessionId: str, keepTurnCount: int = Query(...)):
    db = get_db()
    all_rows = await db.fetch_all(
        "SELECT id FROM ai_conversations WHERE session_id = ? ORDER BY create_time ASC",
        [sessionId],
    )
    if keepTurnCount < len(all_rows):
        ids_to_delete = [r["id"] for r in all_rows[keepTurnCount:]]
        if ids_to_delete:
            placeholders = ",".join("?" for _ in ids_to_delete)
            await db.execute(
                f"DELETE FROM ai_conversations WHERE id IN ({placeholders})",
                ids_to_delete,
            )
    return {"success": True}
