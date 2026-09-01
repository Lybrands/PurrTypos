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
    PlanningResult,
    StepExecutor,
    StepType,
    TaskSpec,
    WorkPlan,
    WorkStep,
)
from purra.planner import build_planner_messages
from domains.agent_output_policy import build_agent_public_progress_policy
from domains.screenplay_agent.adapter import (
    ScreenplayDomainAdapter,
    ScreenplayHostContextProvider,
    ScreenplayToolLoopPolicy,
    validate_screenplay_planning_result,
)
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from domains.screenplay_agent.contracts import ScreenplayStageCommand


def test_screenplay_runtime_allows_long_model_generation_without_removing_bound():
    limits = ScreenplayDomainAdapter(tool_catalog=object()).runtime_limits

    assert limits.provider_invocation_timeout_ms == 300_000
    assert limits.root_run_timeout_ms == 900_000


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


def test_screenplay_planning_policy_only_constrains_explicit_planned_runs():
    policy = ScreenplayToolLoopPolicy()
    capabilities = PlanningCapabilities()
    root = ScreenplayAgentDomainContext(
        project_id="project-1",
        turn_id="turn-1",
    )
    assert not hasattr(policy, "should_plan")
    assert policy.planning_constraints(
        _screenplay_request(root, "分析原作范围"), capabilities
    ).allow_model_only_fallback is False


def test_screenplay_planning_result_rejects_invalid_domain_task_spec_for_repair():
    request = _screenplay_request(
        ScreenplayAgentDomainContext(
            project_id="project-1",
            turn_id="turn-1",
        ),
        "说明核心冲突",
    )
    step = WorkStep(
        id="answer",
        title="回答",
        type=StepType.REVIEW,
        executor=StepExecutor.MODEL,
    )

    reason = validate_screenplay_planning_result(
        request,
        PlanningResult(
            kind="planned",
            work_plan=WorkPlan(
                title="回答",
                steps=(step,),
                task_spec=TaskSpec(
                    goal="回答核心冲突",
                    operation="answer",
                    deliverable="creativeBrief",
                    target={"screenplay": {
                        "version": 1,
                        "scope": {"kind": "current_stage"},
                        "stepBindings": [{
                            "stepId": "answer",
                            "phase": "evidence",
                        }],
                    }},
                ),
            ),
        ),
    )

    assert reason is not None
    assert reason.startswith("answer TaskSpec cannot declare a deliverable")
    assert '"scope":{"kind":"current_stage"}' in reason
    assert '"stepBindings":[{"stepId":"answer","phase":"evidence"}]' in reason
    assert "deliverable must be omitted or empty" in reason


def test_screenplay_repair_guidance_replaces_invalid_stage_phases():
    command = ScreenplayStageCommand.from_mapping({
        "kind": "stage_action",
        "action": "create",
        "targetRole": "creativeBrief",
        "scope": {"kind": "current_stage"},
    })
    request = _screenplay_request(
        ScreenplayAgentDomainContext(
            project_id="project-1",
            turn_id="turn-1",
            stage_command=command,
        ),
        "创建创意简报",
    )
    steps = (
        WorkStep(
            id="evidence",
            title="梳理证据",
            type=StepType.ANALYZE,
            executor=StepExecutor.MODEL,
        ),
        WorkStep(
            id="draft",
            title="创作",
            type=StepType.WRITE,
            executor=StepExecutor.MODEL,
            depends_on=("evidence",),
        ),
        WorkStep(
            id="deliver",
            title="交付",
            type=StepType.REVIEW,
            executor=StepExecutor.MODEL,
            depends_on=("draft",),
        ),
    )
    reason = validate_screenplay_planning_result(
        request,
        PlanningResult(
            kind="planned",
            work_plan=WorkPlan(
                title="创建",
                steps=steps,
                task_spec=TaskSpec(
                    goal="创建创意简报",
                    operation="create",
                    deliverable="creativeBrief",
                    target={"screenplay": {
                        "version": 1,
                        "scope": {"kind": "current_stage"},
                        "stepBindings": [
                            {"stepId": "evidence", "phase": "evidence"},
                            {"stepId": "draft", "phase": "review"},
                            {"stepId": "deliver", "phase": "delivery"},
                        ],
                    }},
                ),
            ),
        ),
    )

    assert reason is not None
    assert reason.startswith("creativeBrief plan phases must be")
    assert '"stepBindings":[{"stepId":"evidence","phase":"evidence"},' in reason
    assert '{"stepId":"draft","phase":"creation"},' in reason
    assert '{"stepId":"deliver","phase":"delivery"}]' in reason
    assert "stageCommand: create, creativeBrief" in reason


def test_screenplay_repair_guidance_adds_missing_formal_phase_todo():
    command = ScreenplayStageCommand.from_mapping({
        "kind": "stage_action",
        "action": "create",
        "targetRole": "creativeBrief",
        "scope": {"kind": "current_stage"},
    })
    request = _screenplay_request(
        ScreenplayAgentDomainContext(
            project_id="project-1",
            turn_id="turn-1",
            stage_command=command,
        ),
        "创建创意简报",
    )
    steps = (
        WorkStep(
            id="s1",
            title="创作",
            type=StepType.WRITE,
            executor=StepExecutor.MODEL,
        ),
        WorkStep(
            id="s2",
            title="交付",
            type=StepType.REVIEW,
            executor=StepExecutor.MODEL,
            depends_on=("s1",),
        ),
    )

    reason = validate_screenplay_planning_result(
        request,
        PlanningResult(
            kind="planned",
            work_plan=WorkPlan(
                title="创建",
                steps=steps,
                task_spec=TaskSpec(
                    goal="创建创意简报",
                    operation="create",
                    deliverable="creativeBrief",
                    target={"screenplay": {
                        "version": 1,
                        "scope": {"kind": "current_stage"},
                        "stepBindings": [
                            {"stepId": "s1", "phase": "creation"},
                            {"stepId": "s2", "phase": "delivery"},
                        ],
                    }},
                ),
            ),
        ),
    )

    assert reason is not None
    assert (
        '"stepBindings":[{"stepId":"s1","phase":"evidence"},'
        '{"stepId":"stage-work","phase":"creation"},'
        '{"stepId":"s2","phase":"delivery"}]'
    ) in reason
    assert (
        'Replace the todos too; their ids in order must equal exactly '
        '["s1","stage-work","s2"]'
    ) in reason

    repaired_steps = (
        WorkStep(
            id="s1",
            title="梳理证据",
            type=StepType.ANALYZE,
            executor=StepExecutor.MODEL,
        ),
        WorkStep(
            id="stage-work",
            title="创作",
            type=StepType.WRITE,
            executor=StepExecutor.MODEL,
            depends_on=("s1",),
        ),
        WorkStep(
            id="s2",
            title="交付",
            type=StepType.REVIEW,
            executor=StepExecutor.MODEL,
            depends_on=("stage-work",),
        ),
    )
    repaired = PlanningResult(
        kind="planned",
        work_plan=WorkPlan(
            title="创建",
            steps=repaired_steps,
            task_spec=TaskSpec(
                goal="创建创意简报",
                operation="create",
                deliverable="creativeBrief",
                target={"screenplay": {
                    "version": 1,
                    "scope": {"kind": "current_stage"},
                    "stepBindings": [
                        {"stepId": "s1", "phase": "evidence"},
                        {"stepId": "stage-work", "phase": "creation"},
                        {"stepId": "s2", "phase": "delivery"},
                    ],
                }},
            ),
        ),
    )
    assert validate_screenplay_planning_result(request, repaired) is None


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
        PlanningCapabilities(planning_context_blocks=bundle.blocks),
        PlannerLimits(),
    )
    policy = facts["planningRules"][0]
    planner_payload = json.loads(str(messages[1].content))
    progress_content = planner_payload["planningContext"][0]["content"]
    planning_content = planner_payload["planningContext"][1]["content"]
    planner_facts = json.loads(planning_content)

    assert len(bundle.blocks) == 2
    assert [block.name for block in bundle.blocks] == [
        "screenplay_public_progress",
        "screenplay_planning_facts",
    ]
    assert all(block.untrusted is False for block in bundle.blocks)
    assert "面向用户的状态标题" in progress_content
    assert "不得包含 Run、Task、Turn" in progress_content
    assert "chain-of-thought" in progress_content
    assert progress_content == build_agent_public_progress_policy()
    assert facts["project"]["id"] == "project-1"
    assert facts["stageCommand"] == stage_command
    assert facts["planningRules"] == [policy]
    assert planner_facts["planningRules"] == [policy]
    assert "needsTodos:true" in policy
    assert "最少且不重复" in policy
    assert "1 至 8" not in policy
    assert "stepBindings" in policy
    assert "Revision" in policy
    assert '"targetRole":"sourceAnalysis"' in planning_content
    assert '"targetRole":"review"' not in planning_content

    runtime_bundle = await provider.build_context(
        request,
        ContextBudget(
            window_tokens=128_000,
            output_reserve_tokens=16_000,
            safety_reserve_tokens=4_000,
            runtime_reserve_tokens=4_000,
        ),
    )
    assert len(runtime_bundle.blocks) == 1
    assert runtime_bundle.blocks[0].name == "screenplay_public_progress"
    assert "不是工具日志或思考过程" in runtime_bundle.blocks[0].content
