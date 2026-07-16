from __future__ import annotations

import pytest

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    ApprovalDecision,
    ApprovalStatus,
    ContextBudget,
    DomainContext,
    ExecutionState,
    ModelRequest,
    PlanningConstraints,
    ResponseConstraints,
    StepExecutor,
    StepStatus,
    StepType,
    TaskStep,
    TaskStepUpdate,
    ToolExecutionMode,
    ToolPolicy,
    ToolRiskLevel,
)
from agent_core.events import AgentCommand, AgentEvent, CoreCommandType, CoreEventType


def test_run_request_snapshots_opaque_context_and_finds_latest_user_text():
    source = {"scope": "alpha", "nested": [{"value": "original"}]}
    request = AgentRunRequest(
        messages=(
            AgentMessage(role="user", content="first"),
            AgentMessage(role="assistant", content="answer"),
            AgentMessage(role="user", content="latest"),
        ),
        model=ModelRequest(provider="OpenAI", model="test-model"),
        domain_context=DomainContext(namespace="test", payload=source),
        tools_enabled=True,
    )
    source["scope"] = "mutated"
    source["nested"][0]["value"] = "mutated"

    assert request.latest_user_text() == "latest"
    assert request.domain_context.payload["scope"] == "alpha"
    assert request.domain_context.payload["nested"][0]["value"] == "original"
    assert request.model.provider == "openai"
    assert request.tools_enabled is True
    with pytest.raises(TypeError):
        request.domain_context.payload["scope"] = "forbidden"  # type: ignore[index]


def test_execution_state_is_run_scoped_but_has_no_core_authorization_field():
    state = ExecutionState(domain={"current": "one"})
    same_state = state
    same_state.domain["current"] = "two"

    assert state.domain["current"] == "two"
    assert not hasattr(state, "allowed_tool_names")


def test_response_constraints_accept_only_a_bounded_exact_integer():
    assert ResponseConstraints().exact_top_level_item_count is None
    assert ResponseConstraints(
        exact_top_level_item_count=2,
    ).exact_top_level_item_count == 2

    with pytest.raises(TypeError, match="integer"):
        ResponseConstraints(exact_top_level_item_count=True)
    with pytest.raises(ValueError, match="between 1 and 100"):
        ResponseConstraints(exact_top_level_item_count=0)


def test_planning_constraints_normalize_dependency_edges_and_reject_bad_pairs():
    constraints = PlanningConstraints(
        context_satisfied_tool_names=frozenset({" cached ", ""}),
        planning_excluded_tool_names=frozenset({" unrelated ", ""}),
        satisfied_tool_dependency_edges=frozenset({
            (" consumer ", " dependency "),
        }),
    )

    assert constraints.context_satisfied_tool_names == frozenset({"cached"})
    assert constraints.planning_excluded_tool_names == frozenset({
        "unrelated",
    })
    assert constraints.satisfied_tool_dependency_edges == frozenset({
        ("consumer", "dependency"),
    })
    with pytest.raises(TypeError, match="pairs"):
        PlanningConstraints(
            satisfied_tool_dependency_edges=frozenset({("only-one",)}),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="non-empty"):
        PlanningConstraints(
            satisfied_tool_dependency_edges=frozenset({("consumer", " ")}),
        )


def test_policy_and_approval_contracts_preserve_security_semantics():
    policy = ToolPolicy(
        mode=ToolExecutionMode.CONFIRM,
        title="Delete item",
        risk_level=ToolRiskLevel.DESTRUCTIVE,
    )

    assert policy.requires_user_approval
    assert ApprovalStatus.REJECTED != ApprovalStatus.APPROVED


def test_event_envelope_accepts_core_and_domain_events_without_mutable_payloads():
    core_event = AgentEvent(type=CoreEventType.RUN_STARTED, run_id="run-1", payload={"status": "running"})
    domain_event = AgentEvent(type="writing.proposed_diff", run_id="run-1", payload={"id": "d1"})

    assert core_event.type == "run.started"
    assert domain_event.type == "writing.proposed_diff"
    with pytest.raises(TypeError):
        core_event.payload["status"] = "done"  # type: ignore[index]


def test_context_budget_preserves_runtime_and_domain_partitions():
    budget = ContextBudget(
        window_tokens=200_000,
        output_reserve_tokens=8_000,
        safety_reserve_tokens=10_000,
        runtime_reserve_tokens=20_000,
        tool_schema_tokens=2_000,
        provider_input_tokens=160_000,
        context_allocations={"primary": 8_000, "secondary": 40_000},
        minimum_message_tokens=2_000,
    )

    assert budget.round_input_tokens == 180_000
    assert budget.allocation_for("primary") == 8_000
    assert budget.allocation_for("secondary") == 40_000
    assert budget.context_pool_tokens == 158_000


def test_control_commands_are_run_bound_and_validate_approval_decisions():
    command = AgentCommand(
        type=CoreCommandType.APPROVAL_RESOLVE,
        run_id="run-1",
        payload={"approval_id": "approval-1", "decision": ApprovalDecision.APPROVE},
    )
    assert command.payload["decision"] == "approve"

    with pytest.raises(ValueError, match="run id"):
        AgentCommand(type=CoreCommandType.RUN_CANCEL, run_id=None)
    with pytest.raises(ValueError, match="approval_id"):
        AgentCommand(
            type=CoreCommandType.APPROVAL_RESOLVE,
            run_id="run-1",
            payload={"decision": "approve"},
        )
    with pytest.raises(ValueError, match="decision"):
        AgentCommand(
            type=CoreCommandType.APPROVAL_RESOLVE,
            run_id="run-1",
            payload={"approval_id": "approval-1", "decision": "maybe"},
        )


def test_task_step_contract_normalizes_enum_strings_and_rejects_invalid_values():
    step = TaskStep(
        id=" read-context ",
        title=" Read context ",
        type="read",  # type: ignore[arg-type]
        executor="tool",  # type: ignore[arg-type]
        status="running",  # type: ignore[arg-type]
        risk_level="write",  # type: ignore[arg-type]
        suggested_tools=(" getChapterContent ", ""),
    )
    update = TaskStepUpdate(
        step_id=" read-context ",
        status="done",  # type: ignore[arg-type]
    )

    assert step.id == "read-context"
    assert step.title == "Read context"
    assert step.type is StepType.READ
    assert step.executor is StepExecutor.TOOL
    assert step.status is StepStatus.RUNNING
    assert step.risk_level is ToolRiskLevel.WRITE
    assert step.suggested_tools == ("getChapterContent",)
    assert update.step_id == "read-context"
    assert update.status is StepStatus.DONE

    with pytest.raises(ValueError):
        TaskStep(
            id="invalid",
            title="Invalid",
            type="unknown",  # type: ignore[arg-type]
            executor=StepExecutor.MODEL,
        )
    with pytest.raises(ValueError, match="id"):
        TaskStepUpdate(step_id="  ", status=StepStatus.DONE)
