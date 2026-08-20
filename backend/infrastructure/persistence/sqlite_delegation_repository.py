"""SQLite adapter for one-Run delegated executions."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from purra.contracts import (
    AgentDelegation,
    DelegationAggregation,
    DelegationContextMode,
)


class SqliteDelegationRepository:
    def __init__(self, db) -> None:
        self._db = db

    async def create(
        self,
        *,
        run_id: str,
        batch_id: str,
        agent_name: str,
        agent_title: str,
        agent_instruction: str,
        objective: str,
        input_payload: Mapping[str, Any] | None = None,
        context_mode: DelegationContextMode = DelegationContextMode.ISOLATED,
        required: bool = True,
        priority: int = 0,
    ) -> AgentDelegation:
        delegation_id = f"delegation_{uuid4().hex[:16]}"
        await self._db.execute(
            "INSERT INTO ai_agent_delegations "
            "(id, run_id, batch_id, agent_name, agent_title, agent_instruction, "
            "objective, input_json, context_mode, required, priority) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                delegation_id,
                _required(run_id, "delegation Run id"),
                _required(batch_id, "delegation batch id"),
                _required(agent_name, "delegation agent name"),
                _required(agent_title, "delegation agent title"),
                _required(agent_instruction, "delegation agent instruction"),
                _required(objective, "delegation objective"),
                json.dumps(dict(input_payload or {}), ensure_ascii=False),
                DelegationContextMode(context_mode).value,
                int(bool(required)),
                int(priority),
            ],
        )
        return await self._require(delegation_id)

    async def start(
        self,
        delegation_id: str,
        *,
        run_id: str,
        batch_id: str,
    ) -> AgentDelegation | None:
        async with self._db.transaction(cancellation_linearizable=True):
            await self._db.execute(
                "UPDATE ai_agent_delegations SET status = 'running', "
                "update_time = CURRENT_TIMESTAMP WHERE id = ? AND run_id = ? "
                "AND batch_id = ? AND status = 'queued'",
                [delegation_id, run_id, batch_id],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            if int((changed or {}).get("count") or 0) != 1:
                return None
            return await self._require(delegation_id)

    async def complete(
        self,
        delegation_id: str,
        *,
        run_id: str,
        batch_id: str,
        result_summary: str,
    ) -> bool:
        return await self._settle(
            delegation_id,
            run_id=run_id,
            batch_id=batch_id,
            status="done",
            result_summary=str(result_summary or ""),
        )

    async def fail(
        self,
        delegation_id: str,
        *,
        run_id: str,
        batch_id: str,
        error: str,
    ) -> bool:
        return await self._settle(
            delegation_id,
            run_id=run_id,
            batch_id=batch_id,
            status="failed",
            error=_required(error, "delegation error"),
        )

    async def cancel(
        self,
        delegation_id: str,
        *,
        run_id: str,
        batch_id: str,
        reason: str,
    ) -> bool:
        return await self._settle(
            delegation_id,
            run_id=run_id,
            batch_id=batch_id,
            status="canceled",
            error=_required(reason, "delegation cancel reason"),
            active_statuses=("queued", "running"),
        )

    async def list_for_run(self, run_id: str) -> tuple[AgentDelegation, ...]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ai_agent_delegations WHERE run_id = ? "
            "ORDER BY create_time ASC, id ASC",
            [_required(run_id, "delegation Run id")],
        )
        return tuple(_delegation(row) for row in rows)

    async def aggregate_batch(
        self,
        run_id: str,
        batch_id: str,
    ) -> DelegationAggregation:
        rows = await self._db.fetch_all(
            "SELECT * FROM ai_agent_delegations WHERE run_id = ? AND batch_id = ? "
            "ORDER BY create_time ASC, id ASC",
            [run_id, batch_id],
        )
        counts = Counter(str(row.get("status") or "") for row in rows)
        required_failures = tuple(
            str(row["id"])
            for row in rows
            if bool(row.get("required"))
            and str(row.get("status") or "") in {"failed", "canceled"}
        )
        pending = counts["queued"] + counts["running"]
        state = "blocked" if required_failures else ("pending" if pending else "ready")
        results = tuple(
            {
                "delegationId": str(row["id"]),
                "agentName": str(row["agent_name"]),
                "agentTitle": str(row["agent_title"]),
                "resultSummary": str(row.get("result_summary") or ""),
            }
            for row in rows
            if str(row.get("status") or "") == "done"
        )
        return DelegationAggregation(
            state=state,
            counts=dict(counts),
            required_failures=required_failures,
            results=results,
        )

    async def cancel_batch(self, run_id: str, batch_id: str) -> int:
        async with self._db.transaction(cancellation_linearizable=True):
            await self._db.execute(
                "UPDATE ai_agent_delegations SET status = 'canceled', "
                "error = 'delegation_batch_canceled', update_time = CURRENT_TIMESTAMP "
                "WHERE run_id = ? AND batch_id = ? AND status IN ('queued', 'running')",
                [run_id, batch_id],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            return int((changed or {}).get("count") or 0)

    async def recover_after_restart(self) -> int:
        """Fail process-owned executions that cannot survive a restart."""

        async with self._db.transaction(cancellation_linearizable=True):
            await self._db.execute(
                "UPDATE ai_agent_delegations SET status = 'failed', "
                "error = 'delegation_interrupted_by_restart', "
                "update_time = CURRENT_TIMESTAMP "
                "WHERE status IN ('queued', 'running')"
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            return int((changed or {}).get("count") or 0)

    async def _settle(
        self,
        delegation_id: str,
        *,
        run_id: str,
        batch_id: str,
        status: str,
        result_summary: str | None = None,
        error: str | None = None,
        active_statuses: tuple[str, ...] = ("running",),
    ) -> bool:
        placeholders = ",".join("?" for _ in active_statuses)
        async with self._db.transaction(cancellation_linearizable=True):
            await self._db.execute(
                "UPDATE ai_agent_delegations SET status = ?, result_summary = ?, "
                "error = ?, update_time = CURRENT_TIMESTAMP WHERE id = ? "
                "AND run_id = ? AND batch_id = ? "
                f"AND status IN ({placeholders})",
                [
                    status,
                    result_summary,
                    error,
                    delegation_id,
                    run_id,
                    batch_id,
                    *active_statuses,
                ],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            return int((changed or {}).get("count") or 0) == 1

    async def _require(self, delegation_id: str) -> AgentDelegation:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_delegations WHERE id = ?",
            [delegation_id],
        )
        if row is None:
            raise LookupError("delegation does not exist")
        return _delegation(row)


def _delegation(row: dict[str, Any]) -> AgentDelegation:
    return AgentDelegation(
        id=str(row["id"]),
        batch_id=str(row["batch_id"]),
        run_id=str(row["run_id"]),
        agent_name=str(row["agent_name"]),
        agent_title=str(row["agent_title"]),
        agent_instruction=str(row["agent_instruction"]),
        objective=str(row["objective"]),
        input_payload=json.loads(str(row.get("input_json") or "{}")),
        context_mode=str(row.get("context_mode") or "isolated"),
        status=str(row.get("status") or "queued"),
        required=bool(row.get("required")),
        priority=int(row.get("priority") or 0),
        result_summary=row.get("result_summary"),
        error=row.get("error"),
        created_at=row.get("create_time"),
        updated_at=row.get("update_time"),
    )


def _required(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


__all__ = ["SqliteDelegationRepository"]
