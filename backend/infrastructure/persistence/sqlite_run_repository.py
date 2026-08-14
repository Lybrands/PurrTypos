"""SQLite implementation of the PurrA RunRepository port."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Sequence
from uuid import uuid4

from purra.contracts import (
    RunCreateParams,
    RunId,
    RunStatus,
    TaskStep,
    TaskStepUpdate,
    TerminalRunStatus,
    TraceRecord,
)
from purra.errors import ContractViolationError, RunCancellationConflictError
from purra.events import AgentEvent, CoreEventType
from purra.json_values import thaw_json_mapping
from purra.ports import (
    CONTROLLER_OWNED_RUN_EVENT_TYPES,
    RunBeginResult,
    RunCommit,
    DelegationRepository,
    validate_run_commit_lifecycle,
)
from infrastructure.persistence import run_store
from infrastructure.persistence.sqlite_delegation_repository import (
    SqliteDelegationRepository,
)
from infrastructure.persistence.run_execution_store import now_ms


DEFAULT_RUN_LEASE_DURATION_MS = 30_000


class SqliteRunRepository:
    def __init__(
        self,
        db,
        *,
        owner_id: str | None = None,
        lease_duration_ms: int = DEFAULT_RUN_LEASE_DURATION_MS,
        delegation_repository: DelegationRepository | None = None,
    ):
        self._db = db
        self._write_lock = asyncio.Lock()
        self._owner_id = str(owner_id or f"executor-{uuid4().hex}").strip()
        self._lease_duration_ms = int(lease_duration_ms)
        self._delegations = (
            delegation_repository or SqliteDelegationRepository(db)
        )
        if self._lease_duration_ms <= 0:
            raise ValueError("lease duration must be positive")

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def lease_duration_ms(self) -> int:
        return self._lease_duration_ms

    async def begin(
        self,
        params: RunCreateParams,
        started_event: AgentEvent,
    ) -> RunBeginResult:
        async with self.write_transaction():
            return await self.begin_in_ambient_transaction(
                params,
                started_event,
            )

    async def begin_in_ambient_transaction(
        self,
        params: RunCreateParams,
        started_event: AgentEvent,
        *,
        persist_legacy_event: bool = True,
    ) -> RunBeginResult:
        if started_event.run_id is not None:
            raise ContractViolationError("run.started template must not have a run_id")
        if started_event.type != CoreEventType.RUN_STARTED:
            raise ContractViolationError("run begin requires a run.started event")
        if not self._db.current_task_owns_transaction():
            raise RuntimeError("run begin requires an ambient transaction")
        run_id = await self.create(params)
        persisted_event = AgentEvent(
            type=started_event.type,
            run_id=run_id,
            payload=started_event.payload,
        )
        if persist_legacy_event:
            await self._append_event_unchecked(run_id, persisted_event)
        return RunBeginResult(run_id=run_id, event=persisted_event)

    async def commit(
        self,
        run_id: RunId,
        commit: RunCommit,
    ) -> tuple[AgentEvent, ...]:
        normalized_run_id = self._validate_commit(run_id, commit)

        async with self.write_transaction():
            await self.apply_commit_in_ambient_transaction(
                normalized_run_id,
                commit,
            )
            persisted_events = []
            for event in commit.events:
                persisted_events.append(
                    await self._append_event_unchecked(
                        normalized_run_id,
                        event,
                    )
                )
        return tuple(persisted_events)

    def _validate_commit(self, run_id: RunId, commit: RunCommit) -> str:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ContractViolationError("run commit requires a run_id")
        for event in commit.events:
            if event.run_id != normalized_run_id:
                raise ContractViolationError(
                    "commit event run_id does not match repository run_id"
                )
        try:
            validate_run_commit_lifecycle(commit)
        except ValueError as error:
            raise ContractViolationError(str(error)) from error
        return normalized_run_id

    @asynccontextmanager
    async def write_transaction(self) -> AsyncIterator[None]:
        """Serialize a Run mutation with every persistence side effect."""
        async with self._write_lock:
            async with self._db.transaction(cancellation_linearizable=True):
                yield

    async def apply_commit_in_ambient_transaction(
        self,
        run_id: RunId,
        commit: RunCommit,
    ) -> None:
        """Apply Run state without writing the legacy event envelope."""
        normalized_run_id = self._validate_commit(run_id, commit)
        if not self._db.current_task_owns_transaction():
            raise RuntimeError("run commit requires an ambient transaction")

        current = await self._db.fetch_one(
            "SELECT status, execution_owner_id, lease_expires_at_ms, "
            "cancel_requested_at_ms "
            "FROM ai_agent_runs WHERE id = ?",
            [normalized_run_id],
        )
        if current is None:
            raise ContractViolationError(
                f"run {normalized_run_id!r} does not exist"
            )
        if current["status"] != RunStatus.RUNNING.value:
            raise ContractViolationError("terminal run cannot be mutated")
        if current.get("execution_owner_id") != self._owner_id:
            raise ContractViolationError(
                "run execution lease is owned by another executor"
            )
        if int(current.get("lease_expires_at_ms") or 0) <= now_ms():
            raise ContractViolationError("run execution lease has expired")
        if (
            current.get("cancel_requested_at_ms") is not None
            and commit.terminal_status is not None
            and commit.terminal_status is not RunStatus.CANCELED
        ):
            raise RunCancellationConflictError(
                "cancel-requested run only accepts a canceled terminal commit"
            )

        if commit.replace_steps is not None:
            await self.replace_steps(normalized_run_id, commit.replace_steps)
        for update in commit.step_updates:
            await self.update_step(normalized_run_id, update)
        if commit.terminal_status is not None:
            await self.transition(
                normalized_run_id,
                commit.terminal_status,
                final_response=commit.final_response,
                error=commit.error,
            )

    async def create(self, params: RunCreateParams) -> RunId:
        created_at = now_ms()
        lineage = params.lineage
        async def create_row() -> RunId:
            return await run_store.create_run(
                self._db,
                session_id=_sqlite_session_id(params.session_id),
                prompt=params.prompt,
                mode=params.mode,
                provenance=params.provenance,
                binding=params.binding,
                execution_owner_id=self._owner_id,
                heartbeat_at_ms=created_at,
                lease_expires_at_ms=created_at + self._lease_duration_ms,
                parent_run_id=(lineage.parent_run_id if lineage else None),
                root_run_id=(lineage.root_run_id if lineage else None),
                delegation_id=(lineage.delegation_id if lineage else None),
                agent_role=(lineage.agent_role if lineage else None),
                run_depth=(lineage.depth if lineage else 0),
            )
        if lineage is None or lineage.delegation_id is None:
            return await create_row()
        async with self._db.transaction():
            run_id = await create_row()
            attached = await self._delegations.attach_child_run(
                delegation_id=lineage.delegation_id,
                child_run_id=run_id,
                worker_id=self._owner_id,
            )
            if not attached:
                raise ContractViolationError(
                    "child run could not attach to the claimed delegation"
                )
        return run_id

    async def bind_conversation(self, run_id: RunId, conversation_id: int) -> None:
        await run_store.set_run_conversation_id(self._db, run_id, conversation_id)

    async def replace_steps(self, run_id: RunId, steps: Sequence[TaskStep]) -> None:
        await run_store.upsert_todos(
            self._db,
            run_id,
            [_storage_step(step) for step in steps],
        )

    async def update_step(self, run_id: RunId, update: TaskStepUpdate) -> None:
        updated = await run_store.update_todo_status(
            self._db,
            run_id,
            update.step_id,
            update.status.value,
            result_summary=update.result_summary,
            error=update.error,
        )
        if updated is None:
            raise ContractViolationError(
                f"step {update.step_id!r} does not exist for run {run_id!r}"
            )

    async def transition(
        self,
        run_id: RunId,
        status: TerminalRunStatus,
        *,
        final_response: str | None = None,
        error: str | None = None,
    ) -> None:
        if status is RunStatus.DONE:
            await run_store.complete_run(
                self._db,
                run_id,
                final_response=final_response or "",
            )
            return
        if status is RunStatus.FAILED:
            if error is None or not str(error).strip():
                raise ContractViolationError("failed run transition requires a non-empty error")
            await run_store.fail_run(
                self._db,
                run_id,
                error=error,
            )
            return
        if status is RunStatus.BLOCKED:
            await run_store.block_run(self._db, run_id)
            return
        if status is RunStatus.CANCELED:
            await run_store.cancel_run(self._db, run_id)
            return
        if status is RunStatus.RUNNING:
            raise ContractViolationError("run repository cannot reopen a run")
        raise ContractViolationError(f"unsupported run status: {status}")

    async def append_event(self, run_id: RunId, event: AgentEvent) -> None:
        if event.run_id is not None and event.run_id != run_id:
            raise ContractViolationError("event run_id does not match repository run_id")
        if event.type in CONTROLLER_OWNED_RUN_EVENT_TYPES:
            raise ContractViolationError(
                f"event type {event.type!r} is owned by AgentRunController"
            )
        async with self._write_lock:
            async with self._db.transaction():
                await self._append_event_unchecked(run_id, event)

    async def _append_event_unchecked(
        self,
        run_id: RunId,
        event: AgentEvent,
    ) -> AgentEvent:
        if event.run_id is not None and event.run_id != run_id:
            raise ContractViolationError("event run_id does not match repository run_id")
        await run_store.append_event(
            self._db,
            run_id,
            event.type,
            thaw_json_mapping(event.payload),
        )
        return event

    async def append_trace(self, run_id: RunId, trace: TraceRecord) -> None:
        await run_store.append_trace(
            self._db,
            run_id,
            stage=trace.stage,
            outcome=trace.outcome,
            details=thaw_json_mapping(trace.details),
            duration_ms=trace.duration_ms,
        )


def _storage_step(step: TaskStep) -> dict:
    return {
        "id": step.id,
        "title": step.title,
        "type": step.type.value,
        "status": step.status.value,
        "executor": step.executor.value,
        "riskLevel": step.risk_level.value if step.risk_level else None,
        "suggestedTools": list(step.suggested_tools),
        "agentRole": step.agent_role,
        "assignment": thaw_json_mapping(step.assignment),
        "dependsOn": list(step.depends_on),
        "description": step.description,
        "resultSummary": step.result_summary,
        "error": step.error,
        "protocolPrivate": step.protocol_private,
        "planningCapability": step.planning_capability,
    }


def _sqlite_session_id(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ContractViolationError("SQLite session_id must be an integer")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text and text.isdecimal():
        return int(text)
    raise ContractViolationError("SQLite session_id must be an integer or numeric string")
