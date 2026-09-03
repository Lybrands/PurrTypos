"""Application service for the screenplay Agent on PurrA durable tasks.

The model Planner owns semantic interpretation. The host validates that intent
against project truth, compiles a product recipe, and delegates durable
execution to PurrA.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    MessageRole,
    AgentRunResult,
    RunBinding,
    RunStatus,
    ExecutionRecipe,
    ExecutionRecipeStep,
    StepStatus,
)
from purra.api import (
    AgentCoreRunOptions,
    DurableTaskContinuation,
    RunRecoverySnapshot,
)
from purra.errors import (
    ModelGatewayError,
    RunCommitProjectionError,
    UnsupportedModelFeatureError,
)
from purra.model_protocol import FeatureRequirement, TaskCapabilityRequirements
from purra.task_admission import (
    ExecutionMode,
    LongTaskDispatchReceipt,
    LongTaskExecutionUpdate,
    TaskAdmissionDecision,
)
from purra.output import RuntimeOutputEvent
from purra.model_protocol import resolve_invocation_output_limit
from purra.output import (
    PublicPresentationMode,
    ResponseTransactionMode,
    ResponseTransactionPolicy,
)
from application.agent_run_service import AgentRunService
from application.agent_cancellation_service import AgentCancellationService
from application.screenplay_agent_task_executor import ScreenplayTaskUnitExecutor
from application.screenplay_checkpoint_planning import (
    SqliteScreenplayCheckpointRepository,
)
from application.model_runtime import (
    fit_output_limit_to_context,
    model_request_from_runtime,
    reasoning_mode_from_options,
)
from application.request_mapping import context_window_tokens
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from domains.screenplay_agent import (
    ContinuationStartLost,
    ScreenplayIntentCommandMismatchError,
    ScreenplayStageCommand,
    ScreenplayRootStartLost,
)
from exceptions import AppError, NotFoundError
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from infrastructure.persistence.sqlite_screenplay_agent_repository import (
    SqliteScreenplayAgentRepository,
)
from infrastructure.persistence.sqlite_screenplay_operation_repository import (
    SqliteScreenplayOperationRepository,
)
from infrastructure.persistence.run_execution_store import (
    SqliteRunControlStore,
    now_ms,
)


_ACTIVE_TASKS: dict[str, asyncio.Task[None]] = {}


class ScreenplayAgentService:
    def __init__(
        self,
        db,
        *,
        owner_id: str,
        composition=None,
        unit_executor_factory: Callable[[Any], object] | None = None,
        projects,
        repository=None,
        output_processor=None,
        track_background=None,
    ) -> None:
        self._db = db
        self._repository = (
            repository
            or SqliteScreenplayAgentRepository(db, owner_id=owner_id)
        )
        self._owner_id = str(owner_id)
        self._composition = composition
        self._run_control = (
            composition.run_control_store
            if composition is not None
            else SqliteRunControlStore(db)
        )
        self._run_service = (
            AgentRunService(composition) if composition is not None else None
        )
        self._unit_executor_factory = (
            unit_executor_factory or self._default_unit_executor
        )
        self._projects = projects
        self._track_background = track_background
        self._output_processor = output_processor
        self._long_tasks = SqliteLongTaskRepository(db)
        self._operations = SqliteScreenplayOperationRepository(db)
        self._cancellation = (
            AgentCancellationService(db, composition)
            if composition is not None else None
        )
        self._truncate_wait_timeout_seconds = 5.0
        self._truncate_poll_interval_seconds = 0.05

    async def submit_turn(
        self,
        *,
        command_id: str,
        project_id: str,
        request,
    ) -> dict[str, Any]:
        turn = await self._repository.begin_turn(
            command_id=str(command_id or "").strip(),
            project_id=str(project_id or "").strip(),
            session_id=request.sessionId,
            content=request.content,
            stage_command=(
                ScreenplayStageCommand.from_mapping(
                    request.stageCommand.model_dump(mode="json")
                ).to_mapping()
                if request.stageCommand is not None
                else None
            ),
            runtime_profile={
                "provider": request.runtime.apiProvider,
                "model": str(request.runtime.options.get("model") or ""),
                "contextWindow": request.runtime.contextWindow,
            },
        )
        return turn

    def dispatch_turn(self, turn_id: str, runtime) -> asyncio.Task[None]:
        active = _ACTIVE_TASKS.get(f"turn:{turn_id}")
        if active is not None and not active.done():
            return active
        task = asyncio.create_task(self.execute_turn(turn_id, runtime))
        self._remember_task(f"turn:{turn_id}", task)
        if callable(self._track_background):
            self._track_background(task)
        return task

    async def execute_turn(self, turn_id: str, runtime) -> None:
        if not await self._repository.claim_turn(turn_id):
            return
        turn = await self._repository.load_turn(turn_id)
        if turn is None:
            return
        if self._run_service is None or self._composition is None:
            await self._settle_execution_exception(
                turn_id,
                RuntimeError("screenplay Root Run composition is required"),
            )
            return
        try:
            request = _root_request(turn, runtime)
            model_request = request.model
            window = int(request.context_window or 200_000)
            output_limit = resolve_invocation_output_limit(
                model_request.capability_snapshot,
                model_request.options.get("max_tokens"),
            )
            output_limit = fit_output_limit_to_context(output_limit, window)
            lifecycle = _ScreenplayTurnRunLifecycle(
                self._db,
                self._repository,
                self._operations,
                turn_id,
            )
            async for _update in self._run_service.run(
                request=request,
                api_key=runtime.apiKey.get_secret_value(),
                options=AgentCoreRunOptions(
                    turn_id=turn_id,
                    output_limit=output_limit,
                    default_context_window_tokens=window,
                    force_planned_tool_choice=False,
                    require_tool_call=False,
                    reasoning_mode=reasoning_mode_from_options(runtime.options),
                    binding=RunBinding(
                        namespace="screenplay.conversation_turn",
                        aggregate_id=str(turn["projectId"]),
                        command_id=str(turn["commandId"]),
                        attributes={
                            "turnExecutionOwner": self._owner_id,
                            "turnAttempt": int(turn["attempt"]),
                        },
                    ),
                    response_transaction_policy=ResponseTransactionPolicy(
                        mode=ResponseTransactionMode.DIRECT_LIVE,
                        public_presentation=PublicPresentationMode.NONE,
                    ),
                ),
                signal=asyncio.Event(),
                run_binding_lifecycle=lifecycle,
                long_task_executor=self._unit_executor_factory(runtime),
            ):
                pass
        except Exception as error:
            await self._settle_execution_exception(turn_id, error)

    def _default_unit_executor(self, runtime):
        if self._composition is None:
            raise RuntimeError("screenplay Root Run composition is required")
        return ScreenplayTaskUnitExecutor(
            self._db,
            runtime=runtime,
            composition=self._composition,
        )

    def dispatch_resumed_operation(
        self,
        operation_id: str,
        runtime,
        *,
        continuation_command: str,
    ) -> asyncio.Task[None]:
        key = f"operation:{operation_id}"
        active = _ACTIVE_TASKS.get(key)
        if active is not None and not active.done():
            return active
        task = asyncio.create_task(
            self.execute_resumed_operation(
                operation_id,
                runtime,
                continuation_command=continuation_command,
            )
        )
        self._remember_task(key, task)
        if callable(self._track_background):
            self._track_background(task)
        return task

    async def execute_resumed_operation(
        self,
        operation_id: str,
        runtime,
        *,
        continuation_command: str,
    ) -> None:
        operation = await self._operations.load(operation_id)
        if operation is None:
            raise NotFoundError("剧本 Agent Operation 不存在")
        if operation.status.value != "running":
            raise AppError("only a resumed screenplay Operation can execute", 409)
        if not operation.long_task_id:
            raise AppError("resumed screenplay Operation has no LongTask", 409)
        turn = await self._repository.load_turn(operation.turn_id)
        if turn is None:
            raise NotFoundError("剧本 Agent Turn 不存在")
        source_root_run_id = str(turn.get("rootRunId") or "").strip()
        if not source_root_run_id:
            raise AppError("resumed screenplay Operation has no Root Run", 409)
        if self._composition is None:
            raise RuntimeError("screenplay Root Run composition is required")
        try:
            source = await self._db.fetch_one(
                "SELECT status FROM ai_agent_runs WHERE id = ?",
                [source_root_run_id],
            )
            if source is None or str(source.get("status") or "") != "canceled":
                raise AppError("resume requires a canceled source Root Run", 409)
            plan = await SqliteScreenplayCheckpointRepository(
                self._db
            ).load_root_plan(source_root_run_id)
            plan = replace(
                plan,
                steps=tuple(
                    step if step.status is StepStatus.DONE else replace(
                        step,
                        status=StepStatus.PENDING,
                        result_summary=None,
                        error=None,
                    )
                    for step in plan.steps
                ),
            )
            task = await self._long_tasks.load(operation.long_task_id)
            if task is None or task.status.value != "running":
                raise AppError("resumed screenplay LongTask is not running", 409)
            reservation = await self._operations.load_continuation_command(
                continuation_command
            )
            if (
                reservation is None
                or str(reservation.get("operation_id") or "") != operation.id
                or str(reservation.get("continuation_status") or "") != "starting"
                or str(reservation.get("continuation_owner_id") or "")
                != self._owner_id
            ):
                raise AppError("screenplay continuation reservation is not owned", 409)
            recipe = _execution_recipe_from_metadata(task.metadata.get("recipe"))
            admission = TaskAdmissionDecision(
                mode=ExecutionMode.DURABLE,
                reason_code="durable_continuation",
                covered_step_ids=tuple(step.id for step in plan.steps),
                execution_recipe=recipe,
            )
            continuation = DurableTaskContinuation(
                source=RunRecoverySnapshot(
                    run_id=source_root_run_id,
                    status=RunStatus.CANCELED,
                    execution_plan=plan,
                ),
                continuation_command=continuation_command,
                receipt=LongTaskDispatchReceipt(
                    task_id=task.id,
                    message="Resume existing durable task",
                    admission=admission,
                ),
            )
            request = _root_request(turn, runtime)
            model_request = request.model
            window = int(request.context_window or 200_000)
            output_limit = resolve_invocation_output_limit(
                model_request.capability_snapshot,
                model_request.options.get("max_tokens"),
            )
            output_limit = fit_output_limit_to_context(output_limit, window)
            lifecycle = _ScreenplayContinuationRunLifecycle(
                self._db,
                self._repository,
                self._operations,
                operation.turn_id,
                source_root_run_id=source_root_run_id,
                continuation_command=continuation_command,
                reservation=reservation,
            )
            async for _update in self._run_service.run(
                request=request,
                api_key=runtime.apiKey.get_secret_value(),
                options=AgentCoreRunOptions(
                    turn_id=operation.turn_id,
                    output_limit=output_limit,
                    default_context_window_tokens=window,
                    force_planned_tool_choice=False,
                    require_tool_call=False,
                    reasoning_mode=reasoning_mode_from_options(runtime.options),
                    binding=RunBinding(
                        namespace="screenplay.conversation_turn",
                        aggregate_id=str(turn["projectId"]),
                        command_id=str(continuation_command),
                        attributes={
                            "continuationOf": source_root_run_id,
                            "operationId": operation.id,
                            "continuationOwner": str(
                                reservation["continuation_owner_id"]
                            ),
                            "continuationEpoch": int(
                                reservation["continuation_epoch"]
                            ),
                            "continuationIdentityDigest": str(
                                reservation["continuation_identity_digest"]
                            ),
                        },
                    ),
                    response_transaction_policy=ResponseTransactionPolicy(
                        mode=ResponseTransactionMode.DIRECT_LIVE,
                        public_presentation=PublicPresentationMode.NONE,
                    ),
                    durable_continuation=continuation,
                ),
                signal=asyncio.Event(),
                run_binding_lifecycle=lifecycle,
                long_task_executor=self._unit_executor_factory(runtime),
            ):
                pass
        except Exception as error:
            await self._settle_execution_exception(operation.turn_id, error)

    async def _settle_execution_exception(
        self,
        turn_id: str,
        error: Exception,
    ) -> None:
        if isinstance(error, (
            ContinuationStartLost,
            RunCommitProjectionError,
            ScreenplayRootStartLost,
        )):
            # The Root terminal transaction rolled back in full. Leave the
            # durable business state retryable instead of compensating it into
            # a terminal product failure outside that transaction.
            return
        code, message = _task_failure(error)
        with suppress(Exception):
            operation = await self._operations.load_for_turn(turn_id)
            if operation is not None and not operation.status.terminal:
                await self._operations.fail(
                    operation.id,
                    code=code,
                    message=message,
                    command_id=(
                        f"operation:fail:{operation.id}:"
                        f"{operation.revision}:{code}"
                    ),
                )
            if operation is not None:
                await self._repository.fail_task(
                    turn_id,
                    code=code,
                    message=message,
                )
            else:
                await self._repository.fail_turn(
                    turn_id,
                    code=(
                        code
                        if isinstance(
                            error,
                            (
                                ModelGatewayError,
                                ScreenplayIntentCommandMismatchError,
                            ),
                        )
                        else "screenplay_intent_failed"
                    ),
                    message=message,
                )

    async def cancel_turn(self, turn_id: str, *, idempotency_key: str):
        try:
            receipt = await self._operations.request_cancel(
                turn_id,
                idempotency_key=idempotency_key,
            )
        except LookupError as error:
            raise NotFoundError("剧本 Agent Turn 不存在") from error
        except ValueError as error:
            raise AppError(str(error), 409) from error
        if receipt.terminal_status in {"succeeded", "failed", "canceled"}:
            return receipt.to_mapping()

        operation = await self._operations.load_for_turn(turn_id)
        turn = await self._repository.load_turn(turn_id)
        root_run_id = str((turn or {}).get("rootRunId") or "").strip()
        if root_run_id and self._cancellation is not None:
            await self._cancellation.cancel(root_run_id)

        if operation is not None:
            self._cancel_task(f"operation:{operation.id}")
        async with self._db.transaction(cancellation_linearizable=True):
            if operation is not None and operation.long_task_id:
                task = await self._long_tasks.load(operation.long_task_id)
                if task is not None and not task.status.terminal:
                    await self._long_tasks.cancel(task.id)
        # Operation and Turn remain active-but-cancel-requested until the Root
        # terminal commit closes the execution tree.  The Root projector owns
        # the atomic business settlement.
        return receipt.to_mapping()

    async def _publish_task_update(
        self,
        turn_id: str,
        update: LongTaskExecutionUpdate,
    ) -> None:
        if self._output_processor is None:
            return
        run_id = str(update.event.run_id or "").strip()
        if not run_id:
            raise RuntimeError("long task progress requires its parent Run")
        await self._output_processor.accept_runtime_event(RuntimeOutputEvent(
            event_id=f"long-task-{uuid4().hex}",
            run_id=run_id,
            turn_id=turn_id,
            event_type=str(update.event.type),
            payload=update.event.payload,
            occurred_at=datetime.now(timezone.utc),
        ))

    async def prepare_resume(self, operation_id: str, *, idempotency_key: str, request):
        operation = await self._operations.load(operation_id)
        if operation is None:
            raise NotFoundError("剧本 Agent Operation 不存在")
        requirements = _capability_requirements(
            operation.requirements_json,
            request.runtime,
        )
        try:
            model_request = model_request_from_runtime(
                request.runtime,
                requirements=requirements,
            )
        except UnsupportedModelFeatureError as error:
            raise AppError("model_capability_incompatible", 409) from error
        snapshot = model_request.capability_snapshot.to_mapping(
            include_digest=True,
        )
        turn = await self._repository.load_turn(operation.turn_id)
        if turn is None:
            raise AppError("screenplay continuation Turn is missing", 409)
        prior = await self._operations.load_continuation_command(idempotency_key)
        source_root_run_id = str(
            (prior or {}).get("continuation_source_root_run_id")
            or turn.get("rootRunId")
            or ""
        ).strip()
        source = await self._db.fetch_one(
            "SELECT status, session_id, binding_aggregate_id FROM ai_agent_runs "
            "WHERE id = ?",
            [source_root_run_id],
        )
        if (
            source is None
            or str(source.get("status") or "") != "canceled"
            or int(source.get("session_id") or 0) != operation.session_id
            or str(source.get("binding_aggregate_id") or "") != operation.project_id
        ):
            raise AppError("resume requires a canceled matching Root Run", 409)
        try:
            resumed = await self._operations.resume_with_model(
                operation.id,
                command_id=idempotency_key,
                expected_revision=request.expectedOperationRevision,
                capability_snapshot=snapshot,
            )
            reservation = await self._operations.claim_continuation_start(
                command_id=idempotency_key,
                operation_id=operation.id,
                turn_id=operation.turn_id,
                source_root_run_id=source_root_run_id,
                session_id=operation.session_id,
                project_id=operation.project_id,
                owner_id=self._owner_id,
            )
        except ValueError as error:
            raise AppError(str(error), 409) from error
        return {
            "operationId": resumed.id,
            "turnId": resumed.turn_id,
            "status": resumed.status.value,
            "revision": resumed.revision,
            "capabilitySnapshotDigest": model_request.capability_snapshot.digest(),
            "continuationCommand": idempotency_key,
            "continuationRootRunId": (
                str(reservation.get("continuation_root_run_id") or "") or None
            ),
            "dispatchRequired": bool(reservation["_acquired"]),
        }

    async def truncate_from_turn(self, turn_id: str):
        await self._cancel_truncated_runtime(turn_id)
        result = await self._repository.truncate_from_turn(turn_id)
        for deleted_turn_id in result["deletedTurnIds"]:
            self._cancel_task(f"turn:{deleted_turn_id}")
        for deleted_operation_id in result.get("deletedOperationIds", ()):
            self._cancel_task(f"operation:{deleted_operation_id}")
        return result

    async def _cancel_truncated_runtime(self, turn_id: str) -> None:
        rows = await self._truncate_rows(turn_id)
        for row in rows:
            if str(row.get("turn_status") or "") not in {
                "queued", "planning", "running", "paused",
            }:
                continue
            await self._operations.request_cancel(
                str(row["turn_id"]),
                idempotency_key=f"truncate:{row['turn_id']}:cancel",
            )
        pre_root_rows = [row for row in rows if not row.get("root_run_id")]
        await self._cancel_local_truncate_tasks(pre_root_rows)
        for row in pre_root_rows:
            await self._repository.release_canceled_turn_claim(
                str(row["turn_id"])
            )

        deadline = time.monotonic() + max(
            0.0,
            float(self._truncate_wait_timeout_seconds),
        )
        cancellation_roots: set[str] = set()
        root_executions_signaled = False
        while True:
            rows = await self._truncate_rows(turn_id)
            checked_at = now_ms()
            for row in rows:
                await self._repository.release_expired_canceled_claim(
                    str(row["turn_id"]),
                    checked_at,
                )
            root_ids = tuple(dict.fromkeys(
                str(row.get("root_run_id") or "").strip()
                for row in rows
                if str(row.get("root_run_id") or "").strip()
            ))
            active_roots = await self._active_root_ids(root_ids)
            if active_roots and self._cancellation is None:
                raise AppError(
                    "truncate cancellation requires Agent composition",
                    409,
                )
            for root_run_id in active_roots:
                cancellation_roots.add(root_run_id)
                assert self._cancellation is not None
                await self._cancellation.cancel(root_run_id)
            if root_ids and not root_executions_signaled:
                root_rows = [row for row in rows if row.get("root_run_id")]
                await self._cancel_local_truncate_tasks(root_rows)
                for row in root_rows:
                    await self._repository.release_canceled_turn_claim(
                        str(row["turn_id"])
                    )
                root_executions_signaled = True
            await self._finalize_truncated_tasks(rows)
            if not await self._truncate_runtime_active(
                root_ids=root_ids,
                turn_ids=tuple(str(row["turn_id"]) for row in rows),
                task_ids=tuple(
                    str(row.get("task_id") or "").strip()
                    for row in rows
                    if str(row.get("task_id") or "").strip()
                ),
                cancellation_roots=tuple(cancellation_roots),
            ):
                await self._settle_truncated_cancellations(rows)
                break
            if time.monotonic() >= deadline:
                raise AppError("truncate cancellation timed out", 409)
            await asyncio.sleep(max(
                0.001,
                float(self._truncate_poll_interval_seconds),
            ))

    async def _finalize_truncated_tasks(self, rows) -> None:
        for task_id in dict.fromkeys(
            str(row.get("task_id") or "").strip()
            for row in rows
            if str(row.get("task_id") or "").strip()
        ):
            task = await self._long_tasks.load(task_id)
            if (
                task is not None
                and not task.status.terminal
                and task.cancellation_requested_at_ms is not None
            ):
                await self._long_tasks.finalize_if_complete(task_id)

    async def _settle_truncated_cancellations(self, rows) -> None:
        for row in rows:
            if not row.get("cancel_receipt_id"):
                continue
            await self._operations.settle_cancel(
                str(row["turn_id"]),
                receipt_id=str(row["cancel_receipt_id"]),
            )

    @staticmethod
    async def _cancel_local_truncate_tasks(rows) -> None:
        keys = {
            key
            for row in rows
            for key in (
                f"turn:{row['turn_id']}",
                (
                    f"operation:{row['operation_id']}"
                    if row.get("operation_id") else ""
                ),
            )
            if key
        }
        tasks = []
        for key in keys:
            task = _ACTIVE_TASKS.get(key)
            if task is None or task.done():
                continue
            task.cancel()
            tasks.append((key, task))
        for key, task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
            if _ACTIVE_TASKS.get(key) is task:
                _ACTIVE_TASKS.pop(key, None)

    async def _truncate_rows(self, turn_id: str):
        turn = await self._db.fetch_one(
            "SELECT project_id, session_id, rowid FROM screenplay_agent_turns "
            "WHERE id = ?",
            [turn_id],
        )
        if turn is None:
            raise NotFoundError("剧本 Agent Turn 不存在")
        return await self._db.fetch_all(
            "SELECT t.id AS turn_id, t.status AS turn_status, "
            "t.cancel_receipt_id AS cancel_receipt_id, "
            "t.planner_run_id AS root_run_id, o.id AS operation_id, "
            "o.long_task_id AS task_id "
            "FROM screenplay_agent_turns AS t "
            "LEFT JOIN screenplay_agent_operations AS o ON o.turn_id = t.id "
            "WHERE t.project_id = ? AND t.session_id = ? AND t.rowid >= ? "
            "ORDER BY t.rowid",
            [turn["project_id"], turn["session_id"], turn["rowid"]],
        )

    async def _active_root_ids(self, run_ids: Sequence[str]) -> tuple[str, ...]:
        if not run_ids:
            return ()
        activity = await self._run_control.inspect_activity(
            run_ids=tuple(run_ids),
        )
        running = {
            run_id
            for run_id in run_ids
            if (
                (state := await self._run_control.get(run_id)) is not None
                and state.status is RunStatus.RUNNING
            )
        }
        return tuple(sorted({
            *running,
            *activity.draining_cancellation_run_ids,
        }))

    async def _truncate_runtime_active(
        self,
        *,
        root_ids: Sequence[str],
        turn_ids: Sequence[str],
        task_ids: Sequence[str],
        cancellation_roots: Sequence[str],
    ) -> bool:
        if turn_ids:
            claimed_turns = await self._db.fetch_one(
                "SELECT COUNT(*) AS count FROM screenplay_agent_turns WHERE "
                f"id IN ({_sql_marks(turn_ids)}) AND ("
                "execution_owner_id IS NOT NULL OR lease_expires_at_ms IS NOT NULL)",
                list(turn_ids),
            )
            if int((claimed_turns or {}).get("count") or 0):
                return True
        activity = await self._run_control.inspect_activity(
            run_ids=tuple({*root_ids, *cancellation_roots}),
            task_ids=tuple(task_ids),
        )
        return not activity.quiescent

    @staticmethod
    def _remember_task(key: str, task: asyncio.Task[None]) -> None:
        _ACTIVE_TASKS[key] = task
        task.add_done_callback(
            lambda completed: _ACTIVE_TASKS.pop(key, None)
            if _ACTIVE_TASKS.get(key) is completed
            else None
        )

    @staticmethod
    def _cancel_task(key: str) -> None:
        task = _ACTIVE_TASKS.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    async def get_snapshot(self, *, project_id: str, session_id: int):
        snapshot = await self._repository.get_snapshot(
            project_id=project_id,
            session_id=session_id,
        )
        for task in snapshot["tasks"]:
            task["resultRevision"] = None
            revision_id = str(task.get("resultRevisionId") or "").strip()
            if not revision_id:
                continue
            try:
                task["resultRevision"] = await self._projects.get_revision(
                    revision_id,
                    view="summary",
                )
            except NotFoundError:
                # Legacy or synthetic Task records can retain a stable result
                # reference after the Revision itself has been removed. Keep
                # the reference visible without inventing replacement metadata.
                continue
        revisions_by_task = {
            str(task["id"]): task.get("resultRevision")
            for task in snapshot["tasks"]
        }
        for operation in snapshot.get("operations", ()):
            operation["resultRevision"] = revisions_by_task.get(
                str(operation.get("taskId") or "")
            )
        return snapshot

def _task_failure(error: Exception) -> tuple[str, str]:
    if isinstance(error, ScreenplayIntentCommandMismatchError):
        return error.code, str(error)
    if not isinstance(error, ModelGatewayError):
        return "screenplay_task_failed", str(error) or "剧本任务执行失败。"
    message = _task_failure_message(error.code)
    if message.startswith("剧本任务执行失败") and str(error):
        message = str(error)
    return error.code, message


def _task_failure_message(code: str) -> str:
    messages = {
        "model_output_truncated": (
            "模型本轮输出额度耗尽，未形成完整候选稿；不完整结果未被保存。"
            "请重试；若重复出现，请更换模型或减少本次生成的内容量。"
        ),
        "model_output_filtered": "模型输出被服务商安全策略中止，请调整要求后重试。",
        "upstream_stream_interrupted": "模型流式响应在完成前中断，请检查网络后重试。",
        "unsupported_model_finish_reason": "模型以不受支持的状态结束，请更换模型后重试。",
    }
    return messages.get(code, "剧本任务执行失败，请查看诊断信息后重试。")


def _capability_requirements(stored: Mapping[str, Any], runtime):
    raw = dict(stored.get("capabilityRequirements") or {})
    return TaskCapabilityRequirements(
        reasoning_mode=reasoning_mode_from_options(runtime.options),
        tool_calling=FeatureRequirement(
            str(raw.get("toolCalling") or "optional")
        ),
        structured_output_level=str(
            raw.get("structuredOutputLevel") or "none"
        ),
        streaming_required=bool(raw.get("streamingRequired", True)),
        cancellation_required=bool(raw.get("cancellationRequired", True)),
    )


class _ScreenplayTurnRunLifecycle:
    def __init__(self, db, turns, operations, turn_id: str) -> None:
        self._db = db
        self._turns = turns
        self._operations = operations
        self._turn_id = str(turn_id)

    async def validate(self) -> None:
        turn = await self._turns.load_turn(self._turn_id)
        if turn is None or turn["status"] != "planning":
            raise ValueError("screenplay Turn is not startable")

    async def before_submit(self) -> None:
        turn = await self._turns.load_turn(self._turn_id)
        if turn is None or not await self._turns.validate_claimed_turn_start(
            self._turn_id,
            attempt=int(turn["attempt"]),
        ):
            raise ScreenplayRootStartLost("screenplay Turn is not startable")

    async def on_run_started(self, run_id: str) -> None:
        turn = await self._turns.load_turn(self._turn_id)
        if turn is None or str(turn.get("rootRunId") or "") != run_id:
            raise ScreenplayRootStartLost(
                "screenplay Root atomic binding is missing"
            )

    async def on_run_finished(self, result: AgentRunResult) -> None:
        turn = await self._turns.load_turn(self._turn_id)
        operation = await self._operations.load_for_turn(self._turn_id)
        if turn is None:
            raise RuntimeError("screenplay Root Turn projection disappeared")
        if result.status is RunStatus.DONE:
            if turn["status"] != "completed":
                raise RuntimeError(
                    "screenplay Root completed before its Turn projection"
                )
            if turn["assistantContent"] != result.final_response:
                raise RuntimeError(
                    "screenplay Root response conflicts with its Turn projection"
                )
            if operation is not None and operation.status.value != "succeeded":
                raise RuntimeError(
                    "screenplay durable Root completed before its Operation"
                )
            return
        expected = "failed"
        if result.status is RunStatus.CANCELED:
            expected = "canceled"
            if operation is not None and operation.long_task_id:
                task = await self._db.fetch_one(
                    "SELECT status FROM ai_agent_long_tasks WHERE id = ?",
                    [operation.long_task_id],
                )
                if task == {"status": "paused"}:
                    expected = "paused"
        if turn["status"] != expected:
            raise RuntimeError(
                "screenplay Root terminal state conflicts with Turn projection"
            )
        if operation is not None and operation.status.value != expected:
            raise RuntimeError(
                "screenplay Root terminal state conflicts with Operation projection"
            )

    async def on_start_failed(self, code: str):
        turn = await self._turns.load_turn(self._turn_id)
        if (
            turn is None
            or turn.get("cancelRequestedAtMs") is not None
            or turn.get("executionOwnerId") != self._turns.owner_id
        ):
            return None
        return await self._turns.fail_turn(
            self._turn_id,
            code=str(code or "screenplay_root_start_failed"),
            message="剧本 Agent Root Run 启动失败。",
        )


class _ScreenplayContinuationRunLifecycle(_ScreenplayTurnRunLifecycle):
    def __init__(
        self,
        db,
        turns,
        operations,
        turn_id: str,
        *,
        source_root_run_id: str,
        continuation_command: str,
        reservation: Mapping[str, Any],
    ) -> None:
        super().__init__(db, turns, operations, turn_id)
        self._source_root_run_id = str(source_root_run_id)
        self._continuation_command = str(continuation_command)
        self._reservation = dict(reservation)

    async def validate(self) -> None:
        turn = await self._turns.load_turn(self._turn_id)
        operation = await self._operations.load_for_turn(self._turn_id)
        source = await self._db.fetch_one(
            "SELECT status FROM ai_agent_runs WHERE id = ?",
            [self._source_root_run_id],
        )
        if (
            turn is None
            or turn["status"] != "running"
            or str(turn.get("rootRunId") or "") != self._source_root_run_id
            or operation is None
            or operation.status.value != "running"
            or source is None
            or str(source.get("status") or "") != "canceled"
        ):
            raise ValueError("screenplay continuation is not startable")

    async def on_run_started(self, run_id: str) -> None:
        command = await self._operations.load_continuation_command(
            self._continuation_command
        )
        turn = await self._turns.load_turn(self._turn_id)
        if (
            command is None
            or str(command.get("continuation_status") or "") != "bound"
            or str(command.get("continuation_root_run_id") or "") != run_id
            or turn is None
            or str(turn.get("rootRunId") or "") != run_id
        ):
            raise ValueError("screenplay continuation atomic binding is missing")

    async def before_submit(self) -> None:
        await self.validate()
        command = await self._operations.load_continuation_command(
            self._continuation_command
        )
        if (
            command is None
            or str(command.get("continuation_status") or "") != "starting"
            or str(command.get("continuation_owner_id") or "")
            != str(self._reservation.get("continuation_owner_id") or "")
            or int(command.get("continuation_epoch") or 0)
            != int(self._reservation.get("continuation_epoch") or 0)
            or str(command.get("continuation_identity_digest") or "")
            != str(self._reservation.get("continuation_identity_digest") or "")
        ):
            raise ContinuationStartLost(
                "screenplay continuation reservation was lost"
            )

    async def on_start_failed(self, code: str):
        del code
        await self._operations.release_continuation_start(
            command_id=self._continuation_command,
            owner_id=str(self._reservation["continuation_owner_id"]),
            epoch=int(self._reservation["continuation_epoch"]),
        )
        # Root begin and product binding rolled back together. Keep the resumed
        # durable work active and make the same command claimable again.
        return None


def _execution_recipe_from_metadata(value: object) -> ExecutionRecipe:
    if not isinstance(value, Mapping):
        raise ValueError("screenplay durable Recipe is missing")
    raw = dict(value)
    reserved = {"kind", "maxParallelism", "steps"}
    steps = raw.get("steps")
    if (
        not isinstance(steps, Sequence)
        or isinstance(steps, (str, bytes, bytearray))
        or not steps
    ):
        raise ValueError("screenplay durable Recipe steps are missing")
    parsed = []
    for item in steps:
        if not isinstance(item, Mapping):
            raise ValueError("screenplay durable Recipe step is invalid")
        step = dict(item)
        step_reserved = {
            "id", "kind", "dependsOn", "inputRef", "executor",
            "plannerStepId", "maxAttempts",
        }
        parsed.append(ExecutionRecipeStep(
            id=str(step.get("id") or ""),
            kind=str(step.get("kind") or ""),
            depends_on=tuple(step.get("dependsOn") or ()),
            input_ref=str(step.get("inputRef") or "") or None,
            executor=str(step.get("executor") or "") or None,
            plan_step_id=str(step.get("plannerStepId") or "") or None,
            max_attempts=int(step.get("maxAttempts") or 1),
            metadata={
                key: item_value
                for key, item_value in step.items()
                if key not in step_reserved
            },
        ))
    return ExecutionRecipe(
        kind=str(raw.get("kind") or ""),
        steps=tuple(parsed),
        max_parallelism=int(raw.get("maxParallelism") or 1),
        metadata={key: item for key, item in raw.items() if key not in reserved},
    )


def _root_request(turn: Mapping[str, Any], runtime) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(
            role=MessageRole.USER,
            content=str(turn["userContent"]),
        ),),
        model=model_request_from_runtime(runtime),
        domain_context=ScreenplayAgentDomainContext(
            project_id=str(turn["projectId"]),
            turn_id=str(turn["id"]),
            locale=str(getattr(runtime, "locale", "zh-CN") or "zh-CN"),
        ).to_core_context(),
        session_id=int(turn["sessionId"]),
        mode="agent",
        context_window=context_window_tokens(
            runtime.contextWindow or runtime.options.get("context_window")
        ),
        tools_enabled=True,
        metadata={"locale": str(getattr(runtime, "locale", "zh-CN"))},
    )


def _sql_marks(values: Sequence[object]) -> str:
    return ",".join("?" for _ in values)


__all__ = [
    "ScreenplayAgentService",
]
