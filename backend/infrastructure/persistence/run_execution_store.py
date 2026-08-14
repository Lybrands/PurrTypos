"""Atomic execution ownership and cancellation operations for Agent Runs."""

from __future__ import annotations

from time import time
from typing import Any

from purra.contracts import RunExecutionLease


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


async def claim_run_for_cancellation(
    db,
    *,
    run_id: str,
    owner_id: str,
    lease_duration_ms: int,
    timestamp_ms: int | None = None,
) -> bool:
    """Claim an unowned canceled-requested Run for canonical settlement."""

    normalized_run = _required_text(run_id, "run id")
    normalized_owner = _required_text(owner_id, "owner id")
    claimed_at = now_ms() if timestamp_ms is None else int(timestamp_ms)
    deadline = _lease_deadline(claimed_at, lease_duration_ms)
    async with db.transaction(cancellation_linearizable=True):
        await db.execute(
            "UPDATE ai_agent_runs SET execution_owner_id = ?, "
            "heartbeat_at_ms = ?, lease_expires_at_ms = ?, "
            "execution_attempt = execution_attempt + 1, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? "
            "AND status = 'running' AND cancel_requested_at_ms IS NOT NULL "
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


async def fence_cancellation_tree(db, root_run_id: str) -> dict[str, Any]:
    """Atomically fence a Root and request cancellation for its current tree."""

    normalized_root = _required_text(root_run_id, "root run id")
    requested_at = now_ms()
    if not db.current_task_owns_transaction():
        async with db.transaction(cancellation_linearizable=True):
            return await fence_cancellation_tree(db, normalized_root)
    root = await db.fetch_one(
        "SELECT id, status, cancellation_epoch FROM ai_agent_runs WHERE id = ?",
        [normalized_root],
    )
    if root is None:
        raise LookupError("Root Run does not exist")
    existing = await db.fetch_one(
        "SELECT * FROM ai_agent_run_cancellations WHERE root_run_id = ?",
        [normalized_root],
    )
    if existing is None:
        epoch = int(root.get("cancellation_epoch") or 0) + 1
        await db.execute(
            "UPDATE ai_agent_runs SET cancellation_epoch = ?, "
            "cancel_requested_at_ms = COALESCE(cancel_requested_at_ms, ?), "
            "update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [epoch, requested_at, normalized_root],
        )
        await db.execute(
            "INSERT INTO ai_agent_run_cancellations "
            "(root_run_id, cancellation_epoch, status, requested_at_ms) "
            "VALUES (?, ?, 'draining', ?)",
            [normalized_root, epoch, requested_at],
        )
    else:
        epoch = int(existing["cancellation_epoch"])
    await db.execute(
        "UPDATE ai_agent_runs SET cancel_requested_at_ms = "
        "COALESCE(cancel_requested_at_ms, ?), update_time = CURRENT_TIMESTAMP "
        "WHERE (id = ? OR root_run_id = ?) AND status = 'running'",
        [requested_at, normalized_root, normalized_root],
    )
    descendants = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE root_run_id = ? "
        "AND id <> ? AND status = 'running'",
        [normalized_root, normalized_root],
    )
    unbound_delegations = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_delegations "
        "WHERE root_run_id = ? AND status IN ('queued', 'claimed', 'running') "
        "AND child_run_id IS NULL",
        [normalized_root],
    )
    await db.execute(
        "UPDATE ai_agent_delegations SET status = 'canceled', "
        "error = 'parent_canceled', worker_id = NULL, "
        "claim_expires_at_ms = NULL, update_time = CURRENT_TIMESTAMP "
        "WHERE root_run_id = ? AND status IN ('queued', 'claimed', 'running')",
        [normalized_root],
    )
    children_canceled = int((descendants or {}).get("count") or 0) + int(
        (unbound_delegations or {}).get("count") or 0
    )
    await db.execute(
        "UPDATE ai_agent_run_cancellations SET children_canceled = MAX("
        "children_canceled, ?), update_time = CURRENT_TIMESTAMP "
        "WHERE root_run_id = ? AND cancellation_epoch = ?",
        [children_canceled, normalized_root, epoch],
    )
    receipt = await db.fetch_one(
        "SELECT * FROM ai_agent_run_cancellations WHERE root_run_id = ?",
        [normalized_root],
    )
    assert receipt is not None
    return receipt


async def list_orphaned_run_candidates(
    db,
    *,
    timestamp_ms: int | None = None,
    after_restart: bool = False,
) -> tuple[dict[str, Any], ...]:
    """Read abandoned Run candidates without changing their lifecycle."""

    checked_at = now_ms() if timestamp_ms is None else int(timestamp_ms)
    condition = "" if after_restart else (
        "AND (candidate.execution_owner_id IS NULL "
        "OR candidate.lease_expires_at_ms IS NULL "
        "OR candidate.lease_expires_at_ms <= ?) "
        "AND (candidate.parent_run_id IS NOT NULL OR NOT EXISTS ("
        "SELECT 1 FROM ai_agent_runs AS child WHERE child.root_run_id = "
        "candidate.id AND child.id <> candidate.id AND child.status = 'running' "
        "AND child.execution_owner_id IS NOT NULL "
        "AND child.lease_expires_at_ms > ?)) "
    )
    params = [] if after_restart else [checked_at, checked_at]
    rows = await db.fetch_all(
        "SELECT candidate.id, candidate.root_run_id, candidate.parent_run_id, "
        "candidate.run_depth, candidate.cancel_requested_at_ms, "
        "candidate.execution_owner_id, candidate.execution_attempt "
        "FROM ai_agent_runs AS candidate WHERE candidate.status = 'running' "
        + condition
        + "ORDER BY candidate.run_depth ASC, candidate.create_time ASC, "
        "candidate.id ASC",
        params,
    )
    return tuple(dict(row) for row in rows)


async def claim_orphaned_run_for_recovery(
    db,
    *,
    run_id: str,
    owner_id: str,
    lease_duration_ms: int,
    expected_owner_id: str | None,
    expected_attempt: int,
    timestamp_ms: int | None = None,
    after_restart: bool = False,
) -> bool:
    """CAS one still-orphaned Run to a canonical recovery owner."""

    normalized_run = _required_text(run_id, "run id")
    normalized_owner = _required_text(owner_id, "owner id")
    claimed_at = now_ms() if timestamp_ms is None else int(timestamp_ms)
    deadline = _lease_deadline(claimed_at, lease_duration_ms)
    condition = (
        "AND execution_attempt = ? AND ((execution_owner_id IS NULL AND ? IS NULL) "
        "OR execution_owner_id = ?) "
    ) + ("" if after_restart else (
        "AND (execution_owner_id IS NULL OR lease_expires_at_ms IS NULL "
        "OR lease_expires_at_ms <= ?)"
    ))
    params: list[object] = [
        normalized_owner,
        claimed_at,
        deadline,
        normalized_run,
        int(expected_attempt),
        expected_owner_id,
        expected_owner_id,
    ]
    if not after_restart:
        params.append(claimed_at)
    async with db.transaction(cancellation_linearizable=True):
        await db.execute(
            "UPDATE ai_agent_runs SET execution_owner_id = ?, "
            "heartbeat_at_ms = ?, lease_expires_at_ms = ?, "
            "execution_attempt = execution_attempt + 1, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? "
            "AND status = 'running' " + condition,
            params,
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
