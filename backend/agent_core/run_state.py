"""Pure, immutable Agent Run and todo state transitions."""

from __future__ import annotations

from dataclasses import dataclass, replace

from agent_core.contracts import (
    RunId,
    RunStatus,
    StepExecutor,
    StepStatus,
    StepType,
    TaskPlan,
    TaskStep,
    TaskStepUpdate,
)
from agent_core.errors import ContractViolationError


_TERMINAL_STATUSES = frozenset({
    RunStatus.DONE,
    RunStatus.BLOCKED,
    RunStatus.FAILED,
    RunStatus.CANCELED,
})
_FINISHED_STEP_STATUSES = frozenset({
    StepStatus.DONE,
    StepStatus.BLOCKED,
    StepStatus.FAILED,
})


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    """Detached state of one run at a single logical instant."""

    run_id: RunId
    title: str
    goal: str | None
    status: RunStatus
    steps: tuple[TaskStep, ...] = ()
    final_response: str = ""
    error: str | None = None

    def __post_init__(self) -> None:
        run_id = str(self.run_id or "").strip()
        title = str(self.title or "").strip()
        steps = tuple(self.steps)
        status = RunStatus(self.status)
        if not run_id:
            raise ValueError("run snapshot requires a run id")
        if not title:
            raise ValueError("run snapshot requires a title")
        if len({step.id for step in steps}) != len(steps):
            raise ValueError("run snapshot step ids must be unique")
        running_count = sum(step.status is StepStatus.RUNNING for step in steps)
        if running_count > 1:
            raise ValueError("run snapshot may have at most one running step")
        if status in _TERMINAL_STATUSES and running_count:
            raise ValueError("terminal run snapshot cannot have a running step")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "goal", _optional_text(self.goal))
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "final_response", str(self.final_response or ""))
        object.__setattr__(self, "error", _optional_text(self.error))

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES


@dataclass(frozen=True, slots=True)
class RunTransition:
    """Pure reducer output; callers decide how and when to persist it."""

    before: RunSnapshot
    after: RunSnapshot
    step_updates: tuple[TaskStepUpdate, ...] = ()

    def __post_init__(self) -> None:
        updates = tuple(self.step_updates)
        if self.before.run_id != self.after.run_id:
            raise ValueError("run transition cannot change run id")
        object.__setattr__(self, "step_updates", updates)

    @property
    def changed(self) -> bool:
        return self.before != self.after


class RunStateMachine:
    """Stateless reducer for sequential model/tool todo plans."""

    @staticmethod
    def initialize(
        run_id: RunId,
        plan: TaskPlan | None = None,
        *,
        default_title: str = "To-dos",
    ) -> RunSnapshot:
        if plan is None:
            return RunSnapshot(
                run_id=run_id,
                title=default_title,
                goal=None,
                status=RunStatus.RUNNING,
            )

        steps = tuple(_initial_step(step) for step in plan.steps)
        for step in steps:
            _validate_step_execution_contract(step)
        first_runnable = next(
            (
                index
                for index, step in enumerate(steps)
                if step.status is not StepStatus.DONE
                and step.type is not StepType.CONFIRM
            ),
            -1,
        )
        if first_runnable >= 0:
            steps = _replace_at(
                steps,
                first_runnable,
                replace(steps[first_runnable], status=StepStatus.RUNNING),
            )
        return RunSnapshot(
            run_id=run_id,
            title=plan.title,
            goal=plan.goal,
            status=RunStatus.RUNNING,
            steps=steps,
        )

    @staticmethod
    def on_model_delta(state: RunSnapshot) -> RunTransition:
        if state.terminal or any(
            step.status is StepStatus.RUNNING and _is_tool_step(step)
            for step in state.steps
        ):
            return _unchanged(state)
        index = _next_incomplete_index(state.steps)
        if index < 0 or not _is_model_step(state.steps[index]):
            return _unchanged(state)
        if state.steps[index].status is StepStatus.RUNNING:
            return _unchanged(state)
        return _with_step_changes(
            state,
            ((index, replace(state.steps[index], status=StepStatus.RUNNING)),),
        )

    @staticmethod
    def on_tool_calls_started(
        state: RunSnapshot,
        tool_names: frozenset[str] | set[str] | tuple[str, ...],
    ) -> RunTransition:
        if state.terminal:
            return _unchanged(state)
        requested = frozenset(
            str(name).strip() for name in tool_names if str(name).strip()
        )
        allowed = RunStateMachine.allowed_tool_names_for_current_transition(state)
        if not requested or not requested.issubset(allowed):
            raise ContractViolationError(
                "tool calls are outside the current plan transition"
            )

        steps = state.steps
        changes: list[tuple[int, TaskStep]] = []
        running_model = next(
            (
                index
                for index, step in enumerate(steps)
                if step.status is StepStatus.RUNNING and _is_model_step(step)
            ),
            -1,
        )
        if running_model >= 0:
            completed = replace(
                steps[running_model],
                status=StepStatus.DONE,
                result_summary=(
                    steps[running_model].result_summary
                    or "Model reasoning completed before tool execution."
                ),
            )
            changes.append((running_model, completed))
            steps = _replace_at(steps, running_model, completed)

        tool_index = next(
            (
                index
                for index, step in enumerate(steps)
                if step.status is StepStatus.RUNNING and _is_tool_step(step)
            ),
            -1,
        )
        if tool_index < 0:
            tool_index = _next_incomplete_index(steps)
        if tool_index < 0 or not _is_tool_step(steps[tool_index]):
            raise ContractViolationError("current plan transition is not a tool step")
        if steps[tool_index].status is not StepStatus.RUNNING:
            changes.append((
                tool_index,
                replace(steps[tool_index], status=StepStatus.RUNNING),
            ))
        return _with_step_changes(state, tuple(changes))

    @staticmethod
    def on_tool_round_completed(state: RunSnapshot) -> RunTransition:
        if state.terminal:
            return _unchanged(state)
        tool_index = next(
            (
                index
                for index, step in enumerate(state.steps)
                if step.status is StepStatus.RUNNING and _is_tool_step(step)
            ),
            -1,
        )
        if tool_index < 0:
            return _unchanged(state)

        completed = replace(
            state.steps[tool_index],
            status=StepStatus.DONE,
            result_summary=(
                state.steps[tool_index].result_summary
                or "Planned tool step completed."
            ),
        )
        steps = _replace_at(state.steps, tool_index, completed)
        changes: list[tuple[int, TaskStep]] = [(tool_index, completed)]
        next_index = _next_incomplete_index(steps)
        if next_index >= 0 and steps[next_index].type is not StepType.CONFIRM:
            running = replace(steps[next_index], status=StepStatus.RUNNING)
            changes.append((next_index, running))
        return _with_step_changes(state, tuple(changes))

    @staticmethod
    def complete(state: RunSnapshot, final_response: str = "") -> RunTransition:
        if state.terminal:
            return _unchanged(state)
        has_incomplete_non_model = any(
            step.status in {StepStatus.PENDING, StepStatus.RUNNING}
            and not _is_model_step(step)
            for step in state.steps
        )
        changes: list[tuple[int, TaskStep]] = []
        for index, step in enumerate(state.steps):
            if step.status not in {StepStatus.PENDING, StepStatus.RUNNING}:
                continue
            if _is_model_step(step) and (
                step.status is StepStatus.RUNNING or not has_incomplete_non_model
            ):
                updated = replace(
                    step,
                    status=StepStatus.DONE,
                    result_summary=(
                        step.result_summary
                        or "Final response covered this model step."
                    ),
                )
            else:
                updated = replace(
                    step,
                    status=StepStatus.BLOCKED,
                    result_summary=(
                        step.result_summary
                        or "This planned step was not executed in the run."
                    ),
                )
            changes.append((index, updated))
        return _with_step_changes(
            state,
            tuple(changes),
            status=(RunStatus.BLOCKED if has_incomplete_non_model else RunStatus.DONE),
            final_response=final_response,
        )

    @staticmethod
    def fail(state: RunSnapshot, error: str) -> RunTransition:
        if state.terminal:
            return _unchanged(state)
        normalized_error = _optional_text(error)
        if normalized_error is None:
            raise ContractViolationError("failed run requires a non-empty error")
        changes = tuple(
            (
                index,
                replace(step, status=StepStatus.FAILED, error=normalized_error),
            )
            for index, step in enumerate(state.steps)
            if step.status is StepStatus.RUNNING
        )
        return _with_step_changes(
            state,
            changes,
            status=RunStatus.FAILED,
            error=normalized_error,
        )

    @staticmethod
    def cancel(
        state: RunSnapshot,
        reason: str = "request_canceled",
    ) -> RunTransition:
        if state.terminal:
            return _unchanged(state)
        normalized_reason = _optional_text(reason) or "request_canceled"
        changes = tuple(
            (
                index,
                replace(
                    step,
                    status=StepStatus.BLOCKED,
                    result_summary=(
                        step.result_summary
                        or "Run was canceled before this step completed."
                    ),
                ),
            )
            for index, step in enumerate(state.steps)
            if step.status in {StepStatus.PENDING, StepStatus.RUNNING}
        )
        return _with_step_changes(
            state,
            changes,
            status=RunStatus.CANCELED,
            error=normalized_reason,
        )

    @staticmethod
    def allowed_tool_names(state: RunSnapshot) -> frozenset[str]:
        return frozenset(
            tool
            for step in state.steps
            if _is_tool_step(step)
            for tool in step.suggested_tools
        )

    @staticmethod
    def allowed_tool_names_for_current_transition(
        state: RunSnapshot,
    ) -> frozenset[str]:
        if state.terminal:
            return frozenset()
        running_index = next(
            (
                index
                for index, step in enumerate(state.steps)
                if step.status is StepStatus.RUNNING
            ),
            -1,
        )
        if running_index < 0:
            return frozenset()
        running = state.steps[running_index]
        if _is_tool_step(running):
            return frozenset(running.suggested_tools)
        if not _is_model_step(running):
            return frozenset()
        for step in state.steps[running_index + 1:]:
            if step.status in _FINISHED_STEP_STATUSES:
                continue
            return (
                frozenset(step.suggested_tools)
                if _is_tool_step(step)
                else frozenset()
            )
        return frozenset()


def _initial_step(step: TaskStep) -> TaskStep:
    return replace(
        step,
        status=(
            StepStatus.DONE
            if step.status is StepStatus.DONE
            else StepStatus.PENDING
        ),
        result_summary=(step.result_summary if step.status is StepStatus.DONE else None),
        error=None,
    )


def _validate_step_execution_contract(step: TaskStep) -> None:
    if step.type is StepType.READ and step.executor is not StepExecutor.TOOL:
        raise ContractViolationError("read step must use the tool executor")
    if step.executor is StepExecutor.TOOL and not step.suggested_tools:
        raise ContractViolationError("tool step requires an explicit tool allowlist")


def _is_tool_step(step: TaskStep) -> bool:
    return step.executor is StepExecutor.TOOL or step.type is StepType.READ


def _is_model_step(step: TaskStep) -> bool:
    return (
        step.executor is StepExecutor.MODEL
        and step.type not in {StepType.READ, StepType.CONFIRM}
    )


def _next_incomplete_index(steps: tuple[TaskStep, ...]) -> int:
    return next(
        (
            index
            for index, step in enumerate(steps)
            if step.status not in _FINISHED_STEP_STATUSES
        ),
        -1,
    )


def _with_step_changes(
    state: RunSnapshot,
    changes: tuple[tuple[int, TaskStep], ...],
    *,
    status: RunStatus | None = None,
    final_response: str | None = None,
    error: str | None = None,
) -> RunTransition:
    steps = state.steps
    updates: list[TaskStepUpdate] = []
    for index, step in changes:
        steps = _replace_at(steps, index, step)
        updates.append(TaskStepUpdate(
            step_id=step.id,
            status=step.status,
            result_summary=step.result_summary,
            error=step.error,
        ))
    after = replace(
        state,
        steps=steps,
        status=(state.status if status is None else status),
        final_response=(
            state.final_response if final_response is None else final_response
        ),
        error=(state.error if error is None else error),
    )
    return RunTransition(before=state, after=after, step_updates=tuple(updates))


def _replace_at(
    steps: tuple[TaskStep, ...],
    index: int,
    step: TaskStep,
) -> tuple[TaskStep, ...]:
    return steps[:index] + (step,) + steps[index + 1:]


def _unchanged(state: RunSnapshot) -> RunTransition:
    return RunTransition(before=state, after=state)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
