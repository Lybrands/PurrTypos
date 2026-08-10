"""Generic durable execution for host-compiled recipes.

PurrA owns lifecycle, dependency scheduling, persistence and progress events.
The host owns durable task identity and the executors for each recipe step.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from purra.contracts import AgentRunRequest, ExecutionRecipe, TaskPlan
from purra.events import AgentEvent, CoreEventType
from purra.json_values import freeze_json_mapping, thaw_json_mapping
from purra.long_tasks.contracts import (
    LongTaskCreateCommand,
    LongTaskRecord,
    LongTaskStatus,
    LongTaskUnitRecord,
    LongTaskUnitResult,
    LongTaskUnitSpec,
    LongTaskUnitStatus,
)
from purra.long_tasks.coordinator import LongTaskCoordinator
from purra.long_tasks.ports import LongTaskRepository
from purra.normalization import non_negative_int, optional_text, required_text
from purra.ports import CancellationSignal
from purra.recovery import FailureCategory, FailureSignal
from purra.task_admission.contracts import (
    LongTaskDispatchReceipt,
    LongTaskExecutionResult,
    LongTaskExecutionStatus,
    LongTaskExecutionUpdate,
    TaskAdmissionDecision,
)
from purra.work_items import WorkItemLifecycle
from purra.work_items.contracts import (
    WorkItemCreateCommand,
    WorkItemRunLinkCommand,
    WorkItemRunRelation,
    WorkItemStatus,
    WorkItemTransitionCommand,
)
from purra.work_items.ports import WorkItemRepository
from purra.work_items.errors import WorkItemConflictError, WorkItemStateError


@dataclass(frozen=True, slots=True)
class DurableTaskDescriptor:
    """Host-owned durable identity, independent from recipe mechanics."""

    namespace: str
    owner_id: str
    idempotency_key: str
    message: str | None = None
    failed_resume_attempts: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("namespace", "owner_id", "idempotency_key"):
            object.__setattr__(
                self,
                name,
                required_text(getattr(self, name), f"durable task {name}"),
            )
        object.__setattr__(self, "message", optional_text(self.message))
        object.__setattr__(
            self,
            "failed_resume_attempts",
            non_negative_int(
                self.failed_resume_attempts,
                "durable failed resume attempts",
            ),
        )
        object.__setattr__(self, "metadata", freeze_json_mapping(self.metadata))


@runtime_checkable
class DurableTaskDescriptorResolver(Protocol):
    async def resolve(
        self,
        request: AgentRunRequest,
        plan: TaskPlan,
        decision: TaskAdmissionDecision,
    ) -> DurableTaskDescriptor: ...


@dataclass(frozen=True, slots=True)
class DurableUnitExecutionContext:
    task: LongTaskRecord
    unit: LongTaskUnitRecord
    dependency_outputs: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.task, LongTaskRecord):
            raise TypeError("durable unit context requires a LongTaskRecord")
        if not isinstance(self.unit, LongTaskUnitRecord):
            raise TypeError("durable unit context requires a LongTaskUnitRecord")
        outputs = {
            required_text(key, "durable dependency unit id"): required_text(
                value,
                "durable dependency output ref",
            )
            for key, value in self.dependency_outputs.items()
        }
        object.__setattr__(
            self,
            "dependency_outputs",
            freeze_json_mapping(outputs),
        )


@runtime_checkable
class DurableUnitExecutor(Protocol):
    async def execute(
        self,
        context: DurableUnitExecutionContext,
        signal: CancellationSignal | None = None,
    ) -> LongTaskUnitResult: ...


class DurableExecutorRegistry:
    """Immutable executor lookup keyed by host-authored recipe executor id."""

    def __init__(self, executors: Mapping[str, DurableUnitExecutor]) -> None:
        values = {
            required_text(key, "durable executor id"): value
            for key, value in executors.items()
        }
        if not values:
            raise ValueError("durable executor registry cannot be empty")
        if not all(isinstance(value, DurableUnitExecutor) for value in values.values()):
            raise TypeError("durable executors must implement DurableUnitExecutor")
        self._executors = values

    def require(self, executor_id: str) -> DurableUnitExecutor:
        normalized = required_text(executor_id, "durable executor id")
        executor = self.get(normalized)
        if executor is None:
            raise LookupError(f"durable executor is not registered: {normalized}")
        return executor

    def get(self, executor_id: str) -> DurableUnitExecutor | None:
        return self._executors.get(str(executor_id or "").strip())


class RecipeLongTaskDispatcher:
    """Production implementation of PurrA's LongTaskDispatcher port."""

    def __init__(
        self,
        *,
        work_item_repository: WorkItemRepository,
        long_task_repository: LongTaskRepository,
        descriptor_resolver: DurableTaskDescriptorResolver,
        executor_registry: DurableExecutorRegistry,
        worker_id: str,
        id_factory: Callable[[], str] | None = None,
        lease_duration_ms: int = 300_000,
        retry_backoff_ms: tuple[int, ...] = (),
        idle_poll_ms: int = 100,
    ) -> None:
        self._work_item_repository = work_item_repository
        self._work_items = WorkItemLifecycle(work_item_repository)
        self._long_tasks = long_task_repository
        self._descriptors = descriptor_resolver
        self._executors = executor_registry
        self._worker_id = required_text(worker_id, "durable dispatcher worker id")
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._lease_duration_ms = int(lease_duration_ms)
        if self._lease_duration_ms <= 0:
            raise ValueError("durable dispatcher lease must be positive")
        self._retry_backoff_ms = tuple(
            max(0, int(value)) for value in retry_backoff_ms
        )
        self._idle_poll_ms = max(1, int(idle_poll_ms))

    async def dispatch(
        self,
        request: AgentRunRequest,
        plan: TaskPlan,
        decision: TaskAdmissionDecision,
        *,
        parent_run_id: str,
        signal: CancellationSignal | None = None,
    ) -> LongTaskDispatchReceipt:
        del signal
        recipe = decision.execution_recipe
        if recipe is None:
            raise ValueError("durable dispatch requires an execution recipe")
        units = _compile_recipe_units(recipe, decision.covered_step_ids)
        parent_run_id = required_text(parent_run_id, "durable parent Run id")
        descriptor = await self._descriptors.resolve(request, plan, decision)
        if descriptor.namespace != request.domain_context.namespace:
            raise ValueError("durable task namespace does not match the request")
        session_id = (
            str(request.session_id).strip()
            if request.session_id is not None
            else None
        )
        metadata = {
            **thaw_json_mapping(descriptor.metadata),
            "idempotencyKey": descriptor.idempotency_key,
            "sessionId": session_id,
            "recipe": recipe.to_metadata(),
            "goal": plan.task_spec.goal if plan.task_spec is not None else plan.title,
        }
        reusable = await self._find_reusable(
            descriptor,
            recipe=recipe,
            session_id=session_id,
        )
        previous_status = reusable.status if reusable is not None else None
        if reusable is None:
            task = await self._create(
                descriptor,
                recipe=recipe,
                units=units,
                metadata=metadata,
                parent_run_id=parent_run_id,
            )
        else:
            task = await self._link_and_resume(
                reusable,
                parent_run_id,
                additional_attempts=descriptor.failed_resume_attempts,
            )
        return LongTaskDispatchReceipt(
            task_id=task.id,
            message=(
                descriptor.message
                or _dispatch_message(task)
            ),
            metadata={
                "namespace": task.namespace,
                "kind": task.kind,
                "ownerId": task.owner_id,
                "status": task.status.value,
                "totalUnits": task.total_units,
                "completedUnits": task.completed_units,
                "reused": reusable is not None,
                "resumed": previous_status in {
                    LongTaskStatus.PAUSED,
                    LongTaskStatus.FAILED,
                } and task.status is LongTaskStatus.RUNNING,
                "alreadyCompleted": previous_status is LongTaskStatus.COMPLETED,
            },
        )

    async def execute(
        self,
        task_id: str,
        *,
        parent_run_id: str,
        observer: Callable[[LongTaskExecutionUpdate], Awaitable[None]],
        signal: CancellationSignal | None = None,
    ) -> LongTaskExecutionResult:
        parent_run_id = required_text(parent_run_id, "durable parent Run id")
        runner = _RecipeUnitRunner(
            repository=self._long_tasks,
            executors=self._executors,
            observer=observer,
            parent_run_id=parent_run_id,
        )
        await runner.emit_progress(task_id)
        task = await LongTaskCoordinator(
            self._long_tasks,
            worker_id=self._worker_id,
            lease_duration_ms=self._lease_duration_ms,
            retry_backoff_ms=self._retry_backoff_ms,
            idle_poll_ms=self._idle_poll_ms,
        ).run(task_id, runner, signal)
        await runner.emit_progress(task.id)
        units = await self._long_tasks.list_units(task.id)
        work_item = await self._work_items.get(task.work_item_id)
        if task.status is LongTaskStatus.COMPLETED:
            await self._settle_work_item(work_item.id, WorkItemStatus.COMPLETED)
            return LongTaskExecutionResult(
                task_id=task.id,
                status=LongTaskExecutionStatus.COMPLETED,
                final_response=_final_response(units),
                metadata={"completedUnits": task.completed_units},
            )
        if task.status is LongTaskStatus.CANCELED:
            await self._settle_work_item(work_item.id, WorkItemStatus.CANCELED)
            status = LongTaskExecutionStatus.CANCELED
        elif task.status is LongTaskStatus.PAUSED:
            status = LongTaskExecutionStatus.PAUSED
        else:
            status = LongTaskExecutionStatus.FAILED
        return LongTaskExecutionResult(
            task_id=task.id,
            status=status,
            error=(
                next((
                    unit.error_code
                    for unit in units
                    if unit.status is LongTaskUnitStatus.FAILED
                    and unit.error_code
                ), None)
                or next((unit.error_code for unit in units if unit.error_code), None)
            ),
            metadata={"completedUnits": task.completed_units},
        )

    async def _settle_work_item(
        self,
        work_item_id: str,
        target: WorkItemStatus,
    ) -> None:
        item = await self._work_items.get(work_item_id)
        if item.status is target:
            return
        if item.status is not WorkItemStatus.OPEN:
            raise RuntimeError("durable task and Work Item terminal states conflict")
        command = WorkItemTransitionCommand(
            work_item_id=item.id,
            expected_revision=item.revision,
        )
        try:
            if target is WorkItemStatus.COMPLETED:
                await self._work_items.complete(command)
            else:
                await self._work_items.cancel(command)
        except (WorkItemConflictError, WorkItemStateError):
            if (await self._work_items.get(item.id)).status is not target:
                raise

    async def _find_reusable(
        self,
        descriptor: DurableTaskDescriptor,
        *,
        recipe: ExecutionRecipe,
        session_id: str | None,
    ) -> LongTaskRecord | None:
        active = await self._long_tasks.find_active(
            namespace=descriptor.namespace,
            owner_id=descriptor.owner_id,
            kind=recipe.kind,
            session_id=session_id,
            match_session=True,
        )
        if active is not None:
            if active.metadata.get("idempotencyKey") != descriptor.idempotency_key:
                raise RuntimeError("durable_task_scope_conflict")
            _require_same_recipe(active, recipe)
            return active
        recent = await self._long_tasks.list_for_owner(
            namespace=descriptor.namespace,
            owner_id=descriptor.owner_id,
            kind=recipe.kind,
            limit=20,
        )
        reusable = next((
            task
            for task in recent
            if task.metadata.get("idempotencyKey") == descriptor.idempotency_key
            and _same_optional_text(task.metadata.get("sessionId"), session_id)
            and task.status in {LongTaskStatus.COMPLETED, LongTaskStatus.FAILED}
        ), None)
        if reusable is not None:
            _require_same_recipe(reusable, recipe)
        return reusable

    async def _link_and_resume(
        self,
        task: LongTaskRecord,
        parent_run_id: str,
        *,
        additional_attempts: int,
    ) -> LongTaskRecord:
        work_item = await self._work_items.get(task.work_item_id)
        if parent_run_id != task.created_by_run_id:
            links = await self._work_item_repository.list_run_links(work_item.id)
            if not any(link.run_id == parent_run_id for link in links):
                await self._work_items.link_run(WorkItemRunLinkCommand(
                    work_item_id=work_item.id,
                    run_id=parent_run_id,
                    relation=WorkItemRunRelation.REFERENCE,
                    expected_revision=work_item.revision,
                ))
        if task.status is LongTaskStatus.PAUSED:
            return await self._long_tasks.resume(task.id)
        if task.status is LongTaskStatus.FAILED and additional_attempts:
            return await self._long_tasks.resume(
                task.id,
                additional_attempts=additional_attempts,
            )
        return task

    async def _create(
        self,
        descriptor: DurableTaskDescriptor,
        *,
        recipe: ExecutionRecipe,
        units: tuple[LongTaskUnitSpec, ...],
        metadata: Mapping[str, Any],
        parent_run_id: str,
    ) -> LongTaskRecord:
        work_item = await self._work_items.begin(WorkItemCreateCommand(
            namespace=descriptor.namespace,
            kind=recipe.kind,
            owner_id=descriptor.owner_id,
            created_by_run_id=parent_run_id,
            metadata={
                "idempotencyKey": descriptor.idempotency_key,
                **thaw_json_mapping(descriptor.metadata),
            },
        ))
        try:
            task = await self._long_tasks.create(
                f"longtask_{self._id_factory()}",
                LongTaskCreateCommand(
                    namespace=descriptor.namespace,
                    kind=recipe.kind,
                    owner_id=descriptor.owner_id,
                    work_item_id=work_item.id,
                    created_by_run_id=parent_run_id,
                    units=units,
                    max_parallelism=recipe.max_parallelism,
                    metadata=metadata,
                ),
            )
        except BaseException:
            await self._work_items.cancel(WorkItemTransitionCommand(
                work_item_id=work_item.id,
                expected_revision=work_item.revision,
            ))
            raise
        if task.work_item_id != work_item.id:
            await self._work_items.cancel(WorkItemTransitionCommand(
                work_item_id=work_item.id,
                expected_revision=work_item.revision,
            ))
            if task.metadata.get("idempotencyKey") != descriptor.idempotency_key:
                raise RuntimeError("durable_task_scope_conflict")
            _require_same_recipe(task, recipe)
            return await self._link_and_resume(
                task,
                parent_run_id,
                additional_attempts=descriptor.failed_resume_attempts,
            )
        return task


class _RecipeUnitRunner:
    def __init__(
        self,
        *,
        repository: LongTaskRepository,
        executors: DurableExecutorRegistry,
        observer: Callable[[LongTaskExecutionUpdate], Awaitable[None]],
        parent_run_id: str,
    ) -> None:
        self._repository = repository
        self._executors = executors
        self._observer = observer
        self._parent_run_id = parent_run_id

    async def run_unit(self, task, unit, signal=None) -> LongTaskUnitResult:
        await self.emit_progress(task.id)
        context = DurableUnitExecutionContext(
            task=task,
            unit=unit,
            dependency_outputs=await self._dependency_outputs(unit),
        )
        executor = self._executors.require(str(unit.metadata.get("executor") or ""))
        result = await executor.execute(context, signal)
        if not isinstance(result, LongTaskUnitResult):
            raise TypeError("durable unit executor returned an invalid result")
        return result

    def classify_unit_failure(self, task, unit, error: Exception) -> FailureSignal:
        del task
        executor = self._executors.get(str(unit.metadata.get("executor") or ""))
        if executor is None:
            return _permanent_execution_failure(error)
        classifier = getattr(executor, "classify_failure", None)
        if not callable(classifier):
            return _permanent_execution_failure(error)
        try:
            failure = classifier(error)
        except Exception:
            return _permanent_execution_failure(error)
        if not isinstance(failure, FailureSignal):
            return _permanent_execution_failure(error)
        return failure

    async def on_unit_settled(self, task_id: str) -> None:
        await self.emit_progress(task_id)

    async def emit_progress(self, task_id: str) -> None:
        task = await self._repository.load(task_id)
        if task is None:
            raise LookupError("long task does not exist")
        units = await self._repository.list_units(task.id)
        await self._observer(LongTaskExecutionUpdate(AgentEvent(
            type=CoreEventType.LONG_TASK_PROGRESS,
            run_id=self._parent_run_id,
            payload={
                "taskId": task.id,
                "status": task.status.value,
                "revision": task.revision,
                "totalUnits": task.total_units,
                "completedUnits": task.completed_units,
                "failedUnits": task.failed_units,
                "updateTime": task.update_time,
                "units": [
                    {
                        "id": unit.id,
                        "position": unit.position,
                        "plannerStepId": str(
                            unit.metadata.get("plannerStepId") or unit.id
                        ),
                        "kind": str(unit.metadata.get("unitKind") or ""),
                        "status": unit.status.value,
                        "attempt": unit.attempt,
                        "maxAttempts": unit.max_attempts,
                        **({"runId": unit.run_id} if unit.run_id else {}),
                        **(
                            {"outputRef": unit.output_ref}
                            if unit.output_ref
                            else {}
                        ),
                        **({"errorCode": unit.error_code} if unit.error_code else {}),
                        "updateTime": unit.update_time,
                    }
                    for unit in units
                ],
            },
        )))

    async def _dependency_outputs(
        self,
        unit: LongTaskUnitRecord,
    ) -> Mapping[str, str]:
        if not unit.dependencies:
            return {}
        units = {
            item.id: item
            for item in await self._repository.list_units(unit.task_id)
        }
        outputs: dict[str, str] = {}
        for dependency_id in unit.dependencies:
            dependency = units.get(dependency_id)
            if dependency is None or not dependency.output_ref:
                raise RuntimeError("durable_dependency_output_missing")
            outputs[dependency_id] = dependency.output_ref
        return outputs


def _compile_recipe_units(
    recipe: ExecutionRecipe,
    covered_step_ids: Sequence[str],
) -> tuple[LongTaskUnitSpec, ...]:
    covered = frozenset(covered_step_ids)
    mapped: set[str] = set()
    units: list[LongTaskUnitSpec] = []
    for position, step in enumerate(recipe.steps):
        plan_step_id = step.plan_step_id or step.id
        if plan_step_id not in covered:
            raise ValueError(
                "execution recipe maps to an unadmitted plan step: "
                + plan_step_id
            )
        mapped.add(plan_step_id)
        units.append(LongTaskUnitSpec(
            id=step.id,
            position=position,
            dependencies=step.depends_on,
            input_ref=step.input_ref,
            max_attempts=step.max_attempts,
            metadata={
                **thaw_json_mapping(step.metadata),
                "unitKind": step.kind,
                "executor": step.executor or step.kind,
                "plannerStepId": plan_step_id,
            },
        ))
    missing = covered - mapped
    if missing:
        raise ValueError(
            "execution recipe does not implement admitted plan steps: "
            + ", ".join(sorted(missing))
        )
    return tuple(units)


def _require_same_recipe(
    task: LongTaskRecord,
    recipe: ExecutionRecipe,
) -> None:
    if thaw_json_mapping(task.metadata.get("recipe") or {}) != recipe.to_metadata():
        raise RuntimeError("durable_task_recipe_conflict")


def _same_optional_text(left: object, right: object) -> bool:
    return (
        None if left is None else str(left).strip()
    ) == (
        None if right is None else str(right).strip()
    )


def _permanent_execution_failure(error: Exception) -> FailureSignal:
    code = str(getattr(error, "code", "") or "").strip()
    return FailureSignal(
        category=FailureCategory.BUSINESS_INVARIANT,
        code=(code or str(error) or type(error).__name__)[:240],
        retryable=False,
    )


def _dispatch_message(task: LongTaskRecord) -> str:
    if task.status is LongTaskStatus.COMPLETED:
        return "Durable task is already complete."
    if task.status is LongTaskStatus.FAILED:
        return "Durable task is failed and requires an explicit retry decision."
    return f"Durable task started with {task.total_units} execution units."


def _final_response(units: Sequence[LongTaskUnitRecord]) -> str:
    for unit in reversed(units):
        value = unit.metadata.get("finalResponse")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Durable task completed."


__all__ = [
    "DurableExecutorRegistry",
    "DurableTaskDescriptor",
    "DurableTaskDescriptorResolver",
    "DurableUnitExecutionContext",
    "DurableUnitExecutor",
    "RecipeLongTaskDispatcher",
]
