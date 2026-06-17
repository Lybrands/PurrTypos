"""Persistence helpers for Agent Run state."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


def new_run_id() -> str:
    return f"run_{uuid4().hex[:16]}"


async def create_run(
    db: "DatabaseConnection",
    *,
    session_id: int | None,
    prompt: str,
    mode: str | None,
) -> str:
    run_id = new_run_id()
    await db.execute(
        "INSERT INTO ai_agent_runs (id, session_id, status, mode, prompt) "
        "VALUES (?, ?, 'running', ?, ?)",
        [run_id, session_id, mode, prompt],
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
