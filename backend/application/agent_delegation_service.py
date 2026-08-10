"""Application API for durable parent/child Agent orchestration."""

from __future__ import annotations

from typing import Any

from purra.contracts import (
    AgentDelegation,
    AgentRunResult,
    DelegationAggregation,
    RunLineage,
)
from purra.ports import DelegationRepository
from domains.agent_roles import AgentRoleRegistry


class AgentDelegationService:
    def __init__(
        self,
        repository: DelegationRepository,
        *,
        role_registry: AgentRoleRegistry | None = None,
        max_depth: int = 3,
    ) -> None:
        self._repository = repository
        self._role_registry = role_registry
        self._max_depth = int(max_depth)

    async def delegate(
        self,
        *,
        parent_run_id: str,
        agent_role: str,
        objective: str,
        input_payload: dict[str, Any] | None = None,
        required: bool = True,
        priority: int = 0,
    ) -> dict[str, Any]:
        if self._role_registry is not None:
            self._role_registry.require(agent_role)
        row = await self._repository.create(
            parent_run_id=parent_run_id,
            agent_role=agent_role,
            objective=objective,
            input_payload=input_payload,
            required=required,
            priority=priority,
            max_depth=self._max_depth,
        )
        return _delegation_view(row, self._role_registry)

    async def claim(
        self,
        *,
        parent_run_id: str,
        worker_id: str,
        max_parallel_children: int,
        agent_role: str | None = None,
    ) -> tuple[dict[str, Any], RunLineage] | None:
        claimed = await self._repository.claim_next(
            parent_run_id=parent_run_id,
            worker_id=worker_id,
            max_parallel_children=max_parallel_children,
            agent_role=agent_role,
        )
        if claimed is None:
            return None
        return (
            _delegation_view(claimed.delegation, self._role_registry),
            claimed.lineage,
        )

    async def claim_delegation(
        self,
        *,
        delegation_id: str,
        parent_run_id: str,
        worker_id: str,
        max_parallel_children: int,
    ) -> tuple[dict[str, Any], RunLineage] | None:
        claimed = await self._repository.claim(
            delegation_id=delegation_id,
            parent_run_id=parent_run_id,
            worker_id=worker_id,
            max_parallel_children=max_parallel_children,
        )
        if claimed is None:
            return None
        return (
            _delegation_view(claimed.delegation, self._role_registry),
            claimed.lineage,
        )

    async def record_result(
        self,
        *,
        delegation_id: str,
        child_run_id: str,
        result: AgentRunResult,
    ) -> bool:
        return await self._repository.record_result(
            delegation_id=delegation_id,
            child_run_id=child_run_id,
            result=result,
        )

    async def fail_claim(
        self,
        *,
        delegation_id: str,
        worker_id: str,
        error: str,
    ) -> bool:
        return await self._repository.fail(
            delegation_id=delegation_id,
            worker_id=worker_id,
            error=error,
        )

    async def snapshot(self, parent_run_id: str) -> dict[str, Any]:
        rows = await self._repository.list_for_parent(parent_run_id)
        aggregate = _aggregate(rows, self._role_registry)
        return {
            "items": [
                _delegation_view(row, self._role_registry)
                for row in rows
            ],
            "aggregate": _aggregation_view(aggregate),
        }

    async def cancel_children(self, parent_run_id: str) -> int:
        return await self._repository.cancel_children(parent_run_id)


def _delegation_view(
    row: AgentDelegation,
    role_registry: AgentRoleRegistry | None = None,
) -> dict[str, Any]:
    return {
        "delegationId": row.id,
        "parentRunId": row.parent_run_id,
        "rootRunId": row.root_run_id,
        "childRunId": row.child_run_id,
        "agentRole": row.agent_role,
        "agentTitle": _delegation_title(row, role_registry),
        "objective": row.objective,
        "input": dict(row.input_payload),
        "status": row.status.value,
        "required": row.required,
        "priority": row.priority,
        "resultSummary": row.result_summary,
        "error": row.error,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def _aggregate(
    rows: tuple[AgentDelegation, ...],
    role_registry: AgentRoleRegistry | None = None,
) -> DelegationAggregation:
    counts = {
        status: sum(row.status.value == status for row in rows)
        for status in ("queued", "claimed", "running", "done", "failed", "canceled")
    }
    failures = tuple(
        row.id
        for row in rows
        if row.required and row.status.value in {"failed", "canceled"}
    )
    pending = counts["queued"] + counts["claimed"] + counts["running"]
    return DelegationAggregation(
        state=("pending" if pending else ("blocked" if failures else "ready")),  # type: ignore[arg-type]
        counts=counts,
        required_failures=failures,
        results=tuple(
            {
                "delegationId": row.id,
                "agentRole": row.agent_role,
                "agentTitle": _delegation_title(row, role_registry),
                "childRunId": row.child_run_id,
                "summary": row.result_summary or "",
            }
            for row in rows
            if row.status.value == "done"
        ),
    )


def _delegation_title(
    row: AgentDelegation,
    role_registry: AgentRoleRegistry | None,
) -> str:
    title = _role_title(row.agent_role, role_registry)
    unit_id = str(row.input_payload.get("unitId") or "").strip()
    try:
        attempt = max(1, int(row.input_payload.get("attempt") or 1))
    except (TypeError, ValueError):
        attempt = 1
    if unit_id:
        title += f" · {unit_id}"
    if attempt > 1:
        title += f" · 重试 {attempt - 1}"
    return title


def _aggregation_view(value: DelegationAggregation) -> dict[str, Any]:
    return {
        "state": value.state,
        "counts": dict(value.counts),
        "requiredFailures": list(value.required_failures),
        "results": [dict(item) for item in value.results],
    }


def _role_title(
    role_id: str,
    role_registry: AgentRoleRegistry | None,
) -> str:
    definition = role_registry.get(role_id) if role_registry else None
    return definition.title if definition else role_id
