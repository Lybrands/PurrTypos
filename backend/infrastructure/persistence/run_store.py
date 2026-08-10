"""SQLite persistence helpers for Agent Run state."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from purra.contracts import RunBinding, RunProvenance
    from database.connection import DatabaseConnection


TRACE_EVENT_TYPE = "agentRunTrace"


def _thaw_mapping(value) -> dict[str, Any]:
    from purra.json_values import thaw_json_mapping

    return thaw_json_mapping(value)


def new_run_id() -> str:
    return f"run_{uuid4().hex[:16]}"


async def create_run(
    db: "DatabaseConnection",
    *,
    session_id: int | None,
    prompt: str,
    mode: str | None,
    provenance: "RunProvenance | None" = None,
    binding: "RunBinding | None" = None,
    execution_owner_id: str | None = None,
    lease_expires_at_ms: int | None = None,
    heartbeat_at_ms: int | None = None,
    parent_run_id: str | None = None,
    root_run_id: str | None = None,
    delegation_id: str | None = None,
    agent_role: str | None = None,
    run_depth: int = 0,
) -> str:
    run_id = new_run_id()
    normalized_root_run_id = str(root_run_id or "").strip() or run_id
    execution_intent = (
        provenance.execution_intent if provenance is not None else None
    )
    provenance_values = (
        [
            provenance.model_provider,
            provenance.model_name,
            provenance.context_window,
            provenance.endpoint_digest,
            provenance.request_profile_digest,
            (
                execution_intent.requested_reasoning_mode
                if execution_intent is not None else None
            ),
            execution_intent.output_contract if execution_intent else None,
            (
                execution_intent.tool_protocol_contract
                if execution_intent else None
            ),
            execution_intent.recovery_policy_id if execution_intent else None,
            (
                execution_intent.capability_snapshot_digest
                if execution_intent else None
            ),
        ]
        if provenance is not None
        else [None, None, None, None, None, None, None, None, None, None]
    )
    binding_values = (
        [
            binding.namespace,
            binding.aggregate_id,
            binding.command_id,
            json.dumps(
                _thaw_mapping(binding.attributes),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ]
        if binding is not None
        else [None, None, None, None]
    )
    await db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, mode, prompt, "
        "model_provider, model_name, context_window, endpoint_digest, "
        "request_profile_digest, requested_reasoning_mode, output_contract, "
        "tool_protocol_contract, recovery_policy_id, capability_snapshot_digest, "
        "binding_namespace, binding_aggregate_id, "
        "binding_command_id, binding_attributes_json, parent_run_id, "
        "root_run_id, delegation_id, "
        "agent_role, run_depth, execution_owner_id, lease_expires_at_ms, "
        "heartbeat_at_ms, execution_attempt) "
        "VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            run_id,
            session_id,
            mode,
            prompt,
            *provenance_values,
            *binding_values,
            parent_run_id,
            normalized_root_run_id,
            delegation_id,
            agent_role,
            int(run_depth),
            execution_owner_id,
            lease_expires_at_ms,
            heartbeat_at_ms,
            1 if execution_owner_id else 0,
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
                "(run_id, step_id, title, status, executor, step_type, "
                "risk_level, description, expected_tools, agent_role, "
                "assignment_json, depends_on_json, result_summary, "
                "error, protocol_private, planning_capability, sort) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    run_id,
                    str(step.get("id") or f"step-{idx + 1}"),
                    str(step.get("title") or f"步骤 {idx + 1}"),
                    str(step.get("status") or "pending"),
                    str(step.get("executor") or "model"),
                    str(step.get("type") or "analyze"),
                    step.get("riskLevel"),
                    step.get("description"),
                    json.dumps(step.get("suggestedTools") or [], ensure_ascii=False),
                    step.get("agentRole"),
                    json.dumps(step.get("assignment") or {}, ensure_ascii=False),
                    json.dumps(step.get("dependsOn") or [], ensure_ascii=False),
                    step.get("resultSummary"),
                    step.get("error"),
                    1 if step.get("protocolPrivate") else 0,
                    step.get("planningCapability"),
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
        "request_profile_digest, requested_reasoning_mode, output_contract, "
        "tool_protocol_contract, recovery_policy_id, capability_snapshot_digest, "
        "binding_namespace, binding_aggregate_id, "
        "binding_command_id, binding_attributes_json, parent_run_id, "
        "root_run_id, delegation_id, "
        "agent_role, run_depth, execution_owner_id, lease_expires_at_ms, "
        "heartbeat_at_ms, execution_attempt, cancel_requested_at_ms, "
        "final_response, create_time, update_time "
        "FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )


async def get_latest_run_for_session(
    db: "DatabaseConnection",
    session_id: int,
) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT id, session_id, conversation_id, status, mode, prompt, "
        "model_provider, model_name, context_window, endpoint_digest, "
        "request_profile_digest, requested_reasoning_mode, output_contract, "
        "tool_protocol_contract, recovery_policy_id, capability_snapshot_digest, "
        "binding_namespace, binding_aggregate_id, "
        "binding_command_id, binding_attributes_json, parent_run_id, "
        "root_run_id, delegation_id, "
        "agent_role, run_depth, execution_owner_id, lease_expires_at_ms, "
        "heartbeat_at_ms, execution_attempt, cancel_requested_at_ms, "
        "final_response, create_time, update_time "
        "FROM ai_agent_runs WHERE session_id = ? "
        "ORDER BY create_time DESC LIMIT 1",
        [int(session_id)],
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
    return _event_rows_to_records(rows)


async def get_run_events_page(
    db: "DatabaseConnection",
    run_id: str,
    *,
    after_id: int = 0,
    limit: int = 100,
) -> tuple[list[dict[str, Any]], bool]:
    """Read one stable, forward-only page from a Run's durable event log."""

    normalized_after = int(after_id)
    normalized_limit = int(limit)
    if normalized_after < 0:
        raise ValueError("event cursor must be non-negative")
    if normalized_limit < 1 or normalized_limit > 500:
        raise ValueError("event page limit must be between 1 and 500")
    rows = await db.fetch_all(
        "SELECT id, event_type, payload_json, create_time "
        "FROM ai_agent_run_events WHERE run_id = ? AND id > ? "
        "ORDER BY id ASC LIMIT ?",
        [run_id, normalized_after, normalized_limit + 1],
    )
    has_more = len(rows) > normalized_limit
    return _event_rows_to_records(rows[:normalized_limit]), has_more


def _event_rows_to_records(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
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
        "execution_owner_id = NULL, lease_expires_at_ms = NULL, "
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
    assignment: dict[str, Any] = {}
    depends_on: list[str] = []
    try:
        parsed = json.loads(row.get("assignment_json") or "{}")
        if isinstance(parsed, dict):
            assignment = parsed
    except json.JSONDecodeError:
        assignment = {}
    try:
        parsed = json.loads(row.get("depends_on_json") or "[]")
        if isinstance(parsed, list):
            depends_on = [str(item) for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        depends_on = []

    step = {
        "id": str(row.get("step_id") or ""),
        "title": str(row.get("title") or ""),
        "status": str(row.get("status") or "pending"),
        "executor": str(row.get("executor") or "model"),
        "type": str(row.get("step_type") or "analyze"),
        "riskLevel": row.get("risk_level"),
        "description": row.get("description"),
        "suggestedTools": expected_tools,
        "agentRole": row.get("agent_role"),
        "assignment": assignment,
        "dependsOn": depends_on,
        "resultSummary": row.get("result_summary"),
        "error": row.get("error"),
        "protocolPrivate": bool(row.get("protocol_private")),
        "planningCapability": row.get("planning_capability"),
    }
    return {
        key: value
        for key, value in step.items()
        if (
            key == "suggestedTools"
            or value not in (None, "", [], {}, False)
        )
    }
