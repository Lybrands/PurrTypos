from __future__ import annotations

import json

import pytest

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    MessageOrigin,
    MessageRole,
    ModelCompletion,
    ModelRequest,
    PlannerLimits,
    PlanningCapabilities,
    PlanningConstraints,
    PlanningKind,
    PlanningTurn,
    ReasoningMode,
    StepExecutor,
    StepStatus,
    StepType,
    TaskStep,
    ToolBatchOutcome,
)
from agent_core.errors import (
    InvalidPlannerOutputError,
    RepairablePlannerOutputError,
    UnsupportedModelFeatureError,
)
from agent_core.planner import (
    PLANNER_SYSTEM_PROMPT,
    AgentPlanner,
    build_execution_message,
    build_planner_messages,
    normalize_task_plan,
    parse_planner_output,
)


class FakeModelGateway:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.invocations = []

    async def complete(self, messages, invocation, signal=None):
        self.invocations.append((tuple(messages), invocation, signal))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return ModelCompletion(
            message=AgentMessage(role=MessageRole.ASSISTANT, content=response),
            model="planner-model",
        )

    async def stream(self, messages, invocation, signal=None):  # pragma: no cover
        raise AssertionError("planner must not stream")


def _request() -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role=MessageRole.USER, content="research then change it"),),
        model=ModelRequest(provider="test", model="model"),
        domain_context=DomainContext(namespace="test"),
        tools_enabled=True,
    )


@pytest.mark.asyncio
async def test_planner_repairs_missing_host_task_admission_semantics():
    missing_scope = (
        '{"needsTodos":true,"title":"写完正文","goal":"写完正文",'
        '"taskSpec":{"goal":"写完正文","target":{},"operation":"write"},'
        '"todos":[{"id":"draft","title":"生成正文","type":"write",'
        '"executor":"tool","expectedTools":["proposeSceneDraft"],'
        '"riskLevel":"write"}]}'
    )
    repaired = (
        '{"needsTodos":true,"title":"写完正文","goal":"写完正文",'
        '"taskSpec":{"goal":"写完正文","target":{'
        '"domainAction":"generate_scene_drafts","scope":"all_remaining"},'
        '"operation":"write"},"todos":[{"id":"draft",'
        '"title":"生成正文","type":"write","executor":"tool",'
        '"expectedTools":["proposeSceneDraft"],"riskLevel":"write"}]}'
    )
    gateway = FakeModelGateway(missing_scope, repaired)
    result = await AgentPlanner(gateway).create_plan(
        _request(),
        PlanningCapabilities(
            available_tool_names=frozenset({"proposeSceneDraft"}),
            host_planning_facts={
                "taskAdmissionVocabulary": {
                    "domainActions": ["generate_scene_drafts"],
                    "requiredForTools": {
                        "proposeSceneDraft": "generate_scene_drafts",
                    },
                    "scopes": ["next_scene", "count", "all_remaining"],
                },
            },
        ),
    )

    assert len(gateway.invocations) == 2
    assert result.plan.task_spec is not None
    assert result.plan.task_spec.target["scope"] == "all_remaining"


@pytest.mark.asyncio
async def test_planner_owns_fifty_item_durable_decomposition():
    scene_ids = [f"s{index:03d}" for index in range(1, 51)]
    group_sizes = [3, 8, 5, 11, 4, 9, 10]
    execution_units = []
    offset = 0
    previous = None
    for index, size in enumerate(group_sizes, start=1):
        unit_id = f"model-unit-{index}"
        execution_units.append({
            "id": unit_id,
            "kind": "scene_generation",
            "itemIds": scene_ids[offset:offset + size],
            "dependsOn": [previous] if previous else [],
            **({
                "dependencyReason": "需要上一单元生成的连续性状态",
            } if previous else {}),
        })
        previous = unit_id
        offset += size
    execution_units.append({
        "id": "model-finalize",
        "kind": "finalize",
        "dependsOn": [previous],
    })
    gateway = FakeModelGateway(json.dumps({
        "needsTodos": True,
        "title": "创作五十场正文",
        "goal": "完成全部五十场正文",
        "taskSpec": {
            "goal": "完成全部五十场正文",
            "operation": "write",
            "target": {
                "domainAction": "generate_scene_drafts",
                "scope": "all_remaining",
                "executionUnits": execution_units,
            },
        },
        "todos": [{
            "id": "draft",
            "title": "创作正文",
            "type": "write",
            "executor": "tool",
            "expectedTools": ["proposeSceneDraft"],
            "riskLevel": "write",
        }],
    }, ensure_ascii=False))
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"proposeSceneDraft"}),
        host_planning_facts={
            "taskAdmissionVocabulary": {
                "domainActions": ["generate_scene_drafts"],
                "requiredForTools": {
                    "proposeSceneDraft": "generate_scene_drafts",
                },
                "scopes": ["all_remaining"],
            },
            "durableExecutionPlan": {
                "requiredForAction": "generate_scene_drafts",
                "requiredItemIds": scene_ids,
                "generationUnitKind": "scene_generation",
                "terminalUnitKind": "finalize",
            },
        },
    )

    result = await AgentPlanner(gateway).create_plan(_request(), capabilities)

    units = result.plan.task_spec.target["executionUnits"]
    assert [
        len(unit["itemIds"])
        for unit in units
        if unit["kind"] == "scene_generation"
    ] == group_sizes
    assert len(gateway.invocations) == 1


@pytest.mark.asyncio
async def test_planner_owns_optional_continuity_reviewer_nodes():
    execution_units = [
        {
            "id": "writer-a",
            "kind": "scene_generation",
            "itemIds": ["s01"],
            "dependsOn": [],
        },
        {
            "id": "writer-b",
            "kind": "scene_generation",
            "itemIds": ["s02"],
            "dependsOn": [],
        },
        {
            "id": "reviewer",
            "kind": "continuity_review",
            "itemIds": ["s01", "s02"],
            "dependsOn": ["writer-a", "writer-b"],
        },
        {
            "id": "finalize",
            "kind": "finalize",
            "dependsOn": ["reviewer"],
        },
    ]
    response = {
        "needsTodos": True,
        "title": "创作并审阅正文",
        "goal": "完成两场连续正文",
        "taskSpec": {
            "goal": "完成两场连续正文",
            "operation": "write",
            "target": {
                "domainAction": "generate_scene_drafts",
                "scope": "all_remaining",
                "executionUnits": execution_units,
            },
        },
        "todos": [{
            "id": "draft",
            "title": "创作正文",
            "type": "write",
            "executor": "tool",
            "expectedTools": ["proposeSceneDraft"],
            "riskLevel": "write",
        }],
    }
    gateway = FakeModelGateway(json.dumps(response, ensure_ascii=False))
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"proposeSceneDraft"}),
        host_planning_facts={
            "taskAdmissionVocabulary": {
                "domainActions": ["generate_scene_drafts"],
                "requiredForTools": {
                    "proposeSceneDraft": "generate_scene_drafts",
                },
                "scopes": ["all_remaining"],
            },
            "durableExecutionPlan": {
                "requiredForAction": "generate_scene_drafts",
                "requiredItemIds": ["s01", "s02"],
                "generationUnitKind": "scene_generation",
                "reviewUnitKind": "continuity_review",
                "terminalUnitKind": "finalize",
            },
        },
    )

    result = await AgentPlanner(gateway).create_plan(_request(), capabilities)

    units = result.plan.task_spec.target["executionUnits"]
    assert [unit["kind"] for unit in units] == [
        "scene_generation",
        "scene_generation",
        "continuity_review",
        "finalize",
    ]
    assert units[-1]["dependsOn"] == ("reviewer",)
    assert len(gateway.invocations) == 1


@pytest.mark.asyncio
async def test_planner_repairs_omitted_first_dependency_without_host_rewrite():
    execution_units = [
        {
            "id": "model-write",
            "kind": "scene_generation",
            "itemIds": ["s01", "s02"],
        },
        {
            "id": "model-finalize",
            "kind": "finalize",
            "dependsOn": ["model-write"],
        },
    ]
    response = {
        "needsTodos": True,
        "title": "创作正文",
        "goal": "完成两场正文",
        "taskSpec": {
            "goal": "完成两场正文",
            "operation": "write",
            "target": {
                "domainAction": "generate_scene_drafts",
                "scope": "all_remaining",
                "executionUnits": execution_units,
            },
        },
        "todos": [{
            "id": "draft",
            "title": "创作正文",
            "type": "write",
            "executor": "tool",
            "expectedTools": ["proposeSceneDraft"],
            "riskLevel": "write",
        }],
    }
    repaired = json.loads(json.dumps(response))
    repaired["taskSpec"]["target"]["executionUnits"][0]["dependsOn"] = []
    gateway = FakeModelGateway(
        json.dumps(response, ensure_ascii=False),
        json.dumps(repaired, ensure_ascii=False),
    )
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"proposeSceneDraft"}),
        host_planning_facts={
            "taskAdmissionVocabulary": {
                "domainActions": ["generate_scene_drafts"],
                "requiredForTools": {
                    "proposeSceneDraft": "generate_scene_drafts",
                },
                "scopes": ["all_remaining"],
            },
            "durableExecutionPlan": {
                "requiredForAction": "generate_scene_drafts",
                "requiredItemIds": ["s01", "s02"],
                "generationUnitKind": "scene_generation",
                "terminalUnitKind": "finalize",
            },
        },
    )

    result = await AgentPlanner(gateway).create_plan(_request(), capabilities)

    units = result.plan.task_spec.target["executionUnits"]
    assert units[0]["dependsOn"] == ()
    assert units[1]["dependsOn"] == ("model-write",)
    assert len(gateway.invocations) == 2


@pytest.mark.asyncio
async def test_planner_repairs_terminal_barrier_without_host_rewrite():
    execution_units = [
        {
            "id": "model-write-a",
            "kind": "scene_generation",
            "itemIds": ["s01"],
            "dependsOn": [],
        },
        {
            "id": "model-write-b",
            "kind": "scene_generation",
            "itemIds": ["s02"],
            "dependsOn": [],
        },
        {
            "id": "model-finalize",
            "kind": "finalize",
            "dependsOn": ["model-write-b"],
        },
    ]
    response = {
        "needsTodos": True,
        "title": "创作正文",
        "goal": "完成两场正文",
        "taskSpec": {
            "goal": "完成两场正文",
            "operation": "write",
            "target": {
                "domainAction": "generate_scene_drafts",
                "scope": "all_remaining",
                "executionUnits": execution_units,
            },
        },
        "todos": [{
            "id": "draft",
            "title": "创作正文",
            "type": "write",
            "executor": "tool",
            "expectedTools": ["proposeSceneDraft"],
            "riskLevel": "write",
        }],
    }
    repaired = json.loads(json.dumps(response))
    repaired["taskSpec"]["target"]["executionUnits"][-1]["dependsOn"] = [
        "model-write-a",
        "model-write-b",
    ]
    gateway = FakeModelGateway(
        json.dumps(response, ensure_ascii=False),
        json.dumps(repaired, ensure_ascii=False),
    )
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"proposeSceneDraft"}),
        host_planning_facts={
            "taskAdmissionVocabulary": {
                "domainActions": ["generate_scene_drafts"],
                "requiredForTools": {
                    "proposeSceneDraft": "generate_scene_drafts",
                },
                "scopes": ["all_remaining"],
            },
            "durableExecutionPlan": {
                "requiredForAction": "generate_scene_drafts",
                "requiredItemIds": ["s01", "s02"],
                "generationUnitKind": "scene_generation",
                "terminalUnitKind": "finalize",
            },
        },
    )

    result = await AgentPlanner(gateway).create_plan(_request(), capabilities)

    units = result.plan.task_spec.target["executionUnits"]
    assert units[0]["itemIds"] == ("s01",)
    assert units[1]["itemIds"] == ("s02",)
    assert units[2]["dependsOn"] == ("model-write-a", "model-write-b")
    assert len(gateway.invocations) == 2


@pytest.mark.asyncio
async def test_planner_repairs_missing_durable_units_instead_of_host_fallback():
    base = {
        "needsTodos": True,
        "title": "创作正文",
        "goal": "完成两场正文",
        "taskSpec": {
            "goal": "完成两场正文",
            "operation": "write",
            "target": {
                "domainAction": "generate_scene_drafts",
                "scope": "all_remaining",
            },
        },
        "todos": [{
            "id": "draft",
            "title": "创作正文",
            "type": "write",
            "executor": "tool",
            "expectedTools": ["proposeSceneDraft"],
            "riskLevel": "write",
        }],
    }
    repaired = json.loads(json.dumps(base))
    repaired["taskSpec"]["target"]["executionUnits"] = [
        {
            "id": "model-write",
            "kind": "scene_generation",
            "itemIds": ["s01", "s02"],
            "dependsOn": [],
        },
        {
            "id": "model-finalize",
            "kind": "finalize",
            "dependsOn": ["model-write"],
        },
    ]
    gateway = FakeModelGateway(
        json.dumps(base, ensure_ascii=False),
        json.dumps(repaired, ensure_ascii=False),
    )
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"proposeSceneDraft"}),
        host_planning_facts={
            "taskAdmissionVocabulary": {
                "domainActions": ["generate_scene_drafts"],
                "requiredForTools": {
                    "proposeSceneDraft": "generate_scene_drafts",
                },
                "scopes": ["all_remaining"],
            },
            "durableExecutionPlan": {
                "requiredForAction": "generate_scene_drafts",
                "requiredItemIds": ["s01", "s02"],
                "generationUnitKind": "scene_generation",
                "terminalUnitKind": "finalize",
            },
        },
    )

    result = await AgentPlanner(gateway).create_plan(_request(), capabilities)

    assert len(gateway.invocations) == 2
    assert len(result.plan.task_spec.target["executionUnits"]) == 2
    assert "executionUnits" in gateway.invocations[1][0][-1].content


@pytest.mark.asyncio
async def test_planner_repairs_explicit_scene_scope_without_scene_ids():
    missing_ids = (
        '{"needsTodos":true,"title":"写指定正文","goal":"写指定正文",'
        '"taskSpec":{"goal":"写指定正文","target":{'
        '"domainAction":"generate_scene_drafts",'
        '"scope":"explicit_scene_ids"},"operation":"write"},'
        '"todos":[{"id":"draft","title":"生成正文","type":"write",'
        '"executor":"tool","expectedTools":["proposeSceneDraft"],'
        '"riskLevel":"write"}]}'
    )
    repaired = (
        '{"needsTodos":true,"title":"写指定正文","goal":"写指定正文",'
        '"taskSpec":{"goal":"写指定正文","target":{'
        '"domainAction":"generate_scene_drafts",'
        '"scope":"explicit_scene_ids","sceneIds":["s05","s06"]},'
        '"operation":"write"},"todos":[{"id":"draft",'
        '"title":"生成正文","type":"write","executor":"tool",'
        '"expectedTools":["proposeSceneDraft"],"riskLevel":"write"}]}'
    )
    gateway = FakeModelGateway(missing_ids, repaired)
    result = await AgentPlanner(gateway).create_plan(
        _request(),
        PlanningCapabilities(
            available_tool_names=frozenset({"proposeSceneDraft"}),
            host_planning_facts={
                "taskAdmissionVocabulary": {
                    "domainActions": ["generate_scene_drafts"],
                    "requiredForTools": {
                        "proposeSceneDraft": "generate_scene_drafts",
                    },
                    "scopes": ["explicit_scene_ids"],
                },
            },
        ),
    )

    assert len(gateway.invocations) == 2
    assert result.plan.task_spec is not None
    assert list(result.plan.task_spec.target["sceneIds"]) == ["s05", "s06"]


@pytest.mark.asyncio
async def test_planner_uses_non_streaming_gateway_and_normalizes_safe_plan():
    gateway = FakeModelGateway(
        '{"needsTodos":true,"title":"Do work","goal":"finish",'
        '"todos":[{"id":"inspect","title":"Inspect","type":"read",'
        '"executor":"tool","expectedTools":["lookup"],"riskLevel":"read"},'
        '{"id":"answer","title":"Answer","type":"review",'
        '"executor":"model","expectedTools":[]}]}'
    )
    result = await AgentPlanner(gateway).create_plan(
        _request(),
        PlanningCapabilities(available_tool_names=frozenset({"lookup"})),
    )

    assert result.kind is PlanningKind.PLANNED
    assert result.model == "planner-model"
    assert result.plan.steps[0].executor is StepExecutor.TOOL
    assert result.plan.steps[0].suggested_tools == ("lookup",)
    assert gateway.invocations[0][1].reasoning_mode is ReasoningMode.DISABLED
    assert gateway.invocations[0][1].tools == ()

    execution_message = build_execution_message(result.plan)
    assert "lookup" not in execution_message.content
    assert "expectedTools" not in execution_message.content
    assert "sole tool authorization" in execution_message.content
    execution_payload = json.loads(execution_message.content.rsplit("\n", 1)[-1])
    assert execution_payload == {
        "stepCount": 2,
        "steps": [
            {
                "position": 1,
                "type": "read",
                "executor": "tool",
                "riskLevel": "read",
            },
            {
                "position": 2,
                "type": "review",
                "executor": "model",
                "riskLevel": "read",
            },
        ],
    }


@pytest.mark.asyncio
async def test_planner_repairs_invalid_json_once_before_stopping():
    gateway = FakeModelGateway(
        '{"needsTodos":true',
        '{"needsTodos":false,"reason":"repaired"}',
    )

    result = await AgentPlanner(gateway).create_plan(
        _request(),
        PlanningCapabilities(),
    )

    assert result.kind is PlanningKind.DIRECT_RESPONSE
    assert result.model_call_count == 2
    assert len(gateway.invocations) == 2
    assert "not valid JSON" in gateway.invocations[1][0][-1].content


@pytest.mark.asyncio
async def test_planner_repairs_valid_json_with_invalid_schema_once():
    gateway = FakeModelGateway(
        '{"title":"missing protocol discriminator","todos":[]}',
        '{"needsTodos":false,"reason":"repaired"}',
    )

    result = await AgentPlanner(gateway).create_plan(
        _request(),
        PlanningCapabilities(),
    )

    assert result.kind is PlanningKind.DIRECT_RESPONSE
    assert result.model_call_count == 2
    assert len(gateway.invocations) == 2
    assert "missing needsTodos" in gateway.invocations[1][0][-1].content


@pytest.mark.asyncio
async def test_planner_stops_after_one_failed_schema_repair():
    invalid = '{"title":"still missing needsTodos"}'
    gateway = FakeModelGateway(invalid, invalid)

    with pytest.raises(InvalidPlannerOutputError, match="needsTodos"):
        await AgentPlanner(gateway).create_plan(
            _request(),
            PlanningCapabilities(),
        )

    assert len(gateway.invocations) == 2


@pytest.mark.asyncio
async def test_planner_accepts_exact_host_artifact_continuity_selection():
    gateway = FakeModelGateway(
        '{"needsTodos":true,"title":"继续场景表","goal":"完成场景表",'
        '"taskSpec":{"goal":"完成场景表","operation":"write",'
        '"instruction":"继续未完成场景表","deliverable":"场景表",'
        '"target":{"artifactContinuity":{"action":"continue",'
        '"artifactId":"artifact-1","workItemId":"work-item-1"}}},'
        '"todos":[{"id":"continue","title":"继续整理",'
        '"type":"write","executor":"model","riskLevel":"read"}]}'
    )
    capabilities = PlanningCapabilities(host_planning_facts={
        "artifactContinuity": {
            "defaultAction": "ignore",
            "candidates": [{
                "artifactId": "artifact-1",
                "workItemId": "work-item-1",
                "kind": "scene_list_batches",
            }],
        },
    })

    result = await AgentPlanner(gateway).create_plan(_request(), capabilities)

    assert result.plan.task_spec is not None
    assert result.plan.task_spec.target["artifactContinuity"] == {
        "action": "continue",
        "artifactId": "artifact-1",
        "workItemId": "work-item-1",
    }
    assert result.model_call_count == 1


@pytest.mark.asyncio
async def test_planner_repairs_hallucinated_artifact_continuity_id():
    invalid = (
        '{"needsTodos":true,"title":"继续","goal":"继续",'
        '"taskSpec":{"goal":"继续","target":{"artifactContinuity":'
        '{"action":"continue","artifactId":"made-up",'
        '"workItemId":"work-item-1"}}},'
        '"todos":[{"id":"respond","title":"回应",'
        '"type":"review","executor":"model","riskLevel":"read"}]}'
    )
    repaired = (
        '{"needsTodos":true,"title":"回答新问题","goal":"回答",'
        '"taskSpec":{"goal":"回答","target":{"artifactContinuity":'
        '{"action":"ignore"}}},'
        '"todos":[{"id":"respond","title":"回应",'
        '"type":"review","executor":"model","riskLevel":"read"}]}'
    )
    gateway = FakeModelGateway(invalid, repaired)
    capabilities = PlanningCapabilities(host_planning_facts={
        "artifactContinuity": {"candidates": [{
            "artifactId": "artifact-1",
            "workItemId": "work-item-1",
        }]},
    })

    result = await AgentPlanner(gateway).create_plan(_request(), capabilities)

    assert result.model_call_count == 2
    assert result.plan.task_spec is not None
    assert result.plan.task_spec.target["artifactContinuity"] == {
        "action": "ignore",
    }
    assert "exact host candidate" in gateway.invocations[1][0][-1].content


@pytest.mark.asyncio
async def test_planner_drops_artifact_continuity_when_host_has_no_candidates():
    gateway = FakeModelGateway(
        '{"needsTodos":true,"title":"生成剩余场景","goal":"完成正文",'
        '"taskSpec":{"goal":"完成正文","operation":"write","target":{'
        '"artifactContinuity":{"action":"continue",'
        '"artifactId":"invented","workItemId":"invented"},'
        '"domainAction":"generate_scene_drafts",'
        '"scope":"all_remaining"}},'
        '"todos":[{"id":"draft","title":"生成正文","type":"write",'
        '"executor":"tool","expectedTools":["proposeSceneDraft"],'
        '"riskLevel":"write"}]}'
    )
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"proposeSceneDraft"}),
        host_planning_facts={
            "taskAdmissionVocabulary": {
                "domainActions": ["generate_scene_drafts"],
                "requiredForTools": {
                    "proposeSceneDraft": "generate_scene_drafts",
                },
                "scopes": ["all_remaining"],
            },
        },
    )

    result = await AgentPlanner(gateway).create_plan(_request(), capabilities)

    assert result.model_call_count == 1
    assert result.plan.task_spec is not None
    assert "artifactContinuity" not in result.plan.task_spec.target
    assert result.plan.task_spec.target["scope"] == "all_remaining"


@pytest.mark.asyncio
async def test_required_stage_deliverable_repairs_premature_direct_response():
    gateway = FakeModelGateway(
        '{"needsTodos":false,"reason":"I will fetch it next"}',
        '{"needsTodos":true,"title":"Create proposal","todos":['
        '{"id":"propose","title":"Create proposal","type":"write",'
        '"executor":"tool","expectedTools":["proposeResult"],'
        '"riskLevel":"write"}]}',
    )
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"readSource", "proposeResult"}),
        host_planning_facts={
            "stageDeliverableRequired": True,
            "completionCapabilities": ["proposeResult"],
        },
    )

    result = await AgentPlanner(gateway).create_plan(_request(), capabilities)

    assert result.kind is PlanningKind.PLANNED
    assert result.plan.steps[0].suggested_tools == ("proposeResult",)
    assert result.model_call_count == 2
    assert "requires completing" in gateway.invocations[1][0][-1].content


@pytest.mark.asyncio
async def test_required_stage_deliverable_allows_final_response_after_completion():
    gateway = FakeModelGateway(
        '{"needsTodos":false,"reason":"formal proposal is complete"}'
    )
    completed = TaskStep(
        id="propose",
        title="Create proposal",
        type=StepType.WRITE,
        executor=StepExecutor.TOOL,
        status=StepStatus.DONE,
        suggested_tools=("proposeResult",),
    )
    turn = PlanningTurn(
        revision=2,
        round_number=3,
        remaining_model_rounds=3,
        messages=(),
        completed_steps=(completed,),
        last_tool_outcome=ToolBatchOutcome.COMPLETED,
    )
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"proposeResult"}),
        host_planning_facts={
            "stageDeliverableRequired": True,
            "completionCapabilities": ["proposeResult"],
        },
    )

    result = await AgentPlanner(gateway).revise_plan(
        _request(), capabilities, turn,
    )

    assert result.kind is PlanningKind.DIRECT_RESPONSE
    assert result.model_call_count == 1


@pytest.mark.asyncio
async def test_runtime_revision_enforces_zero_tool_steps_in_final_model_round():
    tool_plan = (
        '{"needsTodos":true,"title":"Retry","todos":['
        '{"id":"retry","title":"Retry tool","type":"write",'
        '"executor":"tool","expectedTools":["proposeResult"],'
        '"riskLevel":"write"}]}'
    )
    gateway = FakeModelGateway(tool_plan, tool_plan)
    turn = PlanningTurn(
        revision=4,
        round_number=6,
        remaining_model_rounds=1,
        messages=(),
        completed_steps=(),
        last_tool_outcome=ToolBatchOutcome.FAILED,
    )

    with pytest.raises(InvalidPlannerOutputError):
        await AgentPlanner(gateway).revise_plan(
            _request(),
            PlanningCapabilities(
                available_tool_names=frozenset({"proposeResult"}),
            ),
            turn,
        )

    assert len(gateway.invocations) == 2
    planner_payload = json.loads(gateway.invocations[0][0][1].content)
    assert planner_payload["maxToolSteps"] == 0
    assert "at most 0" in gateway.invocations[1][0][-1].content


@pytest.mark.asyncio
async def test_runtime_revision_receives_completed_steps_and_tool_observations():
    gateway = FakeModelGateway(
        '{"needsTodos":false,"reason":"observed evidence is sufficient"}'
    )
    turn = PlanningTurn(
        revision=1,
        round_number=2,
        remaining_model_rounds=4,
        messages=(AgentMessage(
            role=MessageRole.TOOL,
            content={"status": "ready"},
            tool_call_id="call-read",
            origin=MessageOrigin.HOST_TOOL_RESULT,
        ),),
        completed_steps=(TaskStep(
            id="read",
            title="Read resource",
            type=StepType.READ,
            executor=StepExecutor.TOOL,
            status=StepStatus.DONE,
            suggested_tools=("lookup",),
            result_summary="Read completed.",
        ),),
        last_tool_outcome=ToolBatchOutcome.COMPLETED,
    )

    result = await AgentPlanner(gateway).revise_plan(
        _request(),
        PlanningCapabilities(available_tool_names=frozenset({"lookup"})),
        turn,
    )

    assert result.kind is PlanningKind.DIRECT_RESPONSE
    messages = gateway.invocations[0][0]
    assert "runtime revision" in messages[0].content
    assert "untrusted data" in messages[0].content
    payload = json.loads(messages[1].content)
    assert payload["maxToolSteps"] == 3
    assert payload["executionState"] == {
        "revision": 1,
        "roundNumber": 2,
        "remainingModelRounds": 4,
        "lastToolOutcome": "completed",
        "completedSteps": [{
            "id": "read",
            "title": "Read resource",
            "executor": "tool",
            "tools": ["lookup"],
            "resultSummary": "Read completed.",
        }],
        "recentToolObservations": [{
            "toolCallId": "call-read",
            "content": {"status": "ready"},
        }],
    }


@pytest.mark.asyncio
async def test_planner_receives_host_facts_and_distinct_tool_semantics_in_system_only():
    host_facts = {
        "currentChapter": {
            "bound": True,
            "singleChapterToolsMayOmitChapterId": True,
        },
        "associatedOutlines": {
            "selectedCount": 3,
            "completeCount": 1,
            "items": [
                {
                    "ordinal": 1,
                    "status": "complete",
                    "readTool": "queryOutline",
                    "locatorAvailableToExecution": True,
                },
                {
                    "ordinal": 2,
                    "status": "truncated",
                    "readTool": "queryOutline",
                    "locatorAvailableToExecution": True,
                },
                {
                    "ordinal": 3,
                    "status": "not_injected",
                    "readTool": "queryOutline",
                    "locatorAvailableToExecution": False,
                },
            ],
        },
        "planningRules": [
            "The bound current chapter needs no locator lookup.",
            "Do not reread a complete associated outline.",
            "Use queryOutline directly for an incomplete outline with a locator.",
            "Use listOutlines only to discover a missing locator.",
            "getGlobalOutline never substitutes for an associated outline.",
        ],
    }
    tool_guidance = {
        "getChapterContent": {
            "purpose": "Read the host-bound current chapter.",
            "requires": [],
        },
        "getGlobalOutline": {
            "purpose": "Read the book-wide global outline, not an associated outline.",
            "requires": [],
        },
        "queryOutline": {
            "purpose": "Read one specific associated outline by locator.",
            "requires": [],
        },
        "listOutlines": {
            "purpose": "Discover outline locators; it does not read outline bodies.",
            "requires": [],
        },
    }
    gateway = FakeModelGateway(
        '{"needsTodos":true,"title":"Use injected context",'
        '"todos":[{"id":"read-current","title":"Read current chapter",'
        '"type":"read","executor":"tool",'
        '"expectedTools":["getChapterContent"]},'
        '{"id":"analyze","title":"Analyze with associated outline",'
        '"type":"analyze","executor":"model"}]}'
    )
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset(tool_guidance),
        host_planning_facts=host_facts,
        tool_guidance=tool_guidance,
    )

    result = await AgentPlanner(gateway).create_plan(_request(), capabilities)

    messages = gateway.invocations[0][0]
    host_context = json.loads(messages[0].content.rsplit("\n", 1)[-1])
    user_payload = json.loads(messages[1].content)
    assert host_context == {
        "facts": host_facts,
        "toolGuidance": tool_guidance,
    }
    assert user_payload == {
        "mode": "",
        "userText": "research then change it",
        "availableTools": sorted(tool_guidance),
        "maxToolSteps": 4,
    }
    assert "facts" not in user_payload
    assert "toolGuidance" not in user_payload
    assert "outlineId" not in json.dumps(host_context, ensure_ascii=False)
    assert (
        host_context["toolGuidance"]["getGlobalOutline"]["purpose"]
        != host_context["toolGuidance"]["queryOutline"]["purpose"]
    )
    assert result.kind is PlanningKind.PLANNED
    assert result.plan.steps[0].suggested_tools == ("getChapterContent",)
    assert all(
        tool not in result.plan.steps[0].suggested_tools
        for tool in ("getGlobalOutline", "listOutlines", "queryOutline")
    )
    assert "exactly one expectedTools entry" in messages[0].content
    assert "smallest non-redundant tool chain" in messages[0].content
    assert "host expands mandatory" in messages[0].content
    assert "do not" in messages[0].content.lower()
    assert "explicit evidence scope" in messages[0].content
    assert "host facts mark the selected evidence incomplete" in messages[0].content


def test_planner_messages_without_host_context_keep_original_structure():
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"lookup"}),
    )

    messages = build_planner_messages(_request(), capabilities)

    assert messages == (
        AgentMessage(role=MessageRole.SYSTEM, content=PLANNER_SYSTEM_PROMPT),
        AgentMessage(
            role=MessageRole.USER,
            content=json.dumps(
                {
                    "mode": "",
                    "userText": "research then change it",
                    "availableTools": ["lookup"],
                    "maxToolSteps": 4,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ),
    )


def test_planner_messages_filter_context_satisfied_tool_and_clean_dependencies():
    source_guidance = {
        "readCached": {
            "purpose": "Read evidence already present in trusted context.",
            "requires": [],
        },
        "readFresh": {
            "purpose": "Read supplemental evidence not present in context.",
            "requires": ["readCached"],
        },
        "readUnrelated": {
            "purpose": "Broaden beyond the request evidence scope.",
            "requires": [],
        },
    }
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({
            "readCached",
            "readFresh",
            "readUnrelated",
        }),
        tool_guidance=source_guidance,
        constraints=PlanningConstraints(
            context_satisfied_tool_names=frozenset({"readCached"}),
            planning_excluded_tool_names=frozenset({"readUnrelated"}),
        ),
    )

    messages = build_planner_messages(_request(), capabilities)

    host_context = json.loads(messages[0].content.rsplit("\n", 1)[-1])
    user_payload = json.loads(messages[1].content)
    assert user_payload["availableTools"] == ["readFresh"]
    assert host_context == {
        "toolGuidance": {
            "readFresh": {
                "purpose": "Read supplemental evidence not present in context.",
                "requires": [],
            },
        },
        "planningConstraints": {
            "contextSatisfiedTools": ["readCached"],
            "planningExcludedTools": ["readUnrelated"],
        },
    }

    # Planner projection must not mutate the runtime capability catalog or its
    # detached guidance snapshot.
    assert capabilities.available_tool_names == frozenset({
        "readCached",
        "readFresh",
        "readUnrelated",
    })
    assert capabilities.tool_guidance["readFresh"]["requires"] == ["readCached"]
    assert source_guidance["readFresh"]["requires"] == ["readCached"]


def test_planner_messages_waive_only_one_dependency_edge_without_removing_tools():
    source_guidance = {
        "catalog": {"purpose": "Discover locators.", "requires": []},
        "readCurrent": {
            "purpose": "Read the host-bound current item.",
            "requires": ["catalog"],
        },
        "readMany": {
            "purpose": "Read arbitrary items.",
            "requires": ["catalog"],
        },
    }
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset(source_guidance),
        tool_guidance=source_guidance,
        constraints=PlanningConstraints(
            satisfied_tool_dependency_edges=frozenset({
                ("readCurrent", "catalog"),
            }),
        ),
    )

    messages = build_planner_messages(_request(), capabilities)

    host_context = json.loads(messages[0].content.rsplit("\n", 1)[-1])
    user_payload = json.loads(messages[1].content)
    assert user_payload["availableTools"] == [
        "catalog",
        "readCurrent",
        "readMany",
    ]
    assert host_context["toolGuidance"]["readCurrent"]["requires"] == []
    assert host_context["toolGuidance"]["readMany"]["requires"] == [
        "catalog"
    ]
    assert host_context["planningConstraints"] == {
        "satisfiedToolDependencyEdges": [{
            "tool": "readCurrent",
            "dependency": "catalog",
        }],
    }
    assert "edge-scoped waivers" in messages[0].content.casefold()
    assert capabilities.available_tool_names == frozenset(source_guidance)
    assert capabilities.tool_guidance["readCurrent"]["requires"] == ["catalog"]
    assert source_guidance["readCurrent"]["requires"] == ["catalog"]


@pytest.mark.asyncio
async def test_planner_retries_only_an_explicit_unsupported_reasoning_feature():
    gateway = FakeModelGateway(
        UnsupportedModelFeatureError(),
        '{"needsTodos":false,"reason":"one response is enough"}',
    )
    result = await AgentPlanner(gateway).create_plan(
        _request(), PlanningCapabilities()
    )

    assert result.kind is PlanningKind.DIRECT_RESPONSE
    assert result.model_call_count == 2
    assert len(gateway.invocations) == 2
    assert gateway.invocations[1][1].reasoning_mode is ReasoningMode.DEFAULT


def test_planner_output_is_strict_and_never_expands_tool_authority():
    capabilities = PlanningCapabilities(available_tool_names=frozenset({"lookup"}))

    with pytest.raises(InvalidPlannerOutputError, match="needsTodos"):
        normalize_task_plan({"reason": "ambiguous"}, capabilities)
    with pytest.raises(InvalidPlannerOutputError, match="unavailable"):
        normalize_task_plan({
            "needsTodos": True,
            "todos": [{
                "id": "bad",
                "title": "Bad",
                "type": "read",
                "executor": "tool",
                "expectedTools": ["admin"],
            }],
        }, capabilities)
    with pytest.raises(InvalidPlannerOutputError, match="model steps"):
        normalize_task_plan({
            "needsTodos": True,
            "todos": [{
                "id": "bad",
                "title": "Bad",
                "type": "review",
                "executor": "model",
                "expectedTools": ["lookup"],
            }],
        }, capabilities)


def test_host_declared_task_tool_repairs_model_executor_discriminator():
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"proposeSceneDraft"}),
        host_planning_facts={
            "taskAdmissionVocabulary": {
                "domainActions": ["generate_scene_drafts"],
                "requiredForTools": {
                    "proposeSceneDraft": "generate_scene_drafts",
                },
                "scopes": ["next_scene", "all_remaining"],
            },
        },
    )

    result = normalize_task_plan({
        "needsTodos": True,
        "title": "继续创作",
        "goal": "继续创作剧本正文",
        "taskSpec": {
            "goal": "继续创作剧本正文",
            "operation": "write",
            "target": {
                "domainAction": "generate_scene_drafts",
                "scope": "all_remaining",
            },
        },
        "todos": [{
            "id": "draft",
            "title": "创作正文",
            "type": "write",
            "executor": "model",
            "expectedTools": ["proposeSceneDraft"],
            "riskLevel": "write",
        }],
    }, capabilities)

    step = result.plan.steps[0]
    assert step.executor is StepExecutor.TOOL
    assert step.suggested_tools == ("proposeSceneDraft",)


@pytest.mark.asyncio
async def test_host_parameterized_scope_requires_positive_count():
    invalid = (
        '{"needsTodos":true,"title":"继续创作","goal":"继续创作",'
        '"taskSpec":{"goal":"继续创作","operation":"write","target":{'
        '"domainAction":"generate_scene_drafts","scope":"next_episodes"}},'
        '"todos":[{"id":"draft","title":"创作正文","type":"write",'
        '"executor":"tool","expectedTools":["proposeSceneDraft"],'
        '"riskLevel":"write"}]}'
    )
    repaired = invalid.replace(
        '"scope":"next_episodes"',
        '"scope":"next_episodes","count":3',
    )
    gateway = FakeModelGateway(invalid, repaired)
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"proposeSceneDraft"}),
        host_planning_facts={
            "taskAdmissionVocabulary": {
                "domainActions": ["generate_scene_drafts"],
                "requiredForTools": {
                    "proposeSceneDraft": "generate_scene_drafts",
                },
                "scopes": ["next_episodes"],
                "scopeParameters": {
                    "next_episodes": {
                        "count": "positive_episode_count",
                    },
                },
            },
        },
    )

    result = await AgentPlanner(gateway).create_plan(_request(), capabilities)

    assert result.plan.task_spec is not None
    assert result.plan.task_spec.target["count"] == 3
    assert len(gateway.invocations) == 2


def test_multi_tool_destructive_step_is_repairable_but_never_locally_expanded():
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"update", "delete"}),
    )

    with pytest.raises(RepairablePlannerOutputError, match="exactly one"):
        normalize_task_plan({
            "needsTodos": True,
            "todos": [{
                "id": "mutate",
                "title": "Mutate and delete",
                "type": "write",
                "executor": "tool",
                "expectedTools": ["update", "delete"],
                "riskLevel": "destructive",
            }],
        }, capabilities)


def test_duplicate_tool_names_collapse_but_five_tool_steps_require_repair():
    duplicate = normalize_task_plan({
        "needsTodos": True,
        "todos": [{
            "id": "read",
            "title": "Read",
            "type": "read",
            "executor": "tool",
            "expectedTools": ["read", "read"],
        }],
    }, PlanningCapabilities(available_tool_names=frozenset({"read"})))

    assert duplicate.plan.steps[0].suggested_tools == ("read",)

    with pytest.raises(RepairablePlannerOutputError, match="execution limit is 4"):
        normalize_task_plan({
            "needsTodos": True,
            "todos": [
                {
                    "id": f"read-{index}",
                    "title": f"Read {index}",
                    "type": "read",
                    "executor": "tool",
                    "expectedTools": [f"read{index}"],
                }
                for index in range(5)
            ],
        }, PlanningCapabilities(
            available_tool_names=frozenset(f"read{index}" for index in range(5)),
        ))


@pytest.mark.asyncio
async def test_planner_repairs_multi_tool_step_once_with_a_new_model_plan():
    gateway = FakeModelGateway(
        '{"needsTodos":true,"title":"bad","todos":[{"id":"both",'
        '"title":"Both","type":"read","executor":"tool",'
        '"expectedTools":["readA","readB"]}]}',
        '{"needsTodos":true,"title":"fixed","todos":['
        '{"id":"read-a","title":"Read A","type":"read",'
        '"executor":"tool","expectedTools":["readA"]},'
        '{"id":"read-b","title":"Read B","type":"read",'
        '"executor":"tool","expectedTools":["readB"]}]}',
    )

    result = await AgentPlanner(gateway).create_plan(
        _request(),
        PlanningCapabilities(available_tool_names=frozenset({"readA", "readB"})),
    )

    assert [step.id for step in result.plan.steps] == ["read-a", "read-b"]
    assert [step.suggested_tools for step in result.plan.steps] == [
        ("readA",),
        ("readB",),
    ]
    assert len(gateway.invocations) == 2
    repair_messages = gateway.invocations[1][0]
    assert repair_messages[-2].role is MessageRole.ASSISTANT
    assert "Do not mechanically expand" in repair_messages[-1].content
    assert "never broaden" in repair_messages[-1].content


@pytest.mark.asyncio
async def test_planner_repairs_model_read_of_injected_context_once():
    gateway = FakeModelGateway(
        '{"needsTodos":true,"title":"bad","todos":[{"id":"read",'
        '"title":"Read injected context","type":"read","executor":"model",'
        '"expectedTools":[]}]}',
        '{"needsTodos":true,"title":"fixed","todos":[{"id":"analyze",'
        '"title":"Analyze injected context","type":"analyze",'
        '"executor":"model","expectedTools":[]}]}',
    )

    result = await AgentPlanner(gateway).create_plan(
        _request(),
        PlanningCapabilities(),
    )

    assert [step.id for step in result.plan.steps] == ["analyze"]
    assert len(gateway.invocations) == 2
    assert "model analysis/review" in gateway.invocations[1][0][-1].content


@pytest.mark.asyncio
async def test_planner_repairs_context_satisfied_tool_once_with_a_new_plan():
    gateway = FakeModelGateway(
        '{"needsTodos":true,"title":"bad","todos":[{"id":"reread",'
        '"title":"Read cached evidence","type":"read","executor":"tool",'
        '"expectedTools":["readCached"]}]}',
        '{"needsTodos":true,"title":"fixed","todos":[{"id":"analyze",'
        '"title":"Analyze cached evidence","type":"analyze",'
        '"executor":"model","expectedTools":[]}]}',
    )
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"readCached", "readFresh"}),
        constraints=PlanningConstraints(
            context_satisfied_tool_names=frozenset({"readCached"}),
        ),
    )

    result = await AgentPlanner(gateway).create_plan(_request(), capabilities)

    assert [step.id for step in result.plan.steps] == ["analyze"]
    assert len(gateway.invocations) == 2
    initial_payload = json.loads(gateway.invocations[0][0][1].content)
    assert initial_payload["availableTools"] == ["readFresh"]
    repair_message = gateway.invocations[1][0][-1]
    assert "context-satisfied tools: readCached" in repair_message.content
    assert "Do not reuse a tool named" in repair_message.content


@pytest.mark.asyncio
async def test_planner_fails_after_repeated_context_satisfied_tool_violation():
    invalid = (
        '{"needsTodos":true,"title":"bad","todos":[{"id":"reread",'
        '"title":"Read cached evidence","type":"read","executor":"tool",'
        '"expectedTools":["readCached"]}]}'
    )
    gateway = FakeModelGateway(invalid, invalid)
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"readCached"}),
        constraints=PlanningConstraints(
            context_satisfied_tool_names=frozenset({"readCached"}),
        ),
    )

    with pytest.raises(RepairablePlannerOutputError, match="context-satisfied"):
        await AgentPlanner(gateway).create_plan(_request(), capabilities)

    assert len(gateway.invocations) == 2


@pytest.mark.asyncio
async def test_planner_repairs_request_scope_excluded_tool_once():
    gateway = FakeModelGateway(
        '{"needsTodos":true,"title":"bad","todos":[{"id":"broad",'
        '"title":"Read unrelated dashboard","type":"read","executor":"tool",'
        '"expectedTools":["readDashboard"]}]}',
        '{"needsTodos":true,"title":"fixed","todos":[{"id":"analyze",'
        '"title":"Analyze selected evidence","type":"analyze",'
        '"executor":"model","expectedTools":[]}]}',
    )
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"readDashboard"}),
        constraints=PlanningConstraints(
            planning_excluded_tool_names=frozenset({"readDashboard"}),
        ),
    )

    result = await AgentPlanner(gateway).create_plan(_request(), capabilities)

    assert [step.id for step in result.plan.steps] == ["analyze"]
    assert len(gateway.invocations) == 2
    initial_payload = json.loads(gateway.invocations[0][0][1].content)
    assert initial_payload["availableTools"] == []
    repair_message = gateway.invocations[1][0][-1]
    assert "excluded by the request's evidence/action scope" in (
        repair_message.content
    )


@pytest.mark.asyncio
async def test_planner_stops_after_repeated_model_read_error():
    invalid = (
        '{"needsTodos":true,"title":"bad","todos":[{"id":"read",'
        '"title":"Read injected context","type":"read","executor":"model",'
        '"expectedTools":[]}]}'
    )
    gateway = FakeModelGateway(invalid, invalid)

    with pytest.raises(RepairablePlannerOutputError, match="reserved"):
        await AgentPlanner(gateway).create_plan(
            _request(),
            PlanningCapabilities(),
        )

    assert len(gateway.invocations) == 2


@pytest.mark.asyncio
async def test_planner_stops_after_one_invalid_correction():
    invalid = (
        '{"needsTodos":true,"title":"bad","todos":[{"id":"both",'
        '"title":"Both","type":"write","executor":"tool",'
        '"expectedTools":["writeA","writeB"],"riskLevel":"destructive"}]}'
    )
    gateway = FakeModelGateway(invalid, invalid)

    with pytest.raises(RepairablePlannerOutputError, match="exactly one"):
        await AgentPlanner(gateway).create_plan(
            _request(),
            PlanningCapabilities(
                available_tool_names=frozenset({"writeA", "writeB"}),
            ),
        )

    assert len(gateway.invocations) == 2


@pytest.mark.asyncio
async def test_planner_repairs_unknown_or_forbidden_steps_once_then_fails_closed():
    cases = (
        (
            '{"needsTodos":true,"todos":[{"id":"bad","title":"Bad",'
            '"type":"read","executor":"tool","expectedTools":["unknown"]}]}',
            "unavailable",
        ),
        (
            '{"needsTodos":true,"todos":[{"id":"bad","title":"Bad",'
            '"type":"confirm","executor":"model"}]}',
            "confirm",
        ),
        (
            '{"needsTodos":true,"todos":[{"id":"bad","title":"Bad",'
            '"type":"read","executor":"model",'
            '"expectedTools":["lookup"]}]}',
            "model steps",
        ),
    )
    for response, message in cases:
        gateway = FakeModelGateway(response, response)
        with pytest.raises(InvalidPlannerOutputError, match=message):
            await AgentPlanner(gateway).create_plan(
                _request(),
                PlanningCapabilities(available_tool_names=frozenset({"lookup"})),
            )
        assert len(gateway.invocations) == 2
        assert message in gateway.invocations[1][0][-1].content


@pytest.mark.asyncio
async def test_repeated_unknown_tool_remains_hard_invalid_with_context_constraints():
    response = (
        '{"needsTodos":true,"todos":[{"id":"bad","title":"Bad",'
        '"type":"read","executor":"tool","expectedTools":["unknown"]}]}'
    )
    gateway = FakeModelGateway(response, response)
    capabilities = PlanningCapabilities(
        available_tool_names=frozenset({"readCached"}),
        constraints=PlanningConstraints(
            context_satisfied_tool_names=frozenset({"readCached"}),
        ),
    )

    with pytest.raises(InvalidPlannerOutputError, match="unavailable"):
        await AgentPlanner(gateway).create_plan(_request(), capabilities)

    assert len(gateway.invocations) == 2


def test_planner_limit_accepts_zero_tool_steps_but_rejects_any_tool_step():
    limits = PlannerLimits(max_tool_steps=0)
    direct = normalize_task_plan(
        {"needsTodos": False, "reason": "direct"},
        PlanningCapabilities(),
        limits,
    )
    assert direct.kind is PlanningKind.DIRECT_RESPONSE

    with pytest.raises(RepairablePlannerOutputError, match="execution limit is 0"):
        normalize_task_plan({
            "needsTodos": True,
            "todos": [{
                "id": "read",
                "title": "Read",
                "type": "read",
                "executor": "tool",
                "expectedTools": ["read"],
            }],
        }, PlanningCapabilities(
            available_tool_names=frozenset({"read"}),
        ), limits)


def test_parser_accepts_json_fence_but_rejects_non_object_output():
    assert parse_planner_output('```json\n{"needsTodos":false}\n```')["needsTodos"] is False
    assert parse_planner_output(
        'Here is the plan:\n{"needsTodos":false,"reason":"done"}'
    )["reason"] == "done"
    with pytest.raises(InvalidPlannerOutputError, match="object"):
        parse_planner_output("[]")
