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
        status = str(run.get("status") or "")
        if status not in {"done", "blocked", "failed", "canceled"}:
            return None
        if run.get("session_id") is None:
            return None
        session = await db.fetch_one(
            "SELECT chapter_id FROM ai_sessions WHERE id = ?",
            [int(run["session_id"])],
        )
        if session is None:
            return None
        durable_dispatch = await db.fetch_one(
            "SELECT id FROM ai_agent_run_events WHERE run_id = ? "
            "AND event_type = 'long_task.dispatched' ORDER BY id DESC LIMIT 1",
            [normalized_run_id],
        )
        # Only the canonical final public output projector may create
        # Assistant正文. Detached terminal materialization persists the turn
        # shell and structured Run state; status errors and dispatch receipts
        # never become authored content.
        response = (
            str(run.get("final_response") or "").strip()
            if status == "done" and durable_dispatch is None
            else ""
        )

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

        if existing_id is not None:
            # A proposal resolution may have created an authoritative shell
            # while the Run was still active. Terminal materialization fills
            # only server-owned projection fields and preserves its mutable
            # product overlay (agent_process).
            await db.execute(
                "UPDATE ai_conversations SET chapter_id = ?, prompt = ?, "
                "response = ?, model = ?, task_plan = ? "
                "WHERE id = ? AND session_id = ?",
                [
                    session.get("chapter_id"),
                    str(run.get("prompt") or ""),
                    response,
                    run.get("model_name"),
                    json.dumps(task_plan, ensure_ascii=False),
                    int(existing_id),
                    int(run["session_id"]),
                ],
            )
            return int(existing_id)

        conversation_id = await db.execute_and_get_id(
            "INSERT INTO ai_conversations "
            "(session_id, chapter_id, prompt, response, model, task_plan) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                int(run["session_id"]),
                session.get("chapter_id"),
                str(run.get("prompt") or ""),
                response,
                run.get("model_name"),
                json.dumps(task_plan, ensure_ascii=False),
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


async def materialize_recovered_run_conversations(
    db,
    run_ids,
) -> tuple[str, ...]:
    """Project selected terminal root Writing Runs before another turn."""

    materialized: list[str] = []
    for value in run_ids:
        run_id = str(value or "").strip()
        if not run_id:
            continue
        owned_root = await db.fetch_one(
            "SELECT r.id FROM ai_agent_runs AS r "
            "JOIN ai_sessions AS s ON s.id = r.session_id "
            "WHERE r.id = ? AND r.parent_run_id IS NULL "
            "AND r.binding_namespace = 'writing.chat.request' "
            "AND r.binding_aggregate_id = CAST(r.session_id AS TEXT)",
            [run_id],
        )
        if owned_root is None:
            continue
        if await ensure_terminal_run_conversation(db, run_id) is not None:
            materialized.append(run_id)
    return tuple(materialized)


async def materialize_terminal_writing_run_holes(db) -> tuple[str, ...]:
    """Idempotently fill every terminal Writing root lacking its Conversation."""

    rows = await db.fetch_all(
        "SELECT r.id FROM ai_agent_runs AS r "
        "JOIN ai_sessions AS s ON s.id = r.session_id "
        "WHERE r.parent_run_id IS NULL "
        "AND r.binding_namespace = 'writing.chat.request' "
        "AND r.binding_aggregate_id = CAST(r.session_id AS TEXT) "
        "AND r.status IN ('done', 'blocked', 'failed', 'canceled') "
        "AND r.conversation_id IS NULL "
        "ORDER BY r.create_time ASC, r.rowid ASC"
    )
    return await materialize_recovered_run_conversations(
        db,
        tuple(str(row["id"]) for row in rows),
    )
