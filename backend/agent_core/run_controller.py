"""Port-backed persistence and event orchestration for Core Run state."""

from __future__ import annotations

import asyncio
from typing import Awaitable, TypeVar

from agent_core.contracts import (
    RunCreateParams,
    RunStatus,
    TaskPlan,
    TaskStep,
    ToolBatchOutcome,
    TraceRecord,
)
from agent_core.events import AgentEvent, CoreEventType
from agent_core.ports import EventSink, RunCommit, RunRepository
from agent_core.run_state import RunSnapshot, RunStateMachine, RunTransition


_ReceiptT = TypeVar("_ReceiptT")


class AgentRunController:
    """Own one live run while depending only on Core ports."""

    def __init__(
        self,
        *,
        repository: RunRepository,
        event_sink: EventSink,
    ):
        self._repository = repository
        self._event_sink = event_sink
        self._snapshot: RunSnapshot | None = None
        self._mutation_lock = asyncio.Lock()

    @property
    def run_id(self) -> str | None:
        return self._snapshot.run_id if self._snapshot is not None else None

    @property
    def status(self) -> RunStatus | None:
        return self._snapshot.status if self._snapshot is not None else None

    @property
    def error(self) -> str | None:
        return self._snapshot.error if self._snapshot is not None else None

    @property
    def snapshot(self) -> RunSnapshot | None:
        return self._snapshot

    async def start(
        self,
        params: RunCreateParams,
        plan: TaskPlan | None = None,
    ) -> RunSnapshot:
        async with self._mutation_lock:
            await self._begin_unlocked(params)
            if plan is not None:
                return await self._install_plan_unlocked(plan)
            return self._require_started()

    async def begin(self, params: RunCreateParams) -> RunSnapshot:
        """Atomically create the Run and its first persisted outbox event."""

        async with self._mutation_lock:
            return await self._begin_unlocked(params)

    async def _begin_unlocked(self, params: RunCreateParams) -> RunSnapshot:
        if self._snapshot is not None:
            raise RuntimeError("run controller has already started")
        event_template = AgentEvent(type=CoreEventType.RUN_STARTED, payload={
            "status": RunStatus.RUNNING.value,
            "title": "To-dos",
            "goal": None,
        })
        begun, canceled = await _await_repository_receipt(
            self._repository.begin(params, event_template)
        )
        snapshot = RunStateMachine.initialize(begun.run_id)
        self._snapshot = snapshot
        _raise_if_canceled(canceled)
        await self._publish((begun.event,))
        return snapshot

    async def bind_conversation(self, conversation_id: int) -> None:
        state = self._require_started()
        await self._repository.bind_conversation(state.run_id, conversation_id)

    async def install_plan(self, plan: TaskPlan) -> RunSnapshot:
        """Install the validated plan after the run has become observable.

        Starting before model planning lets a host receive ``run.started`` and
        persist a canceled/failed terminal state even when the planner never
        returns. A plan is write-once so its tool grants cannot be replaced
        while a run is executing.
        """

        async with self._mutation_lock:
            return await self._install_plan_unlocked(plan)

    async def revise_plan(self, plan: TaskPlan) -> RunSnapshot:
        """Atomically replace tentative steps after observing runtime evidence."""

        async with self._mutation_lock:
            state = self._require_started()
            revised = RunStateMachine.revise_plan(state, plan)
            event = AgentEvent(
                type=CoreEventType.RUN_TODOS_UPDATED,
                run_id=state.run_id,
                payload=_todos_payload(revised),
            )
            persisted, canceled = await _await_repository_receipt(
                self._repository.commit(
                    state.run_id,
                    RunCommit(replace_steps=revised.steps, events=(event,)),
                )
            )
            if self._snapshot is not state:
                raise RuntimeError("stale run plan revision")
            self._snapshot = revised
            _raise_if_canceled(canceled)
            await self._publish(persisted)
            return revised

    async def _install_plan_unlocked(self, plan: TaskPlan) -> RunSnapshot:
        state = self._require_started()
        if state.terminal:
            raise RuntimeError("cannot install a plan on a terminal run")
        if state.steps:
            raise RuntimeError("run plan has already been installed")
        planned = RunStateMachine.initialize(state.run_id, plan)
        event = AgentEvent(
            type=CoreEventType.RUN_TODOS_UPDATED,
            run_id=state.run_id,
            payload=_todos_payload(planned),
        )
        persisted, canceled = await _await_repository_receipt(
            self._repository.commit(
                state.run_id,
                RunCommit(replace_steps=planned.steps, events=(event,)),
            )
        )
        self._snapshot = planned
        _raise_if_canceled(canceled)
        await self._publish(persisted)
        return planned

    def current_allowed_tool_names(self) -> frozenset[str]:
        state = self._snapshot
        if state is None:
            return frozenset()
        return RunStateMachine.allowed_tool_names_for_current_transition(state)

    def future_allowed_tool_names(self) -> frozenset[str]:
        state = self._snapshot
        if state is None:
            return frozenset()
        return RunStateMachine.future_allowed_tool_names(state)

    def allowed_tool_names(self) -> frozenset[str]:
        state = self._snapshot
        if state is None:
            return frozenset()
        return RunStateMachine.allowed_tool_names(state)

    async def record_trace(self, trace: TraceRecord) -> None:
        state = self._require_started()
        await self._repository.append_trace(state.run_id, trace)

    async def on_model_delta(self) -> None:
        async with self._mutation_lock:
            state = self._require_started()
            await self._apply(RunStateMachine.on_model_delta(state))

    async def on_tool_calls_started(self, tool_names: tuple[str, ...]) -> None:
        async with self._mutation_lock:
            state = self._require_started()
            await self._apply(
                RunStateMachine.on_tool_calls_started(state, tool_names)
            )

    async def on_tool_round_completed(
        self,
        outcome: ToolBatchOutcome = ToolBatchOutcome.COMPLETED,
    ) -> None:
        async with self._mutation_lock:
            state = self._require_started()
            await self._apply(
                RunStateMachine.on_tool_round_completed(state, outcome)
            )

    async def on_tool_round_failed(
        self,
        error: str = "tool_execution_failed",
    ) -> None:
        async with self._mutation_lock:
            state = self._require_started()
            await self._apply(RunStateMachine.on_tool_round_failed(state, error))

    async def complete(self, final_response: str = "") -> None:
        async with self._mutation_lock:
            state = self._require_started()
            await self._apply(RunStateMachine.complete(state, final_response))

    async def fail(self, error: str) -> None:
        async with self._mutation_lock:
            state = self._require_started()
            await self._apply(RunStateMachine.fail(state, error))

    async def cancel(self, reason: str = "request_canceled") -> None:
        async with self._mutation_lock:
            state = self._require_started()
            await self._apply(RunStateMachine.cancel(state, reason))

    async def current_steps(self) -> tuple[TaskStep, ...]:
        state = self._require_started()
        return state.steps

    async def _apply(self, transition: RunTransition) -> None:
        if not transition.changed:
            return
        before = self._require_started()
        if before is not transition.before:
            raise RuntimeError("stale run transition")

        events = _transition_events(transition)
        terminal_status = (
            transition.after.status
            if transition.after.status is not before.status
            else None
        )
        persisted, canceled = await _await_repository_receipt(
            self._repository.commit(
                before.run_id,
                RunCommit(
                    step_updates=transition.step_updates,
                    terminal_status=terminal_status,  # type: ignore[arg-type]
                    final_response=(
                        transition.after.final_response
                        if terminal_status is RunStatus.DONE
                        else None
                    ),
                    error=(
                        transition.after.error
                        if terminal_status is RunStatus.FAILED
                        else None
                    ),
                    events=events,
                ),
            )
        )
        if self._snapshot is not before:
            raise RuntimeError("stale run transition")
        self._snapshot = transition.after
        _raise_if_canceled(canceled)
        await self._publish(persisted)

    async def _publish(self, events: tuple[AgentEvent, ...]) -> None:
        for event in events:
            await self._event_sink.emit(event)

    def _require_started(self) -> RunSnapshot:
        if self._snapshot is None:
            raise RuntimeError("run controller has not started")
        return self._snapshot


async def _await_repository_receipt(
    operation: Awaitable[_ReceiptT],
) -> tuple[_ReceiptT, bool]:
    """Settle a cancellation-linearizable repository operation.

    A canceled child means the port guarantees no durable write. If the child
    returns a receipt after ``cancel()``, the durable write won the race and
    the controller must apply that receipt before propagating caller cancel.
    """

    task = asyncio.create_task(operation)
    caller_canceled = False
    while True:
        try:
            return await asyncio.shield(task), caller_canceled
        except asyncio.CancelledError:
            # A canceled repository child is authoritative: the port promises
            # that no durable write exists, so propagate its cancellation.
            if task.cancelled():
                raise

            # Cancellation of the controller is forwarded to the repository
            # exactly once. The repository may then either roll back and
            # cancel, or finish a commit and return its durable receipt.
            # Continue through shield so repeated caller cancel() requests
            # cannot interrupt that settlement or cancel the child again.
            if not caller_canceled:
                caller_canceled = True
                task.cancel()

            # The receipt may have won the same event-loop tick as caller
            # cancellation. Read it synchronously rather than awaiting again.
            if task.done():
                return task.result(), True


def _raise_if_canceled(canceled: bool) -> None:
    if canceled:
        raise asyncio.CancelledError


def _todos_payload(state: RunSnapshot) -> dict[str, object]:
    return {
        "title": state.title,
        "goal": state.goal,
        "status": state.status.value,
        "steps": [_step_payload(step) for step in state.steps],
    }


def _transition_events(transition: RunTransition) -> tuple[AgentEvent, ...]:
    state = transition.after
    events = [
        AgentEvent(
            type=CoreEventType.RUN_TODO_UPDATED,
            run_id=state.run_id,
            payload={
                "step_id": step.id,
                "step": _step_payload(step),
                "status": state.status.value,
            },
        )
        for update in transition.step_updates
        for step in state.steps
        if step.id == update.step_id
    ]
    if state.status is transition.before.status:
        return tuple(events)

    event_type = {
        RunStatus.DONE: CoreEventType.RUN_COMPLETED,
        RunStatus.BLOCKED: CoreEventType.RUN_BLOCKED,
        RunStatus.FAILED: CoreEventType.RUN_FAILED,
        RunStatus.CANCELED: CoreEventType.RUN_CANCELED,
    }[state.status]
    payload: dict[str, object] = {"status": state.status.value}
    if state.status is RunStatus.DONE:
        payload["final_response"] = state.final_response
    if state.status is RunStatus.FAILED:
        payload["error"] = state.error
    if state.status is RunStatus.CANCELED:
        payload["reason"] = state.error
    events.append(AgentEvent(
        type=event_type,
        run_id=state.run_id,
        payload=payload,
    ))
    return tuple(events)


def _step_payload(step: TaskStep) -> dict[str, object]:
    return {
        "id": step.id,
        "title": step.title,
        "type": step.type.value,
        "executor": step.executor.value,
        "status": step.status.value,
        "risk_level": step.risk_level.value if step.risk_level else None,
        "suggested_tools": list(step.suggested_tools),
        "description": step.description,
        "result_summary": step.result_summary,
        "error": step.error,
    }
