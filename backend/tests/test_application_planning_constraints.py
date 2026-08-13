import json

import pytest

from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    ContextBudget,
    DomainContext,
    ModelRequest,
    PlannerLimits,
    PlanningCapabilities,
    PlanningConstraints,
    TaskSpec,
)
from purra.planner import build_planner_messages
from application.planning_constraints import RequiredToolPlanningPolicy
from domains.screenplay_agent.adapter import (
    ScreenplayHostContextProvider,
    ScreenplayToolLoopPolicy,
)
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from domains.screenplay_agent.contracts import ScreenplayStageCommand


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


def _screenplay_request(
    context: ScreenplayAgentDomainContext,
    text: str,
) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content=text),),
        model=ModelRequest(provider="fixture", model="model"),
        domain_context=context.to_core_context(),
        mode="agent",
    )


def test_screenplay_planning_policy_plans_only_non_empty_root_turns():
    policy = ScreenplayToolLoopPolicy()
    capabilities = PlanningCapabilities()
    root = ScreenplayAgentDomainContext(
        project_id="project-1",
        turn_id="turn-1",
    )
    child = ScreenplayAgentDomainContext(
        project_id="project-1",
        task_id="task-1",
        unit_id="unit-1",
        target_role="sourceAnalysis",
        expected_part_type="document",
        expected_part_key="main",
    )

    assert policy.should_plan(
        _screenplay_request(root, "分析原作范围"), capabilities
    ) is True
    assert policy.should_plan(
        _screenplay_request(root, " \n"), capabilities
    ) is False
    assert policy.should_plan(
        _screenplay_request(child, "生成素材分析"), capabilities
    ) is False


@pytest.mark.asyncio
async def test_screenplay_planning_context_reaches_planner_once_with_host_command():
    stage_command = {
        "kind": "stage_action",
        "action": "create",
        "targetRole": "sourceAnalysis",
        "scope": {"kind": "current_stage"},
    }

    async def load_context(project_id: str):
        assert project_id == "project-1"
        return {
            "project": {"id": "wrong-project", "stage": "sourceAnalysis"},
            "stageCommand": {
                "kind": "stage_action",
                "action": "review",
                "targetRole": "review",
                "scope": {"kind": "current_stage"},
            },
            "planningRules": ["workspace must not replace host policy"],
        }

    request = _screenplay_request(
        ScreenplayAgentDomainContext(
            project_id="project-1",
            turn_id="turn-1",
            stage_command=ScreenplayStageCommand.from_mapping(stage_command),
        ),
        "开始分析",
    )
    provider = ScreenplayHostContextProvider(
        planning_context_loader=load_context,
    )
    bundle = await provider.build_planning_context(
        request,
        ContextBudget(
            window_tokens=128_000,
            output_reserve_tokens=16_000,
            safety_reserve_tokens=4_000,
            runtime_reserve_tokens=4_000,
        ),
    )
    facts = bundle.diagnostics["hostPlanningFacts"]
    messages = build_planner_messages(
        request,
        PlanningCapabilities(host_planning_facts=facts),
        PlannerLimits(),
    )
    policy = facts["planningRules"][0]
    system_prompt = str(messages[0].content)
    encoded_policy = json.dumps(
        policy,
        ensure_ascii=False,
    )[1:-1]

    assert bundle.blocks == ()
    assert facts["project"]["id"] == "project-1"
    assert facts["stageCommand"] == stage_command
    assert facts["planningRules"] == [policy]
    assert system_prompt.count(encoded_policy) == 1
    assert "needsTodos:true" in policy
    assert "stepBindings" in policy
    assert "Revision" in policy
    assert '"targetRole":"sourceAnalysis"' in system_prompt
    assert '"targetRole":"review"' not in system_prompt
