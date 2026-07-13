from __future__ import annotations

from collections.abc import Sequence

import pytest

from agent_core.contracts import (
    RunCreateParams,
    RunStatus,
    TaskStep,
    TaskStepUpdate,
    TraceRecord,
)
from agent_core.events import AgentEvent
from agent_core.ports import (
    EventSink,
    RunBeginResult,
    RunCommit,
    RunRepository,
)
from services.agent_run_controller import AgentRunController


class RecordingRepository:
    def __init__(self):
        self.events: list[AgentEvent] = []
        self.traces: list[TraceRecord] = []
        self.steps: list[TaskStep] = []
        self.transitions: list[tuple[RunStatus, str | None, str | None]] = []
        self.fail_event = False
        self.fail_update = False
        self.fail_transition = False

    async def begin(
        self,
        params: RunCreateParams,
        started_event: AgentEvent,
    ) -> RunBeginResult:
        run_id = await self.create(params)
        event = AgentEvent(
            type=started_event.type,
            run_id=run_id,
            payload=started_event.payload,
        )
        await self.append_event(run_id, event)
        return RunBeginResult(run_id=run_id, event=event)

    async def commit(
        self,
        run_id: str,
        commit: RunCommit,
    ) -> tuple[AgentEvent, ...]:
        if commit.replace_steps is not None:
            await self.replace_steps(run_id, commit.replace_steps)
        for update in commit.step_updates:
            await self.update_step(run_id, update)
        if commit.terminal_status is not None:
            await self.transition(
                run_id,
                commit.terminal_status,
                final_response=commit.final_response,
                error=commit.error,
            )
        for event in commit.events:
            await self.append_event(run_id, event)
        return commit.events

    async def create(self, params: RunCreateParams) -> str:
        self.create_params = params
        return "run-port-test"

    async def bind_conversation(self, run_id: str, conversation_id: int) -> None:
        self.conversation = (run_id, conversation_id)

    async def replace_steps(self, run_id: str, steps: Sequence[TaskStep]) -> None:
        self.steps = list(steps)

    async def update_step(self, run_id: str, update: TaskStepUpdate) -> None:
        if self.fail_update:
            raise RuntimeError("step persistence failed")
        for index, step in enumerate(self.steps):
            if step.id == update.step_id:
                self.steps[index] = TaskStep(
                    id=step.id,
                    title=step.title,
                    type=step.type,
                    executor=step.executor,
                    status=update.status,
                    risk_level=step.risk_level,
                    suggested_tools=step.suggested_tools,
                    description=step.description,
                    result_summary=update.result_summary or step.result_summary,
                    error=update.error or step.error,
                )
                return

    async def transition(
        self,
        run_id: str,
        status,
        *,
        final_response: str | None = None,
        error: str | None = None,
    ) -> None:
        if self.fail_transition:
            raise RuntimeError("transition persistence failed")
        self.transitions.append((status, final_response, error))

    async def append_event(self, run_id: str, event: AgentEvent) -> None:
        if self.fail_event:
            raise RuntimeError("event persistence failed")
        self.events.append(event)

    async def append_trace(self, run_id: str, trace: TraceRecord) -> None:
        self.traces.append(trace)


class RecordingEventSink:
    def __init__(self):
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


async def _start_without_planner(
    repository: RecordingRepository,
    sink: RecordingEventSink,
) -> AgentRunController:
    controller = AgentRunController(repository=repository, event_sink=sink)
    await controller.start(
        session_id="12",
        prompt="port-only run",
        mode="agent",
        key="unused",
        api_provider="unused",
        planner_options={},
        chat_agent_mode="agent",
        available_tool_names=set(),
        use_planner=False,
    )
    return controller


@pytest.mark.asyncio
async def test_controller_runs_with_pure_ports_and_does_not_emit_traces():
    repository = RecordingRepository()
    sink = RecordingEventSink()

    controller = await _start_without_planner(repository, sink)

    assert isinstance(repository, RunRepository)
    assert isinstance(sink, EventSink)
    assert controller.run_id == "run-port-test"
    assert repository.create_params.session_id == "12"
    assert [(trace.stage, trace.outcome) for trace in repository.traces] == [
        ("planner", "skipped"),
    ]
    assert [event.type for event in repository.events] == ["agentRunStarted"]
    assert [event.type for event in sink.events] == ["agentRunStarted"]
    assert repository.events[0] is sink.events[0]


@pytest.mark.asyncio
async def test_controller_does_not_emit_when_event_persistence_fails():
    repository = RecordingRepository()
    repository.fail_event = True
    sink = RecordingEventSink()

    with pytest.raises(RuntimeError, match="event persistence failed"):
        await _start_without_planner(repository, sink)

    assert sink.events == []
    assert len(repository.traces) == 1


@pytest.mark.asyncio
async def test_controller_keeps_running_state_when_terminal_persistence_fails():
    repository = RecordingRepository()
    sink = RecordingEventSink()
    controller = await _start_without_planner(repository, sink)
    repository.fail_transition = True

    with pytest.raises(RuntimeError, match="transition persistence failed"):
        await controller.complete(final_response="answer")

    assert controller.status == "running"
    assert [event.type for event in sink.events] == ["agentRunStarted"]


@pytest.mark.asyncio
async def test_controller_keeps_step_memory_when_step_persistence_fails(monkeypatch):
    async def _plan(**_kwargs):
        return {
            "title": "one step",
            "steps": [{
                "id": "answer",
                "title": "Answer",
                "type": "review",
                "executor": "model",
            }],
        }

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _plan)
    repository = RecordingRepository()
    sink = RecordingEventSink()
    controller = AgentRunController(repository=repository, event_sink=sink)
    await controller.start(
        session_id=None,
        prompt="answer",
        mode="agent",
        key="unused",
        api_provider="unused",
        planner_options={},
        chat_agent_mode="agent",
        available_tool_names=set(),
    )
    before_events = list(sink.events)
    repository.fail_update = True

    with pytest.raises(RuntimeError, match="step persistence failed"):
        await controller.complete(final_response="answer")

    assert controller.status == "running"
    assert controller.current_plan()["steps"][0]["status"] == "running"
    assert sink.events == before_events
