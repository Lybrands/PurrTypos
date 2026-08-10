"""Persistence and durable coordination ports."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from purra.contracts import (
    AgentDelegation,
    AgentRunResult,
    DelegationAggregation,
    DelegationClaim,
    RunCheckpoint,
    RunExecutionLease,
    RunId,
)
from purra.ports.run_lifecycle import (
    CONTROLLER_OWNED_RUN_EVENT_TYPES,
    TERMINAL_RUN_EVENT_TYPES,
    RunBeginResult,
    RunCommit,
    RunRepository,
    validate_run_commit_lifecycle,
)
from purra.ports.projection import DomainEventProjector


@runtime_checkable
class ExecutionLeaseStore(Protocol):
    async def claim(
        self,
        run_id: RunId,
        owner_id: str,
        *,
        lease_duration_ms: int,
    ) -> bool: ...

    async def renew(
        self,
        run_id: RunId,
        owner_id: str,
        *,
        lease_duration_ms: int,
    ) -> bool: ...

    async def release(self, run_id: RunId, owner_id: str) -> bool: ...

    async def request_cancellation(self, run_id: RunId) -> bool: ...

    async def get(self, run_id: RunId) -> RunExecutionLease | None: ...


@runtime_checkable
class DelegationRepository(Protocol):
    async def create(
        self,
        *,
        parent_run_id: RunId,
        agent_role: str,
        objective: str,
        input_payload: Mapping[str, Any] | None = None,
        required: bool = True,
        priority: int = 0,
        max_depth: int = 3,
    ) -> AgentDelegation: ...

    async def claim_next(
        self,
        *,
        parent_run_id: RunId,
        worker_id: str,
        max_parallel_children: int,
        agent_role: str | None = None,
    ) -> DelegationClaim | None: ...

    async def claim(
        self,
        *,
        delegation_id: str,
        parent_run_id: RunId,
        worker_id: str,
        max_parallel_children: int,
    ) -> DelegationClaim | None: ...

    async def record_result(
        self,
        *,
        delegation_id: str,
        child_run_id: RunId,
        result: AgentRunResult,
    ) -> bool: ...

    async def attach_child_run(
        self,
        *,
        delegation_id: str,
        child_run_id: RunId,
        worker_id: str,
    ) -> bool: ...

    async def fail(
        self,
        *,
        delegation_id: str,
        worker_id: str,
        error: str,
    ) -> bool: ...

    async def list_for_parent(
        self,
        parent_run_id: RunId,
    ) -> tuple[AgentDelegation, ...]: ...

    async def aggregate(self, parent_run_id: RunId) -> DelegationAggregation: ...

    async def cancel_children(self, parent_run_id: RunId) -> int: ...


@runtime_checkable
class CheckpointStore(Protocol):
    async def load(
        self,
        run_id: RunId,
        *,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> RunCheckpoint | None: ...


__all__ = [name for name in globals() if not name.startswith("_")]
