"""Canonical recovery for abandoned Agent Runs."""

from __future__ import annotations

from datetime import datetime, timezone

from application.agent_cancellation_service import AgentCancellationService
from purra.contracts import RunStatus, StepStatus, TaskStepUpdate
from purra.events import AgentEvent, CoreEventType
from purra.orphan_recovery import OrphanRecoveryCoordinator
from purra.output import RunLifecycleOutputDraft
from purra.ports import RunCommit
from purra.run_control import OrphanRunDecision, OrphanRunDisposition


class AgentOrphanRecoveryService:
    """Compose generic orphan recovery with product-owned Run settlement."""

    def __init__(self, db, composition) -> None:
        settler = _AgentOrphanRunSettler(db, composition)
        self._coordinator = OrphanRecoveryCoordinator(
            control=composition.run_control_store,
            long_tasks=composition.long_task_repository,
            owner_id=composition.execution_owner_id,
            settle=settler.settle,
        )

    async def recover(
        self,
        *,
        timestamp_ms: int | None = None,
        after_restart: bool = False,
    ) -> tuple[str, ...]:
        return await self._coordinator.recover(
            timestamp_ms=timestamp_ms,
            after_restart=after_restart,
        )


class _AgentOrphanRunSettler:
    """Translate a Core recovery decision into product persistence outputs."""

    def __init__(self, db, composition) -> None:
        self._db = db
        self._composition = composition
        self._cancellation = AgentCancellationService(db, composition)

    async def settle(
        self,
        decision: OrphanRunDecision,
        after_restart: bool,
    ) -> None:
        status = decision.terminal_status
        if status is None:
            raise ValueError("orphan settlement requires a terminal decision")
        explicitly_canceled = (
            decision.disposition is OrphanRunDisposition.CANCEL
        )
        if explicitly_canceled:
            await self._cancellation.cancel(decision.run_id)
        await self._commit_terminal(
            decision.run_id,
            status=status,
            reason=self._recovery_reason(
                decision,
                after_restart=after_restart,
            ),
        )
        if explicitly_canceled:
            await self._cancellation.cancel(decision.run_id)

    @staticmethod
    def _recovery_reason(
        decision: OrphanRunDecision,
        *,
        after_restart: bool,
    ) -> str:
        if decision.disposition is OrphanRunDisposition.PAUSE_RECOVERABLE:
            return decision.reason.value
        return (
            "execution_recovery_after_restart"
            if after_restart
            else "execution_lease_expired"
        )

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

__all__ = ["AgentOrphanRecoveryService"]
