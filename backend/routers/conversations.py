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
    context_compaction_json = (
        json.dumps(body.contextCompaction, ensure_ascii=False)
        if body.contextCompaction is not None
        else None
    )
    context_budget_json = (
        json.dumps(body.contextBudget, ensure_ascii=False)
        if body.contextBudget is not None
        else None
    )
    screenplay_proposal_json = (
        json.dumps(body.screenplayProposal, ensure_ascii=False)
        if body.screenplayProposal is not None
        else None
    )
    agent_process_json = (
        json.dumps(body.agentProcess, ensure_ascii=False)
        if body.agentProcess is not None
        else None
    )
    values = [
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
        context_compaction_json,
        context_budget_json,
        screenplay_proposal_json,
        agent_process_json,
    ]
    async with db.transaction():
        existing = None
        if body.agentRunId:
            existing = await db.fetch_one(
                "SELECT conversation_id FROM ai_agent_runs "
                "WHERE id = ? AND session_id = ?",
                [str(body.agentRunId), body.sessionId],
            )
        existing_id = (existing or {}).get("conversation_id")
        if existing_id is not None:
            conversation_id = int(existing_id)
            await db.execute(
                "UPDATE ai_conversations SET chapter_id = ?, prompt = ?, "
                "response = ?, model = ?, thinking = ?, tool_call_segments = ?, "
                "thinking_blocks = ?, thinking_durations_ms = ?, duration_ms = ?, "
                "task_plan = ?, context_compaction = ?, context_budget = ?, "
                "screenplay_proposal = ?, agent_process = ? "
                "WHERE id = ? AND session_id = ?",
                [*values, conversation_id, body.sessionId],
            )
        else:
            conversation_id = await db.execute_and_get_id(
                """INSERT INTO ai_conversations
                   (session_id, chapter_id, prompt, response, model, thinking,
                    tool_call_segments, thinking_blocks, thinking_durations_ms,
                    duration_ms, task_plan, context_compaction, context_budget,
                    screenplay_proposal, agent_process)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [body.sessionId, *values],
            )
            if body.agentRunId and conversation_id is not None:
                from infrastructure.persistence.run_store import (
                    set_run_conversation_id,
                )

                await set_run_conversation_id(
                    db,
                    str(body.agentRunId),
                    int(conversation_id),
                )
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
        "SELECT c.id, c.session_id, c.chapter_id, c.prompt, c.response, "
        "c.create_time, c.model, c.thinking, c.tool_call_segments, "
        "c.thinking_blocks, c.thinking_durations_ms, c.duration_ms, "
        "c.task_plan, c.context_compaction, c.context_budget, c.agent_process, "
        "(SELECT r.id FROM ai_agent_runs AS r "
        "  WHERE r.conversation_id = c.id "
        "  ORDER BY r.update_time DESC LIMIT 1) AS agent_run_id, "
        "(SELECT lt.id FROM ai_agent_runs AS r "
        "  JOIN ai_agent_work_item_runs AS wir ON wir.run_id = r.id "
        "  JOIN ai_agent_long_tasks AS lt ON lt.work_item_id = wir.work_item_id "
        "  WHERE r.conversation_id = c.id "
        "    AND ((json_extract(lt.metadata_json, '$.sessionId') IS NOT NULL "
        "      AND CAST(json_extract(lt.metadata_json, '$.sessionId') AS INTEGER) = c.session_id) "
        "      OR (json_extract(lt.metadata_json, '$.sessionId') IS NULL "
        "        AND lt.created_by_run_id = r.id)) "
        "  ORDER BY lt.update_time DESC LIMIT 1) AS long_task_id, "
        "COALESCE(c.screenplay_proposal, ("
        "  SELECT e.payload_json FROM ai_agent_runs AS r "
        "  JOIN ai_agent_run_events AS e ON e.run_id = r.id "
        "  WHERE r.conversation_id = c.id "
        "    AND e.event_type = 'screenplay.document_proposal' "
        "  ORDER BY e.id DESC LIMIT 1"
        ")) AS screenplay_proposal "
        "FROM ai_conversations AS c WHERE c.session_id = ? ORDER BY c.id ASC",
        [sessionId],
    )
    return {"success": True, "data": rows}


@router.delete("/conversations/{sessionId}/after-turn")
async def delete_after_turn(sessionId: str, keepTurnCount: int = Query(...)):
    db = get_db()
    async with db.transaction():
        all_rows = await db.fetch_all(
            "SELECT id FROM ai_conversations WHERE session_id = ? ORDER BY id ASC",
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
                await db.execute(
                    "DELETE FROM ai_conversation_summaries WHERE session_id = ?",
                    [sessionId],
                )
    return {"success": True}
