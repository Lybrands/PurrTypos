"""Application-owned persistent cancellation for one Run."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from purra.contracts import RunStatus, StepStatus, TaskStepUpdate
from purra.errors import ContractViolationError
from purra.events import AgentEvent, CoreEventType
from purra.output import RunLifecycleOutputDraft
from purra.ports import RunCommit
from purra.run_control import RunCancellationReceipt


class AgentCancellationService:
    """Request cancellation for one Run and its delegated executions."""

    def __init__(
        self,
        db,
        composition,
    ) -> None:
        self._db = db
        self._composition = composition
        self._control = composition.run_control_store

    async def cancel(self, run_id: str) -> dict[str, object] | None:
        normalized = str(run_id or "").strip()
        if not normalized:
            return None
        state = await self._control.get(normalized)
        if state is None:
            return None
        persisted = await self._control.load_cancellation_receipt(normalized)
        if persisted is not None and not persisted.draining:
            return _receipt_payload(persisted)
        if persisted is None and state.status is not RunStatus.RUNNING:
            raise ContractViolationError(
                "terminal Run has no cancellation audit receipt"
            )

        fenced = await self._control.fence_cancellation(normalized)
        terminalized = await self._terminalize_without_live_executor(normalized)
        final = await self._control.complete_cancellation(
            normalized,
            terminalized=terminalized,
        )
        return _receipt_payload(
            final,
            newly_requested=fenced.newly_requested,
        )

    async def _terminalize_without_live_executor(self, run_id: str) -> bool:
        claimed = await self._control.claim_for_cancellation(
            run_id,
            self._composition.execution_owner_id,
            lease_duration_ms=30_000,
        )
        if not claimed:
            return False
        todos = await self._db.fetch_all(
            "SELECT step_id, status FROM ai_agent_run_todos WHERE run_id = ? "
            "ORDER BY sort, id",
            [run_id],
        )
        updates = tuple(
            TaskStepUpdate(
                step_id=str(todo["step_id"]),
                status=StepStatus.BLOCKED,
                result_summary="执行已取消。",
            )
            for todo in todos
            if str(todo.get("status") or "") in {"pending", "running"}
        )
        event = AgentEvent(
            type=CoreEventType.RUN_CANCELED,
            run_id=run_id,
            payload={
                "status": RunStatus.CANCELED.value,
                "reason": "cancellation_requested_without_live_executor",
            },
        )
        identity = await self._db.fetch_one(
            "SELECT turn_id FROM ai_agent_run_events WHERE run_id = ? "
            "AND source_event_key = ? AND kind = 'run.lifecycle' "
            "AND event_id IS NOT NULL ORDER BY sequence LIMIT 1",
            [run_id, f"run:{run_id}:running"],
        )
        occurred_at = datetime.now(timezone.utc)
        try:
            await self._composition.output_repository.commit_run_lifecycle(
                run_id,
                RunCommit(
                    step_updates=updates,
                    terminal_status=RunStatus.CANCELED,
                    events=(event,),
                ),
                RunLifecycleOutputDraft(
                    source_event_key=f"run:{run_id}:canceled",
                    status=RunStatus.CANCELED,
                    turn_id=str((identity or {}).get("turn_id") or "") or None,
                    payload=event.payload,
                    occurred_at=occurred_at,
                ),
            )
        except BaseException:
            # The terminal transaction rolled back. Relinquish only this
            # cancellation worker's temporary lease so the durable receipt can
            # be retried immediately instead of waiting for lease expiry.
            await asyncio.shield(
                self._control.release(
                    run_id,
                    self._composition.execution_owner_id,
                )
            )
            raise
        return True


def _receipt_payload(
    receipt: RunCancellationReceipt,
    *,
    newly_requested: bool | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": (
            "cancel_requested"
            if receipt.status is RunStatus.RUNNING
            else receipt.status.value
        ),
        "newlyRequested": (
            receipt.newly_requested
            if newly_requested is None
            else bool(newly_requested)
        ),
        "delegationsCanceled": receipt.delegations_canceled,
        "terminalized": receipt.terminalized,
        "cancellationStatus": "draining" if receipt.draining else "completed",
        "cancellationEpoch": receipt.cancellation_epoch,
    }
    if receipt.tombstoned:
        payload["cancellationReceipt"] = "tombstoned"
    return payload


__all__ = ["AgentCancellationService"]
