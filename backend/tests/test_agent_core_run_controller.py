from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Sequence

import pytest

from agent_core.contracts import (
    RunCreateParams,
    RunStatus,
    StepExecutor,
    StepStatus,
    StepType,
    TaskPlan,
    TaskStep,
    TaskStepUpdate,
    ToolBatchOutcome,
    TraceRecord,
)
from agent_core.events import AgentEvent, CoreEventType
from agent_core.errors import ContractViolationError
from agent_core.ports import RunBeginResult, RunCommit, RuntimeObserver
from agent_core.run_controller import AgentRunController


class RecordingRepository:
    def __init__(self):
        self.events: list[AgentEvent] = []
        self.traces: list[TraceRecord] = []
        self.steps: list[TaskStep] = []
        self.transitions: list[tuple[RunStatus, str | None, str | None]] = []
        self.fail_update = False
        self.fail_transition = False
        self.fail_event = False
        self.fail_begin = False
        self.fail_replace = False

    async def begin(
        self,
        params: RunCreateParams,
        started_event: AgentEvent,
    ) -> RunBeginResult:
        if self.fail_begin:
            raise RuntimeError("begin persistence failed")
        self.create_params = params
        event = AgentEvent(
            type=started_event.type,
            run_id="run-controller",
            payload=started_event.payload,
        )
        self.events.append(event)
        return RunBeginResult(run_id="run-controller", event=event)

    async def commit(
        self,
        run_id: str,
        commit: RunCommit,
    ) -> tuple[AgentEvent, ...]:
        steps = list(self.steps)
        transitions = list(self.transitions)
        events = list(self.events)
        if commit.replace_steps is not None:
            if self.fail_replace:
                raise RuntimeError("plan persistence failed")
            steps = list(commit.replace_steps)
        if commit.step_updates and self.fail_update:
            raise RuntimeError("step persistence failed")
        for update in commit.step_updates:
            for index, step in enumerate(steps):
                if step.id == update.step_id:
                    steps[index] = replace(
                        step,
                        status=update.status,
                        result_summary=update.result_summary or step.result_summary,
                        error=update.error or step.error,
                    )
                    break
            else:
                raise AssertionError("unknown step")
        if commit.terminal_status is not None:
            if self.fail_transition:
                raise RuntimeError("terminal persistence failed")
            transitions.append((
                commit.terminal_status,
                commit.final_response,
                commit.error,
            ))
        if commit.events and self.fail_event:
            raise RuntimeError("event persistence failed")
        events.extend(commit.events)
        self.steps = steps
        self.transitions = transitions
        self.events = events
        return commit.events

    async def create(self, params: RunCreateParams) -> str:
        self.create_params = params
        return "run-controller"

    async def bind_conversation(self, run_id: str, conversation_id: int) -> None:
        self.conversation = (run_id, conversation_id)

    async def replace_steps(self, run_id: str, steps: Sequence[TaskStep]) -> None:
        self.steps = list(steps)

    async def update_step(self, run_id: str, update: TaskStepUpdate) -> None:
        if self.fail_update:
            raise RuntimeError("step persistence failed")
        for index, step in enumerate(self.steps):
            if step.id == update.step_id:
                self.steps[index] = replace(
                    step,
                    status=update.status,
                    result_summary=update.result_summary or step.result_summary,
                    error=update.error or step.error,
                )
                return
        raise AssertionError("unknown step")

    async def transition(
        self,
        run_id: str,
        status,
        *,
        final_response: str | None = None,
        error: str | None = None,
    ) -> None:
        if self.fail_transition:
            raise RuntimeError("terminal persistence failed")
        self.transitions.append((status, final_response, error))

    async def append_event(self, run_id: str, event: AgentEvent) -> None:
        if self.fail_event:
            raise RuntimeError("event persistence failed")
        self.events.append(event)

    async def append_trace(self, run_id: str, trace: TraceRecord) -> None:
        self.traces.append(trace)


class RecordingSink:
    def __init__(self):
        self.events: list[AgentEvent] = []
        self.fail = False

    async def emit(self, event: AgentEvent) -> None:
        if self.fail:
            raise RuntimeError("sink delivery failed")
        self.events.append(event)


class DelayedTerminalAckRepository(RecordingRepository):
    """Commit a terminal state, then hold its acknowledgement to the caller."""

    def __init__(self):
        super().__init__()
        self.terminal_committed = asyncio.Event()
        self.release_terminal_ack = asyncio.Event()

    async def commit(
        self,
        run_id: str,
        commit: RunCommit,
    ) -> tuple[AgentEvent, ...]:
        events = await super().commit(run_id, commit)
        if commit.terminal_status is not None:
            self.terminal_committed.set()
            try:
                await self.release_terminal_ack.wait()
            except asyncio.CancelledError:
                # Durable commit already happened, so the port must return its
                # receipt even when cancellation wins the delayed-ack race.
                return events
        return events


class RepeatedCancelTerminalAckRepository(RecordingRepository):
    """Keep a durable receipt pending after observing the first cancellation."""

    def __init__(self):
        super().__init__()
        self.terminal_committed = asyncio.Event()
        self.first_child_cancel = asyncio.Event()
        self.release_terminal_ack = asyncio.Event()
        self.child_cancel_count = 0

    async def commit(
        self,
        run_id: str,
        commit: RunCommit,
    ) -> tuple[AgentEvent, ...]:
        events = await super().commit(run_id, commit)
        if commit.terminal_status is not None:
            self.terminal_committed.set()
            while not self.release_terminal_ack.is_set():
                try:
                    await self.release_terminal_ack.wait()
                except asyncio.CancelledError:
                    # The first cancel asks the port to settle. Any additional
                    # count means the controller leaked a repeated caller
                    # cancellation into the receipt-producing child task.
                    self.child_cancel_count += 1
                    self.first_child_cancel.set()
        return events


def _plan() -> TaskPlan:
    return TaskPlan(
        title="read and answer",
        steps=(
            TaskStep(
                id="read",
                title="Read",
                type=StepType.READ,
                executor=StepExecutor.TOOL,
                suggested_tools=("readChapter",),
            ),
            TaskStep(
                id="answer",
                title="Answer",
                type=StepType.REVIEW,
                executor=StepExecutor.MODEL,
            ),
        ),
    )


async def _started():
    repository = RecordingRepository()
    sink = RecordingSink()
    controller = AgentRunController(repository=repository, event_sink=sink)
    await controller.start(
        RunCreateParams(session_id="12", prompt="work", mode="agent"),
        _plan(),
    )
    return controller, repository, sink


@pytest.mark.asyncio
async def test_controller_is_runtime_observer_and_uses_core_events():
    controller, repository, sink = await _started()

    assert isinstance(controller, RuntimeObserver)
    assert controller.current_allowed_tool_names() == {"readChapter"}
    assert controller.future_allowed_tool_names() == frozenset()
    assert [event.type for event in repository.events] == [
        CoreEventType.RUN_STARTED,
        CoreEventType.RUN_TODOS_UPDATED,
    ]
    assert repository.events == sink.events
    assert all(
        persisted is emitted
        for persisted, emitted in zip(repository.events, sink.events)
    )


@pytest.mark.asyncio
async def test_controller_rejects_non_atomic_tool_plan_before_persistence():
    repository = RecordingRepository()
    sink = RecordingSink()
    controller = AgentRunController(repository=repository, event_sink=sink)
    await controller.begin(
        RunCreateParams(session_id=None, prompt="unsafe", mode="agent")
    )
    event_count = len(repository.events)

    with pytest.raises(ContractViolationError, match="exactly one"):
        await controller.install_plan(TaskPlan(
            title="unsafe",
            steps=(TaskStep(
                id="combined",
                title="Combined write",
                type=StepType.WRITE,
                executor=StepExecutor.TOOL,
                suggested_tools=("update", "delete"),
            ),),
        ))

    assert repository.steps == []
    assert len(repository.events) == event_count
    assert controller.snapshot is not None
    assert controller.snapshot.steps == ()


@pytest.mark.asyncio
async def test_controller_progresses_steps_and_persists_terminal_before_events():
    controller, repository, sink = await _started()

    await controller.on_tool_calls_started(("readChapter",))
    await controller.on_tool_round_completed()
    await controller.on_model_delta()
    await controller.complete("final")

    assert controller.status is RunStatus.DONE
    assert [step.status for step in await controller.current_steps()] == [
        StepStatus.DONE,
        StepStatus.DONE,
    ]
    assert repository.transitions == [(RunStatus.DONE, "final", None)]
    assert sink.events[-1].type == CoreEventType.RUN_COMPLETED
    assert repository.events[-1] is sink.events[-1]


@pytest.mark.asyncio
async def test_controller_persists_declined_tool_as_blocked_then_finishes_report():
    controller, repository, sink = await _started()

    await controller.on_tool_round_completed(ToolBatchOutcome.DECLINED)

    steps = await controller.current_steps()
    assert [step.status for step in steps] == [
        StepStatus.BLOCKED,
        StepStatus.PENDING,
    ]
    assert steps[0].result_summary == (
        "User declined approval; the planned tool was not executed."
    )
    assert steps[0].error == "approval_rejected"
    assert repository.steps == list(steps)
    assert controller.current_allowed_tool_names() == frozenset()
    assert controller.future_allowed_tool_names() == frozenset()

    declined_event = sink.events[-1]
    assert declined_event.type == CoreEventType.RUN_TODO_UPDATED
    assert declined_event.payload["step"]["status"] == "blocked"
    assert declined_event.payload["step"]["error"] == "approval_rejected"
    assert declined_event.payload["step"]["result_summary"] == (
        "User declined approval; the planned tool was not executed."
    )
    assert "Planned tool step completed." not in str(declined_event.payload)

    await controller.on_model_delta()
    await controller.complete("not applied")

    final_steps = await controller.current_steps()
    assert controller.status is RunStatus.DONE
    assert [step.status for step in final_steps] == [
        StepStatus.BLOCKED,
        StepStatus.DONE,
    ]
    assert final_steps[0].error == "approval_rejected"
    assert repository.steps == list(final_steps)
    assert repository.transitions == [(RunStatus.DONE, "not applied", None)]
    assert sink.events[-1].type == CoreEventType.RUN_COMPLETED


@pytest.mark.asyncio
async def test_step_persistence_failure_does_not_advance_memory_or_emit():
    controller, repository, sink = await _started()
    before = controller.snapshot
    before_event_count = len(sink.events)
    repository.fail_update = True

    with pytest.raises(RuntimeError, match="step persistence failed"):
        await controller.on_tool_round_completed()

    assert controller.snapshot is before
    assert len(sink.events) == before_event_count


@pytest.mark.asyncio
async def test_terminal_persistence_failure_keeps_memory_running_and_emits_nothing():
    repository = RecordingRepository()
    sink = RecordingSink()
    controller = AgentRunController(repository=repository, event_sink=sink)
    await controller.start(
        RunCreateParams(session_id=None, prompt="answer", mode="agent"),
        TaskPlan(
            title="answer",
            steps=(TaskStep(
                id="answer",
                title="Answer",
                type=StepType.REVIEW,
                executor=StepExecutor.MODEL,
            ),),
        ),
    )
    before = controller.snapshot
    before_event_count = len(sink.events)
    repository.fail_transition = True

    with pytest.raises(RuntimeError, match="terminal persistence failed"):
        await controller.complete("answer")

    assert controller.snapshot is before
    assert controller.status is RunStatus.RUNNING
    assert len(sink.events) == before_event_count


@pytest.mark.asyncio
async def test_canceled_terminal_waiter_cannot_write_a_second_terminal_state():
    repository = DelayedTerminalAckRepository()
    sink = RecordingSink()
    controller = AgentRunController(repository=repository, event_sink=sink)
    await controller.start(
        RunCreateParams(session_id=None, prompt="answer", mode="agent")
    )

    completion = asyncio.create_task(controller.complete("answer"))
    await repository.terminal_committed.wait()
    completion.cancel()
    with pytest.raises(asyncio.CancelledError):
        await completion

    # Receipt is applied to memory before caller cancellation propagates. A
    # disconnect cleanup therefore observes DONE and cannot write CANCELED.
    assert controller.status is RunStatus.DONE
    assert len(repository.transitions) == 1
    await controller.cancel("consumer_disconnected")
    assert repository.transitions == [(RunStatus.DONE, "answer", None)]
    assert [event.type for event in repository.events].count(
        CoreEventType.RUN_COMPLETED
    ) == 1
    assert CoreEventType.RUN_COMPLETED not in [event.type for event in sink.events]
    assert CoreEventType.RUN_CANCELED not in [event.type for event in repository.events]


@pytest.mark.asyncio
async def test_repeated_caller_cancel_cannot_interrupt_repository_receipt_settlement():
    repository = RepeatedCancelTerminalAckRepository()
    sink = RecordingSink()
    controller = AgentRunController(repository=repository, event_sink=sink)
    await controller.start(
        RunCreateParams(session_id=None, prompt="answer", mode="agent")
    )

    completion = asyncio.create_task(controller.complete("answer"))
    await repository.terminal_committed.wait()

    completion.cancel()
    await repository.first_child_cancel.wait()
    completion.cancel()
    await asyncio.sleep(0)

    assert not completion.done()
    assert repository.child_cancel_count == 1

    repository.release_terminal_ack.set()
    with pytest.raises(asyncio.CancelledError):
        await completion

    assert controller.status is RunStatus.DONE
    assert repository.transitions == [(RunStatus.DONE, "answer", None)]
    assert repository.child_cancel_count == 1
    assert CoreEventType.RUN_COMPLETED not in [event.type for event in sink.events]


@pytest.mark.asyncio
async def test_controller_serializes_concurrent_terminal_mutations():
    repository = DelayedTerminalAckRepository()
    sink = RecordingSink()
    controller = AgentRunController(repository=repository, event_sink=sink)
    await controller.start(
        RunCreateParams(session_id=None, prompt="answer", mode="agent")
    )

    completion = asyncio.create_task(controller.complete("answer"))
    await repository.terminal_committed.wait()
    cancellation = asyncio.create_task(
        controller.cancel("consumer_disconnected")
    )
    await asyncio.sleep(0)

    assert len(repository.transitions) == 1
    repository.release_terminal_ack.set()
    await asyncio.gather(completion, cancellation)

    assert controller.status is RunStatus.DONE
    assert repository.transitions == [(RunStatus.DONE, "answer", None)]
    assert [event.type for event in sink.events].count(
        CoreEventType.RUN_COMPLETED
    ) == 1
    assert CoreEventType.RUN_CANCELED not in [event.type for event in sink.events]


@pytest.mark.asyncio
async def test_event_persistence_failure_never_reaches_sink():
    controller, repository, sink = await _started()
    before = controller.snapshot
    before_event_count = len(sink.events)
    before_repo_event_count = len(repository.events)
    repository.fail_event = True

    with pytest.raises(RuntimeError, match="event persistence failed"):
        await controller.on_tool_round_completed()

    assert controller.snapshot is before
    assert len(repository.events) == before_repo_event_count
    assert len(sink.events) == before_event_count


@pytest.mark.asyncio
async def test_trace_is_repository_only_and_terminal_is_idempotent():
    controller, repository, sink = await _started()
    await controller.record_trace(TraceRecord(stage="test", outcome="ok"))
    before_event_count = len(sink.events)

    await controller.cancel("client_disconnected")
    after_cancel_count = len(sink.events)
    await controller.complete("late")
    await controller.fail("late")

    assert [(trace.stage, trace.outcome) for trace in repository.traces] == [
        ("test", "ok")
    ]
    assert before_event_count < after_cancel_count
    assert len(sink.events) == after_cancel_count
    assert controller.status is RunStatus.CANCELED


@pytest.mark.asyncio
async def test_controller_can_start_before_planning_and_install_plan_once():
    repository = RecordingRepository()
    sink = RecordingSink()
    controller = AgentRunController(repository=repository, event_sink=sink)
    await controller.start(
        RunCreateParams(session_id=None, prompt="work", mode="agent")
    )

    assert controller.snapshot is not None
    assert controller.snapshot.steps == ()
    assert [event.type for event in sink.events] == [CoreEventType.RUN_STARTED]

    planned = await controller.install_plan(_plan())

    assert planned.steps
    assert repository.steps == list(planned.steps)
    assert sink.events[-1].type == CoreEventType.RUN_TODOS_UPDATED
    with pytest.raises(RuntimeError, match="already"):
        await controller.install_plan(_plan())


@pytest.mark.asyncio
async def test_failed_plan_persistence_keeps_unplanned_memory_state():
    repository = RecordingRepository()
    sink = RecordingSink()
    controller = AgentRunController(repository=repository, event_sink=sink)
    await controller.start(
        RunCreateParams(session_id=None, prompt="work", mode="agent")
    )
    before = controller.snapshot

    repository.fail_replace = True
    with pytest.raises(RuntimeError, match="plan persistence failed"):
        await controller.install_plan(_plan())

    assert controller.snapshot is before
    assert [event.type for event in sink.events] == [CoreEventType.RUN_STARTED]


@pytest.mark.asyncio
async def test_begin_failure_leaves_repository_memory_and_sink_unchanged():
    repository = RecordingRepository()
    repository.fail_begin = True
    sink = RecordingSink()
    controller = AgentRunController(repository=repository, event_sink=sink)

    with pytest.raises(RuntimeError, match="begin persistence failed"):
        await controller.begin(
            RunCreateParams(session_id=None, prompt="work", mode="agent")
        )

    assert controller.snapshot is None
    assert repository.events == []
    assert sink.events == []


@pytest.mark.asyncio
async def test_sink_failure_keeps_committed_snapshot_and_outbox_for_replay():
    controller, repository, sink = await _started()
    before_repo_event_count = len(repository.events)
    sink.fail = True

    with pytest.raises(RuntimeError, match="sink delivery failed"):
        await controller.on_tool_round_completed()

    assert controller.snapshot is not None
    assert [step.status for step in controller.snapshot.steps] == [
        StepStatus.DONE,
        StepStatus.RUNNING,
    ]
    persisted = repository.events[before_repo_event_count:]
    assert len(persisted) == 2

    sink.fail = False
    for event in persisted:
        await sink.emit(event)
    assert sink.events[-2:] == persisted
    assert all(
        delivered is stored
        for delivered, stored in zip(sink.events[-2:], persisted)
    )


@pytest.mark.asyncio
async def test_begin_sink_failure_still_leaves_run_and_started_outbox_committed():
    repository = RecordingRepository()
    sink = RecordingSink()
    sink.fail = True
    controller = AgentRunController(repository=repository, event_sink=sink)

    with pytest.raises(RuntimeError, match="sink delivery failed"):
        await controller.begin(
            RunCreateParams(session_id=None, prompt="work", mode="agent")
        )

    assert controller.snapshot is not None
    assert controller.status is RunStatus.RUNNING
    assert [event.type for event in repository.events] == [
        CoreEventType.RUN_STARTED
    ]
    assert sink.events == []
