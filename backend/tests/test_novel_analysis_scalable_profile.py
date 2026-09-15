from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from hashlib import sha256

import pytest

from agents.novel_analysis.domain import (
    NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
)
from agents.novel_analysis.coverage_execution import (
    ScalableCoverageError,
    ScalableCoverageUnitExecutor,
)
from agents.novel_analysis.child_submission import (
    SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
)
from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.composition import (
    create_isolated_scalable_novel_analysis_composition,
)
from agents.novel_analysis.map_execution import (
    READ_NOVEL_SOURCE_SLICE,
    SCALABLE_MAP_SCOPE_STATE_KEY,
)
from agents.novel_analysis.map_execution import (
    PurrAScalableMapChildRunner,
    ScalableMapUnitExecutor,
)
from agents.novel_analysis.reduce_execution import (
    PurrAScalableReduceChildRunner,
    READ_NOVEL_ANALYSIS_REDUCE_INPUTS,
    ScalableReduceExecutionError,
    ScalableReduceOutputError,
    ScalableReduceUnitExecutor,
)
from agents.novel_analysis.review_execution import (
    PurrAScalableReviewChildRunner,
    READ_NOVEL_ANALYSIS_REVIEW_INPUT,
    ScalableReviewExecutionError,
    ScalableReviewOutputError,
    ScalableReviewUnitExecutor,
)
from agents.novel_analysis.synthesize_execution import (
    PurrAScalableSynthesisChildRunner,
    READ_NOVEL_ANALYSIS_SYNTHESIS_INPUTS,
    ScalableSynthesisExecutionError,
    ScalableSynthesisOutputError,
    ScalableSynthesisUnitExecutor,
)
from agents.novel_analysis.skill_creation import READ_NOVEL_ANALYSIS_SKILL_INPUT
from agents.novel_analysis.planner_contract import (
    AnalysisPass,
    ScalableAnalysisPlan,
)
from agents.novel_analysis.scalable_profile import (
    ScalableAnalysisExecutionStateFactory,
    build_scalable_novel_analysis_profile,
    validate_scalable_analysis_plan,
)
from agents.novel_analysis.scalable_executor import (
    ScalableNovelAnalysisUnitExecutor,
)
from agents.novel_analysis.stage_output import NovelAnalysisStageOutput
from database.connection import DatabaseConnection
from application.agent_run_service import AgentRunService
from purra.api import AgentCoreRunOptions
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRunResult,
    DomainContext,
    ExecutionPlan,
    MessageRole,
    ModelRequest,
    PlanningKind,
    PlanningCapabilities,
    PlanningConstraints,
    PlanningMode,
    PlanningResult,
    StepExecutor,
    StepType,
    TaskSpec,
    TaskStep,
    RunBinding,
    RunStatus,
    WorkPlan,
    WorkStep,
)
from purra.model_protocol import generic_capability_snapshot
from purra.planner import build_planner_messages
from tests.support.planning_stream import route_planning_stream


def test_planner_contract_ignores_non_authoritative_explanatory_fields():
    raw = {
        "schemaVersion": 1,
        "passes": [{
            "id": "story",
            "dimensions": ["plot"],
            "reason": "聚合主线",
        }],
        "reduceFanIn": 4,
        "synthesisSections": ["故事大纲"],
        "qualityChecks": ["覆盖整书"],
        "explanation": "分析计划说明",
    }
    assert ScalableAnalysisPlan.from_mapping(raw).to_mapping() == {
        "schemaVersion": 1,
        "passes": [{"id": "story", "dimensions": ["plot"]}],
        "reduceFanIn": 4,
        "synthesisSections": ["故事大纲"],
        "qualityChecks": ["覆盖整书"],
    }


@pytest.fixture
async def db(tmp_path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        yield connection
    finally:
        await connection.close()


async def _seed(db, *, repeats=100):
    text = "潮水漫过旧城。" * repeats
    digest = sha256(text.encode()).hexdigest()
    await db.execute(
        "INSERT INTO novel_source_works (id, title, source_type) "
        "VALUES ('work', '来源', 'external_text')"
    )
    await db.execute(
        "INSERT INTO novel_source_revisions "
        "(id, work_id, version_no, content_digest, parser_version, byte_count, character_count) "
        "VALUES ('revision', 'work', 1, 'revision-digest', 1, ?, ?)",
        [len(text.encode()), len(text)],
    )
    await db.execute(
        "INSERT INTO novel_source_sections "
        "(id, revision_id, ordinal, title, text_content, content_digest, byte_count, character_count) "
        "VALUES ('section', 'revision', 0, '第一章', ?, ?, ?, ?)",
        [text, digest, len(text.encode()), len(text)],
    )
    return text, digest


def _request(digest, *, metadata=None):
    return AgentRunRequest(
        messages=(AgentMessage(MessageRole.USER, "分析整部作品"),),
        model=ModelRequest(
            provider="test",
            model="test-model",
            capability_snapshot=replace(
                generic_capability_snapshot(),
                profile_id="test:model",
                context_window_tokens=32_768,
                max_generation_tokens=4_096,
            ),
        ),
        context_window=32_768,
        domain_context=DomainContext(
            namespace=NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
            payload={
                "sourceRevisionId": "revision",
                "commandId": "command",
            },
        ),
        metadata=metadata or {},
    )


def _semantic_plan():
    return ScalableAnalysisPlan(
        passes=(AnalysisPass("story", ("characters", "plot")),),
        reduce_fan_in=2,
        synthesis_sections=("人物", "情节"),
        quality_checks=("覆盖全部分片",),
    )


def _planning_result(target):
    return PlanningResult(
        kind=PlanningKind.PLANNED,
        work_plan=WorkPlan(
            title="整书分析",
            task_spec=TaskSpec(
                goal="形成整书分析",
                operation="analyze",
                target=target,
            ),
            steps=(WorkStep(
                id="plan-whole-work",
                title="规划并分析整部作品",
                type=StepType.ANALYZE,
                executor=StepExecutor.MODEL,
            ),),
        ),
    )


@pytest.mark.asyncio
async def test_root_prepares_manifest_for_planner_without_exposing_source_tool(db):
    _, digest = await _seed(db)
    profile = build_scalable_novel_analysis_profile(db=db)
    prepared = await profile.prepare_request(_request(digest))

    assert prepared.planning_mode is PlanningMode.PLANNED
    assert prepared.tools_enabled is True
    assert prepared.metadata["sliceManifest"]["sliceCount"] >= 1
    assert profile.adapter.tool_catalog.enabled_names(prepared) == frozenset()
    bundle = await profile.adapter.context_provider.build_planning_context(
        prepared, _Budget()
    )
    assert "潮水" not in bundle.blocks[0].content
    planner_context = json.loads(bundle.blocks[0].content)
    assert planner_context["sourceScale"]["sliceCount"] >= 1
    assert "sliceManifest" not in planner_context
    assert "sourceTokenLimit" not in bundle.blocks[0].content

    constraints = profile.adapter.planning_policy.planning_constraints(
        prepared,
        PlanningCapabilities(
            available_tool_names=frozenset({"delegateToAgents", "listAgents"}),
            constraints=PlanningConstraints(),
        ),
    )
    assert constraints.planning_excluded_tool_names == frozenset({
        "delegateToAgents", "listAgents",
    })
    assert constraints.min_initial_visible_steps == 1
    planner_messages = build_planner_messages(
        prepared,
        replace(
            PlanningCapabilities(
                available_tool_names=frozenset({
                    "delegateToAgents", "listAgents",
                }),
                constraints=PlanningConstraints(),
            ),
            constraints=constraints,
            planning_context_blocks=bundle.blocks,
        ),
        profile.adapter.planner_limits,
    )
    assert len(planner_messages[0].content) < 4_000
    assert "Every tool step" not in planner_messages[0].content
    planner_payload = json.loads(planner_messages[1].content)
    assert planner_payload["availableTools"] == []
    assert "sourceTokenLimit" not in planner_messages[1].content


@pytest.mark.asyncio
async def test_map_child_is_reactive_and_receives_only_bound_slice_tool(db):
    _, digest = await _seed(db)
    profile = build_scalable_novel_analysis_profile(db=db)
    request = _request(digest, metadata={
        "parentRunId": "root-run",
        "agentId": "child-agent",
    })
    child = await profile.prepare_request(request)

    assert child.planning_mode is PlanningMode.REACTIVE
    assert "response_format" not in child.model.options
    assert profile.adapter.tool_catalog.enabled_names(child) == frozenset({
        READ_NOVEL_SOURCE_SLICE,
        READ_NOVEL_ANALYSIS_REDUCE_INPUTS,
        READ_NOVEL_ANALYSIS_SYNTHESIS_INPUTS,
        READ_NOVEL_ANALYSIS_SKILL_INPUT,
        READ_NOVEL_ANALYSIS_REVIEW_INPUT,
        SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
    })
    with pytest.raises(ValueError, match="cannot create a durable"):
        await profile.evaluate(child, ExecutionPlan(
            title="invalid",
            task_spec=TaskSpec(goal="invalid", operation="analyze"),
            steps=(TaskStep(
                id="invalid",
                title="invalid",
                type=StepType.ANALYZE,
                executor=StepExecutor.MODEL,
            ),),
        ))


def test_map_child_projects_structured_input_into_operation_display_state():
    scope = {
        "sourceRevisionId": "revision",
        "sourceRevisionDigest": "revision-digest",
        "sliceId": "slice-2",
        "slicePosition": 1,
        "tokenCount": 100,
        "ranges": [{
            "sectionId": "section",
            "sectionOrdinal": 2,
            "startCharacter": 0,
            "endCharacter": 10,
            "contentDigest": "section-digest",
        }],
    }
    request = replace(
        _request("unused", metadata={
            "parentRunId": "root-run",
            "agentId": "child-agent",
        }),
        messages=(AgentMessage(
            MessageRole.USER,
            "分析当前分片\n\nInput:\n" + json.dumps({
                "mapSliceScope": scope,
            }),
        ),),
    )

    state = ScalableAnalysisExecutionStateFactory().create(request)

    assert state.domain[SCALABLE_MAP_SCOPE_STATE_KEY]["slicePosition"] == 1
    assert state.domain[SCALABLE_MAP_SCOPE_STATE_KEY]["ranges"][0][
        "sectionOrdinal"
    ] == 2


@pytest.mark.asyncio
async def test_root_admission_compiles_semantic_plan_into_scalable_recipe(db):
    _, digest = await _seed(db)
    profile = build_scalable_novel_analysis_profile(db=db)
    request = await profile.prepare_request(_request(digest))
    semantic = _semantic_plan()
    planning = _planning_result(semantic.to_mapping())

    assert validate_scalable_analysis_plan(request, planning) is None
    plan = ExecutionPlan(
        title=planning.work_plan.title,
        task_spec=planning.work_plan.task_spec,
        steps=(TaskStep(
            id="plan-whole-work",
            title="规划并分析整部作品",
            type=StepType.ANALYZE,
            executor=StepExecutor.MODEL,
        ),),
        work_step_ids=("plan-whole-work",),
    )
    decision = await profile.evaluate(request, plan)

    assert decision.execution_recipe.kind == "novel_analysis.scalable.v2"
    assert decision.metadata["recipeVersion"] == 2
    assert decision.metadata["sliceManifest"]["sliceCount"] >= 1
    assert any(step.kind == "map" for step in decision.execution_recipe.steps)
    assert decision.covered_step_ids == ("plan-whole-work",)

    four_steps = replace(planning, work_plan=replace(
        planning.work_plan,
        steps=tuple(
            replace(planning.work_plan.steps[0], id=f"semantic-{index}")
            for index in range(4)
        ),
    ))
    assert "one to three semantic" in validate_scalable_analysis_plan(
        request, four_steps
    )


@pytest.mark.asyncio
async def test_scalable_profile_has_an_isolated_composition_without_v1_fallback(db):
    _, digest = await _seed(db)
    composition = create_isolated_scalable_novel_analysis_composition(db)
    try:
        prepared = await composition.prepare_request(_request(digest))
        assert prepared.metadata["sliceManifest"]["sliceCount"] >= 1
        assert composition.agent_profile_ids == (
            "novel_analysis.scalable.v2",
        )
    finally:
        await composition.shutdown()


class _Budget:
    def allocation_for(self, name):
        del name
        return 8_192


@pytest.mark.asyncio
async def test_simulated_provider_runs_real_map_and_multilevel_reduce_dag(
    db, monkeypatch
):
    await _seed(db, repeats=6_000)
    semantic = _semantic_plan().to_mapping()
    tool_calls = []
    reduce_reads = []

    async def planner(_key, _messages, options, _provider, signal=None):
        assert signal is not None
        return {
            "applied_generation_limit": options.get("max_tokens"),
            "message": {"role": "assistant", "content": json.dumps({
                "needsTodos": True,
                "title": "整书分析",
                "goal": "形成整书分析",
                "taskSpec": {
                    "goal": "形成整书分析",
                    "target": semantic,
                    "operation": "analyze",
                    "constraints": [],
                    "preserve": [],
                },
                "todos": [{
                    "id": "plan-map",
                    "title": "规划分片分析",
                    "type": "analyze",
                    "executor": "model",
                    "expectedTools": [],
                    "riskLevel": "read",
                }, {
                    "id": "plan-reduce",
                    "title": "规划分层归并",
                    "type": "analyze",
                    "executor": "model",
                    "expectedTools": [],
                    "dependsOn": ["plan-map"],
                    "riskLevel": "read",
                }, {
                    "id": "plan-synthesis",
                    "title": "规划整书综合",
                    "type": "review",
                    "executor": "model",
                    "expectedTools": [],
                    "dependsOn": ["plan-reduce"],
                    "riskLevel": "read",
                }],
            }, ensure_ascii=False)},
            "model": "test-model",
            "finish_reason": "stop",
        }

    async def runtime_stream(_key, messages, options, _provider, signal=None):
        assert signal is not None
        has_tool_result = any(item.get("role") == "tool" for item in messages)

        async def stream():
            if not has_tool_result:
                # The source body must enter the model only as a Tool result,
                # never through the Child instruction, objective, or input.
                assert "潮水漫过旧城" not in json.dumps(
                    messages, ensure_ascii=False
                )
                serialized = json.dumps(messages, ensure_ascii=False)
                tool_name = (
                    READ_NOVEL_ANALYSIS_REVIEW_INPUT
                    if READ_NOVEL_ANALYSIS_REVIEW_INPUT in serialized
                    else READ_NOVEL_ANALYSIS_SKILL_INPUT
                    if READ_NOVEL_ANALYSIS_SKILL_INPUT in serialized
                    else
                    READ_NOVEL_ANALYSIS_SYNTHESIS_INPUTS
                    if READ_NOVEL_ANALYSIS_SYNTHESIS_INPUTS in serialized
                    else
                    READ_NOVEL_ANALYSIS_REDUCE_INPUTS
                    if READ_NOVEL_ANALYSIS_REDUCE_INPUTS in serialized
                    else READ_NOVEL_SOURCE_SLICE
                )
                tool_calls.append(tool_name)
                yield {
                    "choices": [{"delta": {"tool_calls": [{
                        "index": 0,
                        "id": f"read-bound-{tool_name}",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": "{}",
                        },
                    }]}, "finish_reason": "tool_calls"}],
                }
                return
            tool_payloads = [
                json.loads(item["content"])
                for item in messages if item.get("role") == "tool"
            ]
            if any("artifactRef" in item for item in tool_payloads):
                yield {
                    "choices": [{
                        "delta": {
                            "content": "}</system-reminder>本轮处理已结束"
                        },
                        "finish_reason": "stop",
                    }],
                }
                return
            tool_payload = tool_payloads[-1]
            if "coverage" in tool_payload:
                assert tool_payload["coverage"]["covered"] is True
                content = {
                    "summaryMarkdown": "这是审核后可直接展示的整书分析总结。",
                    "facts": [
                        {"id": "fact-residents", "claimNature": "summary", "factKind": "character_summary", "subjectKey": "旧城居民", "predicate": "人物归纳", "value": {"name": "旧城居民", "tags": "群像", "profile_md": "共同承受潮水压力"}, "lifecycleStatus": "active"},
                        {"id": "fact-background", "claimNature": "summary", "factKind": "background", "subjectKey": "故事背景", "predicate": "背景归纳", "value": {"content": "潮水反复侵袭旧城。"}, "lifecycleStatus": "active"},
                    ],
                    "craftCards": [{"id": "craft-pressure", "cardKind": "technique", "title": "持续压力", "bodyMarkdown": "用环境压力推动冲突。"}],
                }
            elif "storyOverview" in tool_payload:
                content = {
                    "status": "generated",
                    "files": [{
                        "path": "SKILL.md",
                            "content": "---\nname: 持续压力\ndescription: 用持续环境压力推动人物选择。\nmetadata:\n  retrieval:\n    intents: [维持冲突]\n    contexts: [环境持续影响人物选择]\n    objectives: [保持叙事压力]\n    keywords: [持续压力]\n---\n\n在需要维持冲突时，让环境变化持续影响人物的选择。",
                    }, {
                        "path": "references/变化方式.md",
                        "content": "压力应随人物选择改变，而不是机械重复。",
                    }],
                    "evidenceRefs": ["craft-pressure"],
                    "scopeNotes": [],
                }
            elif "sliceId" in tool_payload:
                assert tool_payload["sliceId"].startswith("source-slice-")
                assert "潮水漫过旧城" in tool_payload["items"][0]["text"]
                content = {"findings": [{
                    "dimension": "characters",
                    "subject": "旧城居民",
                    "analysis": "潮水构成持续压力。",
                }]}
            elif "sections" not in tool_payload:
                assert len(tool_payload["inputs"]) >= 2
                reduce_reads.append(tuple(
                    item["unitId"] for item in tool_payload["inputs"]
                ))
                content = {
                    "findings": [{
                        "dimension": "characters",
                        "subject": "旧城居民",
                        "analysis": "跨分片归并后仍受潮水持续影响。",
                    }],
                    "conflicts": [],
                }
            else:
                assert tool_payload["sections"] == ["人物", "情节"]
                content = {
                    "summaryMarkdown": "这是一份面向整部作品的分析总结。",
                    "facts": [
                        {"id": "fact-residents", "claimNature": "summary", "factKind": "character_summary", "subjectKey": "旧城居民", "predicate": "人物归纳", "value": {"name": "旧城居民", "tags": "群像", "profile_md": "共同承受潮水压力"}, "lifecycleStatus": "active"},
                        {"id": "fact-background", "claimNature": "summary", "factKind": "background", "subjectKey": "故事背景", "predicate": "背景归纳", "value": {"content": "潮水反复侵袭旧城。"}, "lifecycleStatus": "active"},
                    ],
                    "craftCards": [{"id": "craft-pressure", "cardKind": "technique", "title": "持续压力", "bodyMarkdown": "用环境压力推动冲突。"}],
                }
            yield {
                "choices": [{"delta": {"tool_calls": [{
                    "index": 0,
                    "id": "submit-analysis-result",
                    "type": "function",
                    "function": {
                        "name": SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
                        "arguments": json.dumps(
                            {"result": content}, ensure_ascii=False
                        ),
                    },
                }]}, "finish_reason": "tool_calls"}],
            }

        return {
            "applied_generation_limit": options.get("max_tokens"),
            "stream": stream(),
            "model": "test-model",
        }

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream", planner
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        route_planning_stream(planner, runtime_stream),
    )
    composition = create_isolated_scalable_novel_analysis_composition(db)
    request = _request("unused")
    stage_reports = []

    async def report_stage(**kwargs):
        stage_reports.append(kwargs)

    executor = ScalableNovelAnalysisUnitExecutor(
        db,
        model_name="test-model",
        stage_output=NovelAnalysisStageOutput(
            db,
            reporter=report_stage,
            runtime=object(),
        ),
    )
    results = []
    try:
        async for update in AgentRunService(composition).run(
            request=request,
            api_key="fixture-key",
            options=AgentCoreRunOptions(
                turn_id="scalable-map-vertical",
                default_context_window_tokens=32_768,
                binding=RunBinding(
                    namespace=NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
                    aggregate_id="revision",
                    command_id="command",
                ),
            ),
            signal=asyncio.Event(),
            long_task_executor=executor,
        ):
            if isinstance(update, AgentRunResult):
                results.append(update)
    finally:
        await composition.shutdown()

    assert len(results) == 1
    assert results[0].status is RunStatus.DONE
    assert results[0].final_response == "这是审核后可直接展示的整书分析总结。"
    assert tool_calls.count(READ_NOVEL_SOURCE_SLICE) >= 3
    assert tool_calls.count(READ_NOVEL_ANALYSIS_REDUCE_INPUTS) >= 2
    assert tool_calls.count(READ_NOVEL_ANALYSIS_SYNTHESIS_INPUTS) == 1
    assert tool_calls.count(READ_NOVEL_ANALYSIS_SKILL_INPUT) == 1
    assert tool_calls.count(READ_NOVEL_ANALYSIS_REVIEW_INPUT) == 1
    assert all(len(items) == 2 for items in reduce_reads)
    reported_kinds = {
        report["facts"]["stageKind"] for report in stage_reports
    }
    assert {"reduce", "synthesize", "skill", "review"} <= reported_kinds
    assert all(report["context"].run_id == results[0].run_id for report in stage_reports)
    rows = await db.fetch_all(
        "SELECT id, root_run_id, parent_run_id FROM ai_agent_runs "
        "WHERE parent_run_id IS NOT NULL ORDER BY id"
    )
    assert len(rows) == len(tool_calls)
    assert all(row["root_run_id"] == results[0].run_id for row in rows)
    artifacts = await db.fetch_all(
        "SELECT created_by_run_id FROM ai_agent_artifacts "
        "WHERE namespace = 'purrtypos.novel_analysis.v1'"
    )
    artifact_creators = {item["created_by_run_id"] for item in artifacts}
    assert {item["id"] for item in rows}.issubset(artifact_creators)
    assert artifact_creators - {item["id"] for item in rows} == {
        results[0].run_id
    }
    reduce_units = await db.fetch_all(
        "SELECT output_ref, metadata_json FROM ai_agent_long_task_units "
        "WHERE json_extract(metadata_json, '$.unitKind') = 'reduce'"
    )
    final_reduce = max(
        reduce_units,
        key=lambda item: json.loads(item["metadata_json"])["reduceLevel"],
    )
    final_payload = await NovelAnalysisAttemptArtifactStore(db).load_payload(
        final_reduce["output_ref"].removeprefix("novel-analysis://")
    )
    assert len(final_payload["coveredSliceIds"]) == tool_calls.count(
        READ_NOVEL_SOURCE_SLICE
    )
    synthesis_unit = await db.fetch_one(
        "SELECT output_ref FROM ai_agent_long_task_units "
        "WHERE json_extract(metadata_json, '$.unitKind') = 'synthesize'"
    )
    synthesis_payload = await NovelAnalysisAttemptArtifactStore(db).load_payload(
        synthesis_unit["output_ref"].removeprefix("novel-analysis://")
    )
    assert synthesis_payload["summaryMarkdown"] == "这是一份面向整部作品的分析总结。"
    assert synthesis_payload["coveredSliceIds"] == final_payload["coveredSliceIds"]
    coverage_unit = await db.fetch_one(
        "SELECT output_ref FROM ai_agent_long_task_units "
        "WHERE json_extract(metadata_json, '$.unitKind') = 'coverage'"
    )
    coverage_payload = await NovelAnalysisAttemptArtifactStore(db).load_payload(
        coverage_unit["output_ref"].removeprefix("novel-analysis://")
    )
    assert coverage_payload["covered"] is True
    assert coverage_payload["expectedSliceIds"] == final_payload["coveredSliceIds"]
    assert coverage_payload["synthesisArtifactId"] == synthesis_unit[
        "output_ref"
    ].removeprefix("novel-analysis://")
    review_unit = await db.fetch_one(
        "SELECT output_ref FROM ai_agent_long_task_units "
        "WHERE json_extract(metadata_json, '$.unitKind') = 'review'"
    )
    review_payload = await NovelAnalysisAttemptArtifactStore(db).load_payload(
        review_unit["output_ref"].removeprefix("novel-analysis://")
    )
    assert review_payload["summaryMarkdown"] == results[0].final_response
    assert review_payload["coverageArtifactId"] == coverage_unit[
        "output_ref"
    ].removeprefix("novel-analysis://")
