"""Persistence and execution ports for durable tasks."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from agent_core.long_tasks.contracts import (
    LongTaskCreateCommand,
    LongTaskRecord,
    LongTaskUnitRecord,
    LongTaskUnitResult,
)
from agent_core.ports import CancellationSignal


@runtime_checkable
class LongTaskRepository(Protocol):
    async def create(
        self,
        task_id: str,
        command: LongTaskCreateCommand,
    ) -> LongTaskRecord: ...

    async def load(self, task_id: str) -> LongTaskRecord | None: ...

    async def list_for_owner(
        self,
        *,
        namespace: str,
        owner_id: str,
        kind: str | None = None,
        limit: int = 20,
    ) -> Sequence[LongTaskRecord]: ...

    async def find_active(
        self,
        *,
        namespace: str,
        owner_id: str,
        kind: str,
        session_id: int | None = None,
        match_session: bool = False,
    ) -> LongTaskRecord | None: ...

    async def list_units(self, task_id: str) -> Sequence[LongTaskUnitRecord]: ...

    async def start(self, task_id: str, *, expected_revision: int) -> LongTaskRecord: ...

    async def claim_ready_unit(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_duration_ms: int,
    ) -> LongTaskUnitRecord | None: ...

    async def bind_unit_run(
        self,
        task_id: str,
        unit_id: str,
        *,
        worker_id: str,
        run_id: str,
    ) -> LongTaskUnitRecord: ...

    async def update_unit_progress(
        self,
        task_id: str,
        unit_id: str,
        *,
        worker_id: str,
        metadata: Mapping[str, Any],
    ) -> LongTaskUnitRecord: ...

    async def complete_unit(
        self,
        task_id: str,
        unit_id: str,
        *,
        worker_id: str,
        result: LongTaskUnitResult,
    ) -> LongTaskRecord: ...

    async def fail_unit(
        self,
        task_id: str,
        unit_id: str,
        *,
        worker_id: str,
        error_code: str,
        retryable: bool,
    ) -> LongTaskRecord: ...

    async def interrupt_unit(
        self,
        task_id: str,
        unit_id: str,
        *,
        worker_id: str,
        reason_code: str,
    ) -> LongTaskRecord: ...

    async def pause(self, task_id: str) -> LongTaskRecord: ...

    async def resume(self, task_id: str) -> LongTaskRecord: ...

    async def cancel(self, task_id: str) -> LongTaskRecord: ...

    async def finalize_if_complete(self, task_id: str) -> LongTaskRecord: ...


@runtime_checkable
class LongTaskUnitRunner(Protocol):
    async def run_unit(
        self,
        task: LongTaskRecord,
        unit: LongTaskUnitRecord,
        signal: CancellationSignal | None = None,
    ) -> LongTaskUnitResult: ...


__all__ = ["LongTaskRepository", "LongTaskUnitRunner"]
