from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    ExecutionRecipe,
    ExecutionRecipeStep,
    MessageRole,
    ModelRequest,
    RunCreateParams,
    RunStatus,
    StepExecutor,
    StepStatus,
    StepType,
    TaskPlan,
    TaskStep,
)
from purra.engine.durable_execution import (
    _validate_durable_plan_revision,
    complete_admitted_task,
)
from purra.errors import ContractViolationError
from purra.events import AgentEvent, CoreEventType
from purra.ports import RunBeginResult
from purra.run_controller import AgentRunController
from purra.task_admission import (
    ExecutionMode,
    LongTaskDispatchReceipt,
    LongTaskExecutionResult,
    LongTaskExecutionStatus,
    LongTaskExecutionUpdate,
    TaskAdmissionDecision,
)


class _Repository:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []
        self.commits = []
        self.steps: list[TaskStep] = []
        self.status = RunStatus.RUNNING
        self.error: str | None = None

    async def begin(self, params, started_event):
        del params
        event = AgentEvent(
            type=started_event.type,
            run_id="run-root",
            payload=started_event.payload,
        )
        self.events.append(event)
        return RunBeginResult(run_id="run-root", event=event)

    async def commit(self, run_id, commit):
        assert run_id == "run-root"
        self.commits.append(commit)
        if commit.replace_steps is not None:
            self.steps = list(commit.replace_steps)
        for update in commit.step_updates:
            self.steps = [
                replace(
                    step,
                    status=update.status,
                    result_summary=(
                        update.result_summary
                        if update.result_summary is not None
                        else step.result_summary
                    ),
                    error=(
                        update.error if update.error is not None else step.error
                    ),
                )
                if step.id == update.step_id
                else step
                for step in self.steps
            ]
        if commit.terminal_status is not None:
            self.status = commit.terminal_status
            self.error = commit.error
        self.events.extend(commit.events)
        return commit.events

    async def append_event(self, run_id, event):
        assert run_id == "run-root"
        self.events.append(event)

    async def append_trace(self, run_id, trace):
        del run_id, trace

    async def bind_conversation(self, run_id, conversation_id):
        del run_id, conversation_id


class _Sink:
    def __init__(self) -> None:
        self.buffer: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.buffer.append(event)

    def drain(self) -> tuple[AgentEvent, ...]:
        events = tuple(self.buffer)
        self.buffer.clear()
        return events


def _plan(*, revised: bool = False) -> TaskPlan:
    completed_summary = (
        "Durable execution completed this Planner step."
        if revised
        else None
    )
    return TaskPlan(
        title="Revised durable plan" if revised else "Durable plan",
        steps=(
            TaskStep(
                id="gather",
                title="Gather evidence",
                type=StepType.ANALYZE,
                executor=StepExecutor.MODEL,
                status=StepStatus.DONE if revised else StepStatus.PENDING,
                result_summary=completed_summary,
            ),
            TaskStep(
                id="deliver",
                title=(
                    "Deliver the revised result"
                    if revised
                    else "Deliver the result"
                ),
                type=StepType.WRITE,
                executor=StepExecutor.MODEL,
                depends_on=("gather",),
                description="Use the latest checkpoint" if revised else None,
            ),
        ),
    )


def _history_plan() -> TaskPlan:
    return TaskPlan(
        title="History plan",
        steps=(
            TaskStep(
                id="source",
                title="Read source",
                type=StepType.ANALYZE,
                executor=StepExecutor.MODEL,
            ),
            TaskStep(
                id="gather",
                title="Gather evidence",
                type=StepType.ANALYZE,
                executor=StepExecutor.MODEL,
                depends_on=("source",),
            ),
            TaskStep(
                id="deliver",
                title="Deliver result",
                type=StepType.WRITE,
                executor=StepExecutor.MODEL,
                depends_on=("gather",),
            ),
        ),
    )


def _request() -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role=MessageRole.USER, content="Do the work"),),
        model=ModelRequest(provider="fixture", model="scripted"),
        domain_context=DomainContext(namespace="fixture.durable"),
        session_id="session-1",
        mode="agent",
        context_window=32_000,
        tools_enabled=True,
    )


def _admission() -> TaskAdmissionDecision:
    return TaskAdmissionDecision(
        mode=ExecutionMode.DURABLE,
        reason_code="fixture_durable",
        covered_step_ids=("gather", "deliver"),
        execution_recipe=ExecutionRecipe(
            kind="fixture.durable",
            steps=(
                ExecutionRecipeStep(id="gather", kind="fixture"),
                ExecutionRecipeStep(
                    id="deliver",
                    kind="fixture",
                    depends_on=("gather",),
                ),
            ),
        ),
    )


async def _started() -> tuple[AgentRunController, _Repository, _Sink]:
    repository = _Repository()
    sink = _Sink()
    controller = AgentRunController(repository=repository, event_sink=sink)
    await controller.start(
        RunCreateParams(session_id="session-1", prompt="Do the work", mode="agent"),
        _plan(),
    )
    sink.drain()
    return controller, repository, sink


def _progress_event(*, status: str) -> AgentEvent:
    return AgentEvent(
        type=CoreEventType.LONG_TASK_PROGRESS,
        payload={
            "taskId": "task-1",
            "units": [{"plannerStepId": "gather", "status": status}],
        },
    )


@pytest.mark.asyncio
async def test_durable_checkpoint_atomically_revises_root_plan_before_evidence():
    controller, repository, sink = await _started()

    class _Dispatcher:
        async def dispatch(self, *args, **kwargs):
            del args, kwargs
            return LongTaskDispatchReceipt(
                task_id="task-1",
                message="Dispatched",
            )

        async def execute(self, task_id, *, observer, **kwargs):
            del kwargs
            await observer(LongTaskExecutionUpdate(
                event=_progress_event(status="completed"),
            ))
            await observer(LongTaskExecutionUpdate(
                event=AgentEvent(
                    type="long_task.checkpoint",
                    payload={"taskId": task_id, "checkpoint": "episode-1"},
                ),
                plan_revision=_plan(revised=True),
            ))
            return LongTaskExecutionResult(
                task_id=task_id,
                status=LongTaskExecutionStatus.COMPLETED,
                final_response="Finished",
            )

    yielded = [
        event
        async for event in complete_admitted_task(
            controller=controller,
            request=_request(),
            plan=_plan(),
            admission=_admission(),
            dispatcher=_Dispatcher(),
            sink=sink,
            signal=None,
        )
    ]

    assert controller.status is RunStatus.DONE
    assert repository.status is RunStatus.DONE
    assert [step.title for step in repository.steps] == [
        "Gather evidence",
        "Deliver the revised result",
    ]
    assert repository.steps[1].depends_on == ("gather",)
    checkpoint_index = next(
        index
        for index, event in enumerate(repository.events)
        if event.type == "long_task.checkpoint"
    )
    revision_index = max(
        index
        for index, event in enumerate(repository.events[:checkpoint_index])
        if event.type == CoreEventType.RUN_TODOS_UPDATED
    )
    assert revision_index < checkpoint_index
    revision_commit = next(
        commit
        for commit in repository.commits
        if commit.replace_steps is not None
        and commit.events
        and commit.events[0].type == CoreEventType.RUN_TODOS_UPDATED
        and commit.replace_steps[1].title == "Deliver the revised result"
    )
    assert revision_commit.events[0].payload["steps"][1]["title"] == (
        "Deliver the revised result"
    )
    assert any(event.type == "long_task.checkpoint" for event in yielded)


@pytest.mark.asyncio
async def test_invalid_durable_revision_fails_root_and_cancels_old_recipe():
    controller, repository, sink = await _started()
    canceled = asyncio.Event()
    continued = False
    invalid = _plan(revised=True)
    invalid = replace(
        invalid,
        steps=(
            replace(invalid.steps[0], title="Rewrite completed history"),
            invalid.steps[1],
        ),
    )

    class _Dispatcher:
        async def dispatch(self, *args, **kwargs):
            del args, kwargs
            return LongTaskDispatchReceipt(
                task_id="task-1",
                message="Dispatched",
            )

        async def execute(self, task_id, *, observer, **kwargs):
            nonlocal continued
            del task_id, kwargs
            try:
                await observer(LongTaskExecutionUpdate(
                    event=_progress_event(status="completed"),
                ))
                await observer(LongTaskExecutionUpdate(
                    event=AgentEvent(
                        type="long_task.checkpoint",
                        payload={"checkpoint": "invalid"},
                    ),
                    plan_revision=invalid,
                ))
                continued = True
                await asyncio.Event().wait()
            finally:
                canceled.set()

    yielded = [
        event
        async for event in complete_admitted_task(
            controller=controller,
            request=_request(),
            plan=_plan(),
            admission=_admission(),
            dispatcher=_Dispatcher(),
            sink=sink,
            signal=None,
        )
    ]

    assert canceled.is_set()
    assert not continued
    assert controller.status is RunStatus.FAILED
    assert repository.status is RunStatus.FAILED
    assert repository.error == "durable_plan_revision_contract_violation"
    assert all(event.type != "long_task.checkpoint" for event in repository.events)
    assert yielded[-1].type == CoreEventType.RUN_FAILED


@pytest.mark.asyncio
async def test_durable_update_cannot_forge_todo_replacement_event():
    controller, repository, sink = await _started()
    canceled = asyncio.Event()

    class _Dispatcher:
        async def dispatch(self, *args, **kwargs):
            del args, kwargs
            return LongTaskDispatchReceipt(
                task_id="task-1",
                message="Dispatched",
            )

        async def execute(self, task_id, *, observer, **kwargs):
            del task_id, kwargs
            try:
                await observer(LongTaskExecutionUpdate(
                    event=AgentEvent(
                        type=CoreEventType.RUN_TODOS_UPDATED,
                        payload={"steps": []},
                    ),
                    plan_revision=_plan(revised=True),
                ))
                await asyncio.Event().wait()
            finally:
                canceled.set()

    yielded = [
        event
        async for event in complete_admitted_task(
            controller=controller,
            request=_request(),
            plan=_plan(),
            admission=_admission(),
            dispatcher=_Dispatcher(),
            sink=sink,
            signal=None,
        )
    ]

    assert canceled.is_set()
    assert controller.status is RunStatus.FAILED
    assert sum(
        event.type == CoreEventType.RUN_TODOS_UPDATED
        for event in repository.events
    ) == 1
    assert yielded[-1].payload["error"] == (
        "durable_plan_revision_contract_violation"
    )


def test_task_plan_still_rejects_unknown_and_cyclic_revision_dependencies():
    with pytest.raises(ValueError, match="reference earlier steps"):
        TaskPlan(
            title="Unknown dependency",
            steps=(replace(_plan().steps[0], depends_on=("missing",)),),
        )

    with pytest.raises(ValueError, match="reference earlier steps"):
        TaskPlan(
            title="Cyclic dependency",
            steps=(
                replace(_plan().steps[0], depends_on=("deliver",)),
                _plan().steps[1],
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    (
        lambda step: replace(step, title="Rewrite history"),
        lambda step: replace(step, type=StepType.REVIEW),
        lambda step: replace(
            step,
            executor=StepExecutor.AGENT,
            agent_role="fixture-agent",
        ),
        lambda step: replace(step, depends_on=()),
        lambda step: replace(step, status=StepStatus.PENDING),
        lambda step: replace(step, description="Changed detail"),
        lambda step: replace(step, result_summary="Changed receipt"),
        lambda step: replace(step, error="changed_error"),
    ),
    ids=(
        "title",
        "type",
        "executor",
        "dependencies",
        "done-status",
        "description",
        "result-summary",
        "error",
    ),
)
async def test_durable_revision_rejects_changes_to_completed_step_contract(
    mutate,
):
    repository = _Repository()
    sink = _Sink()
    controller = AgentRunController(repository=repository, event_sink=sink)
    plan = _history_plan()
    await controller.start(
        RunCreateParams(session_id="session-1", prompt="work", mode="agent"),
        plan,
    )
    await controller.sync_durable_execution({
        "source": StepStatus.DONE,
        "gather": StepStatus.DONE,
    })
    assert controller.snapshot is not None
    completed_source = next(
        step
        for step in controller.snapshot.steps
        if step.id == "source"
    )
    completed_gather = next(
        step
        for step in controller.snapshot.steps
        if step.id == "gather"
    )
    revised = replace(
        plan,
        steps=(
            completed_source,
            mutate(completed_gather),
            plan.steps[2],
        ),
    )

    with pytest.raises(
        ContractViolationError,
        match="cannot change completed steps",
    ):
        _validate_durable_plan_revision(
            controller,
            revised,
            ("source", "gather", "deliver"),
        )


@pytest.mark.asyncio
async def test_durable_revision_rejects_added_or_removed_step_ids():
    controller, _repository, _sink = await _started()
    revision = replace(_plan(), steps=(_plan().steps[0],))

    with pytest.raises(
        ContractViolationError,
        match="preserve admitted step ids",
    ):
        _validate_durable_plan_revision(
            controller,
            revision,
            ("gather", "deliver"),
        )

    added = replace(
        _plan(),
        steps=(
            *_plan().steps,
            TaskStep(
                id="extra",
                title="Extra work",
                type=StepType.REVIEW,
                executor=StepExecutor.MODEL,
                depends_on=("deliver",),
            ),
        ),
    )
    with pytest.raises(
        ContractViolationError,
        match="preserve admitted step ids",
    ):
        _validate_durable_plan_revision(
            controller,
            added,
            ("gather", "deliver"),
        )


@pytest.mark.asyncio
async def test_non_persisted_durable_revision_fails_before_checkpoint_emission():
    controller, repository, sink = await _started()
    canceled = asyncio.Event()

    class _Dispatcher:
        async def dispatch(self, *args, **kwargs):
            del args, kwargs
            return LongTaskDispatchReceipt(
                task_id="task-1",
                message="Dispatched",
            )

        async def execute(self, task_id, *, observer, **kwargs):
            del task_id, kwargs
            try:
                await observer(LongTaskExecutionUpdate(
                    event=AgentEvent(
                        type="long_task.checkpoint",
                        payload={"checkpoint": "ephemeral"},
                    ),
                    persist=False,
                    plan_revision=_plan(revised=True),
                ))
            finally:
                canceled.set()

    yielded = [
        event
        async for event in complete_admitted_task(
            controller=controller,
            request=_request(),
            plan=_plan(),
            admission=_admission(),
            dispatcher=_Dispatcher(),
            sink=sink,
            signal=None,
        )
    ]

    assert canceled.is_set()
    assert controller.status is RunStatus.FAILED
    assert all(event.type != "long_task.checkpoint" for event in repository.events)
    assert yielded[-1].payload["error"] == (
        "durable_plan_revision_contract_violation"
    )
