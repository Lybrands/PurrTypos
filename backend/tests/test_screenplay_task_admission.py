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
from infrastructure.screenplay.agent_query import SqliteScreenplayAgentQuery


def _recipe_units(decision):
    assert decision.execution_recipe is not None
    return decision.execution_recipe.to_metadata()["steps"]


def _evaluator(db) -> ScreenplayTaskAdmissionEvaluator:
    return ScreenplayTaskAdmissionEvaluator(SqliteScreenplayAgentQuery(db))


class _Db:
    def __init__(self, rows):
        self.rows = []
        self.episode_rows = []
        for row in rows:
            current = dict(row)
            content = json.loads(str(current.get("content_json") or "{}"))
            current.update({
                "project_id": "project-1",
                "deliverable_id": f"deliverable-{current['id']}",
                "role": (
                    "sceneList"
                    if str(current.get("kind") or "") == "scene_list"
                    else "screenplayDraft"
                ),
                "revision_no": int(current.get("version") or 1),
                "payload_json": content,
                "summary_json": {"title": "测试版本"},
                "part_content_text": "",
            })
            if str(current.get("kind") or "") == "scene_list":
                scenes = content.get("scenes", [])
                grouped = {}
                for scene in scenes:
                    episode = int(scene.get("episodeNumber") or 1)
                    grouped.setdefault(episode, []).append(scene)
                for episode, episode_scenes in grouped.items():
                    self.episode_rows.append({
                        "part_key": str(episode),
                        "position": episode,
                        "payload_json": {
                            "episodeNumber": episode,
                            "scenes": episode_scenes,
                        },
                        "content_text": "",
                    })
            self.rows.append(current)

    async def fetch_one(self, sql, params):
        if "FROM screenplay_revisions AS r" in sql:
            revision_id = str(params[0])
            row = next(
                (item for item in self.rows if str(item["id"]) == revision_id),
                None,
            )
            if row is None:
                return None
            return {
                **row,
                "document_payload": row["payload_json"],
            }
        assert "FROM screenplay_projects" in sql
        assert params == ["project-1"]
        return {
            "id": "project-1",
            "format": "series",
            "source_snapshot_json": '{"type":"original"}',
        }

    async def fetch_all(self, sql, params):
        if "FROM screenplay_revision_parts" in sql:
            assert params == ["scene-list-1"]
            return self.episode_rows
        if "FROM screenplay_revision_inputs" in sql:
            return []
        assert "FROM screenplay_project_heads AS h" in sql
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
            content="继续创作接下来三集",
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


def _agent_step(
    step_id: str,
    role: str,
    scene_ids: list[str],
    *,
    depends_on: tuple[str, ...] = (),
) -> TaskStep:
    return TaskStep(
        id=step_id,
        title=step_id,
        type=(
            StepType.WRITE
            if role == "screenplay_writer"
            else StepType.REVIEW
        ),
        executor=StepExecutor.AGENT,
        risk_level=ToolRiskLevel.WRITE,
        agent_role=role,
        assignment={"sceneIds": scene_ids},
        depends_on=depends_on,
    )


def _draft_plan(
    scene_groups: list[list[str]],
    *,
    scope="all_remaining",
    count=None,
    with_reviewers=True,
) -> TaskPlan:
    writers = tuple(
        _agent_step(
            f"write-{index}",
            "screenplay_writer",
            scene_ids,
        )
        for index, scene_ids in enumerate(scene_groups, start=1)
    )
    if with_reviewers:
        reviewers = tuple(
            _agent_step(
                f"review-{index}",
                "screenplay_reviewer",
                scene_ids,
                depends_on=(f"write-{index}",),
            )
            for index, scene_ids in enumerate(scene_groups, start=1)
        )
        leaves = tuple(step.id for step in reviewers)
    else:
        reviewers = ()
        leaves = tuple(step.id for step in writers)
    target = {
        "domainAction": "generate_scene_drafts",
        "scope": scope,
    }
    if count is not None:
        target["count"] = count
    return TaskPlan(
        title="并行创作并审校",
        task_spec=TaskSpec(
            goal="完成指定剧本正文",
            operation="write",
            target=target,
        ),
        steps=(
            *writers,
            *reviewers,
            TaskStep(
                id="submit",
                title="核验并生成可应用提案",
                type=StepType.WRITE,
                executor=StepExecutor.TOOL,
                risk_level=ToolRiskLevel.WRITE,
                suggested_tools=("proposeSceneDraft",),
                depends_on=leaves,
            ),
        ),
    )


def _inline_plan(*, scope="next_scene") -> TaskPlan:
    return TaskPlan(
        title="创作下一场",
        task_spec=TaskSpec(
            goal="完成下一场正文",
            operation="write",
            target={
                "domainAction": "generate_scene_drafts",
                "scope": scope,
            },
        ),
        steps=(TaskStep(
            id="draft",
            title="创作并提交下一场",
            type=StepType.WRITE,
            executor=StepExecutor.TOOL,
            risk_level=ToolRiskLevel.WRITE,
            suggested_tools=("proposeSceneDraft",),
        ),),
    )


@pytest.mark.asyncio
async def test_three_episode_scope_becomes_host_owned_durable_workflow():
    scenes = [
        {
            "id": f"s{episode:02d}-01",
            "heading": f"第 {episode} 集",
            "episodeNumber": episode,
        }
        for episode in range(5, 8)
    ]
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({"scenes": scenes}),
    }])
    plan = _draft_plan([[scene["id"]] for scene in scenes])

    decision = await _evaluator(db).evaluate(
        _request(draft_scene_count=3),
        plan,
    )

    assert decision.mode is ExecutionMode.DURABLE
    assert decision.covered_step_ids == tuple(step.id for step in plan.steps)
    assert decision.estimated_units == 3
    assert decision.estimated_model_calls == 7
    assert decision.metadata["minimumModelCalls"] == 4
    assert decision.metadata["plannedModelCalls"] == 7
    assert decision.metadata["maxParallelism"] == 3
    assert [
        unit["id"] for unit in _recipe_units(decision)
    ] == [
        "write_s05-01",
        "write_s06-01",
        "write_s07-01",
        "review_draft_continuity",
        "revise_s05-01",
        "revise_s06-01",
        "revise_s07-01",
        "propose_draft",
    ]
    assert [
        unit["agentRole"]
        for unit in _recipe_units(decision)
        if unit.get("agentRole")
    ] == [
        "screenplay_writer",
        "screenplay_writer",
        "screenplay_writer",
        "screenplay_reviewer",
        "screenplay_rewriter",
        "screenplay_rewriter",
        "screenplay_rewriter",
    ]


@pytest.mark.asyncio
async def test_multi_scene_plan_without_agent_steps_uses_host_workflow():
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({
            "scenes": [{"id": "s01"}, {"id": "s02"}],
        }),
    }])

    decision = await _evaluator(db).evaluate(
        _request(draft_scene_count=2),
        _inline_plan(scope="all_remaining"),
    )

    assert decision.mode is ExecutionMode.DURABLE
    assert decision.reason_code == "screenplay_draft_requires_multiple_runs"
    assert decision.metadata["targetSceneIds"] == ["s01", "s02"]
    assert decision.metadata["minimumModelCalls"] == 3
    assert decision.metadata["plannedModelCalls"] == 5


@pytest.mark.asyncio
async def test_model_authored_agent_graph_cannot_change_host_workflow():
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({
            "scenes": [{"id": "s01"}, {"id": "s02"}],
        }),
    }])
    plan = _draft_plan([["s01"]])

    decision = await _evaluator(db).evaluate(
        _request(draft_scene_count=2),
        plan,
    )

    assert decision.mode is ExecutionMode.DURABLE
    assert decision.metadata["targetSceneIds"] == ["s01", "s02"]
    assert [
        unit["id"] for unit in _recipe_units(decision)
    ] == [
        "write_s01",
        "write_s02",
        "review_draft_continuity",
        "revise_s01",
        "revise_s02",
        "propose_draft",
    ]


@pytest.mark.asyncio
async def test_button_scope_binds_authoritative_range_and_host_topology():
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({
            "scenes": [
                {"id": "s01", "episodeNumber": 1},
                {"id": "s02", "episodeNumber": 1},
                {"id": "s03", "episodeNumber": 2},
            ],
        }),
    }])
    plan = _draft_plan([["s01", "s02"]], scope="all_remaining")

    decision = await _evaluator(db).evaluate(
        _request(draft_scope="next_episode"),
        plan,
    )

    assert decision.mode is ExecutionMode.DURABLE
    assert decision.metadata["targetSceneIds"] == ["s01", "s02"]
    assert [
        unit["id"] for unit in _recipe_units(decision)
    ] == [
        "write_s01",
        "write_s02",
        "review_draft_continuity",
        "revise_s01",
        "revise_s02",
        "propose_draft",
    ]


@pytest.mark.asyncio
async def test_button_scope_ignores_model_assignment_outside_bound_range():
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({
            "scenes": [
                {"id": "s01", "episodeNumber": 1},
                {"id": "s02", "episodeNumber": 1},
                {"id": "s03", "episodeNumber": 2},
            ],
        }),
    }])
    plan = _draft_plan([["s01", "s02", "s03"]])

    decision = await _evaluator(db).evaluate(
        _request(draft_scope="next_episode"),
        plan,
    )

    assert decision.mode is ExecutionMode.DURABLE
    assert decision.metadata["targetSceneIds"] == ["s01", "s02"]
    assert all(
        "s03" not in unit.get("sceneIds", [])
        for unit in _recipe_units(decision)
    )


@pytest.mark.asyncio
async def test_next_three_episode_scope_ignores_planner_count_but_validates_assignments():
    scenes = [
        {
            "id": f"s{episode:02d}",
            "heading": f"第 {episode} 集",
            "episodeNumber": episode,
        }
        for episode in range(1, 6)
    ]
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({"scenes": scenes}),
    }])
    plan = _draft_plan(
        [["s01"], ["s02"], ["s03"]],
        scope="next_episodes",
        count=5,
        with_reviewers=False,
    )

    decision = await _evaluator(db).evaluate(
        _request(draft_scope="next_3_episodes"),
        plan,
    )

    assert decision.mode is ExecutionMode.DURABLE
    assert decision.metadata["targetSceneIds"] == ["s01", "s02", "s03"]
    assert decision.metadata["maxParallelism"] == 3


@pytest.mark.asyncio
async def test_custom_episode_scope_binds_the_requested_contiguous_range():
    scenes = [
        {
            "id": f"s{episode:02d}",
            "heading": f"第 {episode} 集",
            "episodeNumber": episode,
        }
        for episode in range(1, 7)
    ]
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({"scenes": scenes}),
    }])
    plan = _draft_plan(
        [["s01", "s02"], ["s03"], ["s04"]],
        scope="next_episodes",
        count=4,
        with_reviewers=False,
    )

    decision = await _evaluator(db).evaluate(
        _request(draft_scope="next_4_episodes"),
        plan,
    )

    assert decision.mode is ExecutionMode.DURABLE
    assert decision.metadata["targetSceneIds"] == [
        "s01", "s02", "s03", "s04",
    ]
    assert decision.metadata["maxParallelism"] == 3


@pytest.mark.asyncio
async def test_next_three_episodes_resolve_after_episode_native_migration():
    scenes = [
        {
            "id": f"s{episode:02d}-01",
            "heading": f"第 {episode} 集",
            "episodeNumber": episode,
        }
        for episode in range(1, 11)
    ]
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({"scenes": scenes}),
    }, {
        "id": "draft-accepted",
        "kind": "scene_draft",
        "version": 3,
        "content_json": json.dumps({
            "schemaVersion": 2,
            "documentKind": "scene_draft_manifest",
            "completedSceneIds": [
                f"s{episode:02d}-01" for episode in range(1, 8)
            ],
        }),
    }])
    plan = _draft_plan(
        [[f"s{episode:02d}-01"] for episode in range(8, 11)],
        scope="next_episodes",
        count=3,
        with_reviewers=False,
    )

    decision = await _evaluator(db).evaluate(
        _request(draft_scope="next_3_episodes"),
        plan,
    )

    assert decision.mode is ExecutionMode.DURABLE
    assert decision.metadata["targetSceneIds"] == [
        "s08-01", "s09-01", "s10-01",
    ]
    assert decision.metadata["maxParallelism"] == 3


@pytest.mark.asyncio
async def test_single_scene_tool_plan_remains_inline():
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({"scenes": [{"id": "s01"}]}),
    }])

    decision = await _evaluator(db).evaluate(
        _request(),
        _inline_plan(),
    )

    assert decision.mode is ExecutionMode.INLINE


@pytest.mark.asyncio
async def test_unresolvable_draft_scope_never_falls_back_to_inline():
    db = _Db([{
        "id": "scene-list-1",
        "kind": "scene_list",
        "version": 1,
        "content_json": json.dumps({"scenes": [{"id": "s01"}]}),
    }])

    decision = await _evaluator(db).evaluate(
        _request(),
        _inline_plan(scope="explicit_scene_ids"),
    )

    assert decision.mode is ExecutionMode.REJECT
    assert decision.reason_code == "screenplay_draft_scope_unresolvable"


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
            "source_snapshot_json": '{"type":"book"}',
        }

    async def fetch_all(self, sql, params):
        if "FROM screenplay_revision_inputs" in sql:
            return []
        assert "FROM screenplay_project_heads AS h" in sql
        assert params == ["project-1"]
        return [{
            "id": "brief-accepted",
            "project_id": "project-1",
            "deliverable_id": "deliverable-brief",
            "role": "creativeBrief",
            "revision_no": 2,
            "payload_json": {},
            "summary_json": {"title": "创作简报"},
            "part_content_text": "",
        }]


def _stage_plan(tool: str) -> TaskPlan:
    return TaskPlan(
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
            suggested_tools=(tool,),
        ),),
    )


@pytest.mark.asyncio
async def test_planned_stage_deliverable_executes_in_primary_agent_run():
    decision = await _evaluator(_StageDb()).evaluate(
        _request(stage="structure", task_intent="stage_deliverable"),
        _stage_plan("finalizeScreenplayStructureProposal"),
    )

    assert decision.mode is ExecutionMode.INLINE
    assert decision.metadata["deliverableKind"] == "episode_outline"
    assert decision.metadata["acceptedInputIds"] == {
        "creative_brief": "brief-accepted",
    }


@pytest.mark.asyncio
async def test_stage_admission_rejects_wrong_completion_capability():
    decision = await _evaluator(_StageDb()).evaluate(
        _request(stage="structure", task_intent="stage_deliverable"),
        _stage_plan("finalizeCreativeBriefProposal"),
    )

    assert decision.mode is ExecutionMode.REJECT
    assert (
        decision.reason_code
        == "screenplay_stage_planner_capability_invalid"
    )
