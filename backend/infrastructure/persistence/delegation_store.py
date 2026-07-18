"""SQLite state machine for parent-to-child Agent delegations."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from agent_core.contracts import AgentRunResult, RunLineage, RunStatus
from agent_core.events import CoreEventType
from infrastructure.persistence import run_execution_store, run_store


ACTIVE_STATUSES = ("claimed", "running")
TERMINAL_STATUSES = ("done", "failed", "canceled")


def new_delegation_id() -> str:
    return f"delegation_{uuid4().hex[:16]}"


async def create_delegation(
    db,
    *,
    parent_run_id: str,
    agent_role: str,
    objective: str,
    input_payload: dict[str, Any] | None = None,
    required: bool = True,
    priority: int = 0,
    max_depth: int = 3,
) -> dict[str, Any]:
    normalized_parent = _required_text(parent_run_id, "parent run id")
    normalized_role = _required_text(agent_role, "agent role")
    normalized_objective = _required_text(objective, "delegation objective")
    async with db.transaction():
        parent = await run_store.get_run(db, normalized_parent)
        if parent is None:
            raise ValueError("parent run does not exist")
        if parent.get("status") != RunStatus.RUNNING.value:
            raise ValueError("parent run is not active")
        if parent.get("cancel_requested_at_ms") is not None:
            raise ValueError("parent run cancellation has been requested")
        child_depth = int(parent.get("run_depth") or 0) + 1
        if child_depth > int(max_depth):
            raise ValueError("delegation depth limit exceeded")
        root_run_id = str(parent.get("root_run_id") or normalized_parent)
        delegation_id = new_delegation_id()
        await db.execute(
            "INSERT INTO ai_agent_delegations "
            "(id, parent_run_id, root_run_id, agent_role, objective, input_json, "
            "required, priority) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                delegation_id,
                normalized_parent,
                root_run_id,
                normalized_role,
                normalized_objective,
                json.dumps(input_payload or {}, ensure_ascii=False),
                1 if required else 0,
                int(priority),
            ],
        )
        await run_store.append_event(
            db,
            normalized_parent,
            CoreEventType.DELEGATION_CREATED,
            {
                "delegationId": delegation_id,
                "agentRole": normalized_role,
                "required": bool(required),
            },
        )
        row = await get_delegation(db, delegation_id)
    assert row is not None
    row["child_depth"] = child_depth
    return row


async def get_delegation(db, delegation_id: str) -> dict[str, Any] | None:
    normalized = _required_text(delegation_id, "delegation id")
    return await db.fetch_one(
        "SELECT * FROM ai_agent_delegations WHERE id = ?",
        [normalized],
    )


async def list_delegations(db, parent_run_id: str) -> list[dict[str, Any]]:
    normalized = _required_text(parent_run_id, "parent run id")
    return await db.fetch_all(
        "SELECT * FROM ai_agent_delegations WHERE parent_run_id = ? "
        "ORDER BY priority DESC, create_time ASC, id ASC",
        [normalized],
    )


async def claim_next(
    db,
    *,
    parent_run_id: str,
    worker_id: str,
    max_parallel_children: int,
    agent_role: str | None = None,
    claim_lease_duration_ms: int = 30_000,
    timestamp_ms: int | None = None,
) -> dict[str, Any] | None:
    normalized_parent = _required_text(parent_run_id, "parent run id")
    normalized_worker = _required_text(worker_id, "worker id")
    limit = int(max_parallel_children)
    if limit <= 0:
        raise ValueError("max parallel children must be positive")
    normalized_role = str(agent_role or "").strip()
    claimed_at = (
        run_execution_store.now_ms()
        if timestamp_ms is None
        else int(timestamp_ms)
    )
    claim_duration = int(claim_lease_duration_ms)
    if claim_duration <= 0:
        raise ValueError("claim lease duration must be positive")
    async with db.transaction():
        parent = await run_store.get_run(db, normalized_parent)
        if (
            parent is None
            or parent.get("status") != RunStatus.RUNNING.value
            or parent.get("cancel_requested_at_ms") is not None
        ):
            return None
        active = await db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_delegations "
            "WHERE parent_run_id = ? AND status IN ('claimed', 'running')",
            [normalized_parent],
        )
        if int((active or {}).get("count") or 0) >= limit:
            return None
        role_clause = " AND agent_role = ?" if normalized_role else ""
        params: list[Any] = [normalized_parent]
        if normalized_role:
            params.append(normalized_role)
        candidate = await db.fetch_one(
            "SELECT id FROM ai_agent_delegations WHERE parent_run_id = ? "
            "AND status = 'queued'" + role_clause + " "
            "ORDER BY priority DESC, create_time ASC, id ASC LIMIT 1",
            params,
        )
        if candidate is None:
            return None
        delegation_id = str(candidate["id"])
        await db.execute(
            "UPDATE ai_agent_delegations SET status = 'claimed', worker_id = ?, "
            "claim_expires_at_ms = ?, claim_attempt = claim_attempt + 1, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? AND status = 'queued'",
            [normalized_worker, claimed_at + claim_duration, delegation_id],
        )
        changed = await db.fetch_one("SELECT changes() AS count")
        if int((changed or {}).get("count") or 0) != 1:
            return None
        await run_store.append_event(
            db,
            normalized_parent,
            CoreEventType.DELEGATION_CLAIMED,
            {"delegationId": delegation_id},
        )
        claimed = await get_delegation(db, delegation_id)
    return claimed


async def attach_child_run(
    db,
    *,
    delegation_id: str,
    child_run_id: str,
    worker_id: str,
) -> bool:
    normalized_delegation = _required_text(delegation_id, "delegation id")
    normalized_child = _required_text(child_run_id, "child run id")
    normalized_worker = _required_text(worker_id, "worker id")
    delegation = await get_delegation(db, normalized_delegation)
    child = await run_store.get_run(db, normalized_child)
    if delegation is None or child is None:
        return False
    if (
        child.get("parent_run_id") != delegation.get("parent_run_id")
        or child.get("root_run_id") != delegation.get("root_run_id")
        or child.get("delegation_id") != normalized_delegation
        or child.get("agent_role") != delegation.get("agent_role")
    ):
        return False
    await db.execute(
        "UPDATE ai_agent_delegations SET child_run_id = ?, status = 'running', "
        "claim_expires_at_ms = NULL, "
        "update_time = CURRENT_TIMESTAMP WHERE id = ? AND status = 'claimed' "
        "AND worker_id = ? AND child_run_id IS NULL",
        [normalized_child, normalized_delegation, normalized_worker],
    )
    changed = await db.fetch_one("SELECT changes() AS count")
    return int((changed or {}).get("count") or 0) == 1


async def record_result(
    db,
    *,
    delegation_id: str,
    child_run_id: str,
    result: AgentRunResult,
) -> bool:
    normalized_delegation = _required_text(delegation_id, "delegation id")
    normalized_child = _required_text(child_run_id, "child run id")
    if result.run_id != normalized_child:
        raise ValueError("child result run id does not match delegation")
    status, event_type = _delegation_terminal(result.status)
    async with db.transaction():
        row = await get_delegation(db, normalized_delegation)
        if row is None or row.get("child_run_id") != normalized_child:
            return False
        if row.get("status") != "running":
            return False
        child = await run_store.get_run(db, normalized_child)
        if (
            child is None
            or child.get("delegation_id") != normalized_delegation
            or child.get("status") != result.status.value
        ):
            return False
        await db.execute(
            "UPDATE ai_agent_delegations SET status = ?, result_summary = ?, "
            "error = ?, claim_expires_at_ms = NULL, update_time = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'running'",
            [
                status,
                result.final_response or None,
                result.error,
                normalized_delegation,
            ],
        )
        await run_store.append_event(
            db,
            str(row["parent_run_id"]),
            event_type,
            {
                "delegationId": normalized_delegation,
                "childRunId": normalized_child,
                "status": status,
            },
        )
    return True


async def fail_claim(
    db,
    *,
    delegation_id: str,
    worker_id: str,
    error: str,
) -> bool:
    normalized_delegation = _required_text(delegation_id, "delegation id")
    normalized_worker = _required_text(worker_id, "worker id")
    normalized_error = _required_text(error, "delegation error")
    async with db.transaction():
        row = await get_delegation(db, normalized_delegation)
        if (
            row is None
            or row.get("status") not in {"claimed", "running"}
            or row.get("worker_id") != normalized_worker
        ):
            return False
        child_run_id = str(row.get("child_run_id") or "").strip()
        if child_run_id:
            await run_execution_store.request_cancellation(db, child_run_id)
        await db.execute(
            "UPDATE ai_agent_delegations SET status = 'failed', error = ?, "
            "claim_expires_at_ms = NULL, update_time = CURRENT_TIMESTAMP WHERE id = ? "
            "AND status IN ('claimed', 'running')",
            [normalized_error, normalized_delegation],
        )
        await run_store.append_event(
            db,
            str(row["parent_run_id"]),
            CoreEventType.DELEGATION_FAILED,
            {
                "delegationId": normalized_delegation,
                "status": "failed",
                "reason": "child_execution_failed",
                "childRunId": child_run_id or None,
            },
        )
    return True


async def cancel_children(db, parent_run_id: str) -> int:
    normalized_parent = _required_text(parent_run_id, "parent run id")
    rows = await list_delegations(db, normalized_parent)
    canceled = 0
    async with db.transaction():
        for row in rows:
            if row.get("status") not in {"queued", "claimed", "running"}:
                continue
            child_run_id = str(row.get("child_run_id") or "").strip()
            if child_run_id:
                await run_execution_store.request_cancellation(db, child_run_id)
            await db.execute(
                "UPDATE ai_agent_delegations SET status = 'canceled', "
                "error = 'parent_canceled', claim_expires_at_ms = NULL, "
                "update_time = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status IN ('queued', 'claimed', 'running')",
                [row["id"]],
            )
            changed = await db.fetch_one("SELECT changes() AS count")
            if int((changed or {}).get("count") or 0) != 1:
                continue
            canceled += 1
            await run_store.append_event(
                db,
                normalized_parent,
                CoreEventType.DELEGATION_CANCELED,
                {"delegationId": row["id"], "childRunId": child_run_id or None},
            )
    return canceled


async def recover_delegations(
    db,
    *,
    timestamp_ms: int | None = None,
) -> dict[str, int]:
    """Requeue abandoned claims and mirror already-terminal child Runs."""

    recovered_at = (
        run_execution_store.now_ms()
        if timestamp_ms is None
        else int(timestamp_ms)
    )
    requeued = 0
    reconciled = 0
    async with db.transaction():
        expired = await db.fetch_all(
            "SELECT id FROM ai_agent_delegations WHERE status = 'claimed' "
            "AND (claim_expires_at_ms IS NULL OR claim_expires_at_ms <= ?)",
            [recovered_at],
        )
        for row in expired:
            await db.execute(
                "UPDATE ai_agent_delegations SET status = 'queued', worker_id = NULL, "
                "claim_expires_at_ms = NULL, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'claimed' "
                "AND (claim_expires_at_ms IS NULL OR claim_expires_at_ms <= ?)",
                [row["id"], recovered_at],
            )
            changed = await db.fetch_one("SELECT changes() AS count")
            requeued += int((changed or {}).get("count") or 0)

        terminal_children = await db.fetch_all(
            "SELECT d.id AS delegation_id, d.child_run_id, r.status, "
            "r.final_response FROM ai_agent_delegations d "
            "JOIN ai_agent_runs r ON r.id = d.child_run_id "
            "WHERE d.status = 'running' AND r.status != 'running'"
        )
        for row in terminal_children:
            child_status = RunStatus(str(row["status"]))
            result = AgentRunResult(
                run_id=str(row["child_run_id"]),
                status=child_status,
                final_response=(
                    str(row.get("final_response") or "")
                    if child_status is RunStatus.DONE
                    else ""
                ),
                error=(
                    str(row.get("final_response") or child_status.value)
                    if child_status is RunStatus.FAILED
                    else None
                ),
            )
            if await record_result(
                db,
                delegation_id=str(row["delegation_id"]),
                child_run_id=str(row["child_run_id"]),
                result=result,
            ):
                reconciled += 1
    return {"requeued": requeued, "reconciled": reconciled}


def lineage_for_claim(claimed: dict[str, Any], *, parent_depth: int) -> RunLineage:
    return RunLineage(
        parent_run_id=str(claimed["parent_run_id"]),
        root_run_id=str(claimed["root_run_id"]),
        delegation_id=str(claimed["id"]),
        agent_role=str(claimed["agent_role"]),
        depth=int(parent_depth) + 1,
    )


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {status: 0 for status in ("queued", "claimed", "running", *TERMINAL_STATUSES)}
    required_failures = []
    results = []
    for row in rows:
        status = str(row.get("status") or "")
        counts[status] = counts.get(status, 0) + 1
        if bool(row.get("required")) and status in {"failed", "canceled"}:
            required_failures.append(str(row.get("id")))
        if status == "done":
            results.append({
                "delegationId": row.get("id"),
                "agentRole": row.get("agent_role"),
                "childRunId": row.get("child_run_id"),
                "summary": row.get("result_summary") or "",
            })
    pending = sum(counts.get(status, 0) for status in ("queued", "claimed", "running"))
    state = "pending" if pending else ("blocked" if required_failures else "ready")
    return {
        "state": state,
        "counts": counts,
        "requiredFailures": required_failures,
        "results": results,
    }


def _delegation_terminal(status: RunStatus) -> tuple[str, CoreEventType]:
    normalized = RunStatus(status)
    if normalized is RunStatus.DONE:
        return "done", CoreEventType.DELEGATION_COMPLETED
    if normalized is RunStatus.CANCELED:
        return "canceled", CoreEventType.DELEGATION_CANCELED
    return "failed", CoreEventType.DELEGATION_FAILED


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized
