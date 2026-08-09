from __future__ import annotations

import pytest

from agent_core.contracts import (
    AgentAssignmentCoverage,
    AgentMessage,
    AgentRunRequest,
    ApprovalDecision,
    ApprovalStatus,
    ContextBudget,
    DomainContext,
    ExecutionRecipe,
    ExecutionRecipeStep,
    ExecutionState,
    ModelRequest,
    PlanningConstraints,
    ResponseConstraints,
    RunBinding,
    RunStatus,
    StepExecutor,
    StepStatus,
    StepType,
    TaskStep,
    TaskStepUpdate,
    TaskPlan,
    ToolExecutionMode,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from agent_core.contracts.context import ContextBudget as ContextBudgetFamily
from agent_core.contracts.messages import AgentMessage as AgentMessageFamily
from agent_core.contracts.planning import TaskPlan as TaskPlanFamily
from agent_core.contracts.runs import RunStatus as RunStatusFamily
from agent_core.contracts.tools import ToolSchema as ToolSchemaFamily
from agent_core.ports.persistence import RunCommit as RunCommitFamily
from agent_core.ports.context import ContextProvider as ContextProviderFamily
from agent_core.ports.model import ModelGateway as ModelGatewayFamily
from agent_core.ports.planning import TaskPlanner as TaskPlannerFamily
from agent_core.ports.tools import ToolCatalog as ToolCatalogFamily
from agent_core.events import AgentCommand, AgentEvent, CoreCommandType, CoreEventType
from agent_core.task_admission import ExecutionMode, TaskAdmissionDecision


def test_phase_one_contract_family_exports_keep_legacy_identity():
    assert AgentMessageFamily is AgentMessage
    assert ContextBudgetFamily is ContextBudget
    assert TaskPlanFamily is TaskPlan
    assert ToolSchemaFamily is ToolSchema
    assert RunStatusFamily is RunStatus
    from agent_core.ports import (
        ContextProvider,
        ModelGateway,
        RunCommit,
        TaskPlanner,
        ToolCatalog,
    )

    assert RunCommitFamily is RunCommit
    assert ContextProviderFamily is ContextProvider
    assert ModelGatewayFamily is ModelGateway
    assert TaskPlannerFamily is TaskPlanner
    assert ToolCatalogFamily is ToolCatalog


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
        required_any_tool_names=frozenset({" required ", ""}),
        minimum_root_agent_count=2,
        planning_excluded_executors=frozenset({"model"}),
        allow_model_only_fallback=False,
    )

    assert constraints.context_satisfied_tool_names == frozenset({"cached"})
    assert constraints.planning_excluded_tool_names == frozenset({
        "unrelated",
    })
    assert constraints.satisfied_tool_dependency_edges == frozenset({
        ("consumer", "dependency"),
    })
    assert constraints.required_any_tool_names == frozenset({"required"})
    assert constraints.minimum_root_agent_count == 2
    assert constraints.planning_excluded_executors == frozenset({
        StepExecutor.MODEL,
    })
    assert constraints.allow_model_only_fallback is False
    with pytest.raises(TypeError, match="pairs"):
        PlanningConstraints(
            satisfied_tool_dependency_edges=frozenset({("only-one",)}),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="non-empty"):
        PlanningConstraints(
            satisfied_tool_dependency_edges=frozenset({("consumer", " ")}),
        )
    with pytest.raises(TypeError, match="boolean"):
        PlanningConstraints(allow_model_only_fallback=0)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must be an integer"):
        PlanningConstraints(minimum_root_agent_count=True)
    with pytest.raises(ValueError, match="unsupported executor"):
        PlanningConstraints(
            planning_excluded_executors=frozenset({"unsupported"}),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="non-negative"):
        PlanningConstraints(minimum_root_agent_count=-1)


def test_agent_assignment_coverage_normalizes_opaque_ids():
    coverage = AgentAssignmentCoverage(
        agent_role=" screenplay_writer ",
        assignment_field=" sceneIds ",
        required_values=(" s08-01 ", "s08-02"),
        root_only=True,
    )

    assert coverage.agent_role == "screenplay_writer"
    assert coverage.assignment_field == "sceneIds"
    assert coverage.required_values == ("s08-01", "s08-02")
    assert coverage.to_planning_payload()["coverage"] == (
        "exactly_once_in_order"
    )


def test_durable_admission_requires_unique_explicit_step_coverage():
    decision = TaskAdmissionDecision(
        mode=ExecutionMode.DURABLE,
        reason_code="large_task",
        covered_step_ids=(" write ",),
    )

    assert decision.covered_step_ids == ("write",)
    assert decision.to_event_payload()["coveredStepIds"] == ["write"]

    with pytest.raises(ValueError, match="requires covered step ids"):
        TaskAdmissionDecision(
            mode=ExecutionMode.DURABLE,
            reason_code="large_task",
        )
    with pytest.raises(ValueError, match="only durable"):
        TaskAdmissionDecision(covered_step_ids=("write",))
    with pytest.raises(ValueError, match="unique"):
        TaskAdmissionDecision(
            mode=ExecutionMode.DURABLE,
            reason_code="large_task",
            covered_step_ids=("write", "write"),
        )
    with pytest.raises(ValueError, match="must be a sequence"):
        TaskAdmissionDecision(
            mode=ExecutionMode.DURABLE,
            reason_code="large_task",
            covered_step_ids="write",  # type: ignore[arg-type]
        )


def test_host_run_binding_is_opaque_normalized_and_immutable():
    source = {"targetRole": "creativeBrief", "nested": {"revision": 1}}
    binding = RunBinding(
        namespace=" screenplay.operation ",
        aggregate_id=" project-1 ",
        command_id=" operation-1 ",
        attributes=source,
    )
    source["nested"]["revision"] = 2

    assert binding.namespace == "screenplay.operation"
    assert binding.aggregate_id == "project-1"
    assert binding.command_id == "operation-1"
    assert binding.attributes["nested"]["revision"] == 1
    with pytest.raises(TypeError):
        binding.attributes["targetRole"] = "draft"  # type: ignore[index]
    with pytest.raises(ValueError, match="namespace"):
        RunBinding(namespace=" ", aggregate_id="p", command_id="c")


def test_execution_recipe_validates_topology_and_protects_reserved_metadata():
    recipe = ExecutionRecipe(
        kind=" screenplay.scene_draft ",
        max_parallelism=2,
        metadata={"kind": "cannot-override", "sourceRevision": 4},
        steps=(
            ExecutionRecipeStep(
                id="prepare",
                kind="context",
                metadata={"id": "cannot-override", "sceneIds": ["s1"]},
            ),
            ExecutionRecipeStep(
                id="write",
                kind="model",
                depends_on=("prepare",),
                executor="screenplay_writer",
            ),
        ),
    )

    metadata = recipe.to_metadata()
    assert metadata["kind"] == "screenplay.scene_draft"
    assert metadata["sourceRevision"] == 4
    assert metadata["maxParallelism"] == 2
    assert metadata["steps"][0]["id"] == "prepare"
    assert metadata["steps"][0]["sceneIds"] == ("s1",)
    with pytest.raises(ValueError, match="topologically ordered"):
        ExecutionRecipe(
            kind="bad-order",
            steps=(
                ExecutionRecipeStep(
                    id="write",
                    kind="model",
                    depends_on=("prepare",),
                ),
                ExecutionRecipeStep(id="prepare", kind="context"),
            ),
        )
    with pytest.raises(ValueError, match="unknown dependencies"):
        ExecutionRecipe(
            kind="unknown",
            steps=(ExecutionRecipeStep(
                id="write",
                kind="model",
                depends_on=("missing",),
            ),),
        )


def test_only_durable_admission_can_carry_an_execution_recipe():
    recipe = ExecutionRecipe(
        kind="batch",
        steps=(ExecutionRecipeStep(id="write", kind="model"),),
    )
    decision = TaskAdmissionDecision(
        mode=ExecutionMode.DURABLE,
        reason_code="large_task",
        covered_step_ids=("write",),
        execution_recipe=recipe,
    )

    assert decision.execution_recipe is recipe
    assert decision.to_event_payload()["executionRecipe"] == {
        "kind": "batch",
        "stepCount": 1,
        "maxParallelism": 1,
    }
    with pytest.raises(ValueError, match="only durable"):
        TaskAdmissionDecision(execution_recipe=recipe)


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


def test_agent_task_step_requires_a_role_and_snapshots_assignment():
    assignment = {"sceneIds": ["s01"]}
    step = TaskStep(
        id="write-1",
        title="创作第一集",
        type=StepType.WRITE,
        executor=StepExecutor.AGENT,
        agent_role="screenplay_writer",
        assignment=assignment,
        depends_on=(),
    )
    assignment["sceneIds"].append("s02")

    assert step.agent_role == "screenplay_writer"
    assert step.assignment["sceneIds"] == ("s01",)
    with pytest.raises(ValueError, match="requires agent_role"):
        TaskStep(
            id="missing-role",
            title="Missing",
            type=StepType.WRITE,
            executor=StepExecutor.AGENT,
        )
    with pytest.raises(ValueError, match="only agent task steps"):
        TaskStep(
            id="model-assignment",
            title="Invalid",
            type=StepType.WRITE,
            executor=StepExecutor.MODEL,
            assignment={"sceneIds": ["s01"]},
        )
