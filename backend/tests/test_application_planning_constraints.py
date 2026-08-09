from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    ModelRequest,
    PlanningCapabilities,
    PlanningConstraints,
    TaskSpec,
)
from application.planning_constraints import RequiredToolPlanningPolicy


class _Policy:
    def should_plan(self, request, capabilities):
        del request, capabilities
        return True

    def planning_constraints(self, request, capabilities):
        del request, capabilities
        return PlanningConstraints(
            required_any_tool_names=frozenset({"existing"}),
        )

    def planning_constraints_for_task(
        self,
        request,
        capabilities,
        task_spec,
    ):
        del request, task_spec
        return capabilities.constraints


def test_required_tool_policy_narrows_request_and_task_constraints():
    request = AgentRunRequest(
        messages=(AgentMessage(role="user", content="执行"),),
        model=ModelRequest(provider="fixture", model="model"),
        domain_context=DomainContext(namespace="test"),
    )
    policy = RequiredToolPlanningPolicy(_Policy(), {"required"})
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"existing", "required"}),
    )

    request_constraints = policy.planning_constraints(
        request,
        capabilities,
    )
    task_constraints = policy.planning_constraints_for_task(
        request,
        PlanningCapabilities(
            available_tool_names=capabilities.available_tool_names,
            constraints=request_constraints,
        ),
        TaskSpec(goal="执行"),
    )

    assert request_constraints.required_any_tool_names == frozenset({
        "existing",
        "required",
    })
    assert task_constraints.required_any_tool_names == frozenset({
        "existing",
        "required",
    })
    assert task_constraints.allow_model_only_fallback is False
