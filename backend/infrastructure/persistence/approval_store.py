"""SQLite persistence operations for one-shot Agent approvals."""

from __future__ import annotations

from time import time
from typing import Any

from agent_core.contracts import ApprovalStatus
from agent_core.events import CoreEventType
from infrastructure.persistence import run_store


PENDING_STATUS = "pending"


def now_ms() -> int:
    return int(time() * 1_000)


async def create_approval(
    db,
    *,
    approval_id: str,
    run_id: str,
    tool_call_id: str,
    tool_name: str,
    title: str,
    risk_level: str,
    summary: str,
    expires_at_ms: int,
) -> None:
    await db.execute(
        "INSERT INTO ai_agent_approvals "
        "(id, run_id, tool_call_id, tool_name, title, risk_level, summary, "
        "status, expires_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        [
            approval_id,
            run_id,
            tool_call_id,
            tool_name,
            title,
            risk_level,
            summary,
            int(expires_at_ms),
        ],
    )


async def get_approval(db, approval_id: str) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT id, run_id, tool_call_id, tool_name, title, risk_level, "
        "summary, status, expires_at_ms, resolved_at_ms, create_time, "
        "update_time FROM ai_agent_approvals WHERE id = ?",
        [approval_id],
    )


async def transition_pending(
    db,
    *,
    approval_id: str,
    run_id: str,
    status: ApprovalStatus,
    require_unexpired: bool = False,
    timestamp_ms: int | None = None,
) -> bool:
    normalized = ApprovalStatus(status)
    resolved_ms = now_ms() if timestamp_ms is None else int(timestamp_ms)
    expiration_guard = " AND expires_at_ms > ?" if require_unexpired else ""
    params: list[Any] = [normalized.value, resolved_ms, approval_id, run_id]
    if require_unexpired:
        params.append(resolved_ms)
    async with db.transaction():
        await db.execute(
            "UPDATE ai_agent_approvals SET status = ?, resolved_at_ms = ?, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? AND run_id = ? "
            "AND status = 'pending'" + expiration_guard,
            params,
        )
        changed = await db.fetch_one("SELECT changes() AS count")
    return int((changed or {}).get("count") or 0) == 1


async def recover_pending_approvals(db) -> int:
    """Fail closed after restart and terminalize Runs abandoned mid-approval."""

    rows = await db.fetch_all(
        "SELECT id, run_id FROM ai_agent_approvals WHERE status = 'pending'"
    )
    if not rows:
        return 0
    recovered_at = now_ms()
    async with db.transaction():
        for row in rows:
            await transition_pending(
                db,
                approval_id=str(row["id"]),
                run_id=str(row["run_id"]),
                status=ApprovalStatus.UNAVAILABLE,
                timestamp_ms=recovered_at,
            )
        run_ids = sorted({str(row["run_id"]) for row in rows})
        for run_id in run_ids:
            run = await run_store.get_run(db, run_id)
            if run is None or run.get("status") != "running":
                continue
            await run_store.cancel_run(db, run_id)
            await run_store.append_event(
                db,
                run_id,
                CoreEventType.RUN_CANCELED,
                {"status": "canceled", "reason": "approval_recovery_after_restart"},
            )
    return len(rows)
