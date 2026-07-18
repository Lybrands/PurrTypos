"""Atomic execution ownership and cancellation operations for Agent Runs."""

from __future__ import annotations

from time import time
from typing import Any

from agent_core.contracts import RunExecutionLease


def now_ms() -> int:
    return int(time() * 1_000)


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _lease_deadline(timestamp_ms: int, lease_duration_ms: int) -> int:
    duration = int(lease_duration_ms)
    if duration <= 0:
        raise ValueError("lease duration must be positive")
    return int(timestamp_ms) + duration


async def claim_run(
    db,
    *,
    run_id: str,
    owner_id: str,
    lease_duration_ms: int,
    timestamp_ms: int | None = None,
) -> bool:
    """Claim an unowned/expired running Run with a compare-and-set update."""

    normalized_run = _required_text(run_id, "run id")
    normalized_owner = _required_text(owner_id, "owner id")
    claimed_at = now_ms() if timestamp_ms is None else int(timestamp_ms)
    deadline = _lease_deadline(claimed_at, lease_duration_ms)
    async with db.transaction():
        await db.execute(
            "UPDATE ai_agent_runs SET execution_owner_id = ?, "
            "heartbeat_at_ms = ?, lease_expires_at_ms = ?, "
            "execution_attempt = execution_attempt + 1, "
            "update_time = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'running' "
            "AND cancel_requested_at_ms IS NULL "
            "AND (execution_owner_id IS NULL OR lease_expires_at_ms IS NULL "
            "OR lease_expires_at_ms <= ?)",
            [
                normalized_owner,
                claimed_at,
                deadline,
                normalized_run,
                claimed_at,
            ],
        )
        changed = await db.fetch_one("SELECT changes() AS count")
    return int((changed or {}).get("count") or 0) == 1


async def renew_lease(
    db,
    *,
    run_id: str,
    owner_id: str,
    lease_duration_ms: int,
    timestamp_ms: int | None = None,
) -> bool:
    """Renew only a still-live lease owned by the caller."""

    normalized_run = _required_text(run_id, "run id")
    normalized_owner = _required_text(owner_id, "owner id")
    heartbeat_at = now_ms() if timestamp_ms is None else int(timestamp_ms)
    deadline = _lease_deadline(heartbeat_at, lease_duration_ms)
    async with db.transaction():
        await db.execute(
            "UPDATE ai_agent_runs SET heartbeat_at_ms = ?, "
            "lease_expires_at_ms = ?, update_time = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'running' AND execution_owner_id = ? "
            "AND lease_expires_at_ms > ?",
            [
                heartbeat_at,
                deadline,
                normalized_run,
                normalized_owner,
                heartbeat_at,
            ],
        )
        changed = await db.fetch_one("SELECT changes() AS count")
    return int((changed or {}).get("count") or 0) == 1


async def release_lease(db, *, run_id: str, owner_id: str) -> bool:
    normalized_run = _required_text(run_id, "run id")
    normalized_owner = _required_text(owner_id, "owner id")
    async with db.transaction():
        await db.execute(
            "UPDATE ai_agent_runs SET execution_owner_id = NULL, "
            "lease_expires_at_ms = NULL, update_time = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'running' AND execution_owner_id = ?",
            [normalized_run, normalized_owner],
        )
        changed = await db.fetch_one("SELECT changes() AS count")
    return int((changed or {}).get("count") or 0) == 1


async def request_cancellation(
    db,
    run_id: str,
    *,
    timestamp_ms: int | None = None,
) -> bool:
    normalized_run = _required_text(run_id, "run id")
    requested_at = now_ms() if timestamp_ms is None else int(timestamp_ms)
    async with db.transaction():
        await db.execute(
            "UPDATE ai_agent_runs SET cancel_requested_at_ms = ?, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? "
            "AND status = 'running' AND cancel_requested_at_ms IS NULL",
            [requested_at, normalized_run],
        )
        changed = await db.fetch_one("SELECT changes() AS count")
    return int((changed or {}).get("count") or 0) == 1


async def get_execution_state(db, run_id: str) -> dict[str, Any] | None:
    normalized_run = _required_text(run_id, "run id")
    return await db.fetch_one(
        "SELECT id, status, execution_owner_id, lease_expires_at_ms, "
        "heartbeat_at_ms, execution_attempt, cancel_requested_at_ms "
        "FROM ai_agent_runs WHERE id = ?",
        [normalized_run],
    )


async def cancellation_requested(db, run_id: str) -> bool:
    state = await get_execution_state(db, run_id)
    return bool(state and state.get("cancel_requested_at_ms") is not None)


class SqliteExecutionLeaseStore:
    """SQLite adapter for the Core execution-lease port."""

    def __init__(self, db) -> None:
        self._db = db

    async def claim(
        self,
        run_id: str,
        owner_id: str,
        *,
        lease_duration_ms: int,
    ) -> bool:
        return await claim_run(
            self._db,
            run_id=run_id,
            owner_id=owner_id,
            lease_duration_ms=lease_duration_ms,
        )

    async def renew(
        self,
        run_id: str,
        owner_id: str,
        *,
        lease_duration_ms: int,
    ) -> bool:
        return await renew_lease(
            self._db,
            run_id=run_id,
            owner_id=owner_id,
            lease_duration_ms=lease_duration_ms,
        )

    async def release(self, run_id: str, owner_id: str) -> bool:
        return await release_lease(
            self._db,
            run_id=run_id,
            owner_id=owner_id,
        )

    async def request_cancellation(self, run_id: str) -> bool:
        return await request_cancellation(self._db, run_id)

    async def get(self, run_id: str) -> RunExecutionLease | None:
        row = await get_execution_state(self._db, run_id)
        if row is None:
            return None
        return RunExecutionLease(
            run_id=str(row["id"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            owner_id=row.get("execution_owner_id"),
            expires_at_ms=row.get("lease_expires_at_ms"),
            heartbeat_at_ms=row.get("heartbeat_at_ms"),
            attempt=int(row.get("execution_attempt") or 0),
            cancellation_requested_at_ms=row.get("cancel_requested_at_ms"),
        )
