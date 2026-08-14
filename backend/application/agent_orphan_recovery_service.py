"""Canonical recovery for abandoned Agent Run trees."""

from __future__ import annotations

from datetime import datetime, timezone

from application.agent_cancellation_service import AgentCancellationService
from infrastructure.persistence.run_execution_store import (
    claim_orphaned_run_for_recovery,
    list_orphaned_run_candidates,
    now_ms,
)
from purra.contracts import RunStatus, StepStatus, TaskStepUpdate
from purra.events import AgentEvent, CoreEventType
from purra.output import RunLifecycleOutputDraft
from purra.ports import RunCommit


class AgentOrphanRecoveryService:
    """Claim abandoned Runs and settle them through canonical output commits."""

    def __init__(self, db, composition) -> None:
        self._db = db
        self._composition = composition
        self._cancellation = AgentCancellationService(db, composition)

    async def recover(
        self,
        *,
        timestamp_ms: int | None = None,
        after_restart: bool = False,
    ) -> tuple[str, ...]:
        checked_at = now_ms() if timestamp_ms is None else int(timestamp_ms)
        candidates = await list_orphaned_run_candidates(
            self._db,
            timestamp_ms=checked_at,
            after_restart=after_restart,
        )
        canceled_roots = {
            str(item["id"])
            for item in candidates
            if _is_root(item) and item.get("cancel_requested_at_ms") is not None
        }
        ordered = sorted(
            candidates,
            key=lambda item: (
                0 if str(item["id"]) in canceled_roots else 1,
                -int(item.get("run_depth") or 0),
                str(item["id"]),
            ),
        )
        recovered: list[str] = []
        for candidate in ordered:
            run_id = str(candidate.get("id") or "").strip()
            if not run_id:
                continue
            if await self._recover_one(
                candidate,
                checked_at=checked_at,
                after_restart=after_restart,
            ):
                recovered.append(run_id)
        for root_run_id in sorted(canceled_roots):
            await self._cancellation.cancel(root_run_id)
        return tuple(recovered)

    async def _recover_one(
        self,
        candidate,
        *,
        checked_at: int,
        after_restart: bool,
    ) -> bool:
        run_id = str(candidate["id"])
        claimed = await claim_orphaned_run_for_recovery(
            self._db,
            run_id=run_id,
            owner_id=self._composition.execution_owner_id,
            lease_duration_ms=30_000,
            expected_owner_id=(
                str(candidate.get("execution_owner_id") or "") or None
            ),
            expected_attempt=int(candidate.get("execution_attempt") or 0),
            timestamp_ms=checked_at,
            after_restart=after_restart,
        )
        if not claimed:
            return False
        current = await self._db.fetch_one(
            "SELECT id, root_run_id, parent_run_id, run_depth, "
            "cancel_requested_at_ms FROM ai_agent_runs WHERE id = ? "
            "AND status = 'running' AND execution_owner_id = ?",
            [run_id, self._composition.execution_owner_id],
        )
        if current is None:
            return False
        canceled = current.get("cancel_requested_at_ms") is not None
        is_root = _is_root(current)
        status = (
            RunStatus.CANCELED
            if canceled
            else RunStatus.FAILED if is_root else RunStatus.BLOCKED
        )
        reason = (
            "execution_recovery_after_restart"
            if after_restart else "execution_lease_expired"
        )
        try:
            if canceled and is_root:
                await self._cancellation.cancel(run_id)
            await self._commit_terminal(run_id, status=status, reason=reason)
            if canceled and is_root:
                await self._cancellation.cancel(run_id)
        except BaseException:
            await self._composition.execution_lease_store.release(
                run_id,
                self._composition.execution_owner_id,
            )
            raise
        return True

    async def _commit_terminal(
        self,
        run_id: str,
        *,
        status: RunStatus,
        reason: str,
    ) -> None:
        rows = await self._db.fetch_all(
            "SELECT step_id, status FROM ai_agent_run_todos WHERE run_id = ? "
            "ORDER BY sort, id",
            [run_id],
        )
        step_status = (
            StepStatus.FAILED if status is RunStatus.FAILED else StepStatus.BLOCKED
        )
        updates = tuple(
            TaskStepUpdate(
                step_id=str(row["step_id"]),
                status=step_status,
                result_summary="执行进程已失去所有权。",
            )
            for row in rows
            if str(row.get("status") or "") in {"pending", "running"}
        )
        event_type = {
            RunStatus.CANCELED: CoreEventType.RUN_CANCELED,
            RunStatus.FAILED: CoreEventType.RUN_FAILED,
            RunStatus.BLOCKED: CoreEventType.RUN_BLOCKED,
        }[status]
        event = AgentEvent(
            type=event_type,
            run_id=run_id,
            payload={"status": status.value, "reason": reason},
        )
        identities = await self._db.fetch_all(
            "SELECT turn_id FROM ai_agent_run_events WHERE run_id = ? "
            "AND source_event_key = ? AND kind = 'run.lifecycle' "
            "AND event_id IS NOT NULL ORDER BY sequence, id",
            [run_id, f"run:{run_id}:running"],
        )
        if len(identities) > 1:
            raise RuntimeError("orphan Run has conflicting canonical identities")
        turn_id = (
            str(identities[0].get("turn_id") or "").strip() or None
            if identities else None
        )
        occurred_at = datetime.now(timezone.utc)
        await self._composition.output_repository.commit_run_lifecycle(
            run_id,
            RunCommit(
                step_updates=updates,
                terminal_status=status,
                error=(reason if status is not RunStatus.CANCELED else None),
                events=(event,),
            ),
            RunLifecycleOutputDraft(
                source_event_key=f"run:{run_id}:{status.value}",
                status=status,
                turn_id=turn_id,
                payload=event.payload,
                occurred_at=occurred_at,
            ),
        )


def _is_root(row) -> bool:
    run_id = str(row.get("id") or "")
    root_id = str(row.get("root_run_id") or "")
    return not row.get("parent_run_id") and (not root_id or root_id == run_id)


__all__ = ["AgentOrphanRecoveryService"]
