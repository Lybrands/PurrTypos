"""Materialize completed detached Agent Runs as ordinary conversations."""

from __future__ import annotations

import json
from typing import Any

from infrastructure.persistence.run_store import get_run, get_run_todos


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def ensure_terminal_run_conversation(db, run_id: str) -> int | None:
    """Persist one terminal Run exactly once, even when its SSE peer left."""

    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        return None

    async with db.transaction():
        run = await get_run(db, normalized_run_id)
        if run is None:
            return None
        existing_id = run.get("conversation_id")
        if existing_id is not None:
            return int(existing_id)
        status = str(run.get("status") or "")
        if status not in {"done", "blocked", "failed", "canceled"}:
            return None
        if run.get("session_id") is None:
            return None
        durable_dispatch = await db.fetch_one(
            "SELECT id FROM ai_agent_run_events WHERE run_id = ? "
            "AND event_type = 'long_task.dispatched' ORDER BY id DESC LIMIT 1",
            [normalized_run_id],
        )
        # A durable dispatch receipt describes orchestration state.  Persist
        # the originating turn so the task can be projected after reload, but
        # never store that receipt as if it were model-authored conversation.
        response = (
            ""
            if durable_dispatch is not None
            else str(run.get("final_response") or "").strip()
        )
        if status != "done":
            response = {
                "blocked": "Agent 未完成全部计划步骤，已安全停止。",
                "failed": "Agent 运行过程中发生异常，已安全停止；请稍后重试。",
                "canceled": "本轮对话已由你手动终止。",
            }[status]
        if not response and durable_dispatch is None:
            return None

        todos = await get_run_todos(db, normalized_run_id)
        plan_row = await db.fetch_one(
            "SELECT payload_json FROM ai_agent_run_events "
            "WHERE run_id = ? AND event_type = 'run.todos_updated' "
            "ORDER BY id DESC LIMIT 1",
            [normalized_run_id],
        )
        plan_payload = _json_object(
            str((plan_row or {}).get("payload_json") or "")
        )
        task_plan = {
            "title": str(plan_payload.get("title") or "任务计划"),
            "status": status,
            "steps": todos,
        }
        goal = plan_payload.get("goal")
        if isinstance(goal, str) and goal.strip():
            task_plan["goal"] = goal.strip()

        proposal_row = await db.fetch_one(
            "SELECT payload_json FROM ai_agent_run_events "
            "WHERE run_id = ? AND event_type = 'screenplay.document_proposal' "
            "ORDER BY id DESC LIMIT 1",
            [normalized_run_id],
        )
        proposal_json = (
            str(proposal_row.get("payload_json") or "")
            if proposal_row is not None
            else None
        )
        conversation_id = await db.execute_and_get_id(
            "INSERT INTO ai_conversations "
            "(session_id, chapter_id, prompt, response, model, task_plan, "
            "screenplay_proposal) VALUES (?, NULL, ?, ?, ?, ?, ?)",
            [
                int(run["session_id"]),
                str(run.get("prompt") or ""),
                response,
                run.get("model_name"),
                json.dumps(task_plan, ensure_ascii=False),
                proposal_json,
            ],
        )
        if conversation_id is None:
            raise RuntimeError("terminal Agent Run conversation was not created")
        await db.execute(
            "UPDATE ai_agent_runs SET conversation_id = ?, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? "
            "AND conversation_id IS NULL",
            [int(conversation_id), normalized_run_id],
        )
        return int(conversation_id)
