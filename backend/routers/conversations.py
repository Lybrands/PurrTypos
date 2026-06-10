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
    subagent_result_json = (
        json.dumps(body.subagentResult, ensure_ascii=False)
        if body.subagentResult is not None
        else None
    )
    await db.execute(
        """INSERT INTO ai_conversations
           (session_id, chapter_id, prompt, response, model, thinking,
            tool_call_segments, thinking_blocks, subagent_result)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            body.sessionId,
            body.chapterId,
            body.prompt,
            body.response,
            body.model,
            body.thinking,
            tool_call_segments_json,
            thinking_blocks_json,
            subagent_result_json,
        ],
    )
    return {"success": True}


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
