"""SQLite persistence helpers for Agent Run state."""

from __future__ import annotations

import json
from dataclasses import fields
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


def runtime_limits_from_mapping(value):
    from purra.contracts import RuntimeLimits
    from purra.errors import ContractViolationError

    try:
        return RuntimeLimits(**value)
    except (TypeError, ValueError) as error:
        raise ContractViolationError(
            "Run runtime limits do not match the current PurrA contract",
            code="runtime_limits_invalid",
        ) from error


async def create_run(
    db: "DatabaseConnection",
    *,
    run_id: str | None = None,
    session_id: int | None,
    prompt: str,
    mode: str | None,
    provenance: "RunProvenance | None" = None,
    binding: "RunBinding | None" = None,
    execution_owner_id: str | None = None,
    lease_expires_at_ms: int | None = None,
    heartbeat_at_ms: int | None = None,
    deadline_at_ms: int | None = None,
    runtime_limits=None,
    agent_preset_snapshot=None,
    root_run_id: str | None = None,
    agent_id: str | None = None,
    parent_run_id: str | None = None,
    agent_tree_lease_owner_id: str | None = None,
    agent_tree_lease_epoch: int | None = None,
    requested_user_max_generation_tokens: int | None = None,
    result_capacity_target_tokens: int | None = None,
    selected_context_window_tokens: int | None = None,
) -> str:
    if runtime_limits is None:
        from purra.contracts import RuntimeLimits

        runtime_limits = RuntimeLimits(max_run_generation_tokens=None)
    runtime_limits_json = json.dumps(
        {
            item.name: getattr(runtime_limits, item.name)
            for item in fields(runtime_limits)
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    preset_snapshot_json = json.dumps(
        _thaw_mapping(agent_preset_snapshot or {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    run_id = str(run_id or "").strip() or new_run_id()
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
            (
                json.dumps(
                    _thaw_mapping(provenance.capability_snapshot),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if provenance.capability_snapshot else None
            ),
        ]
        if provenance is not None
        else [None, None, None, None, None, None, None, None, None, None, None]
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
        "capability_snapshot_json, requested_user_max_generation_tokens, "
        "result_capacity_target_tokens, selected_context_window_tokens, "
        "binding_namespace, binding_aggregate_id, "
        "binding_command_id, binding_attributes_json, "
        "root_run_id, agent_id, parent_run_id, "
        "agent_tree_lease_owner_id, agent_tree_lease_epoch, "
        "execution_owner_id, lease_expires_at_ms, "
        "heartbeat_at_ms, execution_attempt, deadline_at_ms, "
        "runtime_limits_json, agent_preset_snapshot_json) "
        "VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                run_id,
                session_id,
                mode,
                prompt,
                *provenance_values,
                requested_user_max_generation_tokens,
                result_capacity_target_tokens,
                selected_context_window_tokens,
                *binding_values,
                root_run_id or run_id,
                agent_id or run_id,
                parent_run_id,
                agent_tree_lease_owner_id,
                agent_tree_lease_epoch,
                execution_owner_id,
                lease_expires_at_ms,
                heartbeat_at_ms,
                1 if execution_owner_id else 0,
                deadline_at_ms,
                runtime_limits_json,
                preset_snapshot_json,
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
                "risk_level, description, expected_tools, "
                "assignment_json, depends_on_json, result_summary, "
                "error, protocol_private, planning_capability, sort) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        "root_run_id, parent_run_id, agent_id, "
        "model_provider, model_name, context_window, endpoint_digest, "
        "request_profile_digest, requested_reasoning_mode, output_contract, "
        "tool_protocol_contract, recovery_policy_id, capability_snapshot_digest, "
        "capability_snapshot_json, requested_user_max_generation_tokens, "
        "result_capacity_target_tokens, selected_context_window_tokens, "
        "binding_namespace, binding_aggregate_id, "
        "binding_command_id, binding_attributes_json, "
        "execution_owner_id, lease_expires_at_ms, "
        "heartbeat_at_ms, execution_attempt, cancel_requested_at_ms, "
        "cancellation_epoch, "
        "model_attempt_count, unreported_usage_attempts, "
        "unreported_reasoning_attempts, input_tokens, "
        "output_tokens, reasoning_tokens, provider_output_events, "
        "provider_output_bytes, "
        "plan_title, plan_goal, task_spec_json, work_step_ids_json, "
        "execution_checkpoint_json, error, agent_preset_snapshot_json, "
        "final_response, create_time, update_time "
        "FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )


# 会话归属的 Run 集合必须只含根 Run：子 Run（子 Agent）会继承 session_id
# 用于归属，但永远不会物化成对话行；按 session 计数/比对的读路径若把
# 子 Run 算进去，会话一旦用过委派就永远校验不过（发送 409 等）。
ROOT_RUN_SESSION_FILTER = (
    "parent_run_id IS NULL AND (root_run_id IS NULL OR root_run_id = id)"
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
        "capability_snapshot_json, requested_user_max_generation_tokens, "
        "result_capacity_target_tokens, selected_context_window_tokens, "
        "binding_namespace, binding_aggregate_id, "
        "binding_command_id, binding_attributes_json, "
        "execution_owner_id, lease_expires_at_ms, "
        "heartbeat_at_ms, execution_attempt, cancel_requested_at_ms, "
        "final_response, create_time, update_time "
        f"FROM ai_agent_runs WHERE session_id = ? "
        f"AND {ROOT_RUN_SESSION_FILTER} "
        "ORDER BY create_time DESC, rowid DESC LIMIT 1",
        [int(session_id)],
    )


async def get_run_for_session_request(
    db: "DatabaseConnection",
    session_id: int,
    request_id: str,
) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT id, session_id, conversation_id, status, mode, prompt, "
        "model_provider, model_name, context_window, endpoint_digest, "
        "request_profile_digest, requested_reasoning_mode, output_contract, "
        "tool_protocol_contract, recovery_policy_id, capability_snapshot_digest, "
        "capability_snapshot_json, requested_user_max_generation_tokens, "
        "result_capacity_target_tokens, selected_context_window_tokens, "
        "binding_namespace, binding_aggregate_id, "
        "binding_command_id, binding_attributes_json, "
        "execution_owner_id, lease_expires_at_ms, "
        "heartbeat_at_ms, execution_attempt, cancel_requested_at_ms, "
        "final_response, create_time, update_time "
        "FROM ai_agent_runs WHERE session_id = ? "
        "AND parent_run_id IS NULL AND (root_run_id IS NULL OR root_run_id = id) "
        "AND binding_namespace = 'writing.chat.request' "
        "AND binding_aggregate_id = ? AND binding_command_id = ? "
        "ORDER BY rowid DESC LIMIT 1",
        [int(session_id), str(session_id), str(request_id)],
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
    error: str | None = None,
) -> None:
    await db.execute(
        "UPDATE ai_agent_runs SET status = ?, "
        "final_response = COALESCE(?, final_response), "
        "error = COALESCE(?, error), "
        "execution_owner_id = NULL, lease_expires_at_ms = NULL, "
        "update_time = CURRENT_TIMESTAMP WHERE id = ?",
        [status, final_response, error, run_id],
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
    await update_run_status(
        db,
        run_id,
        "failed",
        final_response=error,
        error=error,
    )


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
