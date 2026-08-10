"""SQLite adapter for the Core delegation repository port."""

from __future__ import annotations

import json
from typing import Any, Mapping

from purra.contracts import (
    AgentDelegation,
    AgentRunResult,
    DelegationAggregation,
    DelegationClaim,
)
from infrastructure.persistence import delegation_store, run_store


class SqliteDelegationRepository:
    def __init__(self, db) -> None:
        self._db = db

    async def create(
        self,
        *,
        parent_run_id: str,
        agent_role: str,
        objective: str,
        input_payload: Mapping[str, Any] | None = None,
        required: bool = True,
        priority: int = 0,
        max_depth: int = 3,
    ) -> AgentDelegation:
        row = await delegation_store.create_delegation(
            self._db,
            parent_run_id=parent_run_id,
            agent_role=agent_role,
            objective=objective,
            input_payload=dict(input_payload or {}),
            required=required,
            priority=priority,
            max_depth=max_depth,
        )
        return _delegation(row)

    async def claim_next(
        self,
        *,
        parent_run_id: str,
        worker_id: str,
        max_parallel_children: int,
        agent_role: str | None = None,
    ) -> DelegationClaim | None:
        row = await delegation_store.claim_next(
            self._db,
            parent_run_id=parent_run_id,
            worker_id=worker_id,
            max_parallel_children=max_parallel_children,
            agent_role=agent_role,
        )
        if row is None:
            return None
        parent = await run_store.get_run(self._db, parent_run_id)
        if parent is None:
            return None
        return DelegationClaim(
            delegation=_delegation(row),
            lineage=delegation_store.lineage_for_claim(
                row,
                parent_depth=int(parent.get("run_depth") or 0),
            ),
        )

    async def claim(
        self,
        *,
        delegation_id: str,
        parent_run_id: str,
        worker_id: str,
        max_parallel_children: int,
    ) -> DelegationClaim | None:
        row = await delegation_store.claim_delegation(
            self._db,
            delegation_id=delegation_id,
            parent_run_id=parent_run_id,
            worker_id=worker_id,
            max_parallel_children=max_parallel_children,
        )
        if row is None:
            return None
        parent = await run_store.get_run(self._db, parent_run_id)
        if parent is None:
            return None
        return DelegationClaim(
            delegation=_delegation(row),
            lineage=delegation_store.lineage_for_claim(
                row,
                parent_depth=int(parent.get("run_depth") or 0),
            ),
        )

    async def attach_child_run(
        self,
        *,
        delegation_id: str,
        child_run_id: str,
        worker_id: str,
    ) -> bool:
        return await delegation_store.attach_child_run(
            self._db,
            delegation_id=delegation_id,
            child_run_id=child_run_id,
            worker_id=worker_id,
        )

    async def record_result(
        self,
        *,
        delegation_id: str,
        child_run_id: str,
        result: AgentRunResult,
    ) -> bool:
        return await delegation_store.record_result(
            self._db,
            delegation_id=delegation_id,
            child_run_id=child_run_id,
            result=result,
        )

    async def fail(
        self,
        *,
        delegation_id: str,
        worker_id: str,
        error: str,
    ) -> bool:
        return await delegation_store.fail_claim(
            self._db,
            delegation_id=delegation_id,
            worker_id=worker_id,
            error=error,
        )

    async def list_for_parent(self, parent_run_id: str) -> tuple[AgentDelegation, ...]:
        rows = await delegation_store.list_delegations(self._db, parent_run_id)
        return tuple(_delegation(row) for row in rows)

    async def aggregate(self, parent_run_id: str) -> DelegationAggregation:
        rows = await delegation_store.list_delegations(self._db, parent_run_id)
        value = delegation_store.aggregate(rows)
        return DelegationAggregation(
            state=value["state"],
            counts=value["counts"],
            required_failures=tuple(value["requiredFailures"]),
            results=tuple(value["results"]),
        )

    async def cancel_children(self, parent_run_id: str) -> int:
        return await delegation_store.cancel_children(self._db, parent_run_id)


def _delegation(row: dict[str, Any]) -> AgentDelegation:
    return AgentDelegation(
        id=str(row["id"]),
        parent_run_id=str(row["parent_run_id"]),
        root_run_id=str(row["root_run_id"]),
        child_run_id=row.get("child_run_id"),
        agent_role=str(row["agent_role"]),
        objective=str(row["objective"]),
        input_payload=(
            json.loads(str(row.get("input_json") or "{}"))
            if row.get("input_json")
            else {}
        ),
        status=str(row["status"]),  # type: ignore[arg-type]
        required=bool(row.get("required")),
        priority=int(row.get("priority") or 0),
        result_summary=row.get("result_summary"),
        error=row.get("error"),
        claim_attempt=int(row.get("claim_attempt") or 0),
        created_at=row.get("create_time"),
        updated_at=row.get("update_time"),
    )
