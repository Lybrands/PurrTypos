from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from agents.novel_analysis.composition import (
    create_isolated_scalable_novel_analysis_composition,
)
from agents.novel_analysis.domain import NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE
from agents.novel_analysis.entry_service import (
    NovelAnalysisReplacementExecutionService,
)
from agents.novel_analysis.scalable_executor import ScalableNovelAnalysisUnitExecutor
from agents.novel_analysis.scalable_profile import (
    NOVEL_ANALYSIS_SCALABLE_PROFILE_ID,
    scalable_novel_analysis_implementation,
)
from agents.novel_analysis.product_service import (
    VersionedNovelAnalysisProductService,
)
from agents.novel_analysis.follow_up_service import (
    NovelAnalysisReplacementFollowUpService,
)
from agents.novel_analysis.edit_replay import (
    NovelAnalysisReplacementEditLifecycle,
)
from agents.novel_analysis.recovery_service import (
    NovelAnalysisReplacementRecoveryService,
)
from agents.novel_analysis.review_projection import (
    NovelAnalysisReviewProjection,
    NovelAnalysisReviewProjectionError,
)
from agents.novel_analysis.publication_service import (
    NovelAnalysisReplacementPublicationService,
)
from agents.novel_analysis.run_projection import (
    NovelAnalysisReplacementRunProjection,
    _workflow,
)
from agents.novel_analysis.stream_projection import (
    VersionedNovelAnalysisStreamQuery,
)
from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.shared.implementation import AgentKind, replacement_implementation
from agents.shared.implementation_registry import AgentRolloutPolicy
from agents.shared.saved_model_binding import (
    capture_saved_model_binding,
    resolve_saved_model_runtime,
)
from application.composition_factory import create_versioned_agent_composition


def test_failed_analysis_is_terminal_and_not_resumable() -> None:
    workflow = _workflow(
        {
            "task_status": "failed",
            "state_reason_code": None,
            "state_reason_scope": None,
        },
        [SimpleNamespace(error_code="novel_analysis_skill_output_invalid")],
        {},
    )

    assert workflow["status"] == "failed"
    assert workflow["reasonCode"] == "novel_analysis_skill_output_invalid"
    assert workflow["resumable"] is False
from application.continuation_service import ContinuationService
from application.model_runtime import runtime_from_settings
from application.run_provenance import digest_model_endpoint
from database.connection import DatabaseConnection
from infrastructure.persistence.provider_health_repository import (
    ProviderHealthRepository,
    ProviderHealthScope,
)
from infrastructure.persistence.run_store import create_run
from agents.shared.implementation_store import SqliteAgentImplementationStore
from purra.json_values import thaw_json_mapping
from purra.contracts import (
    ExecutionPlan,
    AgentRunResult,
    RunStatus,
    StepExecutor,
    StepStatus,
    StepType,
    TaskSpec,
    TaskStep,
    RunBinding,
)
from purra.long_tasks import (
    LongTaskRecord,
    LongTaskRunRelation,
    LongTaskStatus,
    LongTaskUnitRecord,
    LongTaskUnitStatus,
)
from purra.run_state import RunSnapshot
from purra.errors import ModelGatewayError
from schemas.novel_sources import NovelAnalysisRuntimeRequest
from schemas.novel_sources import ReviewNovelAnalysisRequest
from exceptions import AppError
from tests.support.planning_stream import route_planning_stream


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute(
        "INSERT INTO novel_source_works (id, title, source_type) "
        "VALUES ('work-1', '测试小说', 'text')"
    )
    await db.execute(
        "INSERT INTO novel_source_revisions "
        "(id, work_id, version_no, content_digest, parser_version, byte_count, character_count) "
        "VALUES ('revision-1', 'work-1', 1, 'revision-digest', 1, 100, 100)"
    )
    source_text = "潮水漫过旧城。" + "风" * 93
    await db.execute(
        "INSERT INTO novel_source_sections "
        "(id, revision_id, ordinal, title, text_content, content_digest, "
        "byte_count, character_count) VALUES "
        "('section-1', 'revision-1', 0, '第一章', ?, 'section-digest', ?, ?)",
        [source_text, len(source_text.encode("utf-8")), len(source_text)],
    )
    try:
        yield db
    finally:
        await db.close()


async def _saved_runtime(db) -> NovelAnalysisRuntimeRequest:
    config = {
        "id": "analysis-model",
        "apiProvider": "openai",
        "name": "fixture",
        "apiKey": "fixture-key",
        "baseUrl": "http://example.test/v1",
        "contextWindow": "128k",
        "profileMaxGenerationTokens": 8_192,
        "maxGenerationTokens": 4_096,
        "supportsThinking": False,
        "thinkingOnly": False,
    }
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ["ai_model_configs", json.dumps([config])],
    )
    persisted = runtime_from_settings(config)
    return NovelAnalysisRuntimeRequest(
        apiKey=config["apiKey"],
        modelConfigId=config["id"],
        apiProvider=persisted.apiProvider,
        baseURL=persisted.baseURL,
        contextWindow=persisted.contextWindow,
        options=persisted.options,
    )


@pytest.mark.asyncio
async def test_partial_rollout_policy_cannot_restore_legacy_analysis_create(
    temp_db,
) -> None:
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(
            frozenset({AgentKind.WRITING})
        ),
    )
    try:
        route = composition.agent_implementation_router.for_create(
            AgentKind.NOVEL_ANALYSIS,
            recipe_version=1,
        )
        assert route.identity == scalable_novel_analysis_implementation(
            recipe_version=1,
        )
        assert "novel_analysis" not in composition.agent_profile_ids
        NovelAnalysisReplacementExecutionService(temp_db, composition)
    finally:
        await composition.shutdown()

@pytest.mark.asyncio
async def test_product_start_uses_create_policy_without_touching_frozen_legacy(
    temp_db,
    monkeypatch,
) -> None:
    runtime = await _saved_runtime(temp_db)
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(
            frozenset({AgentKind.NOVEL_ANALYSIS})
        ),
    )
    calls: list[tuple[str, str]] = []

    class FakeReplacementEntry:
        def __init__(self, db, selected_composition) -> None:
            assert db is temp_db
            assert selected_composition is composition

        async def build_request(self, **kwargs):
            calls.append(("build", kwargs["command_id"]))
            return object()

        async def run(self, **kwargs):
            calls.append(("run", kwargs["command_id"]))
            if False:
                yield None

    monkeypatch.setattr(
        "agents.novel_analysis.product_service."
        "NovelAnalysisReplacementExecutionService",
        FakeReplacementEntry,
    )
    try:
        receipt = await VersionedNovelAnalysisProductService(
            temp_db,
            composition,
        ).start(
            source_revision_id="revision-1",
            command_id="replacement-product-start",
            prompt="分析人物与背景",
            runtime=runtime,
        )
        await asyncio.sleep(0)
    finally:
        await composition.shutdown()

    assert calls == [
        ("build", "replacement-product-start"),
        ("run", "replacement-product-start"),
    ]
    assert receipt == {
        "status": "accepted",
        "sourceRevisionId": "revision-1",
        "commandId": "replacement-product-start",
        "sectionCount": 1,
        "dispatchActive": True,
    }

@pytest.mark.asyncio
async def test_product_pause_routes_by_persisted_run_identity_not_rollout_policy(
    temp_db,
) -> None:
    run_id = await create_run(
        temp_db,
        run_id="replacement-product-root",
        session_id=None,
        prompt="分析",
        mode="novel_analysis",
        binding=RunBinding(
            namespace="purrtypos.novel_analysis",
            aggregate_id="revision-1",
            command_id="replacement-product-root-command",
        ),
    )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'completed' WHERE id = ?",
        [run_id],
    )
    await SqliteAgentImplementationStore(temp_db).bind(
        run_id,
        scalable_novel_analysis_implementation(recipe_version=2),
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, total_units) "
        "VALUES (?, ?, ?, ?, ?, 'running', 1)",
        [
            "replacement-product-task",
            "purrtypos.novel_analysis",
            "novel_analysis.purra-native",
            "revision-1",
            run_id,
        ],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_runs (task_id, run_id, relation) "
        "VALUES (?, ?, 'created')",
        ["replacement-product-task", run_id],
    )
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(),
    )
    try:
        receipt = await VersionedNovelAnalysisProductService(
            temp_db,
            composition,
        ).pause("replacement-product-task", expected_revision=1)
        persisted = await composition.long_task_repository.load(
            "replacement-product-task"
        )
    finally:
        await composition.shutdown()

    assert persisted is not None
    assert persisted.status is LongTaskStatus.PAUSED
    assert receipt["workflowStatus"] == "paused"
    assert receipt["workflowPauseKind"] == "user"

@pytest.mark.asyncio
async def test_product_resume_uses_persisted_replacement_and_saved_model_binding(
    temp_db,
    monkeypatch,
) -> None:
    runtime = await _saved_runtime(temp_db)
    runtime_binding = await capture_saved_model_binding(temp_db, runtime)
    run_id = await create_run(
        temp_db,
        run_id="replacement-resume-root",
        session_id=None,
        prompt="分析",
        mode="novel_analysis",
        binding=RunBinding(
            namespace="purrtypos.novel_analysis",
            aggregate_id="revision-1",
            command_id="replacement-resume-root-command",
        ),
    )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'completed' WHERE id = ?",
        [run_id],
    )
    await SqliteAgentImplementationStore(temp_db).bind(
        run_id,
        scalable_novel_analysis_implementation(recipe_version=2),
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, total_units, "
        "metadata_json) VALUES (?, ?, ?, ?, ?, 'paused', 1, ?)",
        [
            "replacement-resume-task",
            "purrtypos.novel_analysis",
            "novel_analysis.purra-native",
            "revision-1",
            run_id,
            json.dumps({"runtimeBinding": runtime_binding}),
        ],
    )
    calls: list[tuple[str, str]] = []

    class FakeRecovery:
        def __init__(self, db, selected_composition, *, entry_service) -> None:
            assert db is temp_db
            assert selected_composition is composition
            assert isinstance(entry_service, NovelAnalysisReplacementExecutionService)

        async def resume(self, **kwargs):
            calls.append((
                kwargs["task_id"],
                kwargs["run_command_id"],
            ))
            if False:
                yield None

    monkeypatch.setattr(
        "agents.novel_analysis.product_service."
        "NovelAnalysisReplacementRecoveryService",
        FakeRecovery,
    )
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(),
    )
    try:
        receipt = await VersionedNovelAnalysisProductService(
            temp_db,
            composition,
        ).resume(
            task_id="replacement-resume-task",
            run_command_id="replacement-resume-command",
            runtime=runtime,
        )
        await asyncio.sleep(0)
    finally:
        await composition.shutdown()

    assert calls == [(
        "replacement-resume-task",
        "replacement-resume-command",
    )]
    assert receipt["commandStatus"] == "accepted"
    assert receipt["commandId"] == "replacement-resume-command"


@pytest.mark.asyncio
async def test_product_follow_up_routes_replacement_artifact_without_rollout(
    temp_db,
    monkeypatch,
) -> None:
    runtime = await _saved_runtime(temp_db)
    await temp_db.execute(
        "INSERT INTO ai_agent_artifacts "
        "(id, namespace, kind, owner_id, owner_ref_kind, owner_ref_id, "
        "created_by_run_id, schema_version, expected_item_count) "
        "VALUES ('replacement-follow-up-artifact', ?, 'unit_attempt_result', "
        "'revision-1', 'operation', 'op-1', 'root-1', 1, 1)",
        ["purrtypos.novel_analysis.v1"],
    )
    calls: list[tuple[str, str]] = []

    class FakeFollowUp:
        def __init__(self, db, selected_composition) -> None:
            assert db is temp_db
            assert selected_composition is composition

        async def build_request(self, **kwargs):
            calls.append(("build", kwargs["artifact_id"]))
            return object(), "root-1"

        async def run(self, **kwargs):
            calls.append(("run", kwargs["artifact_id"]))
            if False:
                yield None

    monkeypatch.setattr(
        "agents.novel_analysis.product_service."
        "NovelAnalysisReplacementFollowUpService",
        FakeFollowUp,
    )
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(),
    )
    try:
        receipt = await VersionedNovelAnalysisProductService(
            temp_db,
            composition,
        ).follow_up(
            source_revision_id="revision-1",
            artifact_id="replacement-follow-up-artifact",
            prompt="背景是什么？",
            command_id="replacement-follow-up-command",
            runtime=runtime,
        )
        await asyncio.sleep(0)
    finally:
        await composition.shutdown()

    assert calls == [
        ("build", "replacement-follow-up-artifact"),
        ("run", "replacement-follow-up-artifact"),
    ]
    assert receipt["status"] == "accepted"


@pytest.mark.asyncio
async def test_source_only_follow_up_uses_replacement_read_tools(
    temp_db,
    monkeypatch,
) -> None:
    runtime = await _saved_runtime(temp_db)
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(
            frozenset({AgentKind.NOVEL_ANALYSIS})
        ),
    )
    async def provider(_key, messages, options, _provider, signal=None):
        tool_messages = [item for item in messages if item["role"] == "tool"]

        async def stream():
            if not tool_messages:
                yield _tool_call(
                    "list-source",
                    "listAnalysisSourceSegments",
                    {},
                )
                return
            yield {
                "choices": [{
                    "delta": {"content": "故事发生在被潮水侵袭的旧城。"},
                    "finish_reason": "stop",
                }],
            }

        return {
            "applied_generation_limit": options.get("max_tokens"),
            "stream": stream(),
            "model": "fixture",
        }

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        provider,
    )
    try:
        service = NovelAnalysisReplacementFollowUpService(temp_db, composition)
        request, owner_run_id = await service.build_request(
            source_revision_id="revision-1",
            artifact_id=None,
            command_id="source-only-follow-up",
            prompt="故事发生在哪里？",
            runtime=runtime,
        )
        prepared = await composition.prepare_request(request)
        tools = composition.profile(
            NOVEL_ANALYSIS_SCALABLE_PROFILE_ID
        ).adapter.tool_catalog.enabled_names(prepared)
        results = [
            update
            async for update in service.run(
                source_revision_id="revision-1",
                artifact_id=None,
                command_id="source-only-follow-up",
                prompt="故事发生在哪里？",
                runtime=runtime,
                signal=asyncio.Event(),
            )
            if isinstance(update, AgentRunResult)
        ]
    finally:
        await composition.shutdown()

    assert owner_run_id is None
    assert request.metadata["interactionKind"] == "follow_up"
    assert "analysisArtifactId" not in request.metadata
    assert "hostAgentImplementationOwnerRunId" not in request.metadata
    assert tools == {
        "listAnalysisSourceSegments",
        "readAnalysisSourceSegment",
    }
    assert results[-1].status is RunStatus.DONE
    assert results[-1].final_response == "故事发生在被潮水侵袭的旧城。"
    stored = await temp_db.fetch_one(
        "SELECT binding_attributes_json FROM ai_agent_runs WHERE id = ?",
        [results[-1].run_id],
    )
    attributes = json.loads(stored["binding_attributes_json"])
    assert attributes["interactionKind"] == "follow_up"
    assert "analysisArtifactId" not in attributes

@pytest.mark.asyncio
async def test_follow_up_request_reads_new_scalable_artifact(
    temp_db,
    monkeypatch,
) -> None:
    runtime = await _saved_runtime(temp_db)
    run_id = await create_run(
        temp_db,
        run_id="follow-up-source-root",
        session_id=None,
        prompt="分析",
        mode="novel_analysis",
    )
    await SqliteAgentImplementationStore(temp_db).bind(
        run_id,
        scalable_novel_analysis_implementation(recipe_version=2),
    )
    task_id = "follow-up-source-task"
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, total_units, "
        "completed_units) VALUES (?, 'purrtypos.novel_analysis', "
        "'novel_analysis.scalable.v2', 'revision-1', ?, 'completed', 1, 1)",
        [task_id, run_id],
    )
    review_payload = {
        "schemaVersion": 3,
        "kind": "review",
        "childRunId": "follow-up-review-child",
        "skillArtifactId": "skill-artifact",
        "skillDigest": "skill-digest",
        "coverageArtifactId": "coverage-artifact",
        "coverageDigest": "coverage-digest",
        "synthesisArtifactId": "synthesis-artifact",
        "synthesisDigest": "synthesis-digest",
        "summaryMarkdown": "潮水漫过旧城。",
        "facts": [{"id": "fact-background", "claimNature": "fact", "factKind": "background", "subjectKey": "故事背景", "predicate": "环境", "value": "旧城受到潮水侵袭。", "lifecycleStatus": "active"}],
        "craftCards": [],
        "techniqueResult": {"status": "insufficient_material", "candidate": None, "evidenceRefs": [], "scopeNotes": [], "reason": "未提炼技法"},
    }
    receipt = await NovelAnalysisAttemptArtifactStore(temp_db).commit(
        task_id=task_id,
        unit_id="review:artifact",
        attempt=1,
        operation_id=f"{task_id}:review:artifact:1",
        run_id="scalable-review-child",
        payload=review_payload,
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status, output_ref, attempt, "
        "run_id) VALUES (?, 'review:artifact', 'review:artifact', 0, "
        "'completed', ?, 1, ?)",
        [task_id, receipt.resource_ref, run_id],
    )
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(),
    )
    provider_tools = []
    tool_contents = []
    async def provider(_key, messages, options, _provider, signal=None):
        assert signal is not None
        provider_tools.append({
            tool["function"]["name"] for tool in options.get("tools", [])
        })
        tool_messages = [message for message in messages if message["role"] == "tool"]
        tool_contents.extend(message["content"] for message in tool_messages)

        async def stream():
            if not tool_messages:
                yield _tool_call(
                    "read-review",
                    "readAnalysisReviewArtifact",
                    {},
                )
                return
            yield {
                "choices": [{
                    "delta": {"content": "故事背景是被潮水侵袭的旧城。"},
                    "finish_reason": "stop",
                }],
            }

        return {
            "applied_generation_limit": options.get("max_tokens"),
            "stream": stream(),
            "model": "fixture",
        }

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        provider,
    )
    try:
        service = NovelAnalysisReplacementFollowUpService(
            temp_db,
            composition,
        )
        request, owner_run_id = await service.build_request(
            source_revision_id="revision-1",
            artifact_id=receipt.artifact_id,
            command_id="follow-up-command",
            prompt="故事背景是什么？",
            runtime=runtime,
        )
        prepared = await composition.prepare_request(request)
        results = []
        async for update in service.run(
            source_revision_id="revision-1",
            artifact_id=receipt.artifact_id,
            command_id="follow-up-command",
            prompt="故事背景是什么？",
            runtime=runtime,
            signal=asyncio.Event(),
        ):
            if isinstance(update, AgentRunResult):
                results.append(update)
        failure_rows = await temp_db.fetch_all(
            "SELECT event_type, payload_json FROM ai_agent_run_events "
            "WHERE run_id = ? ORDER BY id",
            [results[-1].run_id],
        )
        failure_details = [
            (row["event_type"], json.loads(row["payload_json"]))
            for row in failure_rows[-4:]
        ]
    finally:
        await composition.shutdown()

    assert owner_run_id == run_id
    assert prepared.metadata["hostAgentRuntimeProfile"] == (
        NOVEL_ANALYSIS_SCALABLE_PROFILE_ID
    )
    assert prepared.planning_mode.value == "reactive"
    assert provider_tools == [{
        "listAnalysisSourceSegments",
        "readAnalysisSourceSegment",
        "readAnalysisReviewArtifact",
    }] * 2 + [set()]
    assert any("潮水漫过旧城" in content for content in tool_contents)
    assert len(results) == 1
    assert results[0].status is RunStatus.DONE, json.dumps(
        failure_details, ensure_ascii=False
    )
    assert results[0].final_response == "故事背景是被潮水侵袭的旧城。"


@pytest.mark.asyncio
async def test_review_projection_exposes_publishable_canonical_materials(
    temp_db,
) -> None:
    run_id = await create_run(
        temp_db,
        run_id="scalable-review-root",
        session_id=None,
        prompt="分析整部作品",
        mode="novel_analysis",
        binding=RunBinding(
            namespace="purrtypos.novel_analysis",
            aggregate_id="revision-1",
            command_id="scalable-review-command",
        ),
    )
    task_id = "scalable-review-task"
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, status, prompt, root_run_id, parent_run_id) "
        "VALUES ('scalable-review-child', 'done', '', ?, ?)",
        [run_id, run_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, total_units, "
        "completed_units) VALUES (?, 'purrtypos.novel_analysis', "
        "'novel_analysis.scalable.v2', 'revision-1', ?, 'completed', 1, 1)",
        [task_id, run_id],
    )
    payload = {
        "schemaVersion": 3,
        "kind": "review",
        "childRunId": "scalable-review-child",
        "skillArtifactId": "skill-artifact",
        "skillDigest": "skill-digest",
        "coverageArtifactId": "coverage-artifact",
        "coverageDigest": "coverage-digest",
        "synthesisArtifactId": "synthesis-artifact",
        "synthesisDigest": "synthesis-digest",
        "summaryMarkdown": "这是整部作品的总结。",
            "facts": [
                {"id": "fact-character", "claimNature": "summary", "factKind": "character_summary", "subjectKey": "林澈", "predicate": "人物归纳", "value": {"name": "林澈", "tags": "守门人", "profile_md": "负责守门。"}, "lifecycleStatus": "active"},
                {"id": "fact-background", "claimNature": "summary", "factKind": "background", "subjectKey": "故事背景", "predicate": "背景归纳", "value": {"content": "故事发生在一座封闭旧城。"}, "lifecycleStatus": "active"},
            ],
        "craftCards": [{"id": "craft-deadline", "cardKind": "technique", "title": "限时任务", "bodyMarkdown": "用送药时限制造压力。"}],
        "techniqueResult": {"status": "generated", "candidate": {"techniqueId": "technique-1", "draftId": "draft-1", "versionId": "version-1"}, "evidenceRefs": ["craft-deadline"], "scopeNotes": [], "reason": ""},
    }
    receipt = await NovelAnalysisAttemptArtifactStore(temp_db).commit(
        task_id=task_id,
        unit_id="review:artifact",
        attempt=1,
        operation_id=f"{task_id}:review:artifact:1",
        run_id="scalable-review-child",
        payload=payload,
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status, output_ref, attempt, "
        "run_id) VALUES (?, 'review:artifact', 'review:artifact', 0, "
        "'completed', ?, 1, ?)",
        [task_id, receipt.resource_ref, run_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_runs (task_id, run_id, relation) "
        "VALUES (?, ?, 'created')",
        [task_id, run_id],
    )

    projected = await NovelAnalysisReviewProjection(temp_db).load(
        receipt.resource_ref
    )

    assert projected["artifactContract"] == "purrtypos.novel_analysis.review.v2"
    assert projected["storyOverview"]["summaryMarkdown"] == (
        "这是整部作品的总结。"
    )
    assert projected["facts"][0]["subjectKey"] == "林澈"
    assert projected["craftCards"][0]["id"] == "craft-deadline"

    publication = NovelAnalysisReplacementPublicationService(temp_db)
    reviewed = await publication.review(
        source_artifact_id=receipt.artifact_id,
        command_id="review-canonical-materials",
        payload={
            "facts": projected["facts"],
            "craftCards": projected["craftCards"],
            "storyOverview": projected["storyOverview"],
            "techniqueResult": projected["techniqueResult"],
        },
    )
    published = await publication.publish(str(reviewed["artifactId"]))
    preview = await ContinuationService(temp_db).preview_canon(
        source_revision_id="revision-1",
        source_analysis_id=str(published["id"]),
        fork_section_id="section-1",
    )
    character = next(item for item in preview["records"] if item["factKind"] == "character_summary")
    mapping = next(item for item in preview["materialMapping"] if item["sourceFactId"] == character["sourceFactId"])
    assert character["subjectKey"] == "林澈"
    assert mapping["kind"] == "character"

    continuation_id = await create_run(
        temp_db,
        run_id="scalable-review-continuation",
        session_id=None,
        prompt="继续已暂停的来源分析。",
        mode="novel_analysis",
        binding=RunBinding(
            namespace="purrtypos.novel_analysis",
            aggregate_id="revision-1",
            command_id="automatic-recovery-command",
            attributes={"automaticRecovery": True},
        ),
    )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'done' WHERE id = ?",
        [continuation_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, status, prompt, root_run_id, parent_run_id) "
        "VALUES ('scalable-review-continuation-child', 'done', '', ?, ?)",
        [continuation_id, continuation_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_runs (task_id, run_id, relation) "
        "VALUES (?, ?, 'continuation')",
        [task_id, continuation_id],
    )

    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(),
    )
    try:
        runs = await NovelAnalysisReplacementRunProjection(
            temp_db,
            long_tasks=composition.long_task_repository,
        ).list_for_revision("revision-1")
        stream_query = VersionedNovelAnalysisStreamQuery(
            temp_db,
            output_repository=composition.output_journal,
            long_tasks=composition.long_task_repository,
        )
        stream_page = await stream_query.read_page("revision-1")
        await temp_db.execute(
            "INSERT INTO ai_agent_runs "
            "(id, status, prompt, root_run_id, parent_run_id) "
            "VALUES ('scalable-live-child', 'running', '', ?, ?)",
            [continuation_id, continuation_id],
        )
        live_stream_page = await stream_query.read_page("revision-1")
        await temp_db.execute(
            "DELETE FROM ai_agent_runs WHERE id = 'scalable-live-child'"
        )
    finally:
        await composition.shutdown()
    assert len(runs) == 1
    assert runs[0]["runId"] == continuation_id
    assert runs[0]["commandId"] == "scalable-review-command"
    assert runs[0]["prompt"] == "分析整部作品"
    assert runs[0]["automaticRecovery"] is False
    assert runs[0]["finalResponse"] == "这是整部作品的总结。"
    related_runs = runs[0]["relatedRuns"]
    assert related_runs[0] == {
        "runId": "scalable-review-root",
        "status": "running",
        "role": "previous_root",
    }
    assert [
        {key: item[key] for key in ("runId", "status", "role")}
        for item in related_runs[1:]
    ] == [
        {
            "runId": "scalable-review-child",
            "status": "done",
            "role": "child",
        },
        {
            "runId": "scalable-review-continuation-child",
            "status": "done",
            "role": "child",
        },
    ]
    assert all(item["createTime"] for item in related_runs[1:])
    assert stream_page["runs"] == runs
    assert live_stream_page["projectionVersion"] != stream_page["projectionVersion"]
    assert any(
        item["runId"] == "scalable-live-child" and item["status"] == "running"
        for item in live_stream_page["runs"][0]["relatedRuns"]
    )

@pytest.mark.asyncio
async def test_edit_lifecycle_archives_suffix_only_after_new_root_exists(
    temp_db,
) -> None:
    await temp_db.execute(
        "INSERT INTO novel_analysis_sessions (id, revision_id, title) "
        "VALUES ('edit-session', 'revision-1', '编辑测试')"
    )
    for command in ("target-command", "replacement-command"):
        await temp_db.execute(
            "INSERT INTO novel_analysis_session_commands "
            "(command_id, session_id, revision_id) VALUES (?, 'edit-session', "
            "'revision-1')",
            [command],
        )
    target_id = await create_run(
        temp_db,
        run_id="replacement-edit-target",
        session_id=None,
        prompt="旧追问",
        mode="novel_analysis_follow_up",
        binding=RunBinding(
            namespace="purrtypos.novel_analysis",
            aggregate_id="revision-1",
            command_id="target-command",
            attributes={
                "interactionKind": "follow_up",
                "analysisArtifactId": "review-artifact",
            },
        ),
    )
    later_id = await create_run(
        temp_db,
        run_id="replacement-edit-later",
        session_id=None,
        prompt="后续追问",
        mode="novel_analysis_follow_up",
        binding=RunBinding(
            namespace="purrtypos.novel_analysis",
            aggregate_id="revision-1",
            command_id="later-command",
        ),
    )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'done' WHERE id IN (?, ?)",
        [target_id, later_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, total_units) "
        "VALUES ('edit-task', 'purrtypos.novel_analysis', "
        "'novel_analysis.purra-native', 'revision-1', ?, 'completed', 0)",
        [target_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_task_runs (task_id, run_id, relation) "
        "VALUES ('edit-task', ?, 'created'), ('edit-task', ?, 'continuation')",
        [target_id, later_id],
    )
    lifecycle = NovelAnalysisReplacementEditLifecycle(
        temp_db,
        source_revision_id="revision-1",
        command_id="replacement-command",
        target_run_id=target_id,
    )

    target = await lifecycle.inspect()
    assert target.interaction_kind == "follow_up"
    assert target.analysis_artifact_id == "review-artifact"
    assert await temp_db.fetch_all(
        "SELECT * FROM novel_analysis_superseded_runs"
    ) == []

    new_id = await create_run(
        temp_db,
        run_id="replacement-edit-new",
        session_id=None,
        prompt="新追问",
        mode="novel_analysis_follow_up",
        binding=RunBinding(
            namespace="purrtypos.novel_analysis",
            aggregate_id="revision-1",
            command_id="replacement-command",
        ),
    )
    await lifecycle.on_run_started(new_id)

    superseded = await temp_db.fetch_all(
        "SELECT run_id, replacement_command_id, target_run_id "
        "FROM novel_analysis_superseded_runs ORDER BY run_id"
    )
    assert {row["run_id"] for row in superseded} == {target_id, later_id}
    assert all(row["target_run_id"] == target_id for row in superseded)
    assert new_id not in {row["run_id"] for row in superseded}
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(),
    )
    try:
        visible = await NovelAnalysisReplacementRunProjection(
            temp_db,
            long_tasks=composition.long_task_repository,
        ).list_for_revision("revision-1")
    finally:
        await composition.shutdown()
    assert [row["runId"] for row in visible] == [new_id]


@pytest.mark.asyncio
async def test_edit_lifecycle_rejects_live_suffix_without_hiding_old_branch(
    temp_db,
) -> None:
    await temp_db.execute(
        "INSERT INTO novel_analysis_sessions (id, revision_id, title) "
        "VALUES ('live-edit-session', 'revision-1', '编辑测试')"
    )
    for command in ("live-target-command", "live-later-command", "live-replace"):
        await temp_db.execute(
            "INSERT INTO novel_analysis_session_commands "
            "(command_id, session_id, revision_id) VALUES (?, "
            "'live-edit-session', 'revision-1')",
            [command],
        )
    target_id = await create_run(
        temp_db,
        run_id="live-edit-target",
        session_id=None,
        prompt="旧问题",
        mode="novel_analysis",
        binding=RunBinding(
            namespace="purrtypos.novel_analysis",
            aggregate_id="revision-1",
            command_id="live-target-command",
        ),
    )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'done' WHERE id = ?",
        [target_id],
    )
    await create_run(
        temp_db,
        run_id="live-edit-later",
        session_id=None,
        prompt="仍在执行",
        mode="novel_analysis_follow_up",
        binding=RunBinding(
            namespace="purrtypos.novel_analysis",
            aggregate_id="revision-1",
            command_id="live-later-command",
        ),
    )
    lifecycle = NovelAnalysisReplacementEditLifecycle(
        temp_db,
        source_revision_id="revision-1",
        command_id="live-replace",
        target_run_id=target_id,
    )

    with pytest.raises(AppError, match="等待当前执行结束"):
        await lifecycle.inspect()
    assert await temp_db.fetch_all(
        "SELECT * FROM novel_analysis_superseded_runs"
    ) == []


@pytest.mark.asyncio
async def test_product_edit_routes_follow_up_by_persisted_target_identity(
    temp_db,
    monkeypatch,
) -> None:
    runtime = await _saved_runtime(temp_db)
    await temp_db.execute(
        "INSERT INTO novel_analysis_sessions (id, revision_id, title) "
        "VALUES ('product-edit-session', 'revision-1', '编辑测试')"
    )
    for command in ("product-edit-target-command", "product-edit-command"):
        await temp_db.execute(
            "INSERT INTO novel_analysis_session_commands "
            "(command_id, session_id, revision_id) VALUES (?, "
            "'product-edit-session', 'revision-1')",
            [command],
        )
    target_id = await create_run(
        temp_db,
        run_id="product-edit-target",
        session_id=None,
        prompt="旧追问",
        mode="novel_analysis_follow_up",
        binding=RunBinding(
            namespace="purrtypos.novel_analysis",
            aggregate_id="revision-1",
            command_id="product-edit-target-command",
            attributes={
                "interactionKind": "follow_up",
                "analysisArtifactId": "review-artifact",
            },
        ),
    )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'done' WHERE id = ?",
        [target_id],
    )
    await SqliteAgentImplementationStore(temp_db).bind(
        target_id,
        scalable_novel_analysis_implementation(recipe_version=2),
    )
    calls = []

    class FakeFollowUp:
        def __init__(self, db, selected_composition) -> None:
            assert db is temp_db
            assert selected_composition is composition

        async def build_request(self, **kwargs):
            calls.append(("build", kwargs))
            return object(), target_id

        async def run(self, **kwargs):
            calls.append(("run", kwargs))
            if False:
                yield None

    monkeypatch.setattr(
        "agents.novel_analysis.product_service."
        "NovelAnalysisReplacementFollowUpService",
        FakeFollowUp,
    )
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(),
    )
    try:
        receipt = await VersionedNovelAnalysisProductService(
            temp_db,
            composition,
        ).replace_turn(
            source_revision_id="revision-1",
            command_id="product-edit-command",
            target_run_id=target_id,
            prompt="新的追问",
            runtime=runtime,
        )
        await asyncio.sleep(0)
    finally:
        await composition.shutdown()

    assert [kind for kind, _ in calls] == ["build", "run"]
    assert calls[0][1]["history_before_run_id"] == target_id
    assert calls[1][1]["history_before_run_id"] == target_id
    assert isinstance(
        calls[1][1]["run_binding_lifecycle"],
        NovelAnalysisReplacementEditLifecycle,
    )
    assert receipt["status"] == "accepted"


@pytest.mark.asyncio
async def test_entry_compiles_canonical_request_and_host_owned_runtime_binding(
    temp_db,
) -> None:
    runtime = await _saved_runtime(temp_db)
    composition = create_isolated_scalable_novel_analysis_composition(temp_db)
    try:
        service = NovelAnalysisReplacementExecutionService(temp_db, composition)
        request = await service.build_request(
            source_revision_id="revision-1",
            command_id="command-1",
            prompt="分析人物与世界背景",
            runtime=runtime,
        )
        prepared = await composition.prepare_request(request)
        decision = await composition.profile(NOVEL_ANALYSIS_SCALABLE_PROFILE_ID).evaluate(
            prepared,
            ExecutionPlan(
                title="分析并复核",
                task_spec=TaskSpec(
                    goal="形成可审核分析",
                    operation="analyze",
                    target={
                        "schemaVersion": 1,
                        "passes": [{"id": "story", "dimensions": ["characters"]}],
                        "reduceFanIn": 2,
                        "synthesisSections": ["人物"],
                        "qualityChecks": ["整书覆盖"],
                    },
                ),
                steps=(TaskStep(
                    id="analysis",
                    title="分析小说",
                    type=StepType.ANALYZE,
                    executor=StepExecutor.MODEL,
                ),),
            ),
        )
    finally:
        await composition.shutdown()

    assert request.domain_context.namespace == NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE
    assert set(request.domain_context.payload) == {
        "sourceRevisionId",
        "commandId",
    }
    assert request.domain_context.payload["sourceRevisionId"] == "revision-1"
    binding = request.metadata["runtimeBinding"]
    assert binding["modelConfigId"] == "analysis-model"
    assert "apiKey" not in json.dumps(thaw_json_mapping(binding))
    assert prepared.domain_context.namespace == (
        NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE
    )
    assert composition.agent_profile_ids == (NOVEL_ANALYSIS_SCALABLE_PROFILE_ID,)
    assert thaw_json_mapping(decision.metadata)["runtimeBinding"] == (
        thaw_json_mapping(binding)
    )


class _CapturingRuns:
    def __init__(self) -> None:
        self.calls = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        yield "fixture-update"


@pytest.mark.asyncio
async def test_entry_delegates_to_canonical_run_service_with_replacement_executor(
    temp_db,
) -> None:
    runtime = await _saved_runtime(temp_db)
    runs = _CapturingRuns()
    composition = create_isolated_scalable_novel_analysis_composition(temp_db)
    try:
        service = NovelAnalysisReplacementExecutionService(
            temp_db,
            composition,
            runs=runs,
        )
        updates = [
            update
            async for update in service.run(
                source_revision_id="revision-1",
                command_id="command-1",
                prompt="分析人物与世界背景",
                runtime=runtime,
                signal=asyncio.Event(),
            )
        ]
    finally:
        await composition.shutdown()

    assert updates == ["fixture-update"]
    assert len(runs.calls) == 1
    call = runs.calls[0]
    assert isinstance(
        call["long_task_executor"],
        ScalableNovelAnalysisUnitExecutor,
    )
    assert call["request"].domain_context.namespace == (
        NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE
    )
    assert call["options"].binding.namespace == "purrtypos.novel_analysis"
    assert call["options"].binding.command_id == "command-1"
    assert call["options"].provenance.execution_intent.output_contract == (
        "novel_analysis_scalable_review_v1"
    )
    assert call["options"].response_transaction_policy.public_presentation.value == (
        "none"
    )
    assert scalable_novel_analysis_implementation(
        recipe_version=1,
    ).implementation_id == "purra-native"


def _tool_call(call_id: str, name: str, arguments: dict[str, object]):
    return {
        "choices": [{
            "delta": {"tool_calls": [{
                "index": 0,
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }]},
            "finish_reason": "tool_calls",
        }],
    }
