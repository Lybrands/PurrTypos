from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from infrastructure.persistence.sqlite_work_item_repository import (
    SqliteWorkItemRepository,
)
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    ExecutionRecipe,
    ExecutionRecipeStep,
    MessageRole,
    ModelRequest,
    StepExecutor,
    StepType,
    TaskPlan,
    TaskSpec,
    TaskStep,
)
from purra.long_tasks import (
    DurableExecutorRegistry,
    DurableTaskDescriptor,
    LongTaskStatus,
    LongTaskUnitResult,
    RecipeLongTaskDispatcher,
)
from purra.task_admission import (
    ExecutionMode,
    LongTaskExecutionStatus,
    TaskAdmissionDecision,
)
from purra.work_items import WorkItemStatus


@pytest_asyncio.fixture
async def durable_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


def _request() -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(
            role=MessageRole.USER,
            content="Build the durable fixture.",
        ),),
        model=ModelRequest(provider="fixture", model="scripted"),
        domain_context=DomainContext(
            namespace="test.durable",
            payload={"ownerId": "owner-1"},
        ),
        session_id="session-alpha",
        mode="agent",
        context_window=32_000,
        tools_enabled=True,
    )


def _plan(*step_ids: str) -> TaskPlan:
    return TaskPlan(
        title="Durable fixture",
        task_spec=TaskSpec(goal="Build fixture", operation="write"),
        steps=tuple(TaskStep(
            id=step_id,
            title=step_id,
            type=StepType.WRITE,
            executor=StepExecutor.TOOL,
        ) for step_id in step_ids),
    )


class _DescriptorResolver:
    def __init__(self) -> None:
        self.failed_resume_attempts = 0

    async def resolve(self, request, plan, decision):
        del request, plan, decision
        return DurableTaskDescriptor(
            namespace="test.durable",
            owner_id="owner-1",
            idempotency_key="fixture-revision-1",
            failed_resume_attempts=self.failed_resume_attempts,
        )


@pytest.mark.asyncio
async def test_dispatcher_executes_static_map_reduce_and_delivers_dependencies(
    durable_db,
):
    maps = []
    reducer_inputs = []

    class _MapExecutor:
        async def execute(self, context, signal=None):
            del signal
            maps.append(context.unit.id)
            assert not context.dependency_outputs
            return LongTaskUnitResult(
                output_ref=f"artifact://{context.unit.id}",
            )

    class _ReduceExecutor:
        async def execute(self, context, signal=None):
            del signal
            reducer_inputs.append(dict(context.dependency_outputs))
            return LongTaskUnitResult(
                output_ref="artifact://reduced",
                metadata={"finalResponse": "Durable fixture completed."},
            )

    recipe = ExecutionRecipe(
        kind="fixture.map_reduce",
        max_parallelism=2,
        steps=(
            ExecutionRecipeStep(
                id="map-a",
                kind="map",
                executor="map",
                plan_step_id="generate",
            ),
            ExecutionRecipeStep(
                id="map-b",
                kind="map",
                executor="map",
                plan_step_id="generate",
            ),
            ExecutionRecipeStep(
                id="reduce",
                kind="reduce",
                executor="reduce",
                depends_on=("map-a", "map-b"),
                plan_step_id="review",
            ),
        ),
    )
    decision = TaskAdmissionDecision(
        mode=ExecutionMode.DURABLE,
        reason_code="fixture_requires_multiple_calls",
        covered_step_ids=("generate", "review"),
        execution_recipe=recipe,
    )
    work_items = SqliteWorkItemRepository(durable_db)
    long_tasks = SqliteLongTaskRepository(durable_db)
    dispatcher = RecipeLongTaskDispatcher(
        work_item_repository=work_items,
        long_task_repository=long_tasks,
        descriptor_resolver=_DescriptorResolver(),
        executor_registry=DurableExecutorRegistry({
            "map": _MapExecutor(),
            "reduce": _ReduceExecutor(),
        }),
        worker_id="worker-map-reduce",
        id_factory=lambda: "map-reduce",
    )

    receipt = await dispatcher.dispatch(
        _request(),
        _plan("generate", "review"),
        decision,
        parent_run_id="run-parent",
    )
    updates = []

    async def observe(update):
        updates.append(update)

    result = await dispatcher.execute(
        receipt.task_id,
        parent_run_id="run-parent",
        observer=observe,
    )

    assert result.status is LongTaskExecutionStatus.COMPLETED
    assert result.final_response == "Durable fixture completed."
    assert set(maps) == {"map-a", "map-b"}
    assert reducer_inputs == [{
        "map-a": "artifact://map-a",
        "map-b": "artifact://map-b",
    }]
    task = await long_tasks.load(receipt.task_id)
    assert task is not None
    assert task.status is LongTaskStatus.COMPLETED
    assert task.metadata["sessionId"] == "session-alpha"
    units = await long_tasks.list_units(task.id)
    assert [unit.metadata["plannerStepId"] for unit in units] == [
        "generate",
        "generate",
        "review",
    ]
    assert (await work_items.load(task.work_item_id)).status is WorkItemStatus.COMPLETED
    assert updates
    progress = [update.event.payload for update in updates]
    assert progress[-1]["status"] == "completed"
    assert progress[-1]["revision"] == task.revision
    assert progress[-1]["completedUnits"] == 3
    assert progress[-1]["failedUnits"] == 0
    assert [unit["position"] for unit in progress[-1]["units"]] == [0, 1, 2]
    assert all(unit["maxAttempts"] > 0 for unit in progress[-1]["units"])
    assert any(
        any(unit["status"] == "claimed" for unit in snapshot["units"])
        for snapshot in progress
    )
    assert all(unit["status"] == "completed" for unit in progress[-1]["units"])


@pytest.mark.asyncio
async def test_failed_dispatch_requires_explicit_retry_and_preserves_checkpoints(
    durable_db,
):
    calls = []
    should_fail = True

    class _Executor:
        async def execute(self, context, signal=None):
            nonlocal should_fail
            del signal
            calls.append(context.unit.id)
            if context.unit.id == "write" and should_fail:
                should_fail = False
                raise RuntimeError("permanent_failure")
            if context.unit.id == "write":
                assert dict(context.dependency_outputs) == {
                    "prepare": "artifact://prepare",
                }
            if context.unit.id == "publish":
                assert dict(context.dependency_outputs) == {
                    "write": "artifact://write",
                }
            return LongTaskUnitResult(
                output_ref=f"artifact://{context.unit.id}",
                metadata=(
                    {"finalResponse": "Recovered without replaying prepare."}
                    if context.unit.id == "publish"
                    else {}
                ),
            )

    recipe = ExecutionRecipe(
        kind="fixture.recovery",
        steps=(
            ExecutionRecipeStep(
                id="prepare",
                kind="fixture",
                plan_step_id="build",
            ),
            ExecutionRecipeStep(
                id="write",
                kind="fixture",
                depends_on=("prepare",),
                plan_step_id="build",
            ),
            ExecutionRecipeStep(
                id="publish",
                kind="fixture",
                depends_on=("write",),
                plan_step_id="publish",
            ),
        ),
    )
    decision = TaskAdmissionDecision(
        mode=ExecutionMode.DURABLE,
        reason_code="fixture_requires_recovery",
        covered_step_ids=("build", "publish"),
        execution_recipe=recipe,
    )
    resolver = _DescriptorResolver()
    work_items = SqliteWorkItemRepository(durable_db)
    long_tasks = SqliteLongTaskRepository(durable_db)
    dispatcher = RecipeLongTaskDispatcher(
        work_item_repository=work_items,
        long_task_repository=long_tasks,
        descriptor_resolver=resolver,
        executor_registry=DurableExecutorRegistry({"fixture": _Executor()}),
        worker_id="worker-recovery",
        id_factory=lambda: "recovery",
    )
    request = _request()
    plan = _plan("build", "publish")

    first_receipt = await dispatcher.dispatch(
        request,
        plan,
        decision,
        parent_run_id="run-first",
    )
    first = await dispatcher.execute(
        first_receipt.task_id,
        parent_run_id="run-first",
        observer=lambda update: _ignore(update),
    )
    assert first.status is LongTaskExecutionStatus.FAILED
    assert first.error == "permanent_failure"
    assert calls == ["prepare", "write"]

    blocked_receipt = await dispatcher.dispatch(
        request,
        plan,
        decision,
        parent_run_id="run-without-retry-authority",
    )
    blocked = await dispatcher.execute(
        blocked_receipt.task_id,
        parent_run_id="run-without-retry-authority",
        observer=lambda update: _ignore(update),
    )
    assert blocked.status is LongTaskExecutionStatus.FAILED
    assert blocked_receipt.metadata["resumed"] is False
    assert calls == ["prepare", "write"]

    resolver.failed_resume_attempts = 1
    resumed_receipt = await dispatcher.dispatch(
        request,
        plan,
        decision,
        parent_run_id="run-explicit-retry",
    )
    recovered = await dispatcher.execute(
        resumed_receipt.task_id,
        parent_run_id="run-explicit-retry",
        observer=lambda update: _ignore(update),
    )

    assert resumed_receipt.metadata["resumed"] is True
    assert recovered.status is LongTaskExecutionStatus.COMPLETED
    assert recovered.final_response == "Recovered without replaying prepare."
    assert calls == ["prepare", "write", "write", "publish"]
    units = await long_tasks.list_units(first_receipt.task_id)
    assert [unit.attempt for unit in units] == [1, 2, 1]
    task = await long_tasks.load(first_receipt.task_id)
    assert task is not None
    links = await work_items.list_run_links(task.work_item_id)
    assert {link.run_id for link in links} == {
        "run-first",
        "run-without-retry-authority",
        "run-explicit-retry",
    }


async def _ignore(_value) -> None:
    return None
