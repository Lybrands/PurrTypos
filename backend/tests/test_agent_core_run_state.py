from __future__ import annotations

import pytest

from agent_core.contracts import (
    RunStatus,
    StepExecutor,
    StepStatus,
    StepType,
    TaskPlan,
    TaskStep,
    TaskStepUpdate,
    ToolBatchOutcome,
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


def _agent_step(
    step_id: str,
    *,
    role: str,
    scene_ids: tuple[str, ...],
    depends_on: tuple[str, ...] = (),
) -> TaskStep:
    return TaskStep(
        id=step_id,
        title=step_id,
        type=StepType.WRITE if role == "screenplay_writer" else StepType.REVIEW,
        executor=StepExecutor.AGENT,
        agent_role=role,
        assignment={"sceneIds": scene_ids},
        depends_on=depends_on,
    )


def test_durable_agent_dag_projects_parallel_progress_onto_visible_plan():
    plan = TaskPlan(
        title="Planner-authored screenplay graph",
        steps=(
            _agent_step(
                "write-5",
                role="screenplay_writer",
                scene_ids=("s05-01",),
            ),
            _agent_step(
                "write-6",
                role="screenplay_writer",
                scene_ids=("s06-01",),
            ),
            _agent_step(
                "review-5",
                role="screenplay_reviewer",
                scene_ids=("s05-01",),
                depends_on=("write-5",),
            ),
            _agent_step(
                "review-6",
                role="screenplay_reviewer",
                scene_ids=("s06-01",),
                depends_on=("write-6",),
            ),
            TaskStep(
                id="submit",
                title="submit",
                type=StepType.WRITE,
                executor=StepExecutor.TOOL,
                suggested_tools=("proposeSceneDraft",),
                depends_on=("review-5", "review-6"),
            ),
        ),
    )

    state = RunStateMachine.initialize("run-agent-dag", plan)
    assert [step.status for step in state.steps] == [
        StepStatus.RUNNING,
        StepStatus.RUNNING,
        StepStatus.PENDING,
        StepStatus.PENDING,
        StepStatus.PENDING,
    ]

    reviewed = RunStateMachine.sync_durable_execution(
        state,
        {
            "write-5": StepStatus.DONE,
            "write-6": StepStatus.DONE,
            "review-5": StepStatus.RUNNING,
            "review-6": StepStatus.RUNNING,
            "submit": StepStatus.PENDING,
        },
    ).after
    assert [step.status for step in reviewed.steps] == [
        StepStatus.DONE,
        StepStatus.DONE,
        StepStatus.RUNNING,
        StepStatus.RUNNING,
        StepStatus.PENDING,
    ]

    completed = RunStateMachine.complete_durable_execution(
        reviewed,
        "proposal ready",
        covered_step_ids=tuple(step.id for step in plan.steps),
    ).after
    assert completed.status is RunStatus.DONE
    assert completed.final_response == "proposal ready"
    assert all(step.status is StepStatus.DONE for step in completed.steps)


def test_durable_progress_cannot_start_agent_before_planner_dependencies():
    state = RunStateMachine.initialize(
        "run-invalid-agent-dag",
        TaskPlan(
            title="dependency guard",
            steps=(
                _agent_step(
                    "write",
                    role="screenplay_writer",
                    scene_ids=("s05-01",),
                ),
                _agent_step(
                    "review",
                    role="screenplay_reviewer",
                    scene_ids=("s05-01",),
                    depends_on=("write",),
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match="dependencies"):
        RunStateMachine.sync_durable_execution(
            state,
            {"review": StepStatus.RUNNING},
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


def test_state_machine_reports_only_unfinished_tools_after_current_transition():
    state = RunStateMachine.initialize(
        "run-future",
        TaskPlan(
            title="ordered future tools",
            steps=(
                _step("think", executor=StepExecutor.MODEL, step_type=StepType.ANALYZE),
                _step(
                    "read-a",
                    executor=StepExecutor.TOOL,
                    step_type=StepType.READ,
                    tools=("readA",),
                ),
                _step(
                    "analyze",
                    executor=StepExecutor.MODEL,
                    step_type=StepType.ANALYZE,
                ),
                _step(
                    "read-b",
                    executor=StepExecutor.TOOL,
                    step_type=StepType.READ,
                    tools=("readB",),
                ),
                _step("answer", executor=StepExecutor.MODEL, step_type=StepType.REVIEW),
            ),
        ),
    )

    assert RunStateMachine.allowed_tool_names_for_current_transition(state) == {
        "readA"
    }
    assert RunStateMachine.future_allowed_tool_names(state) == {"readB"}

    read_a_started = RunStateMachine.on_tool_calls_started(state, {"readA"}).after
    assert RunStateMachine.future_allowed_tool_names(read_a_started) == {"readB"}

    read_a_done = RunStateMachine.on_tool_round_completed(read_a_started).after
    assert RunStateMachine.allowed_tool_names_for_current_transition(read_a_done) == {
        "readB"
    }
    assert RunStateMachine.future_allowed_tool_names(read_a_done) == frozenset()


def test_partial_tool_progress_keeps_same_planner_step_authorized():
    state = RunStateMachine.initialize(
        "run-partial",
        TaskPlan(
            title="bounded append",
            steps=(
                _step(
                    "append",
                    executor=StepExecutor.TOOL,
                    step_type=StepType.WRITE,
                    tools=("appendBatch",),
                ),
                _step(
                    "finalize",
                    executor=StepExecutor.TOOL,
                    step_type=StepType.WRITE,
                    tools=("finalizeArtifact",),
                ),
            ),
        ),
    )

    progressed = RunStateMachine.on_tool_round_completed(
        state,
        ToolBatchOutcome.PROGRESSED,
    )

    assert progressed.changed is False
    assert progressed.step_updates == ()
    assert [step.status for step in progressed.after.steps] == [
        StepStatus.RUNNING,
        StepStatus.PENDING,
    ]
    assert RunStateMachine.allowed_tool_names_for_current_transition(
        progressed.after
    ) == {"appendBatch"}

    completed = RunStateMachine.on_tool_round_completed(
        progressed.after,
        ToolBatchOutcome.COMPLETED,
    )
    assert [step.status for step in completed.after.steps] == [
        StepStatus.DONE,
        StepStatus.RUNNING,
    ]
    assert RunStateMachine.allowed_tool_names_for_current_transition(
        completed.after
    ) == {"finalizeArtifact"}


def test_declined_tool_round_blocks_step_and_final_model_can_finish_run():
    state = RunStateMachine.initialize(
        "run-declined",
        TaskPlan(
            title="decline safely",
            steps=(
                _step(
                    "apply",
                    executor=StepExecutor.TOOL,
                    step_type=StepType.WRITE,
                    tools=("applyChange",),
                ),
                _step(
                    "report",
                    executor=StepExecutor.MODEL,
                    step_type=StepType.REVIEW,
                ),
            ),
        ),
    )

    declined = RunStateMachine.on_tool_round_completed(
        state,
        ToolBatchOutcome.DECLINED,
    )

    assert [step.status for step in declined.after.steps] == [
        StepStatus.BLOCKED,
        StepStatus.PENDING,
    ]
    declined_step = declined.after.steps[0]
    assert declined_step.result_summary == (
        "User declined approval; the planned tool was not executed."
    )
    assert declined_step.error == "approval_rejected"
    assert declined.step_updates == (TaskStepUpdate(
        step_id="apply",
        status=StepStatus.BLOCKED,
        result_summary=(
            "User declined approval; the planned tool was not executed."
        ),
        error="approval_rejected",
    ),)
    assert "Planned tool step completed." not in str(declined.after.steps)
    assert RunStateMachine.allowed_tool_names_for_current_transition(
        declined.after
    ) == frozenset()

    reporting = RunStateMachine.on_model_delta(declined.after)
    assert [step.status for step in reporting.after.steps] == [
        StepStatus.BLOCKED,
        StepStatus.RUNNING,
    ]
    completed = RunStateMachine.complete(reporting.after, "not applied")
    assert completed.after.status is RunStatus.DONE
    assert completed.after.final_response == "not applied"
    assert [step.status for step in completed.after.steps] == [
        StepStatus.BLOCKED,
        StepStatus.DONE,
    ]
    assert completed.after.steps[0].error == "approval_rejected"


def test_declined_tool_round_does_not_activate_a_later_tool_grant():
    state = RunStateMachine.initialize(
        "run-declined-before-tool",
        TaskPlan(
            title="do not skip grants",
            steps=(
                _step(
                    "first-write",
                    executor=StepExecutor.TOOL,
                    step_type=StepType.WRITE,
                    tools=("firstWrite",),
                ),
                _step(
                    "later-write",
                    executor=StepExecutor.TOOL,
                    step_type=StepType.WRITE,
                    tools=("laterWrite",),
                ),
            ),
        ),
    )

    declined = RunStateMachine.on_tool_round_completed(
        state,
        ToolBatchOutcome.DECLINED,
    ).after

    assert [step.status for step in declined.steps] == [
        StepStatus.BLOCKED,
        StepStatus.PENDING,
    ]
    assert RunStateMachine.allowed_tool_names_for_current_transition(
        declined
    ) == frozenset()
    assert RunStateMachine.future_allowed_tool_names(declined) == frozenset()
    with pytest.raises(ContractViolationError, match="outside"):
        RunStateMachine.on_tool_calls_started(declined, {"laterWrite"})

    completed = RunStateMachine.complete(declined, "first write declined")
    assert completed.after.status is RunStatus.BLOCKED
    assert completed.after.steps[0].error == "approval_rejected"


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

    with pytest.raises(ContractViolationError, match="exactly one"):
        RunStateMachine.initialize("run-invalid", plan)


@pytest.mark.parametrize(
    "step, message",
    [
        (
            _step(
                "combined",
                executor=StepExecutor.TOOL,
                step_type=StepType.WRITE,
                tools=("update", "delete"),
            ),
            "exactly one",
        ),
        (
            _step(
                "model-grant",
                executor=StepExecutor.MODEL,
                step_type=StepType.REVIEW,
                tools=("delete",),
            ),
            "model step",
        ),
        (
            _step(
                "confirm",
                executor=StepExecutor.MODEL,
                step_type=StepType.CONFIRM,
            ),
            "approval policy",
        ),
    ],
)
def test_state_machine_rejects_non_atomic_or_planner_owned_authority(step, message):
    with pytest.raises(ContractViolationError, match=message):
        RunStateMachine.initialize(
            "run-invalid-authority",
            TaskPlan(title="invalid", steps=(step,)),
        )
