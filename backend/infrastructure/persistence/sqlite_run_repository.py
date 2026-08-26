"""SQLite implementation of the PurrA RunRepository port."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from typing import AsyncIterator, Sequence
from uuid import uuid4

from purra.contracts import (
    ModelTokenUsage,
    RunCreateParams,
    RunId,
    RunStatus,
    TaskStep,
    TaskSpec,
    TaskStepUpdate,
    TerminalRunStatus,
    TraceRecord,
    RuntimeLimits,
)
from purra.api import AgentExecutionCheckpoint
from purra.errors import ContractViolationError, RunCancellationConflictError
from purra.events import AgentEvent, CoreEventType
from purra.json_values import thaw_json_mapping
from purra.ports import (
    CONTROLLER_OWNED_RUN_EVENT_TYPES,
    RunBeginResult,
    RunBudgetSnapshot,
    RunCommit,
    validate_run_commit_lifecycle,
)
from purra.run_state import RunSnapshot
from infrastructure.persistence import run_store
from infrastructure.persistence.run_execution_store import now_ms


DEFAULT_RUN_LEASE_DURATION_MS = 30_000


class SqliteRunRepository:
    def __init__(
        self,
        db,
        *,
        owner_id: str | None = None,
        lease_duration_ms: int = DEFAULT_RUN_LEASE_DURATION_MS,
    ):
        self._db = db
        self._write_lock = asyncio.Lock()
        self._owner_id = str(owner_id or f"executor-{uuid4().hex}").strip()
        self._lease_duration_ms = int(lease_duration_ms)
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

    async def get(self, run_id: RunId) -> RunSnapshot:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ContractViolationError("run id is required", code="run_not_found")
        async with self._db.transaction(write=False):
            row = await run_store.get_run(self._db, normalized_run_id)
            if row is None:
                raise ContractViolationError(
                    f"run {normalized_run_id!r} does not exist",
                    code="run_not_found",
                )
            steps = tuple(
                _task_step(item)
                for item in await run_store.get_run_todos(
                    self._db,
                    normalized_run_id,
                )
            )
        status = RunStatus(str(row["status"]))
        return RunSnapshot(
            run_id=normalized_run_id,
            title=str(row.get("plan_title") or "To-dos"),
            goal=row.get("plan_goal"),
            status=status,
            task_spec=_task_spec(row.get("task_spec_json")),
            steps=steps,
            work_step_ids=_text_tuple_or_none(row.get("work_step_ids_json")),
            final_response=(
                str(row.get("final_response") or "")
                if status is RunStatus.DONE
                else ""
            ),
            error=row.get("error"),
            execution_checkpoint=_checkpoint(
                row.get("execution_checkpoint_json")
            ),
            agent_preset_snapshot=_json_mapping(
                row.get("agent_preset_snapshot_json") or "{}",
                "Run agent preset snapshot",
            ),
        )

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

        if commit.replace_plan is not None:
            plan = commit.replace_plan
            await self._db.execute(
                "UPDATE ai_agent_runs SET plan_title = ?, plan_goal = ?, "
                "task_spec_json = ?, work_step_ids_json = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [
                    plan.title,
                    plan.goal,
                    (
                        json.dumps(
                            plan.task_spec.to_mapping(),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        if plan.task_spec is not None
                        else None
                    ),
                    json.dumps(
                        list(plan.work_step_ids),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    normalized_run_id,
                ],
            )
            await self.replace_steps(
                normalized_run_id,
                plan.steps,
            )
        for update in commit.step_updates:
            await self.update_step(normalized_run_id, update)
        if commit.execution_checkpoint is not None:
            await self._store_checkpoint(
                normalized_run_id,
                commit.execution_checkpoint,
            )
        if commit.terminal_status is not None:
            await self.transition(
                normalized_run_id,
                commit.terminal_status,
                final_response=commit.final_response,
                error=commit.error,
            )

    async def _store_checkpoint(
        self,
        run_id: str,
        checkpoint: AgentExecutionCheckpoint,
    ) -> None:
        if checkpoint.run_id != run_id:
            raise ContractViolationError(
                "Agent execution checkpoint belongs to another Run",
                code="agent_execution_checkpoint_conflict",
            )
        row = await self._db.fetch_one(
            "SELECT execution_checkpoint_json FROM ai_agent_runs WHERE id = ?",
            [run_id],
        )
        current = _checkpoint(
            row.get("execution_checkpoint_json") if row else None
        )
        if current is not None:
            if checkpoint.next_round < current.next_round:
                raise ContractViolationError(
                    "Agent execution checkpoint cannot move backwards",
                    code="agent_execution_checkpoint_conflict",
                )
            if checkpoint.next_round == current.next_round and checkpoint != current:
                raise ContractViolationError(
                    "Agent execution checkpoint content conflicts",
                    code="agent_execution_checkpoint_conflict",
                )
        await self._db.execute(
            "UPDATE ai_agent_runs SET execution_checkpoint_json = ?, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [
                json.dumps(
                    checkpoint.to_mapping(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                run_id,
            ],
        )

    async def create(self, params: RunCreateParams) -> RunId:
        if not self._db.current_task_owns_transaction():
            async with self.write_transaction():
                return await self._create_in_ambient_transaction(params)
        return await self._create_in_ambient_transaction(params)

    async def _create_in_ambient_transaction(
        self,
        params: RunCreateParams,
    ) -> RunId:
        if not self._db.current_task_owns_transaction():
            raise RuntimeError("run creation requires an ambient transaction")
        run_id = params.requested_run_id or run_store.new_run_id()
        root_run_id = params.root_run_id or run_id
        agent_id = params.agent_id or run_id
        if root_run_id == run_id:
            if params.parent_run_id is not None:
                raise ContractViolationError(
                    "Root Run cannot have parent_run_id",
                    code="run_scope_conflict",
                )
        else:
            root = await self._db.fetch_one(
                "SELECT id, root_run_id FROM ai_agent_runs WHERE id = ?",
                [root_run_id],
            )
            if root is None or str(root.get("root_run_id") or root["id"]) != (
                root_run_id
            ):
                raise ContractViolationError(
                    "Run scope root is not a Root Run",
                    code="run_scope_conflict",
                )
            if params.parent_run_id is None:
                raise ContractViolationError(
                    "Child Run scope requires parent_run_id",
                    code="run_scope_conflict",
                )
            parent = await self._db.fetch_one(
                "SELECT root_run_id FROM ai_agent_runs WHERE id = ?",
                [params.parent_run_id],
            )
            if parent is None or str(parent.get("root_run_id") or "") != (
                root_run_id
            ):
                raise ContractViolationError(
                    "Child Run parent belongs to a different Root scope",
                    code="run_scope_conflict",
                )
        created_at = now_ms()
        try:
            return await run_store.create_run(
                self._db,
                run_id=run_id,
                session_id=_sqlite_session_id(params.session_id),
                prompt=params.prompt,
                mode=params.mode,
                provenance=params.provenance,
                binding=params.binding,
                root_run_id=root_run_id,
                agent_id=agent_id,
                parent_run_id=params.parent_run_id,
                agent_tree_lease_owner_id=params.lease_owner_id,
                agent_tree_lease_epoch=params.lease_epoch,
                execution_owner_id=self._owner_id,
                heartbeat_at_ms=created_at,
                lease_expires_at_ms=created_at + self._lease_duration_ms,
                deadline_at_ms=params.deadline_at_ms,
                runtime_limits=params.runtime_limits,
                agent_preset_snapshot=params.agent_preset_snapshot,
            )
        except sqlite3.IntegrityError as error:
            if params.requested_run_id is not None and await self._db.fetch_one(
                "SELECT 1 AS value FROM ai_agent_runs WHERE id = ?",
                [run_id],
            ):
                raise ContractViolationError(
                    "requested Run id already exists",
                    code="run_identity_conflict",
                ) from error
            raise

    async def reserve_model_attempt(
        self,
        run_id: RunId,
        invocation_id: str,
    ) -> RunBudgetSnapshot:
        normalized_run_id = str(run_id or "").strip()
        normalized_invocation = str(invocation_id or "").strip()
        if not normalized_run_id or not normalized_invocation:
            raise ContractViolationError(
                "run id and model invocation id are required"
            )
        async with self.write_transaction():
            run, root = await self._require_budget_scope(normalized_run_id)
            existing = await self._db.fetch_one(
                "SELECT 1 FROM ai_agent_run_model_attempts "
                "WHERE run_id = ? AND invocation_id = ?",
                [normalized_run_id, normalized_invocation],
            )
            if existing is not None:
                return _run_budget_snapshot(run)
            limits = _runtime_limits(root)
            root_snapshot = await self._root_budget_snapshot(root["id"])
            if root_snapshot.model_attempts >= (
                limits.max_model_invocation_attempts
            ):
                raise ContractViolationError(
                    "Root Run model invocation budget was exceeded",
                    code="runtime_budget_exceeded",
                    details={"budgetKind": "model_attempts"},
                )
            await self._db.execute(
                "INSERT INTO ai_agent_run_model_attempts "
                "(run_id, invocation_id) VALUES (?, ?)",
                [normalized_run_id, normalized_invocation],
            )
            await self._db.execute(
                "UPDATE ai_agent_runs SET model_attempt_count = "
                "model_attempt_count + 1, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                [normalized_run_id],
            )
            updated = await self._db.fetch_one(
                "SELECT * FROM ai_agent_runs WHERE id = ?",
                [normalized_run_id],
            )
            return _run_budget_snapshot(updated)

    async def settle_model_attempt(
        self,
        run_id: RunId,
        invocation_id: str,
        usage: ModelTokenUsage | None,
    ) -> RunBudgetSnapshot:
        normalized_run_id = str(run_id or "").strip()
        normalized_invocation = str(invocation_id or "").strip()
        if usage is not None and not isinstance(usage, ModelTokenUsage):
            raise TypeError("model attempt usage must be ModelTokenUsage")
        violation: ContractViolationError | None = None
        async with self.write_transaction():
            run, root = await self._require_budget_scope(normalized_run_id)
            attempt = await self._db.fetch_one(
                "SELECT settled, usage_json FROM ai_agent_run_model_attempts "
                "WHERE run_id = ? AND invocation_id = ?",
                [normalized_run_id, normalized_invocation],
            )
            if attempt is None:
                raise ContractViolationError(
                    "model attempt was not reserved",
                    code="model_attempt_not_reserved",
                )
            usage_payload = _model_usage_payload(usage)
            if int(attempt.get("settled") or 0):
                if _json_value(attempt.get("usage_json")) != usage_payload:
                    raise ContractViolationError("model attempt usage conflicts")
                snapshot = _run_budget_snapshot(run)
            else:
                await self._db.execute(
                    "UPDATE ai_agent_run_model_attempts SET settled = 1, "
                    "usage_json = ?, update_time = CURRENT_TIMESTAMP "
                    "WHERE run_id = ? AND invocation_id = ?",
                    [
                        json.dumps(usage_payload, separators=(",", ":")),
                        normalized_run_id,
                        normalized_invocation,
                    ],
                )
                await self._db.execute(
                    "UPDATE ai_agent_runs SET "
                    "unreported_usage_attempts = unreported_usage_attempts + ?, "
                    "input_tokens = input_tokens + ?, "
                    "output_tokens = output_tokens + ?, "
                    "reasoning_tokens = reasoning_tokens + ?, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                    [
                        int(usage is None),
                        usage.input_tokens if usage is not None else 0,
                        usage.output_tokens if usage is not None else 0,
                        usage.reasoning_output_tokens if usage is not None else 0,
                        normalized_run_id,
                    ],
                )
                run = await self._db.fetch_one(
                    "SELECT * FROM ai_agent_runs WHERE id = ?",
                    [normalized_run_id],
                )
                snapshot = _run_budget_snapshot(run)
            root_snapshot = await self._root_budget_snapshot(root["id"])
            violation = _run_budget_violation(
                _runtime_limits(root),
                root_snapshot,
            )
        if violation is not None:
            raise violation
        return snapshot

    async def _require_budget_scope(self, run_id: str):
        run = await self._db.fetch_one(
            "SELECT * FROM ai_agent_runs WHERE id = ?",
            [run_id],
        )
        if run is None:
            raise ContractViolationError(f"run {run_id!r} does not exist")
        root_run_id = str(run.get("root_run_id") or run["id"])
        root = await self._db.fetch_one(
            "SELECT * FROM ai_agent_runs WHERE id = ?",
            [root_run_id],
        )
        if root is None or str(root.get("root_run_id") or root["id"]) != (
            root_run_id
        ):
            raise ContractViolationError(
                "Run scope root is not a Root Run",
                code="run_scope_conflict",
            )
        if root.get("deadline_at_ms") is not None and int(
            root["deadline_at_ms"]
        ) <= now_ms():
            raise ContractViolationError(
                "Root Run deadline was exceeded",
                code="run_deadline_exceeded",
                details={"runId": root_run_id},
            )
        return run, root

    async def _root_budget_snapshot(self, root_run_id: str) -> RunBudgetSnapshot:
        row = await self._db.fetch_one(
            "SELECT COALESCE(SUM(model_attempt_count), 0) "
            "AS model_attempt_count, "
            "COALESCE(SUM(unreported_usage_attempts), 0) "
            "AS unreported_usage_attempts, "
            "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
            "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
            "COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens "
            "FROM ai_agent_runs WHERE root_run_id = ?",
            [root_run_id],
        )
        return _run_budget_snapshot(row)

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


def _task_step(value: dict) -> TaskStep:
    return TaskStep(
        id=str(value.get("id") or ""),
        title=str(value.get("title") or ""),
        type=str(value.get("type") or "analyze"),
        executor=str(value.get("executor") or "model"),
        status=str(value.get("status") or "pending"),
        risk_level=value.get("riskLevel"),
        suggested_tools=tuple(value.get("suggestedTools") or ()),
        depends_on=tuple(value.get("dependsOn") or ()),
        description=value.get("description"),
        result_summary=value.get("resultSummary"),
        error=value.get("error"),
        protocol_private=bool(value.get("protocolPrivate")),
        planning_capability=value.get("planningCapability"),
    )


def _task_spec(raw: str | None) -> TaskSpec | None:
    if raw is None:
        return None
    value = _json_mapping(raw, "Run task spec")
    return TaskSpec(
        goal=str(value.get("goal") or ""),
        target=value.get("target") or {},
        operation=value.get("operation"),
        instruction=value.get("instruction"),
        constraints=tuple(value.get("constraints") or ()),
        preserve=tuple(value.get("preserve") or ()),
        deliverable=value.get("deliverable"),
    )


def _checkpoint(raw: str | None) -> AgentExecutionCheckpoint | None:
    if raw is None:
        return None
    return AgentExecutionCheckpoint.from_mapping(
        _json_mapping(raw, "Agent execution checkpoint")
    )


def _text_tuple_or_none(raw: str | None) -> tuple[str, ...] | None:
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise ContractViolationError("Run work step ids are invalid") from error
    if not isinstance(value, list):
        raise ContractViolationError("Run work step ids must be a list")
    return tuple(str(item) for item in value)


def _json_mapping(raw: str, label: str) -> dict:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise ContractViolationError(f"{label} is invalid") from error
    if not isinstance(value, dict):
        raise ContractViolationError(f"{label} must be an object")
    return value


def _storage_step(step: TaskStep) -> dict:
    return {
        "id": step.id,
        "title": step.title,
        "type": step.type.value,
        "status": step.status.value,
        "executor": step.executor.value,
        "riskLevel": step.risk_level.value if step.risk_level else None,
        "suggestedTools": list(step.suggested_tools),
        "assignment": {},
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


def _runtime_limits(row) -> RuntimeLimits:
    raw = _json_value((row or {}).get("runtime_limits_json"))
    if not isinstance(raw, dict):
        raw = {}
    allowed = RuntimeLimits.__dataclass_fields__
    return RuntimeLimits(**{key: value for key, value in raw.items() if key in allowed})


def _run_budget_snapshot(row) -> RunBudgetSnapshot:
    value = row or {}
    return RunBudgetSnapshot(
        model_attempts=int(value.get("model_attempt_count") or 0),
        unreported_usage_attempts=int(
            value.get("unreported_usage_attempts") or 0
        ),
        input_tokens=int(value.get("input_tokens") or 0),
        output_tokens=int(value.get("output_tokens") or 0),
        reasoning_tokens=int(value.get("reasoning_tokens") or 0),
    )


def _model_usage_payload(usage: ModelTokenUsage | None):
    if usage is None:
        return None
    return {
        "inputTokens": usage.input_tokens,
        "outputTokens": usage.output_tokens,
        "totalTokens": usage.total_tokens,
        "cachedInputTokens": usage.cached_input_tokens,
        "reasoningOutputTokens": usage.reasoning_output_tokens,
    }


def _json_value(value):
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _run_budget_violation(
    limits: RuntimeLimits,
    snapshot: RunBudgetSnapshot,
) -> ContractViolationError | None:
    if snapshot.unreported_usage_attempts and any(
        limit is not None
        for limit in (
            limits.max_input_tokens,
            limits.max_output_tokens,
            limits.max_reasoning_tokens,
        )
    ):
        kind = "provider_usage_unreported"
    else:
        kind = next(
            (
                name
                for name, value, limit in (
                    ("input_tokens", snapshot.input_tokens, limits.max_input_tokens),
                    ("output_tokens", snapshot.output_tokens, limits.max_output_tokens),
                    (
                        "reasoning_tokens",
                        snapshot.reasoning_tokens,
                        limits.max_reasoning_tokens,
                    ),
                )
                if limit is not None and value > limit
            ),
            None,
        )
    if kind is None:
        return None
    return ContractViolationError(
        "Run model token budget was exceeded",
        code="runtime_budget_exceeded",
        details={"budgetKind": kind},
    )
