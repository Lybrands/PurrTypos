"""SQLite persistence helpers for Agent Run state."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from agent_core.contracts import RunProvenance
    from database.connection import DatabaseConnection


TRACE_EVENT_TYPE = "agentRunTrace"


def new_run_id() -> str:
    return f"run_{uuid4().hex[:16]}"


async def create_run(
    db: "DatabaseConnection",
    *,
    session_id: int | None,
    prompt: str,
    mode: str | None,
    provenance: "RunProvenance | None" = None,
) -> str:
    run_id = new_run_id()
    provenance_values = (
        [
            provenance.model_provider,
            provenance.model_name,
            provenance.context_window,
            provenance.endpoint_digest,
            provenance.request_profile_digest,
        ]
        if provenance is not None
        else [None, None, None, None, None]
    )
    await db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, mode, prompt, "
        "model_provider, model_name, context_window, endpoint_digest, "
        "request_profile_digest) "
        "VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)",
        [
            run_id,
            session_id,
            mode,
            prompt,
            *provenance_values,
        ],
    )
    return run_id


async def set_run_conversation_id(
    db: "DatabaseConnection",
    run_id: str,
    conversation_id: int,
) -> None:
    await db.execute(
        "UPDATE ai_agent_runs SET conversation_id = ?, update_time = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        [conversation_id, run_id],
    )


async def upsert_todos(
    db: "DatabaseConnection",
    run_id: str,
    steps: list[dict[str, Any]],
) -> None:
    async with db.transaction():
        await db.execute("DELETE FROM ai_agent_run_todos WHERE run_id = ?", [run_id])
        for idx, step in enumerate(steps):
            await db.execute(
                "INSERT INTO ai_agent_run_todos "
                "(run_id, step_id, title, status, executor, expected_tools, "
                "result_summary, error, sort) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    run_id,
                    str(step.get("id") or f"step-{idx + 1}"),
                    str(step.get("title") or f"步骤 {idx + 1}"),
                    str(step.get("status") or "pending"),
                    str(step.get("executor") or "model"),
                    json.dumps(step.get("suggestedTools") or [], ensure_ascii=False),
                    step.get("resultSummary"),
                    step.get("error"),
                    idx,
                ],
            )


async def get_run_todos(
    db: "DatabaseConnection",
    run_id: str,
) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM ai_agent_run_todos WHERE run_id = ? ORDER BY sort ASC, id ASC",
        [run_id],
    )
    return [_todo_row_to_step(row) for row in rows]


async def append_event(
    db: "DatabaseConnection",
    run_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    await db.execute(
        "INSERT INTO ai_agent_run_events (run_id, event_type, payload_json) VALUES (?, ?, ?)",
        [run_id, event_type, json.dumps(payload or {}, ensure_ascii=False)],
    )


async def append_trace(
    db: "DatabaseConnection",
    run_id: str,
    *,
    stage: str,
    outcome: str,
    details: dict[str, Any] | None = None,
    duration_ms: int | None = None,
) -> None:
    """Persist compact, non-content runtime diagnostics for an Agent Run."""
    payload: dict[str, Any] = {
        "stage": str(stage),
        "outcome": str(outcome),
    }
    if duration_ms is not None:
        payload["durationMs"] = max(0, int(duration_ms))
    if details:
        payload["details"] = dict(details)
    await append_event(db, run_id, TRACE_EVENT_TYPE, payload)


async def get_run(
    db: "DatabaseConnection",
    run_id: str,
) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT id, session_id, conversation_id, status, mode, prompt, "
        "model_provider, model_name, context_window, endpoint_digest, "
        "request_profile_digest, final_response, create_time, update_time "
        "FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )


async def get_run_events(
    db: "DatabaseConnection",
    run_id: str,
) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, event_type, payload_json, create_time "
        "FROM ai_agent_run_events WHERE run_id = ? ORDER BY id ASC",
        [run_id],
    )
    events: list[dict[str, Any]] = []
    for row in rows:
        payload: dict[str, Any] = {}
        try:
            value = json.loads(row.get("payload_json") or "{}")
            if isinstance(value, dict):
                payload = value
        except (TypeError, json.JSONDecodeError):
            pass
        events.append({
            "id": row.get("id"),
            "eventType": row.get("event_type"),
            "payload": payload,
            "createTime": row.get("create_time"),
        })
    return events


async def update_todo_status(
    db: "DatabaseConnection",
    run_id: str,
    step_id: str,
    status: str,
    *,
    result_summary: str | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    await db.execute(
        "UPDATE ai_agent_run_todos SET status = ?, "
        "result_summary = COALESCE(?, result_summary), "
        "error = COALESCE(?, error), update_time = CURRENT_TIMESTAMP "
        "WHERE run_id = ? AND step_id = ?",
        [status, result_summary, error, run_id, step_id],
    )
    row = await db.fetch_one(
        "SELECT * FROM ai_agent_run_todos WHERE run_id = ? AND step_id = ?",
        [run_id, step_id],
    )
    return _todo_row_to_step(row) if row else None


async def update_run_status(
    db: "DatabaseConnection",
    run_id: str,
    status: str,
    *,
    final_response: str | None = None,
) -> None:
    await db.execute(
        "UPDATE ai_agent_runs SET status = ?, "
        "final_response = COALESCE(?, final_response), "
        "update_time = CURRENT_TIMESTAMP WHERE id = ?",
        [status, final_response, run_id],
    )


async def complete_run(
    db: "DatabaseConnection",
    run_id: str,
    *,
    final_response: str = "",
) -> None:
    await update_run_status(db, run_id, "done", final_response=final_response)


async def fail_run(
    db: "DatabaseConnection",
    run_id: str,
    *,
    error: str,
) -> None:
    await update_run_status(db, run_id, "failed", final_response=error)


async def block_run(
    db: "DatabaseConnection",
    run_id: str,
) -> None:
    await update_run_status(db, run_id, "blocked")


async def cancel_run(
    db: "DatabaseConnection",
    run_id: str,
) -> None:
    await update_run_status(db, run_id, "canceled")


def _todo_row_to_step(row: dict[str, Any]) -> dict[str, Any]:
    expected_tools = []
    try:
        parsed = json.loads(row.get("expected_tools") or "[]")
        if isinstance(parsed, list):
            expected_tools = [str(x) for x in parsed if str(x).strip()]
    except json.JSONDecodeError:
        expected_tools = []

    step = {
        "id": str(row.get("step_id") or ""),
        "title": str(row.get("title") or ""),
        "status": str(row.get("status") or "pending"),
        "executor": str(row.get("executor") or "model"),
        "suggestedTools": expected_tools,
        "resultSummary": row.get("result_summary"),
        "error": row.get("error"),
    }
    return {k: v for k, v in step.items() if v not in (None, "", [])}
