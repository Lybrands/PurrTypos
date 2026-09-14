from __future__ import annotations

from dataclasses import replace
import json

import pytest
import pytest_asyncio

from agents.novel_analysis.composition import (
    create_isolated_novel_analysis_replacement_composition,
)
from agents.novel_analysis.domain import (
    NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS,
    NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
)
from agents.novel_analysis.profile import (
    NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID,
    NovelAnalysisReplacementProfile,
    validate_novel_analysis_replacement_plan,
)
from agents.novel_analysis.follow_up_tools import READ_ANALYSIS_REVIEW_ARTIFACT
from agents.novel_analysis.source_model import (
    NovelAnalysisSourceScopeError,
    SqliteNovelAnalysisSourceRepository,
)
from agents.novel_analysis.source_tools import (
    NOVEL_ANALYSIS_SOURCE_SCOPE_STATE_KEY,
    build_novel_analysis_source_tool_catalog,
)
from agents.novel_analysis.observation_tools import (
    NOVEL_ANALYSIS_OBSERVATIONS_STATE_KEY,
    build_novel_analysis_observation_tool_catalog,
)
from agents.novel_analysis.access_contract import (
    analysis_model_tool_names,
    analysis_read_tool_names,
    analysis_unit_read_guidance,
    validate_analysis_capabilities,
)
from agents.novel_analysis.submission_tool import (
    SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT,
    build_novel_analysis_submission_tool_catalog,
)
from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.recipe import AnalysisUnitKind
from agents.novel_analysis.executor import NovelAnalysisReplacementUnitExecutor
from agents.novel_analysis.domain import NovelAnalysisRequestScope
from agents.shared.implementation import AgentKind, replacement_implementation
from agents.shared.implementation_registry import AgentRolloutPolicy
from agents.shared.composition_routing import RUNTIME_PROFILE_METADATA_KEY
from application.composition_factory import create_versioned_agent_composition
from database.connection import DatabaseConnection
from infrastructure.persistence.run_store import create_run
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    ExecutionPlan,
    ExecutionState,
    MessageRole,
    ModelRequest,
    PlanningKind,
    PlanningMode,
    PlanningResult,
    StepExecutor,
    StepType,
    TaskSpec,
    TaskStep,
    WorkPlan,
    WorkStep,
)
from purra.model_protocol import generic_capability_snapshot
from purra.task_admission import ExecutionMode
from purra.long_tasks import RecipeLongTaskDispatcher
from purra.tools.contracts import inspect_tool_contract


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


async def _seed_source(db) -> None:
    await db.execute(
        "INSERT INTO novel_source_works (id, title, source_type) "
        "VALUES ('work-1', '测试小说', 'text')"
    )
    await db.execute(
        "INSERT INTO novel_source_revisions "
        "(id, work_id, version_no, content_digest, parser_version, byte_count, character_count) "
        "VALUES ('revision-1', 'work-1', 1, 'revision-digest', 1, 100, 100)"
    )
    await db.execute(
        "INSERT INTO novel_source_sections "
        "(id, revision_id, ordinal, title, text_content, content_digest) "
        "VALUES ('section-1', 'revision-1', 0, '第一章', ?, 'digest-section-1')",
        ["潮水漫过旧城。" + "风" * 93],
    )


def _request(**payload_overrides) -> AgentRunRequest:
    payload = {
        "sourceRevisionId": "revision-1",
        "commandId": "command-1",
        "segments": [{
            "id": "segment-1",
            "sectionId": "section-1",
            "sectionDigest": "digest-section-1",
            "sectionOrdinal": 0,
            "startCharacter": 0,
            "endCharacter": 100,
        }],
        **payload_overrides,
    }
    return AgentRunRequest(
        messages=(AgentMessage(MessageRole.USER, "分析这部小说"),),
        model=ModelRequest(
            provider="test",
            model="test-model",
            capability_snapshot=replace(
                generic_capability_snapshot(),
                profile_id="test:model",
            ),
        ),
        domain_context=DomainContext(
            namespace=NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
            payload=payload,
        ),
    )


def _planning_result(
    *, target=None, executor=StepExecutor.MODEL, step_type=StepType.ANALYZE
):
    return PlanningResult(
        kind=PlanningKind.PLANNED,
        work_plan=WorkPlan(
            title="分析并复核",
            task_spec=TaskSpec(
                goal="形成可审核的分析",
                operation="analyze",
                target={} if target is None else target,
            ),
            steps=(WorkStep(
                id="analyze-story",
                title="分析故事",
                type=step_type,
                executor=executor,
            ),),
        ),
    )


def _execution_plan(step_id="analyze-story"):
    return ExecutionPlan(
        title="分析并复核",
        task_spec=TaskSpec(
            goal="形成可审核的分析",
            operation="analyze",
        ),
        steps=(TaskStep(
            id=step_id,
            title="分析故事",
            type=StepType.ANALYZE,
            executor=StepExecutor.MODEL,
        ),),
    )


@pytest.mark.asyncio
async def test_profile_prepares_canonical_scope_and_binds_recipe_version(temp_db) -> None:
    await _seed_source(temp_db)
    profile = NovelAnalysisReplacementProfile(temp_db)

    prepared = await profile.prepare_request(_request())
    state = profile.adapter.execution_state_factory.create(prepared)
    binding = profile.run_binding_attributes(prepared)

    assert prepared.planning_mode.value == "planned"
    assert profile.adapter.runtime_limits.max_model_rounds == 5
    assert state.domain["sourceRevisionId"] == "revision-1"
    assert state.domain["sectionIds"] == ["section-1"]
    assert binding["novelAnalysisBinding"]["segments"][0]["endCharacter"] == 100
    assert binding["agentImplementation"] == replacement_implementation(
        AgentKind.NOVEL_ANALYSIS,
        recipe_version=1,
    ).to_mapping()


@pytest.mark.asyncio
async def test_profile_prepares_reactive_follow_up_with_only_bounded_read_tools(
    temp_db,
) -> None:
    await _seed_source(temp_db)
    profile = NovelAnalysisReplacementProfile(temp_db)
    request = replace(_request(), metadata={
        "interactionKind": "follow_up",
        "analysisArtifactId": "review-artifact",
    })

    prepared = await profile.prepare_request(request)
    state = profile.adapter.execution_state_factory.create(prepared)
    binding = profile.run_binding_attributes(prepared)

    assert prepared.planning_mode is PlanningMode.REACTIVE
    assert prepared.tools_enabled is True
    assert profile.adapter.tool_catalog.enabled_names(prepared) == frozenset({
        "listAnalysisSourceSegments",
        "readAnalysisSourceSegment",
        READ_ANALYSIS_REVIEW_ARTIFACT,
    })
    assert state.domain["analysisArtifactId"] == "review-artifact"
    assert binding["interactionKind"] == "follow_up"


@pytest.mark.asyncio
async def test_profile_rejects_legacy_or_extra_domain_fields(temp_db) -> None:
    profile = NovelAnalysisReplacementProfile(temp_db)

    with pytest.raises(ValueError, match="canonical v1 shape"):
        await profile.prepare_request(_request(section_ids=["section-1"]))


def test_planner_can_only_supply_model_presentation_steps() -> None:
    request = _request()

    assert validate_novel_analysis_replacement_plan(
        request,
        _planning_result(),
    ) is None
    assert validate_novel_analysis_replacement_plan(
        request,
        _planning_result(step_type=StepType.WRITE),
    ) is None
    assert "empty target" in validate_novel_analysis_replacement_plan(
        request,
        _planning_result(target={"sourceRevisionId": "other"}),
    )
    assert "model work" in validate_novel_analysis_replacement_plan(
        request,
        _planning_result(executor=StepExecutor.TOOL),
    )


@pytest.mark.asyncio
async def test_submission_tool_exposes_strict_unit_result_shapes(temp_db) -> None:
    registration = build_novel_analysis_submission_tool_catalog(
        temp_db
    ).get(SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT)
    result_schema = registration.schema.parameters["properties"]["result"]

    assert result_schema["additionalProperties"] is False
    assert result_schema["oneOf"] == [
        {"required": ["facts", "observations"]},
        {"required": ["storyOverview"]},
        {"required": ["techniqueResult"]},
    ]
    assert result_schema["properties"]["facts"]["items"]["properties"][
        "factKind"
    ]["enum"] == sorted(NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS)


@pytest.mark.asyncio
async def test_admission_uses_host_scope_and_planner_only_maps_presentation(temp_db) -> None:
    await _seed_source(temp_db)
    profile = NovelAnalysisReplacementProfile(temp_db)
    request = await profile.prepare_request(_request())

    first = await profile.evaluate(request, _execution_plan("first"))
    second = await profile.evaluate(request, _execution_plan("renamed"))

    assert first.mode is ExecutionMode.DURABLE
    assert first.covered_step_ids == ("first",)
    assert first.metadata["sourceRevisionId"] == "revision-1"
    assert first.metadata["recipeDigest"] == second.metadata["recipeDigest"]
    assert [
        (step.id, step.kind, step.depends_on)
        for step in first.execution_recipe.steps
    ] == [
        (step.id, step.kind, step.depends_on)
        for step in second.execution_recipe.steps
    ]
    assert {step.plan_step_id for step in first.execution_recipe.steps} == {
        "first"
    }


@pytest.mark.asyncio
async def test_source_tools_read_only_frozen_segment_as_complete_spans(temp_db) -> None:
    await _seed_source(temp_db)
    scope = NovelAnalysisRequestScope.from_request(_request())
    catalog = build_novel_analysis_source_tool_catalog(temp_db)
    state = ExecutionState(domain={
        NOVEL_ANALYSIS_SOURCE_SCOPE_STATE_KEY: scope.to_mapping(),
    })

    listing = await catalog.get("listAnalysisSourceSegments").handler(state, {})
    result = await catalog.get("readAnalysisSourceSegment").handler(
        state,
        {"segmentId": "segment-1"},
    )

    listing_payload = json.loads(listing.content)
    payload = json.loads(result.content)
    assert listing_payload["total"] == 1
    assert "text" not in listing_payload["items"][0]
    assert "".join(item["text"] for item in payload["sourceSpans"]) == (
        "潮水漫过旧城。" + "风" * 93
    )
    assert payload["sourceSpans"][0]["sourceSpanId"].startswith("S0-")
    assert inspect_tool_contract(catalog.registrations()).is_valid


@pytest.mark.asyncio
async def test_source_scope_digest_drift_fails_closed(temp_db) -> None:
    await _seed_source(temp_db)
    scope = NovelAnalysisRequestScope.from_request(_request())
    repository = SqliteNovelAnalysisSourceRepository(temp_db)
    await temp_db.execute(
        "UPDATE novel_source_sections SET content_digest = 'changed' WHERE id = 'section-1'"
    )

    with pytest.raises(NovelAnalysisSourceScopeError) as caught:
        await repository.read_segment(scope, "segment-1")
    assert caught.value.code == "analysis_source_scope_drift"


@pytest.mark.asyncio
async def test_source_evidence_is_reconstructed_only_from_host_span_handle(
    temp_db,
) -> None:
    await _seed_source(temp_db)
    scope = NovelAnalysisRequestScope.from_request(_request())
    repository = SqliteNovelAnalysisSourceRepository(temp_db)

    evidence = await repository.resolve_source_span(
        scope,
        segment_id="segment-1",
        source_span_id="S0-100",
    )
    assert evidence["text"] == "潮水漫过旧城。" + "风" * 93

    with pytest.raises(NovelAnalysisSourceScopeError) as caught:
        await repository.resolve_source_span(
            scope,
            segment_id="segment-1",
            source_span_id="S1-99",
        )
    assert caught.value.code == "analysis_source_span_invalid"


@pytest.mark.asyncio
async def test_normalize_observations_are_listable_and_readable_with_evidence() -> None:
    catalog = build_novel_analysis_observation_tool_catalog()
    state = ExecutionState(domain={
        NOVEL_ANALYSIS_OBSERVATIONS_STATE_KEY: [{
            "observationId": "observation-1",
            "cardKind": "pacing",
            "title": "潮水倒计时",
            "bodyMarkdown": "通过潮位变化形成倒计时。",
            "evidenceRefs": [{
                "segmentId": "segment-1",
                "sourceSpanId": "S0-100",
            }],
        }],
    })

    listing = json.loads((await catalog.get("listAnalysisObservations").handler(
        state,
        {"offset": 0, "limit": 50},
    )).content)
    detail = json.loads((await catalog.get("readAnalysisObservations").handler(
        state,
        {"observationIds": ["observation-1"]},
    )).content)

    assert listing == {
        "total": 1,
        "items": [{
            "observationId": "observation-1",
            "cardKind": "pacing",
            "title": "潮水倒计时",
            "evidenceCount": 1,
        }],
        "nextOffset": None,
    }
    assert detail["items"][0]["bodyMarkdown"] == "通过潮位变化形成倒计时。"
    assert detail["items"][0]["evidenceRefs"] == [{
        "segmentId": "segment-1",
        "sourceSpanId": "S0-100",
    }]
    assert inspect_tool_contract(catalog.registrations()).is_valid


@pytest.mark.asyncio
async def test_normalize_observation_read_rejects_unbound_id() -> None:
    catalog = build_novel_analysis_observation_tool_catalog()
    state = ExecutionState(domain={NOVEL_ANALYSIS_OBSERVATIONS_STATE_KEY: []})

    result = await catalog.get("readAnalysisObservations").handler(
        state,
        {"observationIds": ["outside"]},
    )

    assert result.error_code == "tool_input_invalid"
    assert json.loads(result.content)["code"] == "tool_input_invalid"


def test_unit_guidance_and_installed_catalog_share_one_capability_contract(
    temp_db,
) -> None:
    profile = NovelAnalysisReplacementProfile(temp_db)

    validate_analysis_capabilities(profile.adapter.tool_catalog)
    assert analysis_read_tool_names(AnalysisUnitKind.EXTRACT) == (
        "listAnalysisSourceSegments",
        "readAnalysisSourceSegment",
    )
    assert analysis_read_tool_names(AnalysisUnitKind.NORMALIZE) == (
        "listAnalysisObservations",
        "readAnalysisObservations",
    )
    for tool_name in analysis_read_tool_names(AnalysisUnitKind.NORMALIZE):
        assert tool_name in analysis_unit_read_guidance(
            AnalysisUnitKind.NORMALIZE
        )


@pytest.mark.asyncio
async def test_unit_kind_enables_only_its_bounded_read_capabilities(temp_db) -> None:
    await _seed_source(temp_db)
    profile = NovelAnalysisReplacementProfile(temp_db)
    observation = {
        "observationId": "observation-1",
        "cardKind": "pacing",
        "title": "潮水倒计时",
        "bodyMarkdown": "通过潮位变化形成倒计时。",
        "evidenceRefs": [{
            "segmentId": "segment-1",
            "sourceSpanId": "S0-100",
        }],
    }
    root = _request()
    extract = _request(unit={"kind": "extract", "observations": []})
    normalize = _request(unit={
        "kind": "normalize",
        "observations": [observation],
    })

    assert profile.adapter.tool_catalog.enabled_names(root) == frozenset()
    assert profile.adapter.tool_catalog.enabled_names(extract) == frozenset({
        "listAnalysisSourceSegments",
        "readAnalysisSourceSegment",
        SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT,
    })
    assert profile.adapter.tool_catalog.enabled_names(normalize) == frozenset({
        "listAnalysisObservations",
        "readAnalysisObservations",
        SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT,
    })
    prepared = await profile.prepare_request(normalize)
    state = profile.adapter.execution_state_factory.create(prepared)
    assert state.domain["unitKind"] == "normalize"
    assert state.domain[NOVEL_ANALYSIS_OBSERVATIONS_STATE_KEY][0][
        "observationId"
    ] == "observation-1"


@pytest.mark.asyncio
async def test_submission_tool_validates_then_commits_operation_artifact(temp_db) -> None:
    await _seed_source(temp_db)
    await create_run(
        temp_db,
        run_id="analysis-root",
        session_id=None,
        prompt="分析这部小说",
        mode="novel_analysis",
    )
    profile = NovelAnalysisReplacementProfile(temp_db)
    request = replace(
        _request(unit={"kind": "extract", "observations": []}),
        metadata={
            "operationScopeId": "task-1:extract-1:1",
            "operationBinding": {
                "taskId": "task-1",
                "unitId": "extract-1",
                "unitAttempt": 1,
            },
        },
    )
    prepared_state = profile.adapter.execution_state_factory.create(request)
    state = ExecutionState(domain=prepared_state.domain, run_id="analysis-root")
    result = await profile.adapter.tool_catalog.get(
        SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT
    ).handler(state, {"result": {
        "facts": [{
            "factKind": "event",
            "subjectKey": "旧城",
            "predicate": "被潮水淹没",
            "value": True,
            "evidenceRefs": [{
                "segmentId": "segment-1",
                "sourceSpanId": "S0-100",
            }],
        }],
        "observations": [],
    }})

    receipt = json.loads(result.content)
    stored = await NovelAnalysisAttemptArtifactStore(
        temp_db
    ).load_operation_payload(
        task_id="task-1",
        operation_id="task-1:extract-1:1",
    )
    assert result.effect_state.value == "committed"
    assert receipt["artifactRef"].startswith("novel-analysis-v1://")
    assert stored["kind"] == "extract"
    assert stored["facts"][0]["factId"].startswith("F")


@pytest.mark.asyncio
async def test_submission_tool_rejects_fake_span_before_artifact_write(temp_db) -> None:
    await _seed_source(temp_db)
    profile = NovelAnalysisReplacementProfile(temp_db)
    request = replace(
        _request(unit={"kind": "overview", "observations": []}),
        metadata={
            "operationScopeId": "task-1:overview:1",
            "operationBinding": {
                "taskId": "task-1",
                "unitId": "overview",
                "unitAttempt": 1,
            },
        },
    )
    prepared_state = profile.adapter.execution_state_factory.create(request)
    state = ExecutionState(domain=prepared_state.domain, run_id="analysis-root")
    result = await profile.adapter.tool_catalog.get(
        SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT
    ).handler(state, {"result": {"storyOverview": {
        "summaryMarkdown": "潮水逼近旧城。",
        "evidenceRefs": [{
            "segmentId": "segment-1",
            "sourceSpanId": "S1-99",
        }],
    }}})

    assert result.error_code == "tool_input_invalid"
    with pytest.raises(ValueError, match="did not submit"):
        await NovelAnalysisAttemptArtifactStore(temp_db).load_operation_payload(
            task_id="task-1",
            operation_id="task-1:overview:1",
        )


@pytest.mark.asyncio
async def test_unit_context_allows_empty_observation_directory(temp_db) -> None:
    await _seed_source(temp_db)
    profile = NovelAnalysisReplacementProfile(temp_db)
    prepared = await profile.prepare_request(_request(unit={
        "kind": "normalize",
        "observations": [],
    }))
    assert analysis_model_tool_names("normalize")[-1] == (
        SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT
    )
    assert prepared.domain_context.payload["unit"]["observations"] == ()


@pytest.mark.asyncio
async def test_isolated_composition_installs_only_replacement_profile(temp_db) -> None:
    composition = create_isolated_novel_analysis_replacement_composition(temp_db)
    try:
        assert composition.agent_profile_ids == (
            NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID,
        )
        route = composition.agent_implementation_router.for_create(
            AgentKind.NOVEL_ANALYSIS
        )
        assert route.runtime_profile_id == NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_versioned_composition_cannot_route_new_analysis_to_retired_profile(
    temp_db,
) -> None:
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(
            frozenset({AgentKind.WRITING})
        ),
    )
    try:
        assert NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID in composition.agent_profile_ids
        assert (
            composition.agent_implementation_router.for_create(
                AgentKind.NOVEL_ANALYSIS
            ).runtime_profile_id
            == NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID
        )
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_versioned_composition_can_explicitly_route_new_analysis_scope(
    temp_db,
) -> None:
    await _seed_source(temp_db)
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(
            frozenset({AgentKind.NOVEL_ANALYSIS})
        ),
    )
    try:
        prepared = await composition.prepare_request(_request())
        assert prepared.metadata[RUNTIME_PROFILE_METADATA_KEY] == (
            NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID
        )
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_profile_creates_recipe_dispatcher_only_with_unit_executor(
    temp_db,
) -> None:
    composition = create_isolated_novel_analysis_replacement_composition(temp_db)
    try:
        profile = composition.profile(NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID)
        assert profile.create_long_task_dispatcher(
            long_task_repository=composition.long_task_repository,
            executor=None,
        ) is None
        dispatcher = profile.create_long_task_dispatcher(
            long_task_repository=composition.long_task_repository,
            executor=NovelAnalysisReplacementUnitExecutor(
                temp_db,
                model_runner=None,
            ),
        )
        assert isinstance(dispatcher, RecipeLongTaskDispatcher)
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_dispatcher_persists_host_recipe_and_attempt_budget(temp_db) -> None:
    await _seed_source(temp_db)
    await create_run(
        temp_db,
        run_id="analysis-root",
        session_id=None,
        prompt="分析这部小说",
        mode="novel_analysis",
    )
    composition = create_isolated_novel_analysis_replacement_composition(temp_db)
    try:
        profile = composition.profile(NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID)
        request = await profile.prepare_request(_request())
        plan = _execution_plan()
        decision = await profile.evaluate(request, plan)
        dispatcher = profile.create_long_task_dispatcher(
            long_task_repository=composition.long_task_repository,
            executor=NovelAnalysisReplacementUnitExecutor(
                temp_db,
                model_runner=None,
            ),
        )

        receipt = await dispatcher.dispatch(
            request,
            plan,
            decision,
            run_id="analysis-root",
        )
        task = await composition.long_task_repository.load(receipt.task_id)
        units = await composition.long_task_repository.list_units(receipt.task_id)

        assert task.metadata["recipeVersion"] == 1
        assert task.metadata["modelAttemptBudget"] == 8
        assert task.budget_limits.max_invocation_attempts == 8
        assert [unit.metadata["unitKind"] for unit in units] == [
            "extract",
            "normalize",
            "validate_evidence",
            "overview",
            "distill_technique",
            "coverage",
            "review",
        ]
    finally:
        await composition.shutdown()
