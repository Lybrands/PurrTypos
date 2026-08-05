from __future__ import annotations

import json

import pytest

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    MessageRole,
    ModelRequest,
    StepExecutor,
    StepType,
    TaskPlan,
    TaskSpec,
    TaskStep,
    ToolRiskLevel,
)
from agent_core.task_admission import ExecutionMode
from domains.screenplay.contracts import ScreenplayDomainContext
from domains.screenplay.task_admission import ScreenplayTaskAdmissionEvaluator


class _Db:
    def __init__(self, rows):
        self.rows = rows

    async def fetch_all(self, sql, params):
        assert "screenplay_documents" in sql
        assert params == ["project-1"]
        return self.rows


def _request(
    draft_scene_count=1,
    *,
    stage="draft",
    task_intent="chat",
    draft_scope="planner",
):
    return AgentRunRequest(
        messages=(AgentMessage(
            role=MessageRole.USER,
            content="完成所有剩余场景正文",
        ),),
        model=ModelRequest(provider="fixture", model="model"),
        domain_context=ScreenplayDomainContext(
            project_id="project-1",
            requested_stage=stage,
            task_intent=task_intent,
            draft_scene_count=draft_scene_count,
            draft_scope=draft_scope,
        ).to_core_context(),
        session_id=7,
    )


def _execution_units(*scene_groups):
    units = []
    previous = None
    for index, scene_ids in enumerate(scene_groups, start=1):
        unit_id = f"planner-unit-{index}"
        units.append({
            "id": unit_id,
            "kind": "scene_generation",
            "itemIds": list(scene_ids),
            "dependsOn": [previous] if previous else [],
            **({
                "dependencyReason": "需要上一单元生成的连续性状态",
            } if previous else {}),
        })
        previous = unit_id
    units.append({
        "id": "planner-finalize",
        "kind": "finalize",
        "dependsOn": [previous] if previous else [],
    })
    return units


def _parallel_execution_units(*scene_groups):
    units = [
        {
            "id": f"planner-unit-{index}",
            "kind": "scene_generation",
            "itemIds": list(scene_ids),
            "dependsOn": [],
        }
        for index, scene_ids in enumerate(scene_groups, start=1)
    ]
    units.append({
        "id": "planner-finalize",
        "kind": "finalize",
        "dependsOn": [unit["id"] for unit in units],
    })
    return units


def _plan(
    scope="all_remaining",
    count=None,
    scene_ids=None,
    execution_units=None,
):
    target = {
        "domainAction": "generate_scene_drafts",
        "scope": scope,
    }
    if count is not None:
        target["count"] = count
    if scene_ids is not None:
        target["sceneIds"] = scene_ids
    if execution_units is not None:
        target["executionUnits"] = execution_units
    return TaskPlan(
        title="创作正文",
        task_spec=TaskSpec(
            goal="完成正文",
            operation="write",
            target=target,
        ),
        steps=(TaskStep(
            id="draft",
            title="创作正文",
            type=StepType.WRITE,
            executor=StepExecutor.TOOL,
            risk_level=ToolRiskLevel.WRITE,
            suggested_tools=("proposeSceneDraft",),
        ),),
    )


@pytest.mark.asyncio
async def test_all_remaining_draft_becomes_durable_domain_plan():
    scenes = [
        {
            "id": f"s{index:03d}",
            "heading": f"场景 {index}",
            "episodeNumber": 1 if index <= 6 else 2,
        }
        for index in range(1, 13)
    ]
    db = _Db([
        {
            "id": "scene-list-1",
            "kind": "scene_list",
            "version": 1,
            "content_json": json.dumps({"scenes": scenes}),
        },
        {
            "id": "draft-1",
            "kind": "scene_draft",
            "version": 1,
            "content_json": json.dumps({"completedSceneIds": ["s001", "s002"]}),
        },
    ])

    decision = await ScreenplayTaskAdmissionEvaluator(db).evaluate(
        _request(draft_scene_count=1),
        _plan(execution_units=_execution_units(
            ["s003", "s004", "s005"],
            ["s006", "s007"],
            ["s008", "s009", "s010", "s011", "s012"],
        )),
    )

    assert decision.mode is ExecutionMode.DURABLE
    assert decision.estimated_units == 10
    assert decision.estimated_model_calls == 3
    assert decision.metadata["targetSceneIds"] == [
        f"s{index:03d}" for index in range(3, 13)
    ]
    assert [
        unit.get("sceneIds")
        for unit in decision.metadata["executionUnits"]
        if unit["kind"] == "scene_generation"
    ] == [
        ["s003", "s004", "s005"],
        ["s006", "s007"],
        ["s008", "s009", "s010", "s011", "s012"],
    ]
    assert decision.metadata["maxParallelism"] == 1


@pytest.mark.asyncio
async def test_planner_parallel_frontier_sets_bounded_writer_capacity():
    scenes = [
        {"id": f"s{index:03d}", "heading": str(index), "episodeNumber": index}
        for index in range(1, 6)
    ]
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({"scenes": scenes}),
    }])

    decision = await ScreenplayTaskAdmissionEvaluator(db).evaluate(
        _request(draft_scene_count=5),
        _plan(execution_units=_parallel_execution_units(
            ["s001"], ["s002"], ["s003"], ["s004"], ["s005"],
        )),
    )

    assert decision.mode is ExecutionMode.DURABLE
    assert decision.metadata["maxParallelism"] == 3


@pytest.mark.asyncio
async def test_planner_owned_reviewer_graph_is_preserved_without_host_rebatching():
    scenes = [
        {"id": f"s00{index}", "heading": str(index), "episodeNumber": index}
        for index in range(1, 4)
    ]
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({"scenes": scenes}),
    }])
    execution_units = [
        {
            "id": "writer-a",
            "kind": "scene_generation",
            "itemIds": ["s001", "s002"],
            "dependsOn": [],
        },
        {
            "id": "writer-b",
            "kind": "scene_generation",
            "itemIds": ["s003"],
            "dependsOn": [],
        },
        {
            "id": "review-all",
            "kind": "continuity_review",
            "itemIds": ["s001", "s002", "s003"],
            "dependsOn": ["writer-a", "writer-b"],
        },
        {
            "id": "planner-finalize",
            "kind": "finalize",
            "dependsOn": ["review-all"],
        },
    ]

    decision = await ScreenplayTaskAdmissionEvaluator(db).evaluate(
        _request(draft_scene_count=3),
        _plan(execution_units=execution_units),
    )

    assert decision.mode is ExecutionMode.DURABLE
    assert [
        (unit["id"], unit["kind"], unit["dependsOn"])
        for unit in decision.metadata["executionUnits"]
    ] == [
        ("writer-a", "scene_generation", []),
        ("writer-b", "scene_generation", []),
        ("review-all", "continuity_review", ["writer-a", "writer-b"]),
        ("planner-finalize", "finalize", ["review-all"]),
    ]
    assert decision.estimated_model_calls == 3
    assert decision.metadata["maxParallelism"] == 2


@pytest.mark.asyncio
async def test_reviewer_cannot_read_an_unrelated_writer_branch():
    scenes = [
        {"id": "s001", "heading": "一"},
        {"id": "s002", "heading": "二"},
    ]
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({"scenes": scenes}),
    }])
    invalid_units = [
        {
            "id": "writer-a",
            "kind": "scene_generation",
            "itemIds": ["s001"],
            "dependsOn": [],
        },
        {
            "id": "writer-b",
            "kind": "scene_generation",
            "itemIds": ["s002"],
            "dependsOn": [],
        },
        {
            "id": "review-a",
            "kind": "continuity_review",
            "itemIds": ["s002"],
            "dependsOn": ["writer-a"],
        },
        {
            "id": "planner-finalize",
            "kind": "finalize",
            "dependsOn": ["writer-b", "review-a"],
        },
    ]

    decision = await ScreenplayTaskAdmissionEvaluator(db).evaluate(
        _request(draft_scene_count=2),
        _plan(execution_units=invalid_units),
    )

    assert decision.mode is ExecutionMode.REJECT
    assert decision.reason_code == "screenplay_draft_scope_unresolvable"


@pytest.mark.asyncio
async def test_one_scene_that_matches_request_stays_inline():
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({"scenes": [{"id": "s001", "heading": "一"}]}),
    }])

    decision = await ScreenplayTaskAdmissionEvaluator(db).evaluate(
        _request(draft_scene_count=1),
        _plan(scope="next_scene"),
    )

    assert decision.mode is ExecutionMode.INLINE


@pytest.mark.asyncio
async def test_unresolvable_draft_scope_never_falls_back_to_inline():
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({
            "scenes": [{"id": "s001", "heading": "一"}],
        }),
    }])

    decision = await ScreenplayTaskAdmissionEvaluator(db).evaluate(
        _request(draft_scene_count=1),
        _plan(scope="explicit_scene_ids"),
    )

    assert decision.mode is ExecutionMode.REJECT
    assert decision.reason_code == "screenplay_draft_scope_unresolvable"
    assert decision.estimated_units == 0
    assert decision.estimated_model_calls == 0


@pytest.mark.asyncio
async def test_explicit_multi_scene_scope_uses_planner_execution_units():
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({
            "scenes": [
                {"id": "s001", "heading": "一", "episodeNumber": 1},
                {"id": "s002", "heading": "二", "episodeNumber": 1},
            ],
        }),
    }])

    decision = await ScreenplayTaskAdmissionEvaluator(db).evaluate(
        _request(draft_scene_count=2),
        _plan(
            scope="explicit_scene_ids",
            scene_ids=["s001", "s002"],
            execution_units=_execution_units(["s001", "s002"]),
        ),
    )

    assert decision.mode is ExecutionMode.DURABLE
    assert decision.estimated_units == 2
    assert decision.metadata["targetSceneIds"] == ["s001", "s002"]


@pytest.mark.asyncio
async def test_button_scope_rejects_a_planner_range_that_expands_past_next_episode():
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({
            "scenes": [
                {"id": "s001", "heading": "一", "episodeNumber": 1},
                {"id": "s002", "heading": "二", "episodeNumber": 1},
                {"id": "s003", "heading": "三", "episodeNumber": 2},
            ],
        }),
    }])

    decision = await ScreenplayTaskAdmissionEvaluator(db).evaluate(
        _request(draft_scene_count=3, draft_scope="next_episode"),
        _plan(
            scope="all_remaining",
            execution_units=_execution_units(["s001", "s002", "s003"]),
        ),
    )

    assert decision.mode is ExecutionMode.REJECT
    assert decision.reason_code == "screenplay_draft_scope_unresolvable"


@pytest.mark.asyncio
async def test_next_three_episode_scope_uses_one_authoritative_scene_range():
    scenes = [
        {
            "id": f"s{episode:02d}-{scene:02d}",
            "heading": f"第 {episode} 集第 {scene} 场",
            "episodeNumber": episode,
        }
        for episode in range(1, 6)
        for scene in range(1, 5)
    ]
    db = _Db([
        {
            "id": "scene-list-1",
            "kind": "scene_list",
            "version": 1,
            "content_json": json.dumps({"scenes": scenes}),
        },
        {
            "id": "draft-1",
            "kind": "scene_draft",
            "version": 1,
            "content_json": json.dumps({
                "completedSceneIds": [
                    f"s01-{scene:02d}" for scene in range(1, 5)
                ],
            }),
        },
    ])
    selected_ids = [
        f"s{episode:02d}-{scene:02d}"
        for episode in range(2, 5)
        for scene in range(1, 5)
    ]

    decision = await ScreenplayTaskAdmissionEvaluator(db).evaluate(
        _request(draft_scene_count=1, draft_scope="next_3_episodes"),
        _plan(
            scope="next_episodes",
            count=3,
            execution_units=_execution_units(
                selected_ids[:4],
                selected_ids[4:8],
                selected_ids[8:],
            ),
        ),
    )

    assert decision.mode is ExecutionMode.DURABLE
    assert decision.estimated_units == 12
    assert decision.estimated_model_calls == 3
    assert decision.metadata["targetSceneIds"] == selected_ids


@pytest.mark.asyncio
async def test_next_three_episode_scope_rejects_wrong_planner_episode_count():
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({
            "scenes": [
                {
                    "id": f"s{episode:02d}",
                    "heading": f"第 {episode} 集",
                    "episodeNumber": episode,
                }
                for episode in range(1, 6)
            ],
        }),
    }])

    decision = await ScreenplayTaskAdmissionEvaluator(db).evaluate(
        _request(draft_scope="next_3_episodes"),
        _plan(
            scope="next_episodes",
            count=5,
            execution_units=_execution_units(
                ["s01", "s02", "s03", "s04", "s05"],
            ),
        ),
    )

    assert decision.mode is ExecutionMode.REJECT
    assert decision.reason_code == "screenplay_draft_scope_unresolvable"


@pytest.mark.asyncio
async def test_incident_shape_uses_the_planner_owned_unit_decomposition():
    episode_numbers = [
        2,
        3, 3,
        4, 4, 4,
        5, 5, 5,
        6, 6,
        7, 7,
        8, 8,
        9, 9,
        10, 10,
    ]
    scenes = [
        {
            "id": f"s{index:02d}",
            "heading": f"场景 {index}",
            "episodeNumber": episode,
        }
        for index, episode in enumerate(episode_numbers, start=5)
    ]
    db = _Db([
        {
            "id": "scene-list-1",
            "kind": "scene_list",
            "version": 1,
            "content_json": json.dumps({"scenes": scenes}),
        },
    ])

    decision = await ScreenplayTaskAdmissionEvaluator(db).evaluate(
        _request(draft_scene_count=19),
        _plan(
            scope="all_remaining",
            execution_units=_execution_units(
                ["s05", "s06", "s07", "s08"],
                ["s09", "s10"],
                ["s11", "s12", "s13", "s14", "s15", "s16"],
                ["s17", "s18", "s19", "s20", "s21", "s22", "s23"],
            ),
        ),
    )

    assert decision.mode is ExecutionMode.DURABLE
    assert decision.estimated_units == 19
    assert decision.estimated_model_calls == 4
    assert [
        len(unit.get("sceneIds") or [])
        for unit in decision.metadata["executionUnits"]
        if unit["kind"] == "scene_generation"
    ] == [
        4, 2, 6, 7,
    ]


@pytest.mark.asyncio
async def test_large_draft_without_planner_units_is_rejected_not_partitioned():
    scenes = [
        {"id": f"s{index:02d}", "heading": f"场景 {index}"}
        for index in range(1, 20)
    ]
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({"scenes": scenes}),
    }])

    decision = await ScreenplayTaskAdmissionEvaluator(db).evaluate(
        _request(draft_scene_count=19),
        _plan(scope="all_remaining"),
    )

    assert decision.mode is ExecutionMode.REJECT
    assert decision.reason_code == "screenplay_draft_planner_units_missing"


class _StageDb:
    async def fetch_one(self, sql, params):
        assert "screenplay_projects" in sql
        assert params == ["project-1"]
        return {
            "id": "project-1",
            "active_stage": "structure",
            "source_kind": "book",
            "source_book_id": "book-1",
            "format": "竖屏短剧",
        }

    async def fetch_all(self, sql, params):
        assert "screenplay_documents" in sql
        assert params == ["project-1"]
        return [{
            "id": "brief-accepted",
            "kind": "creative_brief",
            "version": 2,
        }]


@pytest.mark.asyncio
async def test_planned_stage_deliverable_executes_in_primary_agent_run():
    decision = await ScreenplayTaskAdmissionEvaluator(_StageDb()).evaluate(
        _request(stage="structure", task_intent="stage_deliverable"),
        TaskPlan(
            title="生成分集结构",
            task_spec=TaskSpec(
                goal="生成正式分集结构提案",
                operation="write",
                target={
                    "domainAction": "generate_stage_deliverable",
                    "scope": "current_stage",
                },
            ),
            steps=(TaskStep(
                id="finalize-outline",
                title="生成并提交分集结构",
                type=StepType.WRITE,
                executor=StepExecutor.TOOL,
                risk_level=ToolRiskLevel.WRITE,
                suggested_tools=("finalizeScreenplayStructureProposal",),
            ),),
        ),
    )

    assert decision.mode is ExecutionMode.INLINE
    assert decision.reason_code == "screenplay_stage_executes_in_primary_run"
    assert decision.metadata["deliverableKind"] == "episode_outline"
    assert decision.metadata["acceptedInputIds"] == {
        "creative_brief": "brief-accepted",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "tool", "reason_code"),
    [
        (
            {
                "domainAction": "generate_stage_deliverable",
                "scope": "all_remaining",
            },
            "finalizeScreenplayStructureProposal",
            "screenplay_stage_planner_scope_invalid",
        ),
        (
            {
                "domainAction": "generate_stage_deliverable",
                "scope": "current_stage",
            },
            "finalizeCreativeBriefProposal",
            "screenplay_stage_planner_capability_invalid",
        ),
    ],
)
async def test_stage_admission_rejects_planner_semantics_it_cannot_execute(
    target,
    tool,
    reason_code,
):
    decision = await ScreenplayTaskAdmissionEvaluator(_StageDb()).evaluate(
        _request(stage="structure", task_intent="stage_deliverable"),
        TaskPlan(
            title="错误的阶段计划",
            task_spec=TaskSpec(
                goal="生成正式分集结构提案",
                operation="write",
                target=target,
            ),
            steps=(TaskStep(
                id="finalize",
                title="提交提案",
                type=StepType.WRITE,
                executor=StepExecutor.TOOL,
                risk_level=ToolRiskLevel.WRITE,
                suggested_tools=(tool,),
            ),),
        ),
    )

    assert decision.mode is ExecutionMode.REJECT
    assert decision.reason_code == reason_code
