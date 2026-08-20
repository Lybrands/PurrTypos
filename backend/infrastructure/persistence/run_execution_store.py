"""Atomic execution ownership and cancellation operations for Agent Runs."""

from __future__ import annotations

from collections.abc import Sequence
from time import time
from typing import Any

from purra.contracts import RunExecutionLease, RunStatus
from purra.errors import ContractViolationError
from purra.run_control import (
    OrphanRunCandidate,
    OrphanTaskEvidence,
    RunActivitySnapshot,
    RunCancellationReceipt,
)
from purra.ports import RunCancellationProjector
from purra.normalization import unique_text_tuple


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


async def _claim_run(
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


async def _claim_run_for_cancellation(
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


async def _renew_lease(
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


async def _release_lease(db, *, run_id: str, owner_id: str) -> bool:
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


async def _request_cancellation(
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


async def _fence_run_cancellation(db, run_id: str) -> dict[str, Any]:
    """Atomically fence one Run and cancel its active delegated executions."""

    normalized_run = _required_text(run_id, "run id")
    requested_at = now_ms()
    if not db.current_task_owns_transaction():
        async with db.transaction(cancellation_linearizable=True):
            return await _fence_run_cancellation(db, normalized_run)
    run = await db.fetch_one(
        "SELECT id, status, cancellation_epoch FROM ai_agent_runs WHERE id = ?",
        [normalized_run],
    )
    if run is None:
        raise LookupError("Run does not exist")
    existing = await db.fetch_one(
        "SELECT * FROM ai_agent_run_cancellations WHERE run_id = ?",
        [normalized_run],
    )
    if existing is None:
        if str(run.get("status") or "") != RunStatus.RUNNING.value:
            raise ContractViolationError(
                "terminal Run has no cancellation audit receipt"
            )
        epoch = int(run.get("cancellation_epoch") or 0) + 1
        await db.execute(
            "UPDATE ai_agent_runs SET cancellation_epoch = ?, "
            "cancel_requested_at_ms = COALESCE(cancel_requested_at_ms, ?), "
            "update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [epoch, requested_at, normalized_run],
        )
        await db.execute(
            "INSERT INTO ai_agent_run_cancellations "
            "(run_id, cancellation_epoch, status, requested_at_ms) "
            "VALUES (?, ?, 'draining', ?)",
            [normalized_run, epoch, requested_at],
        )
    else:
        if str(existing.get("status") or "") == "completed":
            raise ContractViolationError("Run cancellation is already completed")
        epoch = int(existing["cancellation_epoch"])
    await db.execute(
        "UPDATE ai_agent_runs SET cancel_requested_at_ms = "
        "COALESCE(cancel_requested_at_ms, ?), update_time = CURRENT_TIMESTAMP "
        "WHERE id = ? AND status = 'running'",
        [requested_at, normalized_run],
    )
    active_delegations = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_delegations "
        "WHERE run_id = ? AND status IN ('queued', 'running')",
        [normalized_run],
    )
    await db.execute(
        "UPDATE ai_agent_delegations SET status = 'canceled', "
        "error = 'run_canceled', update_time = CURRENT_TIMESTAMP "
        "WHERE run_id = ? AND status IN ('queued', 'running')",
        [normalized_run],
    )
    delegated_canceled = int((active_delegations or {}).get("count") or 0)
    await db.execute(
        "UPDATE ai_agent_run_cancellations SET delegations_canceled = MAX("
        "delegations_canceled, ?), update_time = CURRENT_TIMESTAMP "
        "WHERE run_id = ? AND cancellation_epoch = ?",
        [delegated_canceled, normalized_run, epoch],
    )
    receipt = await db.fetch_one(
        "SELECT * FROM ai_agent_run_cancellations WHERE run_id = ?",
        [normalized_run],
    )
    assert receipt is not None
    return receipt


async def _list_orphaned_run_candidates(
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
    )
    params = [] if after_restart else [checked_at]
    rows = await db.fetch_all(
        "SELECT candidate.id, candidate.cancel_requested_at_ms, "
        "candidate.execution_owner_id, candidate.execution_attempt "
        "FROM ai_agent_runs AS candidate WHERE candidate.status = 'running' "
        + condition
        + "ORDER BY candidate.create_time ASC, candidate.id ASC",
        params,
    )
    return tuple(dict(row) for row in rows)


async def _claim_orphaned_run_for_recovery(
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


async def _get_execution_state(db, run_id: str) -> dict[str, Any] | None:
    normalized_run = _required_text(run_id, "run id")
    return await db.fetch_one(
        "SELECT id, status, execution_owner_id, lease_expires_at_ms, "
        "heartbeat_at_ms, execution_attempt, cancel_requested_at_ms "
        "FROM ai_agent_runs WHERE id = ?",
        [normalized_run],
    )


class SqliteRunControlStore:
    """SQLite adapter for execution leases and detached Run control."""

    def __init__(
        self,
        db,
        *,
        cancellation_projectors: Sequence[RunCancellationProjector] = (),
    ) -> None:
        self._db = db
        self._cancellation_projectors = tuple(cancellation_projectors)

    async def claim(
        self,
        run_id: str,
        owner_id: str,
        *,
        lease_duration_ms: int,
        timestamp_ms: int | None = None,
    ) -> bool:
        return await _claim_run(
            self._db,
            run_id=run_id,
            owner_id=owner_id,
            lease_duration_ms=lease_duration_ms,
            timestamp_ms=timestamp_ms,
        )

    async def renew(
        self,
        run_id: str,
        owner_id: str,
        *,
        lease_duration_ms: int,
        timestamp_ms: int | None = None,
    ) -> bool:
        return await _renew_lease(
            self._db,
            run_id=run_id,
            owner_id=owner_id,
            lease_duration_ms=lease_duration_ms,
            timestamp_ms=timestamp_ms,
        )

    async def release(self, run_id: str, owner_id: str) -> bool:
        return await _release_lease(
            self._db,
            run_id=run_id,
            owner_id=owner_id,
        )

    async def request_cancellation(
        self,
        run_id: str,
        *,
        timestamp_ms: int | None = None,
    ) -> bool:
        return await _request_cancellation(
            self._db,
            run_id,
            timestamp_ms=timestamp_ms,
        )

    async def get(self, run_id: str) -> RunExecutionLease | None:
        row = await _get_execution_state(self._db, run_id)
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

    async def claim_for_cancellation(
        self,
        run_id: str,
        owner_id: str,
        *,
        lease_duration_ms: int,
        timestamp_ms: int | None = None,
    ) -> bool:
        return await _claim_run_for_cancellation(
            self._db,
            run_id=run_id,
            owner_id=owner_id,
            lease_duration_ms=lease_duration_ms,
            timestamp_ms=timestamp_ms,
        )

    async def load_cancellation_receipt(
        self,
        run_id: str,
    ) -> RunCancellationReceipt | None:
        normalized = _required_text(run_id, "run id")
        if not self._db.current_task_owns_transaction():
            async with self._db.transaction(cancellation_linearizable=True):
                return await self.load_cancellation_receipt(normalized)
        run = await self._db.fetch_one(
            "SELECT id, status, cancellation_epoch, execution_owner_id, "
            "lease_expires_at_ms, heartbeat_at_ms FROM ai_agent_runs WHERE id = ?",
            [normalized],
        )
        if run is None:
            return None
        persisted = await self._db.fetch_one(
            "SELECT * FROM ai_agent_run_cancellations WHERE run_id = ?",
            [normalized],
        )
        if persisted is not None:
            completed = str(persisted.get("status") or "") == "completed"
            epoch = int(persisted.get("cancellation_epoch") or 0)
            final_status = str(
                persisted.get("final_status") or RunStatus.CANCELED.value
            )
            if completed and (
                str(run.get("status") or "") != final_status
                or epoch <= 0
                or int(run.get("cancellation_epoch") or 0) != epoch
                or persisted.get("completed_at_ms") is None
            ):
                raise ContractViolationError(
                    "completed cancellation receipt conflicts with Run"
                )
            return RunCancellationReceipt(
                run_id=normalized,
                status=str(run["status"]),  # type: ignore[arg-type]
                cancellation_epoch=epoch,
                newly_requested=False,
                delegations_canceled=int(
                    persisted.get("delegations_canceled") or 0
                ),
                draining=not completed,
            )
        epoch = int(run.get("cancellation_epoch") or 0)
        if str(run.get("status") or "") != RunStatus.CANCELED.value or epoch <= 0:
            return None
        activity = await self.inspect_activity(run_ids=(normalized,))
        if (
            run.get("execution_owner_id") is not None
            or run.get("lease_expires_at_ms") is not None
            or run.get("heartbeat_at_ms") is not None
            or not activity.quiescent
        ):
            raise ContractViolationError(
                "tombstoned cancellation audit is not quiescent"
            )
        return RunCancellationReceipt(
            run_id=normalized,
            status=RunStatus.CANCELED,
            cancellation_epoch=epoch,
            newly_requested=False,
            tombstoned=True,
        )

    async def fence_cancellation(self, run_id: str) -> RunCancellationReceipt:
        normalized = _required_text(run_id, "run id")
        async with self._db.transaction(cancellation_linearizable=True):
            prior = await self._db.fetch_one(
                "SELECT run_id FROM ai_agent_run_cancellations WHERE run_id = ?",
                [normalized],
            )
            raw = await _fence_run_cancellation(self._db, normalized)
            state = await _get_execution_state(self._db, normalized)
            assert state is not None
            receipt = RunCancellationReceipt(
                run_id=normalized,
                status=str(state["status"]),  # type: ignore[arg-type]
                cancellation_epoch=int(raw["cancellation_epoch"]),
                newly_requested=prior is None,
                delegations_canceled=int(raw.get("delegations_canceled") or 0),
                draining=True,
            )
            for projector in self._cancellation_projectors:
                projected = await projector.project(normalized, receipt)
                if projected is not None:
                    raise TypeError("Run cancellation projector must return None")
            return receipt

    async def complete_cancellation(
        self,
        run_id: str,
        *,
        terminalized: bool,
    ) -> RunCancellationReceipt:
        normalized = _required_text(run_id, "run id")
        async with self._db.transaction(cancellation_linearizable=True):
            run = await self._db.fetch_one(
                "SELECT status FROM ai_agent_runs WHERE id = ?",
                [normalized],
            )
            if run is None:
                raise LookupError("Run does not exist")
            completed = str(run.get("status") or "") != RunStatus.RUNNING.value
            if completed:
                await self._db.execute(
                    "UPDATE ai_agent_run_cancellations SET status = 'completed', "
                    "completed_at_ms = ?, final_status = ?, "
                    "update_time = CURRENT_TIMESTAMP "
                    "WHERE run_id = ? AND status = 'draining'",
                    [now_ms(), str(run["status"]), normalized],
                )
            raw = await self._db.fetch_one(
                "SELECT * FROM ai_agent_run_cancellations WHERE run_id = ?",
                [normalized],
            )
            if raw is None:
                raise ContractViolationError("Run cancellation receipt is missing")
            return RunCancellationReceipt(
                run_id=normalized,
                status=str(run["status"]),  # type: ignore[arg-type]
                cancellation_epoch=int(raw["cancellation_epoch"]),
                newly_requested=False,
                delegations_canceled=int(raw.get("delegations_canceled") or 0),
                terminalized=terminalized,
                draining=not completed,
            )

    async def list_orphans(
        self,
        *,
        timestamp_ms: int | None = None,
        after_restart: bool = False,
    ) -> tuple[OrphanRunCandidate, ...]:
        checked_at = now_ms() if timestamp_ms is None else int(timestamp_ms)
        rows = await _list_orphaned_run_candidates(
            self._db,
            timestamp_ms=checked_at,
            after_restart=after_restart,
        )
        candidates: list[OrphanRunCandidate] = []
        for row in rows:
            tasks = await self._db.fetch_all(
                "SELECT DISTINCT binding.task_id, task.status, task.revision "
                "FROM ai_agent_long_task_runs "
                "AS binding JOIN ai_agent_long_tasks AS task "
                "ON task.id = binding.task_id WHERE binding.run_id = ? "
                "AND task.status IN ('pending', 'running', 'paused') "
                "ORDER BY binding.task_id",
                [row["id"]],
            )
            active_tasks: list[OrphanTaskEvidence] = []
            recoverable_tasks: list[OrphanTaskEvidence] = []
            for task in tasks:
                task_id = str(task["task_id"])
                status = str(task["status"])
                evidence = OrphanTaskEvidence(
                    task_id=task_id,
                    revision=int(task["revision"]),
                )
                if status in {"pending", "paused"} or (
                    status == "running" and after_restart
                ):
                    recoverable_tasks.append(evidence)
                    continue
                if status != "running":
                    continue
                live_unit = await self._db.fetch_one(
                    "SELECT 1 AS active FROM ai_agent_long_task_units "
                    "WHERE task_id = ? AND status IN ('claimed', 'running') "
                    "AND (lease_expires_at_ms IS NULL OR lease_expires_at_ms > ?) "
                    "LIMIT 1",
                    [task_id, checked_at],
                )
                (active_tasks if live_unit is not None else recoverable_tasks).append(
                    evidence
                )
            candidates.append(OrphanRunCandidate(
                run_id=str(row["id"]),
                execution_attempt=int(row.get("execution_attempt") or 0),
                execution_owner_id=row.get("execution_owner_id"),
                cancellation_requested_at_ms=row.get("cancel_requested_at_ms"),
                active_tasks=tuple(active_tasks),
                recoverable_tasks=tuple(recoverable_tasks),
            ))
        return tuple(candidates)

    async def claim_orphan(
        self,
        candidate: OrphanRunCandidate,
        owner_id: str,
        *,
        lease_duration_ms: int,
        timestamp_ms: int | None = None,
        after_restart: bool = False,
    ) -> bool:
        if not isinstance(candidate, OrphanRunCandidate):
            raise TypeError("orphan claim requires an OrphanRunCandidate")
        return await _claim_orphaned_run_for_recovery(
            self._db,
            run_id=candidate.run_id,
            owner_id=owner_id,
            lease_duration_ms=lease_duration_ms,
            expected_owner_id=candidate.execution_owner_id,
            expected_attempt=candidate.execution_attempt,
            timestamp_ms=timestamp_ms,
            after_restart=after_restart,
        )

    async def inspect_activity(
        self,
        *,
        run_ids: tuple[str, ...] = (),
        task_ids: tuple[str, ...] = (),
    ) -> RunActivitySnapshot:
        requested_runs = unique_text_tuple(run_ids)
        requested_tasks = unique_text_tuple(task_ids)
        active_runs: tuple[str, ...] = ()
        active_delegations: tuple[str, ...] = ()
        draining: tuple[str, ...] = ()
        active_tasks: tuple[str, ...] = ()
        if requested_runs:
            marks = _sql_marks(requested_runs)
            params = list(requested_runs)
            rows = await self._db.fetch_all(
                "SELECT id FROM ai_agent_runs WHERE "
                f"id IN ({marks}) AND (status = 'running' OR "
                "execution_owner_id IS NOT NULL OR lease_expires_at_ms IS NOT NULL "
                ") ORDER BY id",
                params,
            )
            active_runs = tuple(str(row["id"]) for row in rows)
            rows = await self._db.fetch_all(
                "SELECT DISTINCT run_id FROM ai_agent_delegations WHERE "
                f"run_id IN ({marks}) AND status IN ('queued', 'running') "
                "ORDER BY run_id",
                params,
            )
            active_delegations = tuple(str(row["run_id"]) for row in rows)
            rows = await self._db.fetch_all(
                "SELECT run_id FROM ai_agent_run_cancellations WHERE "
                f"run_id IN ({marks}) AND status <> 'completed' ORDER BY run_id",
                params,
            )
            draining = tuple(str(row["run_id"]) for row in rows)
        if requested_tasks:
            rows = await self._db.fetch_all(
                "SELECT DISTINCT task_id FROM ai_agent_long_task_units WHERE "
                f"task_id IN ({_sql_marks(requested_tasks)}) AND ("
                "status IN ('claimed', 'running') OR worker_id IS NOT NULL OR "
                "lease_expires_at_ms IS NOT NULL) ORDER BY task_id",
                list(requested_tasks),
            )
            active_tasks = tuple(str(row["task_id"]) for row in rows)
        return RunActivitySnapshot(
            requested_run_ids=requested_runs,
            requested_task_ids=requested_tasks,
            active_run_ids=active_runs,
            active_delegation_run_ids=active_delegations,
            active_task_ids=active_tasks,
            draining_cancellation_run_ids=draining,
        )


def _sql_marks(values: Sequence[object]) -> str:
    return ",".join("?" for _ in values)


__all__ = ["SqliteRunControlStore", "now_ms"]
