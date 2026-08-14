"""Application-owned persistent cancellation for one Root Run tree."""

from __future__ import annotations

from infrastructure.persistence.run_execution_store import (
    fence_cancellation_tree,
    now_ms,
    terminalize_orphaned_run,
)


class AgentCancellationService:
    """Request cancellation for a Root and every persisted descendant."""

    def __init__(self, db, composition) -> None:
        self._db = db
        self._composition = composition

    async def cancel(self, run_id: str) -> dict[str, object] | None:
        normalized = str(run_id or "").strip()
        if not normalized:
            return None
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
            await terminalize_orphaned_run(
                self._db,
                target_id,
                reason="cancellation_requested_without_live_executor",
            )

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


__all__ = ["AgentCancellationService"]
