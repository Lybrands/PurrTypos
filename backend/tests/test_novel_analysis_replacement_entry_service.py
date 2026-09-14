from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from agents.novel_analysis.composition import (
    create_isolated_novel_analysis_replacement_composition,
)
from agents.novel_analysis.domain import NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE
from agents.novel_analysis.entry_service import (
    NovelAnalysisReplacementExecutionService,
)
from agents.novel_analysis.executor import NovelAnalysisReplacementUnitExecutor
from agents.novel_analysis.profile import NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID
from agents.novel_analysis.product_service import (
    VersionedNovelAnalysisProductService,
)
from agents.novel_analysis.follow_up_service import (
    NovelAnalysisReplacementFollowUpService,
)
from agents.novel_analysis.edit_replay import (
    NovelAnalysisReplacementEditLifecycle,
)
from agents.novel_analysis.recipe import AnalysisSegment, compile_analysis_recipe
from agents.novel_analysis.recovery_service import (
    NovelAnalysisReplacementRecoveryService,
)
from agents.novel_analysis.review_projection import (
    NovelAnalysisReviewProjection,
    NovelAnalysisReviewProjectionError,
)
from agents.novel_analysis.run_projection import (
    NovelAnalysisReplacementRunProjection,
)
from agents.novel_analysis.stream_projection import (
    VersionedNovelAnalysisStreamQuery,
)
from agents.novel_analysis.submission_tool import SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT
from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.shared.implementation import AgentKind, replacement_implementation
from agents.shared.implementation_registry import AgentRolloutPolicy
from agents.shared.saved_model_binding import (
    capture_saved_model_binding,
    resolve_saved_model_runtime,
)
from application.composition_factory import create_versioned_agent_composition
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
    await db.execute(
        "INSERT INTO novel_source_sections "
        "(id, revision_id, ordinal, title, text_content, content_digest) "
        "VALUES ('section-1', 'revision-1', 0, '第一章', ?, 'section-digest')",
        ["潮水漫过旧城。" + "风" * 93],
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
        assert route.identity == replacement_implementation(
            AgentKind.NOVEL_ANALYSIS,
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
        replacement_implementation(AgentKind.NOVEL_ANALYSIS, recipe_version=1),
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
async def test_product_control_rejects_historical_legacy_task_as_read_only(
    temp_db,
) -> None:
    run_id = await create_run(
        temp_db,
        run_id="legacy-product-root",
        session_id=None,
        prompt="分析",
        mode="novel_analysis",
        binding=RunBinding(
            namespace="novel_source_analysis",
            aggregate_id="revision-1",
            command_id="legacy-product-root-command",
        ),
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, total_units) "
        "VALUES (?, ?, ?, ?, ?, 'running', 1)",
        [
            "legacy-product-task",
            "purrtypos.novel_analysis",
            "novel_source_analysis",
            "revision-1",
            run_id,
        ],
    )
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(
            frozenset({AgentKind.NOVEL_ANALYSIS})
        ),
    )
    try:
        with pytest.raises(AppError, match="旧版小说分析仅供查看"):
            await VersionedNovelAnalysisProductService(
                temp_db,
                composition,
            ).pause("legacy-product-task", expected_revision=1)
        task = await composition.long_task_repository.load(
            "legacy-product-task"
        )
    finally:
        await composition.shutdown()

    assert task is not None
    assert task.status is LongTaskStatus.RUNNING


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
        replacement_implementation(AgentKind.NOVEL_ANALYSIS, recipe_version=1),
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
    calls: list[tuple[str, str, bool]] = []

    class FakeRecovery:
        def __init__(self, db, selected_composition, *, entry_service) -> None:
            assert db is temp_db
            assert selected_composition is composition
            assert isinstance(entry_service, NovelAnalysisReplacementExecutionService)

        async def resume(self, **kwargs):
            calls.append((
                kwargs["task_id"],
                kwargs["run_command_id"],
                kwargs["retry_failed"],
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
            retry_failed=False,
        )
        await asyncio.sleep(0)
    finally:
        await composition.shutdown()

    assert calls == [(
        "replacement-resume-task",
        "replacement-resume-command",
        False,
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
            NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID
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
async def test_product_follow_up_rejects_legacy_artifact_without_fallback(
    temp_db,
) -> None:
    runtime = await _saved_runtime(temp_db)
    await temp_db.execute(
        "INSERT INTO ai_agent_artifacts "
        "(id, namespace, kind, owner_id, owner_ref_kind, owner_ref_id, "
        "created_by_run_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "legacy-follow-up-artifact",
            "purrtypos.novel_analysis",
            "novel_source_analysis_candidate",
            "revision-1",
            "long_task_unit",
            "legacy-task:artifact:review",
            "legacy-root",
            "finalized",
        ],
    )
    composition = create_versioned_agent_composition(
        temp_db,
        agent_rollout_policy=AgentRolloutPolicy(
            frozenset({AgentKind.NOVEL_ANALYSIS})
        ),
    )
    try:
        with pytest.raises(AppError, match="旧版小说分析仅供查看"):
            await VersionedNovelAnalysisProductService(
                temp_db,
                composition,
            ).follow_up(
                source_revision_id="revision-1",
                artifact_id="legacy-follow-up-artifact",
                prompt="继续解释",
                command_id="legacy-follow-up-command",
                runtime=runtime,
            )
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_follow_up_request_routes_by_artifact_owner_when_rollout_is_off(
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
        replacement_implementation(AgentKind.NOVEL_ANALYSIS, recipe_version=1),
    )
    task_id = "follow-up-source-task"
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, total_units, "
        "completed_units) VALUES (?, 'purrtypos.novel_analysis', "
        "'novel_analysis.purra-native', 'revision-1', ?, 'completed', 1, 1)",
        [task_id, run_id],
    )
    review_payload = {
        "schemaVersion": 1,
        "kind": "review",
        "sourceRevisionId": "revision-1",
        "facts": [],
        "observations": [],
        "storyOverview": {
            "summaryMarkdown": "潮水漫过旧城。",
            "evidenceRefs": [{
                "segmentId": "section-1:0:100",
                "sourceSpanId": "S001",
            }],
        },
        "techniqueResult": {"status": "empty", "reason": "材料不足。"},
        "coverageReport": {"missingSegmentIds": []},
        "evidenceIndex": [{
            "segmentId": "section-1:0:100",
            "sourceSpanId": "S001",
            "sectionId": "section-1",
            "sectionOrdinal": 0,
            "startCharacter": 0,
            "endCharacter": 8,
            "text": "潮水漫过旧城。",
        }],
        "reviewStatus": "pending_review",
    }
    receipt = await NovelAnalysisAttemptArtifactStore(temp_db).commit(
        task_id=task_id,
        unit_id="review:artifact",
        attempt=1,
        operation_id=f"{task_id}:review:artifact:1",
        run_id=run_id,
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
        "novel_analysis.purra-native.v1"
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
        replacement_implementation(AgentKind.NOVEL_ANALYSIS, recipe_version=1),
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
    composition = create_isolated_novel_analysis_replacement_composition(temp_db)
    try:
        service = NovelAnalysisReplacementExecutionService(temp_db, composition)
        request = await service.build_request(
            source_revision_id="revision-1",
            command_id="command-1",
            prompt="分析人物与世界背景",
            runtime=runtime,
        )
        prepared = await composition.prepare_request(request)
        decision = await composition.profile(
            NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID
        ).evaluate(
            prepared,
            ExecutionPlan(
                title="分析并复核",
                task_spec=TaskSpec(goal="形成可审核分析", operation="analyze"),
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
        "segments",
    }
    assert request.domain_context.payload["sourceRevisionId"] == "revision-1"
    assert request.domain_context.payload["segments"][0]["sectionDigest"] == (
        "section-digest"
    )
    binding = request.metadata["runtimeBinding"]
    assert binding["modelConfigId"] == "analysis-model"
    assert "apiKey" not in json.dumps(thaw_json_mapping(binding))
    assert prepared.domain_context.namespace == (
        NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE
    )
    assert composition.agent_profile_ids == (NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID,)
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
    composition = create_isolated_novel_analysis_replacement_composition(temp_db)
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
        NovelAnalysisReplacementUnitExecutor,
    )
    assert call["request"].domain_context.namespace == (
        NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE
    )
    assert call["options"].binding.namespace == "purrtypos.novel_analysis"
    assert call["options"].binding.command_id == "command-1"
    assert call["options"].provenance.execution_intent.output_contract == (
        "novel_analysis_review_artifact_v1"
    )
    assert call["options"].response_transaction_policy.public_presentation.value == (
        "none"
    )
    assert replacement_implementation(
        AgentKind.NOVEL_ANALYSIS,
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


def _unit_payload(messages):
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and '"unitKind"' in content:
            return json.loads(content.rsplit("\n", 1)[-1])
    raise AssertionError("unit payload is unavailable")


def _unit_result(kind: str, payload: dict[str, object]) -> dict[str, object]:
    reference = {
        "segmentId": "section-1:0:100",
        "sourceSpanId": "S0-100",
    }
    if kind == "extract":
        return {
            "facts": [{
                "factKind": "event",
                "subjectKey": "旧城",
                "predicate": "被潮水淹没",
                "value": True,
                "evidenceRefs": [reference],
            }],
            "observations": [{
                "cardKind": "pacing",
                "title": "潮水倒计时",
                "bodyMarkdown": "用潮位形成时间压力。",
                "evidenceRefs": [reference],
            }],
        }
    dependency = payload["dependencyResults"][0]
    if kind == "normalize":
        fact = dict(dependency["facts"][0])
        fact.pop("factId")
        observation = dict(dependency["observations"][0])
        observation_id = observation.pop("observationId")
        return {
            "facts": [fact],
            "observations": [{
                **observation,
                "mergedObservationIds": [observation_id],
            }],
        }
    if kind == "overview":
        return {"storyOverview": {
            "summaryMarkdown": "潮水淹没旧城。",
            "evidenceRefs": [reference],
        }}
    if kind == "distill_technique":
        return {"techniqueResult": {
            "status": "empty",
            "reason": "材料不足以形成独立技法。",
        }}
    raise AssertionError(f"unexpected model Unit: {kind}")


@pytest.mark.asyncio
async def test_entry_runs_full_root_recipe_through_real_core(
    temp_db,
    monkeypatch,
) -> None:
    runtime = await _saved_runtime(temp_db)

    async def planner(_key, _messages, options, _provider, signal=None):
        assert signal is not None
        return {
            "applied_generation_limit": options.get("max_tokens"),
            "message": {"role": "assistant", "content": json.dumps({
                "needsTodos": True,
                "title": "分析并复核",
                "goal": "形成可审核分析",
                "taskSpec": {
                    "goal": "形成可审核分析",
                    "target": {},
                    "operation": "analyze",
                    "instruction": "核对来源并生成待审核成果",
                    "constraints": [],
                    "preserve": [],
                    "deliverable": "来源分析 review artifact",
                },
                "todos": [{
                    "id": "analysis",
                    "title": "分析小说",
                    "type": "analyze",
                    "executor": "model",
                    "expectedTools": [],
                    "riskLevel": "read",
                }, {
                    "id": "review",
                    "title": "复核分析",
                    "type": "review",
                    "executor": "model",
                    "expectedTools": [],
                    "dependsOn": ["analysis"],
                    "riskLevel": "read",
                }, {
                    "id": "deliver",
                    "title": "形成审核成果",
                    "type": "review",
                    "executor": "model",
                    "expectedTools": [],
                    "dependsOn": ["review"],
                    "riskLevel": "read",
                }],
            }, ensure_ascii=False)},
            "model": "fixture",
            "finish_reason": "stop",
        }

    async def runtime_stream(_key, messages, options, _provider, signal=None):
        assert signal is not None
        payload = _unit_payload(messages)
        kind = payload["unitKind"]
        tools = [item for item in messages if item["role"] == "tool"]

        async def stream():
            if not tools:
                name = (
                    "readAnalysisSourceSegment"
                    if kind == "extract"
                    else "listAnalysisObservations"
                )
                arguments = (
                    {"segmentId": "section-1:0:100"}
                    if kind == "extract"
                    else {"limit": 20}
                )
                yield _tool_call(f"read-{kind}", name, arguments)
                return
            if "artifactRef" not in json.loads(tools[-1]["content"]):
                yield _tool_call(
                    f"submit-{kind}",
                    SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT,
                    {"result": _unit_result(kind, payload)},
                )
                return
            yield {
                "choices": [{
                    "delta": {"content": "已提交。"},
                    "finish_reason": "stop",
                }],
            }

        return {
            "applied_generation_limit": options.get("max_tokens"),
            "stream": stream(),
            "model": "fixture",
        }

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        planner,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        route_planning_stream(planner, runtime_stream),
    )
    composition = create_isolated_novel_analysis_replacement_composition(temp_db)
    results = []
    long_tasks = composition.long_task_repository
    output_journal = composition.output_journal
    try:
        service = NovelAnalysisReplacementExecutionService(temp_db, composition)
        async for update in service.run(
            source_revision_id="revision-1",
            command_id="root-entry-command",
            prompt="分析人物与世界背景",
            runtime=runtime,
            signal=asyncio.Event(),
        ):
            if isinstance(update, AgentRunResult):
                results.append(update)
    finally:
        await composition.shutdown()

    assert len(results) == 1
    assert results[0].status is RunStatus.DONE
    assert results[0].final_response == ""
    assert results[0].validated_result.startswith("novel-analysis-v1://")
    artifact_id = results[0].validated_result.split("://", 1)[1]
    review = await NovelAnalysisAttemptArtifactStore(temp_db).load_payload(
        artifact_id
    )
    projected = await NovelAnalysisReviewProjection(temp_db).load(
        results[0].validated_result
    )
    assert review["storyOverview"]["summaryMarkdown"] == "潮水淹没旧城。"
    assert review["coverageReport"]["missingSegmentIds"] == []
    assert projected["artifactContract"] == "purrtypos.novel_analysis.review.v1"
    assert projected["storyOverview"]["summaryMarkdown"] == "潮水淹没旧城。"
    assert projected["storyOverview"]["evidence"][0]["excerpt"].startswith(
        "潮水漫过旧城"
    )
    assert projected["facts"][0]["evidence"][0]["sectionTitle"] == "第一章"
    assert projected["publicationSupported"] is True
    runs = await NovelAnalysisReplacementRunProjection(
        temp_db,
        long_tasks=long_tasks,
    ).list_for_revision("revision-1")
    assert len(runs) == 1
    assert runs[0]["runId"] == results[0].run_id
    assert runs[0]["workflowStatus"] == "completed"
    assert runs[0]["artifactRef"] == results[0].validated_result
    assert runs[0]["publishedAnalysisId"] is None
    assert runs[0]["relatedRuns"] == []
    assert runs[0]["analysisPlan"]["steps"][0]["id"] == "analysis"
    assert {unit["kind"] for unit in runs[0]["units"]} >= {
        "extract",
        "overview",
        "review",
    }

    class _NoLegacyRuns:
        async def list_for_revision(self, _revision_id):
            return []

    page = await VersionedNovelAnalysisStreamQuery(
        temp_db,
        output_repository=output_journal,
        historical_analysis=_NoLegacyRuns(),
        long_tasks=long_tasks,
    ).read_page("revision-1", after=0, limit=500)
    assert page["runs"][0]["runId"] == results[0].run_id
    assert all(chunk["runId"] == results[0].run_id for chunk in page["chunks"])

    import routers.novel_sources as novel_sources_router

    monkeypatch.setattr(novel_sources_router, "get_db", lambda: temp_db)
    monkeypatch.setattr(
        "application.agent_composition.get_agent_composition",
        lambda: SimpleNamespace(long_task_repository=long_tasks),
    )
    route_runs = await novel_sources_router.list_analysis_runs("revision-1")
    assert route_runs["data"][0]["artifactRef"] == results[0].validated_result
    route_artifact = await novel_sources_router.get_analysis_artifact(artifact_id)
    assert route_artifact["data"]["artifactContract"] == (
        "purrtypos.novel_analysis.review.v1"
    )
    review_body = ReviewNovelAnalysisRequest.model_validate({
        "facts": projected["facts"],
        "craftCards": projected["craftCards"],
        "storyOverview": projected["storyOverview"],
        "analysisTechniqueResult": projected["analysisTechniqueResult"],
    })
    reviewed_response = await novel_sources_router.review_analysis_artifact(
        artifact_id,
        review_body,
        "review-command-1",
    )
    reviewed = reviewed_response["data"]
    assert reviewed["reviewStatus"] == "reviewed"
    assert reviewed["publicationSupported"] is True
    assert reviewed["sourceArtifactRef"] == results[0].validated_result
    assert reviewed["artifactId"] != artifact_id
    replayed_review = await novel_sources_router.review_analysis_artifact(
        artifact_id,
        review_body,
        "review-command-1",
    )
    assert replayed_review["data"]["artifactId"] == reviewed["artifactId"]
    loaded_review = await novel_sources_router.get_analysis_artifact(
        reviewed["artifactId"]
    )
    assert loaded_review["data"]["facts"] == reviewed["facts"]

    with pytest.raises(AppError, match="请先审核"):
        await novel_sources_router.publish_analysis_artifact(artifact_id)
    published_response = await novel_sources_router.publish_analysis_artifact(
        reviewed["artifactId"]
    )
    published = published_response["data"]
    assert published["schemaVersion"] == 4
    assert published["summary"]["artifactId"] == reviewed["artifactId"]
    assert published["facts"][0]["subjectKey"] == reviewed["facts"][0]["subjectKey"]
    canon = await ContinuationService(temp_db).preview_canon(
        source_revision_id="revision-1",
        source_analysis_id=published["id"],
        fork_section_id="section-1",
    )
    assert canon["records"][0]["subjectKey"] == reviewed["facts"][0]["subjectKey"]
    replayed_publish = await novel_sources_router.publish_analysis_artifact(
        reviewed["artifactId"]
    )
    assert replayed_publish["data"]["id"] == published["id"]
    assert int((await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM novel_source_analysis_facts "
        "WHERE analysis_id = ?",
        [published["id"]],
    ))["count"]) == len(reviewed["facts"])

    updated_payload = {
        "facts": json.loads(json.dumps(reviewed["facts"], ensure_ascii=False)),
        "craftCards": reviewed["craftCards"],
        "storyOverview": reviewed["storyOverview"],
        "analysisTechniqueResult": reviewed["analysisTechniqueResult"],
    }
    updated_payload["facts"][0]["subjectKey"] = "被潮水淹没的旧城"
    updated_body = ReviewNovelAnalysisRequest.model_validate(updated_payload)
    with pytest.raises(AppError, match="identity conflicts"):
        await novel_sources_router.review_analysis_artifact(
            artifact_id,
            updated_body,
            "review-command-1",
        )
    updated_response = await novel_sources_router.review_analysis_artifact(
        reviewed["artifactId"],
        updated_body,
        "review-command-2",
    )
    updated = updated_response["data"]
    assert updated["sourceArtifactRef"] == results[0].validated_result
    assert updated["facts"][0]["subjectKey"] == "被潮水淹没的旧城"
    updated_publish = await novel_sources_router.publish_analysis_artifact(
        updated["artifactId"]
    )
    assert updated_publish["data"]["versionNo"] == 2
    published_runs = await novel_sources_router.list_analysis_runs("revision-1")
    assert published_runs["data"][0]["publishedAnalysisId"] == (
        updated_publish["data"]["id"]
    )
    assert published_runs["data"][0]["artifactRef"] == updated["artifactRef"]

    tampered = json.loads(json.dumps(updated_payload, ensure_ascii=False))
    tampered["facts"][0]["evidence"][0]["excerpt"] = "不存在的伪造引文"
    with pytest.raises(AppError, match="does not match"):
        await novel_sources_router.review_analysis_artifact(
            artifact_id,
            ReviewNovelAnalysisRequest.model_validate(tampered),
            "review-command-tampered",
        )
    assert await SqliteAgentImplementationStore(temp_db).load(
        results[0].run_id
    ) == replacement_implementation(AgentKind.NOVEL_ANALYSIS, recipe_version=1)

    winner = await temp_db.fetch_one(
        "SELECT task_id, attempt FROM ai_agent_long_task_units "
        "WHERE output_ref = ?",
        [results[0].validated_result],
    )
    loser_attempt = int(winner["attempt"]) + 1
    loser = await NovelAnalysisAttemptArtifactStore(temp_db).commit(
        task_id=str(winner["task_id"]),
        unit_id="review:artifact",
        attempt=loser_attempt,
        operation_id=(
            f"{winner['task_id']}:review:artifact:{loser_attempt}"
        ),
        run_id=results[0].run_id,
        payload=review,
    )
    with pytest.raises(
        NovelAnalysisReviewProjectionError,
        match="not the settled Unit winner",
    ):
        await NovelAnalysisReviewProjection(temp_db).load(loser.resource_ref)


@pytest.mark.asyncio
async def test_paused_task_resumes_through_real_continuation_core(
    temp_db,
    monkeypatch,
) -> None:
    runtime = await _saved_runtime(temp_db)
    await temp_db.execute(
        "INSERT INTO novel_analysis_sessions (id, revision_id, title) "
        "VALUES ('resume-session', 'revision-1', '恢复测试')"
    )
    await temp_db.execute(
        "INSERT INTO novel_analysis_session_commands "
        "(command_id, session_id, revision_id) VALUES "
        "('pause-then-resume-task', 'resume-session', 'revision-1')"
    )
    provider_state = {"fail_extract": True, "planner_calls": 0}

    async def planner(_key, _messages, options, _provider, signal=None):
        provider_state["planner_calls"] += 1
        assert signal is not None
        return {
            "applied_generation_limit": options.get("max_tokens"),
            "message": {"role": "assistant", "content": json.dumps({
                "needsTodos": True,
                "title": "分析并复核",
                "goal": "形成可审核分析",
                "taskSpec": {
                    "goal": "形成可审核分析",
                    "target": {},
                    "operation": "analyze",
                    "instruction": "核对来源并生成待审核成果",
                    "constraints": [],
                    "preserve": [],
                    "deliverable": "来源分析 review artifact",
                },
                "todos": [{
                    "id": "analysis",
                    "title": "分析小说",
                    "type": "analyze",
                    "executor": "model",
                    "expectedTools": [],
                    "riskLevel": "read",
                }, {
                    "id": "review",
                    "title": "复核分析",
                    "type": "review",
                    "executor": "model",
                    "expectedTools": [],
                    "dependsOn": ["analysis"],
                    "riskLevel": "read",
                }, {
                    "id": "deliver",
                    "title": "形成审核成果",
                    "type": "review",
                    "executor": "model",
                    "expectedTools": [],
                    "dependsOn": ["review"],
                    "riskLevel": "read",
                }],
            }, ensure_ascii=False)},
            "model": "fixture",
            "finish_reason": "stop",
        }

    async def runtime_stream(_key, messages, options, _provider, signal=None):
        assert signal is not None
        payload = _unit_payload(messages)
        kind = payload["unitKind"]
        tools = [item for item in messages if item["role"] == "tool"]

        async def stream():
            if kind == "extract" and provider_state["fail_extract"]:
                raise ModelGatewayError(
                    "fixture provider is temporarily unavailable",
                    code="provider_rate_limited",
                    retryable=True,
                )
            if not tools:
                yield _tool_call(
                    f"read-{kind}",
                    (
                        "readAnalysisSourceSegment"
                        if kind == "extract"
                        else "listAnalysisObservations"
                    ),
                    (
                        {"segmentId": "section-1:0:100"}
                        if kind == "extract"
                        else {"limit": 20}
                    ),
                )
                return
            if "artifactRef" not in json.loads(tools[-1]["content"]):
                yield _tool_call(
                    f"submit-{kind}",
                    SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT,
                    {"result": _unit_result(kind, payload)},
                )
                return
            yield {
                "choices": [{
                    "delta": {"content": "已提交。"},
                    "finish_reason": "stop",
                }],
            }

        return {
            "applied_generation_limit": options.get("max_tokens"),
            "stream": stream(),
            "model": "fixture",
        }

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        planner,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        route_planning_stream(planner, runtime_stream),
    )
    composition = create_isolated_novel_analysis_replacement_composition(temp_db)
    try:
        entry = NovelAnalysisReplacementExecutionService(temp_db, composition)
        first_results = []
        async for update in entry.run(
            source_revision_id="revision-1",
            command_id="pause-then-resume-task",
            prompt="分析人物与世界背景",
            runtime=runtime,
            signal=asyncio.Event(),
        ):
            if isinstance(update, AgentRunResult):
                first_results.append(update)

        row = await temp_db.fetch_one(
            "SELECT id FROM ai_agent_long_tasks WHERE owner_id = ?",
            ["revision-1"],
        )
        assert row is not None
        task_id = str(row["id"])
        paused = await composition.long_task_repository.load(task_id)
        assert paused.status is LongTaskStatus.PAUSED

        provider_state["fail_extract"] = False
        await ProviderHealthRepository(temp_db).record_success(
            ProviderHealthScope(
                provider="openai",
                model="fixture",
                endpoint_digest=digest_model_endpoint(runtime.baseURL),
            )
        )
        recovery = NovelAnalysisReplacementRecoveryService(
            temp_db,
            composition,
            entry_service=entry,
        )
        resumed_results = []
        async for update in recovery.resume(
            task_id=task_id,
            run_command_id="resume-root-command",
            runtime=runtime,
            signal=asyncio.Event(),
        ):
            if isinstance(update, AgentRunResult):
                resumed_results.append(update)
        final_task = await composition.long_task_repository.load(task_id)
        task_bindings = await composition.long_task_repository.list_run_bindings(
            task_id
        )
        projected_runs = await NovelAnalysisReplacementRunProjection(
            temp_db,
            long_tasks=composition.long_task_repository,
        ).list_for_revision("revision-1")
    finally:
        await composition.shutdown()

    assert len(first_results) == 1
    assert first_results[0].status is RunStatus.CANCELED
    assert first_results[0].error == "long_task_paused"
    assert len(resumed_results) == 1
    assert resumed_results[0].status is RunStatus.DONE
    assert resumed_results[0].validated_result.startswith(
        "novel-analysis-v1://"
    )
    assert provider_state["planner_calls"] == 1
    assert final_task.status is LongTaskStatus.COMPLETED
    assert {
        (binding.run_id, binding.relation)
        for binding in task_bindings
    } == {
        (first_results[0].run_id, LongTaskRunRelation.CREATED),
        (resumed_results[0].run_id, LongTaskRunRelation.CONTINUATION),
    }
    assert [item["runId"] for item in projected_runs] == [
        resumed_results[0].run_id,
        first_results[0].run_id,
    ]
    assert projected_runs[0]["artifactRef"] == resumed_results[0].validated_result
    assert projected_runs[0]["conversationId"] == "resume-session"
    assert projected_runs[0]["workflowStatus"] == "completed"
    assert projected_runs[0]["units"]
    assert projected_runs[1]["artifactRef"] is None
    assert projected_runs[1]["conversationId"] == "resume-session"
    assert projected_runs[1]["workflowStatus"] is None
    assert projected_runs[1]["units"] == []
    assert await SqliteAgentImplementationStore(temp_db).load(
        resumed_results[0].run_id
    ) == replacement_implementation(AgentKind.NOVEL_ANALYSIS, recipe_version=1)
    completed = await NovelAnalysisAttemptArtifactStore(temp_db).load_payload(
        resumed_results[0].validated_result.split("://", 1)[1]
    )
    assert completed["coverageReport"]["missingSegmentIds"] == []


class _RecoveryTasks:
    def __init__(self, task, units) -> None:
        self.task = task
        self.units = units

    async def load(self, task_id):
        return self.task if task_id == self.task.id else None

    async def list_units(self, task_id):
        assert task_id == self.task.id
        return self.units


class _RecoveryRouter:
    async def for_run(self, run_id, **kwargs):
        assert run_id == "source-run"
        assert kwargs["expected_agent_kind"] is AgentKind.NOVEL_ANALYSIS
        return SimpleNamespace(identity=replacement_implementation(
            AgentKind.NOVEL_ANALYSIS,
            recipe_version=1,
        ))


class _RecoveryEntry:
    def __init__(self) -> None:
        self.calls = []

    async def continue_task(self, **kwargs):
        self.calls.append(kwargs)
        yield "continued"


class _RecoveryRuns:
    def __init__(self, snapshot) -> None:
        self.snapshot = snapshot

    async def get(self, run_id):
        assert run_id == self.snapshot.run_id
        return self.snapshot


@pytest.mark.asyncio
async def test_recovery_entry_keeps_task_and_run_commands_distinct(temp_db) -> None:
    runtime = await _saved_runtime(temp_db)
    runtime_binding = await capture_saved_model_binding(temp_db, runtime)
    segment = AnalysisSegment(
        id="section-1:0:100",
        section_id="section-1",
        section_digest="section-digest",
        section_ordinal=0,
        start_character=0,
        end_character=100,
    )
    plan = ExecutionPlan(
        title="分析并复核",
        task_spec=TaskSpec(goal="形成可审核分析", operation="analyze"),
        steps=(TaskStep(
            id="analysis",
            title="分析小说",
            type=StepType.ANALYZE,
            executor=StepExecutor.MODEL,
        ),),
    )
    recipe = compile_analysis_recipe(
        source_revision_id="revision-1",
        segments=(segment,),
        plan_step_ids=("analysis",),
    )
    task = LongTaskRecord(
        id="task-1",
        namespace=NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
        kind=recipe.kind,
        owner_id="revision-1",
        created_by_run_id="source-run",
        status=LongTaskStatus.PAUSED,
        revision=2,
        total_units=len(recipe.steps),
        completed_units=1,
        failed_units=0,
        max_parallelism=recipe.max_parallelism,
        metadata={
            "sourceRevisionId": "revision-1",
            "commandId": "original-task-command",
            "segments": [segment.to_mapping()],
            "recipeVersion": 1,
            "recipeDigest": recipe.metadata["recipeDigest"],
            "runtimeBinding": runtime_binding,
        },
    )
    units = (LongTaskUnitRecord(
        task_id=task.id,
        id=recipe.steps[0].id,
        position=0,
        status=LongTaskUnitStatus.COMPLETED,
        dependencies=(),
        attempt=1,
        max_attempts=2,
        output_ref="novel-analysis-v1://completed",
        metadata=recipe.steps[0].metadata,
    ),)
    snapshot = RunSnapshot(
        run_id="source-run",
        title=plan.title,
        goal=plan.goal,
        status=RunStatus.FAILED,
        task_spec=plan.task_spec,
        steps=plan.steps,
        work_step_ids=plan.work_step_ids,
    )
    tasks = _RecoveryTasks(task, units)
    entry = _RecoveryEntry()
    composition = SimpleNamespace(
        long_task_repository=tasks,
        agent_implementation_router=_RecoveryRouter(),
    )
    service = NovelAnalysisReplacementRecoveryService(
        temp_db,
        composition,
        entry_service=entry,
    )
    service._runs = _RecoveryRuns(snapshot)

    updates = [
        update
        async for update in service.resume(
            task_id="task-1",
            run_command_id="resume-command",
            runtime=runtime,
            signal=asyncio.Event(),
        )
    ]

    assert updates == ["continued"]
    call = entry.calls[0]
    assert call["task_command_id"] == "original-task-command"
    assert call["run_command_id"] == "resume-command"
    continuation = call["durable_continuation"]
    assert continuation.receipt.task_id == "task-1"
    assert continuation.continuation_command == "resume-command"
    assert continuation.source.execution_plan.steps[0].status is StepStatus.PENDING

    recovered_runtime = await service.resolve_automatic_runtime("task-1")
    assert recovered_runtime.modelConfigId == "analysis-model"
    assert recovered_runtime.apiKey.get_secret_value() == "fixture-key"
    assert await capture_saved_model_binding(temp_db, recovered_runtime) == (
        runtime_binding
    )
