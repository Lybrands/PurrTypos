from __future__ import annotations

import pytest

from agent_core.contracts import (
    RunStatus,
    StepExecutor,
    StepStatus,
    StepType,
    TaskPlan,
    TaskStep,
)
from agent_core.errors import ContractViolationError
from agent_core.run_state import RunStateMachine


def _step(
    step_id: str,
    *,
    executor: StepExecutor,
    step_type: StepType,
    tools: tuple[str, ...] = (),
) -> TaskStep:
    return TaskStep(
        id=step_id,
        title=step_id,
        type=step_type,
        executor=executor,
        suggested_tools=tools,
    )


def test_state_machine_advances_model_tool_model_and_scopes_current_tools():
    state = RunStateMachine.initialize(
        "run-1",
        TaskPlan(
            title="ordered",
            steps=(
                _step("think", executor=StepExecutor.MODEL, step_type=StepType.ANALYZE),
                _step(
                    "read",
                    executor=StepExecutor.TOOL,
                    step_type=StepType.READ,
                    tools=("readChapter",),
                ),
                _step("answer", executor=StepExecutor.MODEL, step_type=StepType.REVIEW),
            ),
        ),
    )

    assert [step.status for step in state.steps] == [
        StepStatus.RUNNING,
        StepStatus.PENDING,
        StepStatus.PENDING,
    ]
    assert RunStateMachine.allowed_tool_names_for_current_transition(state) == {
        "readChapter"
    }

    tool_started = RunStateMachine.on_tool_calls_started(
        state,
        frozenset({"readChapter"}),
    )
    assert state.steps[0].status is StepStatus.RUNNING
    assert [step.status for step in tool_started.after.steps] == [
        StepStatus.DONE,
        StepStatus.RUNNING,
        StepStatus.PENDING,
    ]

    tool_done = RunStateMachine.on_tool_round_completed(tool_started.after)
    assert [step.status for step in tool_done.after.steps] == [
        StepStatus.DONE,
        StepStatus.DONE,
        StepStatus.RUNNING,
    ]
    assert RunStateMachine.allowed_tool_names_for_current_transition(
        tool_done.after
    ) == frozenset()

    completed = RunStateMachine.complete(tool_done.after, "answer")
    assert completed.after.status is RunStatus.DONE
    assert completed.after.final_response == "answer"
    assert all(step.status is StepStatus.DONE for step in completed.after.steps)


def test_unauthorized_tool_start_fails_closed_without_mutating_snapshot():
    state = RunStateMachine.initialize(
        "run-1",
        TaskPlan(
            title="safe",
            steps=(
                _step("think", executor=StepExecutor.MODEL, step_type=StepType.ANALYZE),
                _step(
                    "read",
                    executor=StepExecutor.TOOL,
                    step_type=StepType.READ,
                    tools=("allowedTool",),
                ),
            ),
        ),
    )

    with pytest.raises(ContractViolationError, match="outside"):
        RunStateMachine.on_tool_calls_started(
            state,
            frozenset({"laterTool"}),
        )

    assert [step.status for step in state.steps] == [
        StepStatus.RUNNING,
        StepStatus.PENDING,
    ]


def test_final_response_completes_consecutive_model_steps():
    state = RunStateMachine.initialize(
        "run-models",
        TaskPlan(
            title="models",
            steps=(
                _step("analyze", executor=StepExecutor.MODEL, step_type=StepType.ANALYZE),
                _step("review", executor=StepExecutor.MODEL, step_type=StepType.REVIEW),
                _step("answer", executor=StepExecutor.MODEL, step_type=StepType.REVIEW),
            ),
        ),
    )

    transition = RunStateMachine.complete(state, "final")

    assert transition.after.status is RunStatus.DONE
    assert [step.status for step in transition.after.steps] == [
        StepStatus.DONE,
        StepStatus.DONE,
        StepStatus.DONE,
    ]


def test_final_response_blocks_unexecuted_tool_and_downstream_model():
    state = RunStateMachine.initialize(
        "run-blocked",
        TaskPlan(
            title="blocked",
            steps=(
                _step("analyze", executor=StepExecutor.MODEL, step_type=StepType.ANALYZE),
                _step(
                    "read",
                    executor=StepExecutor.TOOL,
                    step_type=StepType.READ,
                    tools=("readChapter",),
                ),
                _step("answer", executor=StepExecutor.MODEL, step_type=StepType.REVIEW),
            ),
        ),
    )

    transition = RunStateMachine.complete(state, "premature")

    assert transition.after.status is RunStatus.BLOCKED
    assert [step.status for step in transition.after.steps] == [
        StepStatus.DONE,
        StepStatus.BLOCKED,
        StepStatus.BLOCKED,
    ]


def test_fail_cancel_and_terminal_transitions_preserve_terminal_state():
    plan = TaskPlan(
        title="terminal",
        steps=(
            _step(
                "read",
                executor=StepExecutor.TOOL,
                step_type=StepType.READ,
                tools=("readChapter",),
            ),
            _step("answer", executor=StepExecutor.MODEL, step_type=StepType.REVIEW),
        ),
    )
    failed = RunStateMachine.fail(
        RunStateMachine.initialize("run-failed", plan),
        "broken",
    ).after
    canceled = RunStateMachine.cancel(
        RunStateMachine.initialize("run-canceled", plan),
        "client_disconnected",
    ).after

    assert failed.status is RunStatus.FAILED
    assert [step.status for step in failed.steps] == [
        StepStatus.FAILED,
        StepStatus.PENDING,
    ]
    assert canceled.status is RunStatus.CANCELED
    assert [step.status for step in canceled.steps] == [
        StepStatus.BLOCKED,
        StepStatus.BLOCKED,
    ]
    assert RunStateMachine.complete(failed, "late").after is failed
    assert RunStateMachine.fail(canceled, "late").after is canceled


def test_invalid_tool_step_contract_is_rejected_at_initialization():
    plan = TaskPlan(
        title="invalid",
        steps=(
            _step("read", executor=StepExecutor.TOOL, step_type=StepType.READ),
        ),
    )

    with pytest.raises(ContractViolationError, match="allowlist"):
        RunStateMachine.initialize("run-invalid", plan)
