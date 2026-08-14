"""Application-owned persistent cancellation for one Root Run tree."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Protocol

from purra.contracts import RunStatus, StepStatus, TaskStepUpdate
from purra.errors import ContractViolationError
from purra.events import AgentEvent, CoreEventType
from purra.output import RunLifecycleOutputDraft
from purra.ports import RunCommit

from infrastructure.persistence.run_execution_store import (
    claim_run_for_cancellation,
    fence_cancellation_tree,
    now_ms,
)


class RootCancellationParticipant(Protocol):
    async def project(
        self,
        root_run_id: str,
        receipt: Mapping[str, object],
    ) -> None: ...


class RootCancellationTargetError(ContractViolationError):
    """Cancellation was requested for a non-Root or malformed Run lineage."""


class AgentCancellationService:
    """Request cancellation for a Root and every persisted descendant."""

    def __init__(
        self,
        db,
        composition,
        *,
        participants: Sequence[RootCancellationParticipant] | None = None,
    ) -> None:
        self._db = db
        self._composition = composition
        self._participants = tuple(
            composition.root_cancellation_participants
            if participants is None else participants
        )

    async def cancel(self, run_id: str) -> dict[str, object] | None:
        normalized = str(run_id or "").strip()
        if not normalized:
            return None
        root = await self._db.fetch_one(
            "SELECT id, status, parent_run_id, root_run_id, "
            "cancellation_epoch FROM ai_agent_runs WHERE id = ?",
            [normalized],
        )
        if root is None:
            return None
        if (
            str(root.get("parent_run_id") or "").strip()
            or str(root.get("root_run_id") or "").strip() != normalized
        ):
            raise RootCancellationTargetError(
                "cancellation target must be the persisted Root Run"
            )
        persisted_receipt = await self._db.fetch_one(
            "SELECT * FROM ai_agent_run_cancellations WHERE root_run_id = ?",
            [normalized],
        )
        if (
            persisted_receipt is not None
            and str(persisted_receipt.get("status") or "") == "completed"
        ):
            receipt_root_id = str(
                persisted_receipt.get("root_run_id") or ""
            ).strip()
            receipt_epoch = int(
                persisted_receipt.get("cancellation_epoch") or 0
            )
            if (
                receipt_root_id != normalized
                or str(root.get("id") or "") != normalized
                or str(root.get("status") or "") != RunStatus.CANCELED.value
                or receipt_epoch <= 0
                or int(root.get("cancellation_epoch") or 0) != receipt_epoch
                or persisted_receipt.get("completed_at_ms") is None
            ):
                raise ContractViolationError(
                    "completed cancellation receipt conflicts with Root Run"
                )
            return {
                "status": RunStatus.CANCELED.value,
                "newlyRequested": False,
                "childrenCanceled": int(
                    persisted_receipt.get("children_canceled") or 0
                ),
                "terminalized": False,
                "cancellationStatus": "completed",
                "cancellationEpoch": receipt_epoch,
            }
        if persisted_receipt is None:
            root_status = str(root.get("status") or "")
            if root_status == RunStatus.CANCELED.value:
                return await self._replay_tombstoned_cancellation(normalized)
            if root_status != RunStatus.RUNNING.value:
                raise ContractViolationError(
                    "terminal Root Run has no cancellation audit receipt"
                )
        state = await self._composition.execution_lease_store.get(normalized)
        if state is None:
            return None
        if state.status.value not in {"running", "canceled"}:
            return {
                "status": state.status.value,
                "newlyRequested": False,
                "childrenCanceled": 0,
                "terminalized": False,
            }

        prior = await self._db.fetch_one(
            "SELECT cancellation_epoch FROM ai_agent_run_cancellations "
            "WHERE root_run_id = ?",
            [normalized],
        )
        async with self._db.transaction(cancellation_linearizable=True):
            receipt = await fence_cancellation_tree(self._db, normalized)
            for participant in self._participants:
                projected = await participant.project(normalized, receipt)
                if projected is not None:
                    raise TypeError(
                        "Root cancellation participant must return None"
                    )
        newly_requested = prior is None
        # The fence makes the tree closed. Re-read the authority each pass;
        # this also recovers after a process crash between request and drain.
        descendants = await self._db.fetch_all(
            "SELECT id FROM ai_agent_runs WHERE root_run_id = ? AND id <> ? "
            "AND status = 'running' ORDER BY run_depth DESC, id",
            [normalized, normalized],
        )
        for target in (*descendants, {"id": normalized}):
            target_id = str(target["id"])
            await self._terminalize_without_live_executor(target_id)

        final_state = await self._composition.execution_lease_store.get(normalized)
        assert final_state is not None
        async with self._db.transaction(cancellation_linearizable=True):
            remaining = await self._db.fetch_one(
                "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE "
                "(id = ? OR root_run_id = ?) AND status = 'running'",
                [normalized, normalized],
            )
            completed = int((remaining or {}).get("count") or 0) == 0
            if completed:
                await self._db.execute(
                    "UPDATE ai_agent_run_cancellations SET status = 'completed', "
                    "completed_at_ms = ?, update_time = CURRENT_TIMESTAMP "
                    "WHERE root_run_id = ? AND status = 'draining'",
                    [now_ms(), normalized],
                )
            receipt = await self._db.fetch_one(
                "SELECT * FROM ai_agent_run_cancellations "
                "WHERE root_run_id = ?",
                [normalized],
            )
        assert receipt is not None
        return {
            "status": (
                "cancel_requested"
                if final_state.status.value == "running"
                else final_state.status.value
            ),
            "newlyRequested": newly_requested,
            "childrenCanceled": int(receipt.get("children_canceled") or 0),
            "terminalized": (
                state.status.value == "running"
                and final_state.status.value == "canceled"
            ),
            "cancellationStatus": "completed" if completed else "draining",
            "cancellationEpoch": int(receipt["cancellation_epoch"]),
        }

    async def _replay_tombstoned_cancellation(
        self,
        root_run_id: str,
    ) -> dict[str, object]:
        if not self._db.current_task_owns_transaction():
            async with self._db.transaction(cancellation_linearizable=True):
                return await self._replay_tombstoned_cancellation(root_run_id)
        root = await self._db.fetch_one(
            "SELECT id, status, parent_run_id, root_run_id, "
            "cancellation_epoch FROM ai_agent_runs WHERE id = ?",
            [root_run_id],
        )
        receipt = await self._db.fetch_one(
            "SELECT root_run_id FROM ai_agent_run_cancellations "
            "WHERE root_run_id = ?",
            [root_run_id],
        )
        active_tree = await self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE "
            "(id = ? OR root_run_id = ?) AND ("
            "status NOT IN ('done', 'failed', 'blocked', 'canceled') OR "
            "execution_owner_id IS NOT NULL OR "
            "lease_expires_at_ms IS NOT NULL OR heartbeat_at_ms IS NOT NULL)",
            [root_run_id, root_run_id],
        )
        active_delegations = await self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_delegations WHERE "
            "root_run_id = ? AND (status IN ('queued', 'claimed', 'running') "
            "OR worker_id IS NOT NULL OR claim_expires_at_ms IS NOT NULL)",
            [root_run_id],
        )
        active_unbound_hosts = await self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_host_child_runs "
            "WHERE run_id IS NULL AND json_valid(contract_json) "
            "AND json_extract(contract_json, '$.rootRunId') = ? AND "
            "(reservation_owner IS NOT NULL OR "
            "reservation_expires_at_ms IS NOT NULL)",
            [root_run_id],
        )

        epoch = int((root or {}).get("cancellation_epoch") or 0)
        valid_root = bool(
            root is not None
            and str(root.get("id") or "") == root_run_id
            and not str(root.get("parent_run_id") or "").strip()
            and str(root.get("root_run_id") or "").strip() == root_run_id
            and str(root.get("status") or "") == RunStatus.CANCELED.value
            and epoch > 0
        )
        if (
            not valid_root
            or receipt is not None
            or int((active_tree or {}).get("count") or 0) != 0
            or int((active_delegations or {}).get("count") or 0) != 0
            or int((active_unbound_hosts or {}).get("count") or 0) != 0
        ):
            raise ContractViolationError(
                "tombstoned cancellation audit is not quiescent"
            )
        return {
            "status": RunStatus.CANCELED.value,
            "newlyRequested": False,
            "childrenCanceled": 0,
            "terminalized": False,
            "cancellationStatus": "completed",
            "cancellationEpoch": epoch,
            "cancellationReceipt": "tombstoned",
        }

    async def _terminalize_without_live_executor(self, run_id: str) -> bool:
        claimed = await claim_run_for_cancellation(
            self._db,
            run_id=run_id,
            owner_id=self._composition.execution_owner_id,
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
                self._composition.execution_lease_store.release(
                    run_id,
                    self._composition.execution_owner_id,
                )
            )
            raise
        return True


__all__ = [
    "AgentCancellationService",
    "RootCancellationParticipant",
    "RootCancellationTargetError",
]
