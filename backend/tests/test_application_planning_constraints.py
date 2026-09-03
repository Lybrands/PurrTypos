import json

from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    ContextBudget,
    ModelRequest,
    PlannerLimits,
    PlanningCapabilities,
    PlanningResult,
    StepExecutor,
    StepType,
    TaskSpec,
    WorkPlan,
    WorkStep,
)
from purra.planner import build_planner_messages
from domains.agent_policy import (
    build_agent_final_response_policy,
)
from application.shared_agent_context import with_shared_agent_context
from domains.screenplay_agent.adapter import (
    ScreenplayDomainAdapter,
    ScreenplayHostContextProvider,
    ScreenplayToolLoopPolicy,
    validate_screenplay_planning_result,
)
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from domains.screenplay_agent.contracts import ScreenplayStageCommand


def test_screenplay_runtime_uses_resource_limits_without_elapsed_time_deadlines():
    limits = ScreenplayDomainAdapter(tool_catalog=object()).runtime_limits

    assert limits.provider_invocation_timeout_ms is None
    assert limits.root_run_timeout_ms is None
    assert limits.max_model_invocation_attempts == 64
    assert limits.max_model_rounds == 8


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
        ScreenplayAgentDomainContext(project_id="project-1", turn_id="turn-1"),
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
                    }},
                ),
            ),
        ),
    )

    assert reason is not None
    assert reason.startswith("answer TaskSpec cannot declare a deliverable")
    assert '"scope":{"kind":"current_stage"}' in reason
    assert "stepBindings" not in reason
    assert "deliverable must be omitted or empty" in reason

def test_screenplay_validator_preserves_model_authored_stage_plan():
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
        WorkStep(id="draft", title="创作", type=StepType.WRITE, executor=StepExecutor.MODEL),
        WorkStep(
            id="refine",
            title="完善关键冲突",
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
                    }},
                ),
            ),
        ),
    )

    assert reason is None

def test_screenplay_validator_accepts_one_model_authored_step_without_padding():
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
    step = WorkStep(
        id="create-brief",
        title="形成创意简报",
        type=StepType.WRITE,
        executor=StepExecutor.MODEL,
    )
    reason = validate_screenplay_planning_result(
        request,
        PlanningResult(
            kind="planned",
            work_plan=WorkPlan(
                title="创建",
                steps=(step,),
                task_spec=TaskSpec(
                    goal="创建创意简报",
                    operation="create",
                    deliverable="creativeBrief",
                    target={"screenplay": {
                        "version": 1,
                        "scope": {"kind": "current_stage"},
                    }},
                ),
            ),
        ),
    )

    assert reason is None

def test_freeform_formal_plan_repair_explains_the_complete_closed_contract():
    request = _screenplay_request(
        ScreenplayAgentDomainContext(project_id="project-1", turn_id="turn-1"),
        "连续创作 3 集",
    )
    steps = (
        WorkStep(
            id="read-episode-baseline",
            title="核对三集设定",
            type=StepType.READ,
            executor=StepExecutor.TOOL,
            capability_names=("getScreenplayEpisodeContext",),
        ),
        WorkStep(
            id="create-episode-drafts",
            title="连续创作三集",
            type=StepType.WRITE,
            executor=StepExecutor.MODEL,
            depends_on=("read-episode-baseline",),
        ),
    )
    plan = PlanningResult(
        kind="planned",
        work_plan=WorkPlan(
            title="连续创作三集",
            steps=steps,
            task_spec=TaskSpec(
                goal="连续创作三集",
                operation="create",
                deliverable="第 1 至 3 集剧本初稿",
                target={"screenplay": {
                    "version": 1,
                    "scope": "剧集 1 至 3",
                }},
            ),
        ),
    )

    reason = validate_screenplay_planning_result(request, plan)

    assert reason is not None
    assert '"kind":"next_episodes","count":N' in reason
    assert '"kind":"episodes","episodeNumbers":[...]' in reason
    assert "consecutive N-episode creation uses next_episodes with count N" in reason
    assert "screenplayDraft" in reason
    assert "formal operations must use exactly one of these deliverable roles" in reason
    assert "stepBindings" not in reason
    assert "Do not change, add, remove, reorder, or rename" in reason

def test_freeform_formal_plan_accepts_model_authored_steps_without_delivery_phase():
    request = _screenplay_request(
        ScreenplayAgentDomainContext(project_id="project-1", turn_id="turn-1"),
        "连续创作 3 集",
    )
    steps = (
        WorkStep(
            id="evidence",
            title="核对素材",
            type=StepType.READ,
            executor=StepExecutor.TOOL,
            capability_names=("getScreenplayEpisodeContext",),
        ),
        WorkStep(
            id="create",
            title="创作三集",
            type=StepType.WRITE,
            executor=StepExecutor.MODEL,
            depends_on=("evidence",),
        ),
    )
    plan = PlanningResult(
        kind="planned",
        work_plan=WorkPlan(
            title="连续创作三集",
            steps=steps,
            task_spec=TaskSpec(
                goal="连续创作三集",
                operation="create",
                deliverable="screenplayDraft",
                target={"screenplay": {
                    "version": 1,
                    "scope": {"kind": "next_episodes", "count": 3},
                }},
            ),
        ),
    )

    assert validate_screenplay_planning_result(request, plan) is None

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
            "project": {
                "id": "wrong-project",
                "stage": "sourceAnalysis",
                "source": {
                    "type": "book",
                    "bookId": "private-book-id",
                    "bookTitle": "公开书名",
                    "scope": {
                        "mode": "selected",
                        "count": 2,
                        "chapterIds": ["private-chapter-id"],
                    },
                },
            },
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
    provider = with_shared_agent_context(ScreenplayHostContextProvider(
        planning_context_loader=load_context,
    ))
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
    planning_content = planner_payload["planningContext"][0]["content"]
    planner_facts = json.loads(planning_content)

    assert len(bundle.blocks) == 1
    assert [block.name for block in bundle.blocks] == [
        "screenplay_planning_facts",
    ]
    assert all(block.untrusted is False for block in bundle.blocks)
    assert "id" not in facts["project"]
    assert facts["project"]["source"] == {
        "type": "book",
        "bookTitle": "公开书名",
        "scope": {"mode": "selected", "count": 2},
    }
    assert facts["stageCommand"] == stage_command
    assert facts["planningRules"] == [policy]
    assert planner_facts["planningRules"] == [policy]
    assert "project-1" not in planning_content
    assert "private-book-id" not in planning_content
    assert "private-chapter-id" not in planning_content
    assert "最少且不重复" in policy
    assert "1 至 8" not in policy
    assert "stepBindings" not in policy
    assert '{\"kind\":\"next_episodes\",\"count\":N}' in policy
    assert "“连续创作 N 集”使用 next_episodes" in policy
    assert "screenplayDraft、review" in policy
    assert "创作分集剧本正文使用 screenplayDraft" in policy
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
    assert len(runtime_bundle.blocks) == 2
    assert [block.name for block in runtime_bundle.blocks] == [
        "agent_final_response",
        "screenplay_planning_facts",
    ]
    assert runtime_bundle.blocks[0].content == build_agent_final_response_policy()
    assert runtime_bundle.blocks[1].content == planning_content
