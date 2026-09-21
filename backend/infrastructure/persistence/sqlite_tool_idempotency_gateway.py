"""SQLite transaction boundary for replay-safe side-effecting tool calls."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable

from purra.cancellation import OperationCanceled
from purra.contracts import (
    DomainEffect,
    ToolCall,
    ToolEffectState,
    ToolHandlerResult,
    ToolPlanningDisposition,
    ToolStepDisposition,
)
from purra.errors import ContractViolationError
from purra.json_values import thaw_json_mapping
from infrastructure.persistence.run_execution_store import now_ms


class SqliteToolIdempotencyGateway:
    def __init__(self, db, *, owner_id: str) -> None:
        self._db = db
        self._owner_id = str(owner_id or "").strip()
        if not self._owner_id:
            raise ValueError("owner id is required")

    async def execute_once(
        self,
        run_id: str,
        tool_call: ToolCall,
        operation: Callable[[], Awaitable[ToolHandlerResult]],
    ) -> ToolHandlerResult:
        normalized_run = str(run_id or "").strip()
        if not normalized_run:
            raise ContractViolationError("idempotent tool execution requires a run id")
        digest = _arguments_digest(tool_call)
        async with self._db.transaction(cancellation_linearizable=True):
            execution = await self._db.fetch_one(
                "SELECT status, execution_owner_id, lease_expires_at_ms, "
                "cancel_requested_at_ms FROM ai_agent_runs WHERE id = ?",
                [normalized_run],
            )
            if execution is None:
                raise ContractViolationError("tool execution run does not exist")
            if execution.get("cancel_requested_at_ms") is not None:
                raise OperationCanceled()
            if (
                execution.get("status") != "running"
                or execution.get("execution_owner_id") != self._owner_id
                or int(execution.get("lease_expires_at_ms") or 0) <= now_ms()
            ):
                raise ContractViolationError("run execution lease is not active")

            existing = await self._db.fetch_one(
                "SELECT tool_name, arguments_digest, content, effects_json, "
                "error_code, step_disposition, planning_disposition, "
                "effect_state "
                "FROM ai_agent_tool_receipts "
                "WHERE run_id = ? AND tool_call_id = ?",
                [normalized_run, tool_call.id],
            )
            if existing is not None:
                if (
                    existing.get("tool_name") != tool_call.name
                    or existing.get("arguments_digest") != digest
                ):
                    raise ContractViolationError(
                        "tool call id was reused with different input"
                    )
                return _stored_result(existing)

            result = await operation()
            if not isinstance(result, ToolHandlerResult):
                return result
            await self._db.execute(
                "INSERT INTO ai_agent_tool_receipts "
                "(run_id, tool_call_id, tool_name, arguments_digest, content, "
                "effects_json, error_code, step_disposition, "
                "planning_disposition, effect_state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    normalized_run,
                    tool_call.id,
                    tool_call.name,
                    digest,
                    result.content,
                    json.dumps([
                        {
                            "type": effect.type,
                            "payload": thaw_json_mapping(effect.payload),
                        }
                        for effect in result.effects
                    ], ensure_ascii=False),
                    result.error_code,
                    result.step_disposition.value,
                    result.planning_disposition.value,
                    result.effect_state.value,
                ],
            )
            return result


def _arguments_digest(tool_call: ToolCall) -> str:
    value = f"{tool_call.name}\0{tool_call.arguments_json}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _stored_result(row: dict) -> ToolHandlerResult:
    effects = []
    try:
        raw_effects = json.loads(row.get("effects_json") or "[]")
        if isinstance(raw_effects, list):
            for item in raw_effects:
                if isinstance(item, dict):
                    effects.append(DomainEffect(
                        type=item.get("type"),
                        payload=item.get("payload") or {},
                    ))
    except (TypeError, ValueError, json.JSONDecodeError):
        effects = []
    return ToolHandlerResult(
        content=str(row.get("content") or ""),
        from_cache=True,
        effects=tuple(effects),
        error_code=row.get("error_code"),
        step_disposition=ToolStepDisposition(
            str(row.get("step_disposition") or "complete")
        ),
        planning_disposition=ToolPlanningDisposition(
            str(row.get("planning_disposition") or "keep_plan")
        ),
        effect_state=ToolEffectState(
            str(row.get("effect_state") or "unknown")
        ),
    )
