from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from application.screenplay_agent_service import (
    ScreenplayAgentService,
    _ACTIVE_TASKS,
    _ScreenplayContinuationRunLifecycle,
    _ScreenplayTurnRunLifecycle,
)
from application.agent_cancellation_service import AgentCancellationService
from application.agent_orphan_recovery_service import AgentOrphanRecoveryService
from application.screenplay_task_resolver import ResolvedScreenplayTask
from application.screenplay_agent_profile import ScreenplayAgentProfile
from application.composition_factory import create_agent_composition
import application.agent_composition as agent_composition
import application.composition_factory as composition_factory
from application.screenplay_candidate_assembler import (
    ScreenplayCandidateAssembler,
)
from application.screenplay_checkpoint_planning import (
    ScreenplayCheckpointDecision,
    ScreenplayCheckpointOutcome,
    ScreenplayCheckpointPlanner,
)
from application.screenplay_structured_call import ScreenplayStructuredCallService
from application.screenplay_tool_calling import ScreenplayToolCallingService
from application.screenplay_part_artifacts import ScreenplayPartArtifactQuery
from application.screenplay_agent_task_executor import (
    ScreenplayTaskUnitExecutor,
    _record_part_run_usage,
)
from application.sse_mapping import canonical_output_to_sse_chunk
from application.screenplay_v2_service import ScreenplayV2ProjectService
from database.connection import DatabaseConnection
from domains.screenplay_agent import (
    ContinuationStartLost,
    OperationUsage,
    ScreenplayIntentAction,
    ScreenplayOperationCreateCommand,
    ScreenplayRootStartLost,
)
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from infrastructure.persistence.sqlite_screenplay_operation_repository import (
    SqliteScreenplayOperationRepository,
)
from infrastructure.persistence.sqlite_screenplay_agent_repository import (
    SqliteScreenplayAgentRepository,
)
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from infrastructure.persistence.sqlite_screenplay_operation_finalizer import (
    ScreenplayOperationFinalizationCommand,
    SqliteScreenplayOperationFinalizer,
)
from infrastructure.screenplay.agent_root_completion_projector import (
    ScreenplayAgentRootCompletionError,
    ScreenplayAgentRootCompletionProjector,
)
from infrastructure.screenplay.agent_continuation_begin_projector import (
    ScreenplayContinuationBeginProjector,
)
from purra.contracts import (
    AgentMessage,
    ModelCompletion,
    ModelFinishReason,
    ModelStream,
    ModelStreamChunk,
    ReasoningMode,
    RunBinding,
    RunCreateParams,
    RunStatus,
    ToolCallDelta,
)
from purra.events import AgentEvent, CoreEventType
from purra.ports import RunCommit
from purra.api import AgentCore
from purra.errors import (
    ContractViolationError,
    ModelGatewayError,
    RunCommitProjectionError,
)
from purra.long_tasks import (
    LongTaskBudgetLimits,
    LongTaskCreateCommand,
    LongTaskUnitResult,
    LongTaskUnitSpec,
    RecipeLongTaskDispatcher,
)
from purra.tools import InMemoryToolCatalog
from domains.screenplay_agent.adapter import (
    ScreenplayExecutionStateFactory,
    ScreenplayHostContextProvider,
    ScreenplayToolLoopPolicy,
)
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from infrastructure.persistence.sqlite_agent_output_repository import (
    SqliteAgentOutputRepository,
)
from infrastructure.persistence.agent_output_publisher import (
    InProcessAgentOutputPublisher,
)
from infrastructure.persistence.run_execution_store import SqliteRunControlStore
from infrastructure.persistence.orphan_run_monitor import monitor_orphaned_runs
from domains.screenplay_agent.recovery import classify_screenplay_run_failure
from exceptions import AppError
from schemas.screenplay_agent import (
    ResumeScreenplayOperationRequest,
    SubmitScreenplayAgentTurnRequest,
)
from schemas.screenplay_v2 import CreateScreenplayV2ProjectRequest


@pytest_asyncio.fixture
async def screenplay_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


class _CoreComposition:
    def __init__(self, db, gateway) -> None:
        self._gateway = gateway
        self._runs = SqliteRunRepository(db)
        self._outputs = SqliteAgentOutputRepository(
            db,
            run_repository=self._runs,
        )
        self._publisher = InProcessAgentOutputPublisher()
        self._leases = SqliteRunControlStore(db)

    def create_core_for_request(self, request, api_key):
        del request, api_key
        return AgentCore(
            model_gateway=self._gateway,
            run_repository=self._runs,
            planning_policy=ScreenplayToolLoopPolicy(),
            context_provider=ScreenplayHostContextProvider(),
            execution_state_factory=ScreenplayExecutionStateFactory(),
            tool_catalog=InMemoryToolCatalog(()),
            output_repository=self._outputs,
            output_publisher=self._publisher,
            execution_lease_store=self._leases,
            execution_owner_id=self._runs.owner_id,
            execution_lease_duration_ms=self._runs.lease_duration_ms,
        )

    def bind_run_profile(self, request, options):
        del request
        return options

    def release_core(self, core) -> None:
        del core


class _Resolver:
    async def resolve(self, **_kwargs):
        return ResolvedScreenplayTask(
            target_role="screenplayDraft",
            episode_numbers=(4, 5, 6),
            episode_scene_ids={
                4: ("ep04_s01",),
                5: ("ep05_s01",),
                6: ("ep06_s01",),
            },
            source_revision_refs=("sprev-scenes", "sprev-brief"),
        )


class _SingleDraftResolver:
    async def resolve(self, **_kwargs):
        return ResolvedScreenplayTask(
            target_role="screenplayDraft",
            episode_numbers=(4,),
            episode_scene_ids={4: ("ep04_s01",)},
            source_revision_refs=("sprev-scenes", "sprev-brief"),
        )


class _SourceAnalysisResolver:
    async def resolve(self, **_kwargs):
        return ResolvedScreenplayTask(
            target_role="sourceAnalysis",
            document_sections=("characters",),
            source_chapters=({
                "id": "chapter-1",
                "title": "第一章",
                "index": 1,
            },),
        )


class _UnitExecutor:
    def __init__(self, db) -> None:
        self._db = db
        self._parts = ScreenplayPartArtifactQuery(db)
        self.calls = []
        self.output_refs = {}

    async def execute(self, context, signal=None):
        del signal
        self.calls.append((context.unit.id, dict(context.dependency_outputs)))
        if context.unit.id == "compose-final-response":
            output = {
                "finalResponse": (
                    "第 4 至 6 集候选稿已经完成。"
                    "可以在候选稿区域查看并继续编辑。"
                ),
                "runId": "run-compose-final-response",
            }
        elif context.unit.id.endswith(":validation"):
            episode_number = int(context.unit.id.split(":")[1])
            output = {
                "executionSummary": f"完成第 {episode_number} 集",
                "sceneListId": "sprev-scenes",
                "episodeDraft": {
                    "episodeNumber": episode_number,
                    "title": f"第 {episode_number} 集",
                    "sceneIds": [f"ep{episode_number:02d}_s01"],
                    "sceneTexts": [{
                        "sceneId": f"ep{episode_number:02d}_s01",
                        "contentText": f"第 {episode_number} 集正文",
                    }],
                    "contentText": f"第 {episode_number} 集正文",
                    "continuitySummary": f"第 {episode_number} 集连续性",
                },
            }
        else:
            output = {"partId": context.unit.id}
        ref = await self._parts.write_host_part(
            project_id=str(context.task.owner_id),
            task_id=context.task.id,
            unit_id=context.unit.id,
            semantic_key=str(context.unit.semantic_key),
            part_kind=str(context.unit.metadata.get("unitKind") or ""),
            output=output,
        )
        self.output_refs[context.unit.id] = ref.output_ref
        await self._db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json) VALUES (?, 'agentRunTrace', ?)",
            [
                ref.run_id,
                json.dumps({
                    "stage": "model_usage",
                    "outcome": "provider_reported",
                    "details": {
                        "actualInputTokens": 10,
                        "actualOutputTokens": 5,
                        "reasoningOutputTokens": 2,
                    },
                }),
            ],
        )
        metadata = (
            {"finalResponse": output["finalResponse"]}
            if "finalResponse" in output
            else {}
        )
        return LongTaskUnitResult(
            output_ref=ref.output_ref,
            run_id=ref.run_id,
            artifact_digest=ref.content_digest,
            validation_receipt=ref.validation_receipt,
            metadata=metadata,
        )


class _ToolCallingUnitExecutor(_UnitExecutor):
    def __init__(
        self,
        db,
        *,
        composition,
        runtime,
        turn_id: str,
    ) -> None:
        super().__init__(db)
        self._candidate_runs = ScreenplayToolCallingService(
            db,
            composition=composition,
        )
        self._runtime = runtime
        self._turn_id = turn_id

    async def execute(self, context, signal=None):
        if context.unit.id != "episode:4:validation":
            return await super().execute(context, signal)
        episode_number = 4
        scene_id = "ep04_s01"
        result = await self._candidate_runs.run_candidate(
            runtime=self._runtime,
            session_id=1,
            system_instruction=(
                "先调用 inspectScreenplayProject 读取项目，再只输出当前场景正文。"
            ),
            user_payload={"episodeNumber": episode_number},
            domain_context=ScreenplayAgentDomainContext(
                project_id=context.task.owner_id,
                task_id=context.task.id,
                unit_id=context.unit.id,
                target_role="screenplayDraft",
                expected_part_type="scene",
                expected_part_key=scene_id,
                tool_access="evidence_read",
            ),
            conversation_turn_id=self._turn_id,
            output_token_cap=16_384,
            bind_run=context.bind_run,
            reasoning_mode=ReasoningMode.DISABLED,
            host_candidate_template={
                "sceneId": scene_id,
            },
            candidate_validation_contract={
                "protocol": "purrtypos.screenplay.candidate-validation/v1",
                "kind": "scene",
                "expectedSceneId": scene_id,
            },
            signal=signal,
        )
        assert result.candidate["payload"]["sceneId"] == scene_id
        output = {
            "executionSummary": f"完成第 {episode_number} 集",
            "sceneListId": "sprev-scenes",
            "episodeDraft": {
                "episodeNumber": episode_number,
                "title": f"第 {episode_number} 集",
                "sceneIds": [scene_id],
                "sceneTexts": [{
                    "sceneId": scene_id,
                    "contentText": f"第 {episode_number} 集正文",
                }],
                "contentText": f"第 {episode_number} 集正文",
                "continuitySummary": f"第 {episode_number} 集连续性",
            },
            "runId": result.run_id,
        }
        ref = await self._parts.write_host_part(
            project_id=context.task.owner_id,
            task_id=context.task.id,
            unit_id=context.unit.id,
            semantic_key=str(context.unit.semantic_key),
            part_kind=str(context.unit.metadata.get("unitKind") or ""),
            output=output,
        )
        self.output_refs[context.unit.id] = ref.output_ref
        return LongTaskUnitResult(
            output_ref=ref.output_ref,
            run_id=ref.run_id,
            artifact_digest=ref.content_digest,
            validation_receipt=ref.validation_receipt,
        )


async def test_screenplay_profile_dispatcher_registers_only_the_injected_executor(
    screenplay_db,
):
    executor = _UnitExecutor(screenplay_db)
    dispatcher = ScreenplayAgentProfile(
        screenplay_db,
        owner_id="screenplay-profile-dispatcher-test",
    ).create_long_task_dispatcher(
        long_task_repository=SqliteLongTaskRepository(screenplay_db),
        executor=executor,
    )

    assert isinstance(dispatcher, RecipeLongTaskDispatcher)
    assert dispatcher._executors.get("screenplay") is executor
    assert dispatcher._executors.get("writing") is None


class _ScriptedPlannerGateway:
    def __init__(self, rounds) -> None:
        self.rounds = list(rounds)
        self.calls = []

    def describe_invocation(self, messages, invocation):
        return {
            "messageCount": len(messages),
            "model": invocation.request.model,
        }

    async def stream(self, messages, invocation, signal=None):
        del signal
        self.calls.append((tuple(messages), invocation))
        round_chunks = self.rounds.pop(0)

        async def chunks():
            for chunk in round_chunks:
                yield chunk

        return ModelStream(chunks=chunks(), model=invocation.request.model)

    async def complete(self, messages, invocation, signal=None):
        del signal
        self.calls.append((tuple(messages), invocation))
        round_chunks = self.rounds.pop(0)
        content = "".join(chunk.content_delta for chunk in round_chunks)
        return ModelCompletion(
            message=AgentMessage(role="assistant", content=content),
            model=invocation.request.model,
            finish_reason=round_chunks[-1].finish_reason,
        )


@pytest.mark.asyncio
async def test_public_final_response_is_a_bound_model_run_with_public_output(
    screenplay_db,
    monkeypatch,
):
    projects = ScreenplayV2ProjectService(screenplay_db)
    workspace = await projects.create_project(
        command_id="create-public-final-response-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Public final response",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    gateway = _ScriptedPlannerGateway([[
        ModelStreamChunk(content_delta="第 4 集候选稿已经完成，主要冲突也已推进。"),
        ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
    ]])
    monkeypatch.setattr(
        agent_composition,
        "ProviderModelGateway",
        lambda *_args, **_kwargs: gateway,
    )
    runtime = SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session["id"],
        "content": "完成第 4 集。",
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://api.deepseek.com/v1",
            "options": {"model": "deepseek-v4-flash"},
            "contextWindow": "128k",
        },
    }).runtime
    composition = create_agent_composition(screenplay_db)
    bound_runs = []
    try:
        result = await ScreenplayStructuredCallService(
            screenplay_db,
            composition=composition,
        ).run_public_text(
            runtime=runtime,
            session_id=session["id"],
            system_instruction="根据公开候选事实总结本轮交付。",
            user_payload={"completedEpisodes": [4]},
            binding_namespace="screenplay.agent.final_response",
            binding_aggregate_id=workspace["project"]["id"],
            binding_command_id="task-final:compose-final-response",
            task_id="task-final",
            unit_id="compose-final-response",
            expected_part_key="final-response",
            conversation_turn_id="turn-public-final-response",
            output_token_cap=1_024,
            reasoning_mode=ReasoningMode.DISABLED,
            bind_run=lambda run_id: _capture_run(bound_runs, run_id),
        )
    finally:
        await composition.shutdown()

    assert result.text == "第 4 集候选稿已经完成，主要冲突也已推进。"
    assert bound_runs == [result.run_id]
    assert await screenplay_db.fetch_one(
        "SELECT binding_namespace, binding_aggregate_id, binding_command_id, "
        "final_response FROM ai_agent_runs WHERE id = ?",
        [result.run_id],
    ) == {
        "binding_namespace": "screenplay.agent.final_response",
        "binding_aggregate_id": workspace["project"]["id"],
        "binding_command_id": "task-final:compose-final-response",
        "final_response": result.text,
    }
    events = await SqliteAgentOutputRepository(
        screenplay_db,
        run_repository=SqliteRunRepository(screenplay_db),
    ).list_events(result.run_id, after_sequence=0)
    assert any(
        event.visibility.value == "public"
        and event.kind.value == "provider.delta_batch"
        and event.channel.value == "final"
        and "".join(
            str(entry.get("payload", {}).get("delta") or "")
            for entry in event.payload.get("entries", ())
            if entry.get("kind") == "provider.content_delta"
        ) == result.text
        for event in events
    )
    assert any(
        event.visibility.value == "public"
        and event.kind.value == "stream.committed"
        and event.channel.value == "final"
        for event in events
    )


async def _capture_run(run_ids: list[str], run_id: str) -> None:
    run_ids.append(run_id)


class _ReviewResolver:
    async def resolve(self, **kwargs):
        assert kwargs["intent"].action is ScreenplayIntentAction.REVIEW
        return ResolvedScreenplayTask(
            target_role="review",
            episode_numbers=(1,),
            episode_scene_ids={1: ("scene-1",)},
            reviewed_draft_id="sprev-draft",
        )


class _ReviewUnitExecutor:
    def __init__(self, db) -> None:
        self._parts = ScreenplayPartArtifactQuery(db)

    async def execute(self, context, signal=None):
        del signal
        if context.unit.id == "compose-final-response":
            output = {
                "finalResponse": "当前完整剧本已审阅，可以查看正式审阅报告。",
            }
        elif context.unit.id.endswith(":validation"):
            output = {
                "title": "第 1 集审阅",
                "executionSummary": "已完成五个维度的审阅。",
                "contentText": "第 1 集审阅正文。",
                "contentJson": {
                    "verdict": "ready",
                    "issues": [],
                    "issueCount": 0,
                    "criticalIssueCount": 0,
                    "reviewedEpisode": 1,
                    "reviewedDraftId": "sprev-draft",
                    "reviewedContentDigest": "sha256:review-input",
                    "reviewDimensions": [
                        "continuity",
                        "character_arc",
                        "structure_rhythm",
                        "dialogue",
                        "format",
                    ],
                    "reviewStatus": "completed",
                    "inputContractVersion": 2,
                    "partReceipts": [
                        "receipt-continuity",
                        "receipt-character-arc",
                        "receipt-structure-rhythm",
                        "receipt-dialogue",
                        "receipt-format",
                    ],
                },
            }
        else:
            output = {"partId": context.unit.id}
        ref = await self._parts.write_host_part(
            project_id=str(context.task.owner_id),
            task_id=context.task.id,
            unit_id=context.unit.id,
            semantic_key=str(context.unit.semantic_key),
            part_kind=str(context.unit.metadata.get("unitKind") or ""),
            output=output,
        )
        return LongTaskUnitResult(
            output_ref=ref.output_ref,
            run_id=ref.run_id,
            artifact_digest=ref.content_digest,
            validation_receipt=ref.validation_receipt,
        )


async def _finalization_fixture(db):
    projects = ScreenplayV2ProjectService(db)
    workspace = await projects.create_project(
        command_id="create-finalizer-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Atomic finalizer",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    project_id = workspace["project"]["id"]
    session = await projects.ensure_current_session(project_id)
    turn_id = "turn-atomic-finalizer"
    await db.execute(
        "INSERT INTO screenplay_agent_turns "
        "(id, project_id, session_id, command_id, status, user_content, "
        "planner_run_id) VALUES (?, ?, ?, ?, 'running', ?, ?)",
        [
            turn_id,
            project_id,
            session["id"],
            "command-atomic-finalizer",
            "生成创作简报",
            "run-planner-finalizer",
        ],
    )
    manifest_digest = "sha256:atomic-finalizer-manifest"
    operations = SqliteScreenplayOperationRepository(db)
    operation = await operations.create(ScreenplayOperationCreateCommand(
        turn_id=turn_id,
        project_id=project_id,
        session_id=session["id"],
        target_role="creativeBrief",
        manifest_digest=manifest_digest,
        requirements_json={
            "targetRole": "creativeBrief",
            "baseRevisionId": None,
            "recipe": {"steps": [
                {
                    "id": "document:validation",
                    "kind": "validate_manifest_part",
                },
                {
                    "id": "compose-final-response",
                    "kind": "compose_final_response",
                },
            ]},
        },
    ))
    await operations.attach_long_task(
        operation.id,
        long_task_id="task-atomic-finalizer",
        command_id="attach-task-atomic-finalizer",
    )
    await db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, "
        "total_units, completed_units) VALUES (?, ?, ?, ?, ?, 'completed', 2, 2)",
        [
            "task-atomic-finalizer",
            "purrtypos.screenplay",
            "screenplay",
            project_id,
            "run-planner-finalizer",
        ],
    )
    await db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status, run_id) "
        "VALUES (?, ?, ?, 0, 'completed', ?)",
        [
            "task-atomic-finalizer",
            "document:validation",
            "document:validation",
            "run-planner-finalizer",
        ],
    )
    await db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status, run_id) "
        "VALUES (?, ?, ?, 1, 'completed', ?)",
        [
            "task-atomic-finalizer",
            "compose-final-response",
            "compose-final-response",
            "run-planner-finalizer",
        ],
    )
    parts = ScreenplayPartArtifactQuery(db)
    candidate_ref = await parts.write_host_part(
        project_id=project_id,
        task_id="task-atomic-finalizer",
        unit_id="document:validation",
        semantic_key="document:validation",
        part_kind="validate_manifest_part",
        output={
            "title": "创作简报",
            "executionSummary": "已完成创作简报",
            "contentText": "# 创作简报\n\n人物驱动。",
            "contentJson": {
                "schemaVersion": 1,
                "documentKind": "creative_brief",
                "fields": {"approach": "人物驱动", "premise": "意外重逢"},
            },
        },
    )
    response_ref = await parts.write_host_part(
        project_id=project_id,
        task_id="task-atomic-finalizer",
        unit_id="compose-final-response",
        semantic_key="compose-final-response",
        part_kind="compose_final_response",
        output={
            "finalResponse": "创作简报已经完成，可以在候选稿中查看。",
        },
    )
    command = ScreenplayOperationFinalizationCommand(
        operation_id=operation.id,
        expected_manifest_digest=manifest_digest,
        candidate_part_refs=(candidate_ref,),
        final_response_ref=response_ref,
    )
    finalizer = SqliteScreenplayOperationFinalizer(
        db,
        candidate_assembler=ScreenplayCandidateAssembler(db),
    )
    return operation, turn_id, command, finalizer


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ("revision", "turn", "operation"))
async def test_operation_finalization_rolls_back_every_write_on_failure(
    screenplay_db,
    failure_point,
):
    operation, turn_id, command, finalizer = await _finalization_fixture(
        screenplay_db
    )
    trigger = {
        "revision": (
            "CREATE TRIGGER inject_finalizer_failure BEFORE INSERT ON "
            "screenplay_revisions WHEN NEW.agent_task_id = 'task-atomic-finalizer' "
            "BEGIN SELECT RAISE(ABORT, 'injected revision failure'); END"
        ),
        "turn": (
            "CREATE TRIGGER inject_finalizer_failure BEFORE UPDATE OF status ON "
            "screenplay_agent_turns WHEN NEW.id = 'turn-atomic-finalizer' "
            "AND NEW.status = 'completed' "
            "BEGIN SELECT RAISE(ABORT, 'injected turn failure'); END"
        ),
        "operation": (
            "CREATE TRIGGER inject_finalizer_failure BEFORE UPDATE OF status ON "
            "screenplay_agent_operations WHEN NEW.id = '" + operation.id + "' "
            "AND NEW.status = 'succeeded' "
            "BEGIN SELECT RAISE(ABORT, 'injected operation failure'); END"
        ),
    }[failure_point]
    await screenplay_db.execute(trigger)

    with pytest.raises(Exception, match=f"injected {failure_point} failure"):
        await finalizer.finalize(command)

    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revisions "
        "WHERE agent_task_id = 'task-atomic-finalizer'"
    ) == {"count": 0}
    turn = await screenplay_db.fetch_one(
        "SELECT status, assistant_content FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    )
    assert turn == {"status": "running", "assistant_content": ""}
    stored_operation = await screenplay_db.fetch_one(
        "SELECT status, result_revision_id, finalization_receipt_id "
        "FROM screenplay_agent_operations WHERE id = ?",
        [operation.id],
    )
    assert stored_operation == {
        "status": "running",
        "result_revision_id": None,
        "finalization_receipt_id": None,
    }
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_operation_commands "
        "WHERE operation_id = ? AND command_type = 'finalize'",
        [operation.id],
    ) == {"count": 0}


async def _answer_projection_fixture(screenplay_db, suffix: str):
    projects = ScreenplayV2ProjectService(screenplay_db)
    workspace = await projects.create_project(
        command_id=f"create-answer-projector-{suffix}",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": f"Atomic answer root {suffix}",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    root_run_id = f"run-atomic-answer-{suffix}"
    turn_id = f"turn-atomic-answer-{suffix}"
    command_id = f"command-atomic-answer-{suffix}"
    run_session_id = (
        None if suffix == "null_session_id"
        else 999_999 if suffix == "wrong_session_id"
        else session["id"]
    )
    binding_project_id = (
        None if suffix == "null_project_id"
        else "wrong-project" if suffix == "wrong_project_id"
        else workspace["project"]["id"]
    )
    binding_command_id = (
        None if suffix == "null_command_id"
        else "wrong-command" if suffix == "wrong_command_id"
        else command_id
    )
    binding_attributes = (
        {"domainNamespace": "purrtypos.screenplay"}
        if suffix == "missing_profile"
        else {
            "agentProfile": "screenplay",
            "domainNamespace": (
                "wrong.domain"
                if suffix == "wrong_domain"
                else "purrtypos.screenplay"
            ),
        }
    )
    canonical_turn_id = (
        None if suffix == "null_turn_id"
        else "wrong-turn" if suffix == "wrong_turn_id"
        else turn_id
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, mode, prompt, binding_namespace, "
        "binding_aggregate_id, binding_command_id, binding_attributes_json) "
        "VALUES (?, ?, 'running', 'agent', ?, ?, ?, ?, ?)",
        [
            root_run_id,
            run_session_id,
            "解释当前项目",
            "screenplay.conversation_turn",
            binding_project_id,
            binding_command_id,
            json.dumps(binding_attributes),
        ],
    )
    if suffix != "missing_turn_event":
        await screenplay_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json, event_id, turn_id, sequence, "
            "source, kind, channel, visibility, occurred_at, emitted_at, "
            "source_event_key) VALUES (?, 'run.started', ?, ?, ?, 1, "
            "'runtime', 'run.lifecycle', 'lifecycle', 'public', ?, ?, ?)",
            [
                root_run_id,
                '{"status":"running"}',
                f"event-atomic-answer-{suffix}",
                canonical_turn_id,
                "2026-08-14T00:00:00+00:00",
                "2026-08-14T00:00:00+00:00",
                f"run:{root_run_id}:running",
            ],
        )
    await screenplay_db.execute(
        "INSERT INTO screenplay_agent_turns "
        "(id, project_id, session_id, command_id, status, user_content, "
        "planner_run_id) VALUES (?, ?, ?, ?, 'planning', ?, ?)",
        [
            turn_id,
            workspace["project"]["id"],
            session["id"],
            command_id,
            "解释当前项目",
            root_run_id,
        ],
    )
    return {
        "rootRunId": root_run_id,
        "turnId": turn_id,
        "sessionId": session["id"],
        "projectId": workspace["project"]["id"],
        "commandId": command_id,
    }


@pytest.mark.asyncio
async def test_answer_projection_joins_root_transaction_and_replays(
    screenplay_db,
):
    identity = await _answer_projection_fixture(screenplay_db, "success")
    root_run_id = identity["rootRunId"]
    turn_id = identity["turnId"]
    projector = ScreenplayAgentRootCompletionProjector(screenplay_db)
    commit = RunCommit(
        terminal_status=RunStatus.DONE,
        final_response="当前项目正在进行素材梳理。",
    )

    with pytest.raises(RuntimeError, match="later projector failed"):
        async with screenplay_db.transaction(cancellation_linearizable=True):
            await projector.project(root_run_id, commit)
            raise RuntimeError("later projector failed")
    assert await screenplay_db.fetch_one(
        "SELECT status, assistant_content FROM screenplay_agent_turns "
        "WHERE id = ?",
        [turn_id],
    ) == {"status": "planning", "assistant_content": ""}

    for _attempt in range(2):
        async with screenplay_db.transaction(cancellation_linearizable=True):
            await projector.project(root_run_id, commit)
    assert await screenplay_db.fetch_one(
        "SELECT status, assistant_content FROM screenplay_agent_turns "
        "WHERE id = ?",
        [turn_id],
    ) == {
        "status": "completed",
        "assistant_content": "当前项目正在进行素材梳理。",
    }


@pytest.mark.asyncio
async def test_orphan_root_cancel_uses_canonical_terminal_projector(
    screenplay_db,
):
    identity = await _answer_projection_fixture(screenplay_db, "orphan_cancel")
    requested = await SqliteScreenplayOperationRepository(
        screenplay_db
    ).request_cancel(
        identity["turnId"],
        idempotency_key="cancel-orphan-answer-root",
    )
    composition = create_agent_composition(screenplay_db)
    try:
        result = await AgentCancellationService(
            screenplay_db,
            composition,
        ).cancel(identity["rootRunId"])
    finally:
        await composition.shutdown()

    assert result is not None
    assert result["cancellationStatus"] == "completed"
    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [identity["rootRunId"]],
    ) == {"status": "canceled"}
    assert await screenplay_db.fetch_one(
        "SELECT status, cancel_receipt_id FROM screenplay_agent_turns "
        "WHERE id = ?",
        [identity["turnId"]],
    ) == {
        "status": "canceled",
        "cancel_receipt_id": requested.id,
    }
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ? "
        "AND source_event_key = ?",
        [
            identity["rootRunId"],
            f"run:{identity['rootRunId']}:canceled",
        ],
    ) == {"count": 1}


@pytest.mark.asyncio
async def test_root_cancel_projector_failure_rolls_back_fence(screenplay_db):
    identity = await _answer_projection_fixture(screenplay_db, "cancel_rollback")

    class FailingProjector:
        async def project(self, root_run_id, receipt):
            assert root_run_id == identity["rootRunId"]
            assert receipt.cancellation_epoch == 1
            raise RuntimeError("injected cancellation projector failure")

    composition = create_agent_composition(
        screenplay_db,
        run_cancellation_projectors=(FailingProjector(),),
    )
    try:
        cancellation = AgentCancellationService(screenplay_db, composition)
        with pytest.raises(
            RuntimeError,
            match="injected cancellation projector failure",
        ):
            await cancellation.cancel(identity["rootRunId"])
    finally:
        await composition.shutdown()

    assert await screenplay_db.fetch_one(
        "SELECT cancel_requested_at_ms, cancellation_epoch FROM ai_agent_runs "
        "WHERE id = ?",
        [identity["rootRunId"]],
    ) == {"cancel_requested_at_ms": None, "cancellation_epoch": 0}
    assert await screenplay_db.fetch_one(
        "SELECT cancel_requested_at_ms FROM screenplay_agent_turns WHERE id = ?",
        [identity["turnId"]],
    ) == {"cancel_requested_at_ms": None}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_cancellations WHERE "
        "run_id = ?",
        [identity["rootRunId"]],
    ) == {"count": 0}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_cancel_commands "
        "WHERE command_id = ?",
        [f"run-cancel:{identity['rootRunId']}"],
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_generic_root_cancel_requests_screenplay_business_cancel(
    screenplay_db,
):
    identity = await _answer_projection_fixture(screenplay_db, "generic_cancel")
    composition = create_agent_composition(screenplay_db)
    try:
        result = await AgentCancellationService(
            screenplay_db,
            composition,
        ).cancel(identity["rootRunId"])
    finally:
        await composition.shutdown()

    assert result is not None and result["cancellationStatus"] == "completed"
    persisted = await screenplay_db.fetch_one(
        "SELECT status, cancel_requested_at_ms, cancel_receipt_id FROM "
        "screenplay_agent_turns WHERE id = ?",
        [identity["turnId"]],
    )
    assert persisted is not None
    assert persisted["status"] == "canceled"
    assert persisted["cancel_requested_at_ms"] is not None
    assert str(persisted["cancel_receipt_id"] or "")
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_cancel_commands "
        "WHERE command_id = ?",
        [f"run-cancel:{identity['rootRunId']}"],
    ) == {"count": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("turn_disposition", ["rotated", "deleted"])
async def test_completed_root_cancel_replays_without_business_projector(
    screenplay_db,
    turn_disposition,
):
    identity = await _answer_projection_fixture(
        screenplay_db,
        f"cancel_replay_{turn_disposition}",
    )
    class CountingProjector:
        calls = 0

        async def project(self, root_run_id, receipt):
            self.calls += 1

    projector = CountingProjector()
    composition = create_agent_composition(
        screenplay_db,
        run_cancellation_projectors=(projector,),
    )
    try:
        cancellation = AgentCancellationService(screenplay_db, composition)
        first = await cancellation.cancel(identity["rootRunId"])
        assert first is not None and first["cancellationStatus"] == "completed"
        receipt_before = await screenplay_db.fetch_one(
            "SELECT * FROM ai_agent_run_cancellations WHERE run_id = ?",
            [identity["rootRunId"]],
        )
        event_count_before = await screenplay_db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_run_events "
            "WHERE run_id = ?",
            [identity["rootRunId"]],
        )

        if turn_disposition == "rotated":
            await screenplay_db.execute(
                "UPDATE screenplay_agent_turns SET planner_run_id = ? "
                "WHERE id = ?",
                ["run-continuation-winner", identity["turnId"]],
            )
        else:
            await screenplay_db.execute(
                "DELETE FROM screenplay_agent_turns WHERE id = ?",
                [identity["turnId"]],
            )

        assert projector.calls == 1
        projector.calls = 0
        replay = await cancellation.cancel(identity["rootRunId"])
    finally:
        await composition.shutdown()

    assert replay is not None
    assert replay["status"] == "canceled"
    assert replay["cancellationStatus"] == "completed"
    assert replay["cancellationEpoch"] == first["cancellationEpoch"]
    assert replay["newlyRequested"] is False
    assert replay["terminalized"] is False
    assert projector.calls == 0
    assert await screenplay_db.fetch_one(
        "SELECT * FROM ai_agent_run_cancellations WHERE run_id = ?",
        [identity["rootRunId"]],
    ) == receipt_before
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ?",
        [identity["rootRunId"]],
    ) == event_count_before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("root_column", "conflicting_value"),
    [("status", "failed"), ("cancellation_epoch", 2)],
)
async def test_completed_root_cancel_receipt_conflict_fails_closed(
    screenplay_db,
    root_column,
    conflicting_value,
):
    identity = await _answer_projection_fixture(
        screenplay_db,
        f"cancel_conflict_{root_column}",
    )
    composition = create_agent_composition(screenplay_db)
    try:
        cancellation = AgentCancellationService(screenplay_db, composition)
        first = await cancellation.cancel(identity["rootRunId"])
        assert first is not None and first["cancellationStatus"] == "completed"
        await screenplay_db.execute(
            f"UPDATE ai_agent_runs SET {root_column} = ? WHERE id = ?",
            [conflicting_value, identity["rootRunId"]],
        )
        with pytest.raises(
            ContractViolationError,
            match="completed cancellation receipt conflicts",
        ):
            await cancellation.cancel(identity["rootRunId"])
    finally:
        await composition.shutdown()

    assert await screenplay_db.fetch_one(
        "SELECT status, cancellation_epoch FROM ai_agent_run_cancellations "
        "WHERE run_id = ?",
        [identity["rootRunId"]],
    ) == {"status": "completed", "cancellation_epoch": 1}


@pytest.mark.asyncio
async def test_truncate_active_foreign_root_times_out_without_deleting_business_state(
    screenplay_db,
):
    identity = await _answer_projection_fixture(screenplay_db, "truncate_timeout")
    await screenplay_db.execute(
        "UPDATE ai_agent_runs SET execution_owner_id = 'foreign-worker', "
        "lease_expires_at_ms = 9999999999999 WHERE id = ?",
        [identity["rootRunId"]],
    )
    composition = create_agent_composition(screenplay_db)
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id=composition.execution_owner_id,
        composition=composition,
        projects=object(),
    )
    service._truncate_wait_timeout_seconds = 0.01
    service._truncate_poll_interval_seconds = 0.001
    try:
        with pytest.raises(AppError, match="truncate cancellation timed out"):
            await service.truncate_from_turn(identity["turnId"])
    finally:
        await composition.shutdown()

    assert await screenplay_db.fetch_one(
        "SELECT status FROM screenplay_agent_turns WHERE id = ?",
        [identity["turnId"]],
    ) == {"status": "planning"}
    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [identity["rootRunId"]],
    ) == {"status": "running"}
    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_run_cancellations WHERE run_id = ?",
        [identity["rootRunId"]],
    ) == {"status": "draining"}


@pytest.mark.asyncio
async def test_truncate_cancellation_error_does_not_delete_business_state(
    screenplay_db,
):
    identity = await _answer_projection_fixture(screenplay_db, "truncate_error")
    local_task = asyncio.create_task(asyncio.Event().wait())
    ScreenplayAgentService._remember_task(
        f"turn:{identity['turnId']}",
        local_task,
    )
    await asyncio.sleep(0)
    composition = create_agent_composition(screenplay_db)
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id=composition.execution_owner_id,
        composition=composition,
        projects=object(),
    )

    class RejectingCancellation:
        async def cancel(self, _run_id):
            raise RuntimeError("injected truncate cancellation failure")

    service._cancellation = RejectingCancellation()
    try:
        with pytest.raises(
            RuntimeError,
            match="injected truncate cancellation failure",
        ):
            await service.truncate_from_turn(identity["turnId"])
        assert local_task.done() is False
    finally:
        local_task.cancel()
        with suppress(asyncio.CancelledError):
            await local_task
        _ACTIVE_TASKS.pop(f"turn:{identity['turnId']}", None)
        await composition.shutdown()

    assert await screenplay_db.fetch_one(
        "SELECT id FROM screenplay_agent_turns WHERE id = ?",
        [identity["turnId"]],
    ) == {"id": identity["turnId"]}
    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [identity["rootRunId"]],
    ) == {"status": "running"}


@pytest.mark.asyncio
async def test_truncate_persists_fence_before_canceling_and_awaiting_local_wrapper(
    screenplay_db,
):
    identity = await _answer_projection_fixture(screenplay_db, "truncate_local")
    local_cleaned = asyncio.Event()

    async def local_wrapper():
        try:
            await asyncio.Event().wait()
        finally:
            local_cleaned.set()

    local_task = asyncio.create_task(local_wrapper())
    ScreenplayAgentService._remember_task(
        f"turn:{identity['turnId']}",
        local_task,
    )
    await asyncio.sleep(0)
    composition = create_agent_composition(screenplay_db)
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id=composition.execution_owner_id,
        composition=composition,
        projects=object(),
    )

    class PersistingCancellation:
        async def cancel(self, run_id):
            assert run_id == identity["rootRunId"]
            assert local_task.done() is False
            async with screenplay_db.transaction(cancellation_linearizable=True):
                await screenplay_db.execute(
                    "UPDATE ai_agent_runs SET status = 'canceled', "
                    "execution_owner_id = NULL, lease_expires_at_ms = NULL, "
                    "cancellation_epoch = 1, cancel_requested_at_ms = 1 "
                    "WHERE id = ?",
                    [run_id],
                )
                await screenplay_db.execute(
                    "INSERT INTO ai_agent_run_cancellations "
                    "(run_id, cancellation_epoch, status, requested_at_ms, "
                    "completed_at_ms) VALUES (?, 1, 'completed', 1, 1)",
                    [run_id],
                )
            return {"cancellationStatus": "completed"}

    service._cancellation = PersistingCancellation()
    try:
        removed = await service.truncate_from_turn(identity["turnId"])
    finally:
        await composition.shutdown()

    assert removed["deletedTurnIds"] == [identity["turnId"]]
    assert local_task.done() is True
    assert local_cleaned.is_set()
    assert f"turn:{identity['turnId']}" not in _ACTIVE_TASKS


async def _claimed_pre_root_turn(db, *, owner_id: str, suffix: str):
    projects = ScreenplayV2ProjectService(db)
    workspace = await projects.create_project(
        command_id=f"create-pre-root-{suffix}",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": f"Pre Root {suffix}",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "竞态"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    repository = SqliteScreenplayAgentRepository(db, owner_id=owner_id)
    turn = await repository.begin_turn(
        command_id=f"turn-pre-root-{suffix}",
        project_id=workspace["project"]["id"],
        session_id=session["id"],
        content="开始分析",
        stage_command=None,
        runtime_profile={},
    )
    assert await repository.claim_turn(turn["id"])
    return repository, turn


@pytest.mark.asyncio
async def test_truncate_waits_for_foreign_pre_root_turn_claim(screenplay_db):
    _repository, turn = await _claimed_pre_root_turn(
        screenplay_db,
        owner_id="foreign-pre-root-owner",
        suffix="foreign",
    )
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id="truncate-owner",
        projects=object(),
    )
    service._truncate_wait_timeout_seconds = 0.01
    service._truncate_poll_interval_seconds = 0.001

    with pytest.raises(AppError, match="truncate cancellation timed out"):
        await service.truncate_from_turn(turn["id"])

    persisted = await screenplay_db.fetch_one(
        "SELECT status, execution_owner_id, cancel_requested_at_ms "
        "FROM screenplay_agent_turns WHERE id = ?",
        [turn["id"]],
    )
    assert persisted is not None
    assert persisted["status"] == "planning"
    assert persisted["execution_owner_id"] == "foreign-pre-root-owner"
    assert persisted["cancel_requested_at_ms"] is not None
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs"
    ) == {"count": 0}

    await screenplay_db.execute(
        "UPDATE screenplay_agent_turns SET lease_expires_at_ms = 0 "
        "WHERE id = ?",
        [turn["id"]],
    )
    removed = await service.truncate_from_turn(turn["id"])
    assert removed["deletedTurnIds"] == [turn["id"]]
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_turns WHERE id = ?",
        [turn["id"]],
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_expired_canceled_turn_claim_release_respects_fresh_heartbeat(
    screenplay_db,
):
    repository, turn = await _claimed_pre_root_turn(
        screenplay_db,
        owner_id="foreign-heartbeat-owner",
        suffix="foreign-heartbeat",
    )
    await SqliteScreenplayOperationRepository(screenplay_db).request_cancel(
        turn["id"],
        idempotency_key="cancel-foreign-heartbeat",
    )
    await screenplay_db.execute(
        "UPDATE screenplay_agent_turns SET lease_expires_at_ms = 200 "
        "WHERE id = ?",
        [turn["id"]],
    )

    assert await repository.release_expired_canceled_claim(
        turn["id"],
        100,
    ) is False
    live = await screenplay_db.fetch_one(
        "SELECT execution_owner_id, lease_expires_at_ms, attempt, "
        "cancel_requested_at_ms FROM screenplay_agent_turns WHERE id = ?",
        [turn["id"]],
    )
    assert live is not None
    assert live["execution_owner_id"] == "foreign-heartbeat-owner"
    assert live["lease_expires_at_ms"] == 200
    assert live["attempt"] == 1
    assert live["cancel_requested_at_ms"] is not None

    assert await repository.release_expired_canceled_claim(
        turn["id"],
        200,
    ) is True
    persisted = await screenplay_db.fetch_one(
        "SELECT execution_owner_id, lease_expires_at_ms, heartbeat_at_ms, "
        "attempt, cancel_requested_at_ms FROM screenplay_agent_turns WHERE id = ?",
        [turn["id"]],
    )
    assert persisted is not None
    assert persisted["execution_owner_id"] is None
    assert persisted["lease_expires_at_ms"] is None
    assert persisted["heartbeat_at_ms"] is None
    assert persisted["attempt"] == 1
    assert persisted["cancel_requested_at_ms"] is not None


@pytest.mark.asyncio
async def test_truncate_fences_and_waits_for_local_pre_root_turn_claim(
    screenplay_db,
):
    _repository, turn = await _claimed_pre_root_turn(
        screenplay_db,
        owner_id="local-pre-root-owner",
        suffix="local",
    )
    fence_seen = asyncio.Event()

    async def local_wrapper():
        try:
            await asyncio.Event().wait()
        finally:
            persisted = await screenplay_db.fetch_one(
                "SELECT cancel_requested_at_ms FROM screenplay_agent_turns "
                "WHERE id = ?",
                [turn["id"]],
            )
            assert persisted is not None
            assert persisted["cancel_requested_at_ms"] is not None
            fence_seen.set()

    local_task = asyncio.create_task(local_wrapper())
    ScreenplayAgentService._remember_task(f"turn:{turn['id']}", local_task)
    await asyncio.sleep(0)
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id="local-pre-root-owner",
        projects=object(),
    )

    removed = await service.truncate_from_turn(turn["id"])

    assert removed["deletedTurnIds"] == [turn["id"]]
    assert local_task.done() is True
    assert fence_seen.is_set()
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs"
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_pre_root_cancel_fence_blocks_submit_after_validate(screenplay_db):
    repository, turn = await _claimed_pre_root_turn(
        screenplay_db,
        owner_id="pre-root-owner",
        suffix="before-submit",
    )
    lifecycle = _ScreenplayTurnRunLifecycle(
        screenplay_db,
        repository,
        SqliteScreenplayOperationRepository(screenplay_db),
        turn["id"],
    )
    await lifecycle.validate()
    await SqliteScreenplayOperationRepository(screenplay_db).request_cancel(
        turn["id"],
        idempotency_key="cancel-before-root-submit",
    )

    with pytest.raises(ScreenplayRootStartLost, match="not startable"):
        await lifecycle.before_submit()
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs"
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_pre_root_cancel_fence_blocks_atomic_root_begin(screenplay_db):
    repository, claimed = await _claimed_pre_root_turn(
        screenplay_db,
        owner_id="pre-root-begin-owner",
        suffix="atomic-begin",
    )
    turn = await repository.load_turn(claimed["id"])
    assert turn is not None
    await SqliteScreenplayOperationRepository(screenplay_db).request_cancel(
        turn["id"],
        idempotency_key="cancel-before-atomic-root-begin",
    )
    params = RunCreateParams(
        session_id=int(turn["sessionId"]),
        prompt="开始分析",
        mode="agent",
        turn_id=turn["id"],
        binding=RunBinding(
            namespace="screenplay.conversation_turn",
            aggregate_id=turn["projectId"],
            command_id=turn["commandId"],
            attributes={
                "agentProfile": "screenplay",
                "domainNamespace": "purrtypos.screenplay",
                "turnExecutionOwner": "pre-root-begin-owner",
                "turnAttempt": int(turn["attempt"]),
            },
        ),
    )
    outputs = SqliteAgentOutputRepository(
        screenplay_db,
        run_repository=SqliteRunRepository(screenplay_db),
        run_begin_projector=ScreenplayContinuationBeginProjector(screenplay_db),
    )

    with pytest.raises(ScreenplayRootStartLost, match="claim was lost"):
        await outputs.begin_run_lifecycle(
            params,
            AgentEvent(
                type=CoreEventType.RUN_STARTED,
                payload={"status": RunStatus.RUNNING.value},
            ),
        )

    assert await screenplay_db.fetch_one(
        "SELECT planner_run_id AS root_run_id FROM screenplay_agent_turns WHERE id = ?",
        [turn["id"]],
    ) == {"root_run_id": None}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs"
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_truncate_repository_rechecks_active_root_inside_delete_transaction(
    screenplay_db,
):
    identity = await _answer_projection_fixture(screenplay_db, "truncate_guard")
    repository = SqliteScreenplayAgentRepository(
        screenplay_db,
        owner_id="truncate-guard-owner",
    )

    with pytest.raises(AppError, match="truncate runtime is still active"):
        await repository.truncate_from_turn(identity["turnId"])

    assert await screenplay_db.fetch_one(
        "SELECT id FROM screenplay_agent_turns WHERE id = ?",
        [identity["turnId"]],
    ) == {"id": identity["turnId"]}


@pytest.mark.asyncio
async def test_truncate_cancels_long_task_claim_before_delete(
    screenplay_db,
):
    paused, turn_id, _root_run_id, _turn = await _paused_continuation_fixture(
        screenplay_db
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status, worker_id, "
        "lease_expires_at_ms) VALUES "
        "('task-atomic-finalizer', 'unit-active', 'unit-active', 1, "
        "'claimed', 'foreign-unit-worker', 9999999999999)"
    )

    class InspectingRepository(SqliteScreenplayAgentRepository):
        async def truncate_from_turn(self, requested_turn_id):
            assert await screenplay_db.fetch_one(
                "SELECT status, worker_id, lease_expires_at_ms FROM "
                "ai_agent_long_task_units WHERE task_id = "
                "'task-atomic-finalizer' AND unit_id = 'unit-active'"
            ) == {
                "status": "canceled",
                "worker_id": None,
                "lease_expires_at_ms": None,
            }
            return await super().truncate_from_turn(requested_turn_id)

    service = ScreenplayAgentService(
        screenplay_db,
        owner_id="truncate-task-owner",
        projects=object(),
        repository=InspectingRepository(
            screenplay_db,
            owner_id="truncate-task-owner",
        ),
    )
    removed = await service.truncate_from_turn(turn_id)

    assert removed["deletedOperationIds"] == [paused.id]
    assert await screenplay_db.fetch_one(
        "SELECT id FROM ai_agent_long_tasks WHERE id = 'task-atomic-finalizer'"
    ) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch",
    (
        "null_turn_id",
        "wrong_turn_id",
        "missing_turn_event",
        "null_session_id",
        "wrong_session_id",
        "null_project_id",
        "wrong_project_id",
        "null_command_id",
        "wrong_command_id",
        "missing_profile",
        "wrong_domain",
    ),
)
async def test_answer_projection_fails_closed_on_root_identity_mismatch(
    screenplay_db,
    mismatch,
):
    identity = await _answer_projection_fixture(screenplay_db, mismatch)
    root_run_id = identity["rootRunId"]

    with pytest.raises(ScreenplayAgentRootCompletionError) as raised:
        async with screenplay_db.transaction(cancellation_linearizable=True):
            await ScreenplayAgentRootCompletionProjector(screenplay_db).project(
                root_run_id,
                RunCommit(
                    terminal_status=RunStatus.DONE,
                    final_response="不应提交",
                ),
            )

    assert raised.value.retryable is False
    assert await screenplay_db.fetch_one(
        "SELECT status, assistant_content FROM screenplay_agent_turns "
        "WHERE id = ?",
        [identity["turnId"]],
    ) == {"status": "planning", "assistant_content": ""}


@pytest.mark.asyncio
async def test_answer_projection_classifies_only_sqlite_transients_as_retryable(
    screenplay_db,
    monkeypatch,
):
    identity = await _answer_projection_fixture(screenplay_db, "transient")

    async def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(screenplay_db, "fetch_all", locked)
    with pytest.raises(ScreenplayAgentRootCompletionError) as raised:
        await ScreenplayAgentRootCompletionProjector(screenplay_db).project(
            identity["rootRunId"],
            RunCommit(
                terminal_status=RunStatus.DONE,
                final_response="稍后重试",
            ),
        )

    assert raised.value.retryable is True


@pytest.mark.asyncio
async def test_operation_finalization_replay_returns_the_same_receipt(screenplay_db):
    operation, turn_id, command, finalizer = await _finalization_fixture(
        screenplay_db
    )

    first = await finalizer.finalize(command)
    replayed = await finalizer.finalize(command)

    assert replayed == first
    assert first.operation_id == operation.id
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revisions "
        "WHERE agent_task_id = 'task-atomic-finalizer'"
    ) == {"count": 1}
    assert await screenplay_db.fetch_one(
        "SELECT status, assistant_content FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    ) == {
        "status": "completed",
        "assistant_content": "创作简报已经完成，可以在候选稿中查看。",
    }
    assert await screenplay_db.fetch_one(
        "SELECT status, result_revision_id, finalization_receipt_id "
        "FROM screenplay_agent_operations WHERE id = ?",
        [operation.id],
    ) == {
        "status": "succeeded",
        "result_revision_id": first.revision_id,
        "finalization_receipt_id": first.id,
    }


@pytest.mark.asyncio
async def test_operation_usage_is_run_idempotent_and_revisioned(screenplay_db):
    operation, _turn_id, _command, _finalizer = await _finalization_fixture(
        screenplay_db
    )
    operations = SqliteScreenplayOperationRepository(screenplay_db)
    operation = await operations.load(operation.id)
    assert operation is not None

    first = await operations.record_usage(
        operation.id,
        run_id="run-usage-1",
        usage=OperationUsage(
            invocation_count=1,
            input_tokens=300,
            output_tokens=60,
            reasoning_tokens=12,
        ),
        expected_revision=operation.revision,
    )
    replay = await operations.record_usage(
        operation.id,
        run_id="run-usage-1",
        usage=OperationUsage(
            invocation_count=1,
            input_tokens=300,
            output_tokens=60,
            reasoning_tokens=12,
        ),
        expected_revision=operation.revision,
    )

    assert replay == first
    assert first.revision == operation.revision + 1
    assert first.usage == OperationUsage(
        invocation_count=1,
        input_tokens=300,
        output_tokens=60,
        reasoning_tokens=12,
    )


@pytest.mark.asyncio
async def test_part_run_usage_settlement_counts_retries_once_per_run(screenplay_db):
    long_tasks = SqliteLongTaskRepository(screenplay_db)
    task = await long_tasks.create(
        "task-part-usage",
        LongTaskCreateCommand(
            namespace="purrtypos.screenplay",
            kind="screenplay.screenplayDraft",
            owner_id="project-part-usage",
            created_by_run_id="root-part-usage",
            units=(LongTaskUnitSpec(id="draft:1:scene-1", position=0),),
            budget_limits=LongTaskBudgetLimits(
                max_invocation_attempts=8,
                max_input_tokens=10_000,
                max_output_tokens=10_000,
                max_reasoning_tokens=10_000,
            ),
        ),
    )
    for values in (
        ("run-part-failed", "failed", 2, 0, 300, 80, 20),
        ("run-part-retry", "done", 1, 0, 200, 40, 10),
    ):
        await screenplay_db.execute(
            "INSERT INTO ai_agent_runs "
            "(id, status, prompt, model_attempt_count, "
            "unreported_usage_attempts, input_tokens, output_tokens, "
            "reasoning_tokens) VALUES (?, ?, '', ?, ?, ?, ?, ?)",
            list(values),
        )

    first = await _record_part_run_usage(
        screenplay_db,
        long_tasks,
        task.id,
        "run-part-failed",
    )
    replay = await _record_part_run_usage(
        screenplay_db,
        long_tasks,
        task.id,
        "run-part-failed",
    )
    settled = await _record_part_run_usage(
        screenplay_db,
        long_tasks,
        task.id,
        "run-part-retry",
    )

    assert replay == first
    assert settled.usage.invocation_count == 3
    assert settled.usage.input_tokens == 500
    assert settled.usage.output_tokens == 120
    assert settled.usage.reasoning_tokens == 30
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_task_usage "
        "WHERE task_id = ?",
        [task.id],
    ) == {"count": 2}


@pytest.mark.asyncio
async def test_unreported_part_run_usage_fails_long_task_before_part_commit(
    screenplay_db,
):
    long_tasks = SqliteLongTaskRepository(screenplay_db)
    task = await long_tasks.create(
        "task-unreported-part-usage",
        LongTaskCreateCommand(
            namespace="purrtypos.screenplay",
            kind="screenplay.screenplayDraft",
            owner_id="project-unreported-part-usage",
            created_by_run_id="root-unreported-part-usage",
            units=(LongTaskUnitSpec(id="draft:1:scene-1", position=0),),
            budget_limits=LongTaskBudgetLimits(
                max_invocation_attempts=8,
                max_input_tokens=10_000,
                max_output_tokens=10_000,
                max_reasoning_tokens=10_000,
            ),
        ),
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, status, prompt, model_attempt_count, "
        "unreported_usage_attempts) VALUES "
        "('run-unreported-part-usage', 'failed', '', 1, 1)"
    )

    with pytest.raises(RuntimeError, match="runtime_budget_exceeded"):
        await _record_part_run_usage(
            screenplay_db,
            long_tasks,
            task.id,
            "run-unreported-part-usage",
        )

    failed = await long_tasks.load(task.id)
    units = await long_tasks.list_units(task.id)
    assert failed is not None and failed.status.value == "failed"
    assert units[0].error_code == "runtime_budget_exceeded"
    assert units[0].metadata["budgetKind"] == "provider_usage_unreported"


@pytest.mark.asyncio
async def test_unit_executor_settles_bound_run_before_host_part_write(
    screenplay_db,
):
    long_tasks = SqliteLongTaskRepository(screenplay_db)
    task = await long_tasks.create(
        "task-bound-run-settlement",
        LongTaskCreateCommand(
            namespace="purrtypos.screenplay",
            kind="screenplay.screenplayDraft",
            owner_id="project-bound-run-settlement",
            created_by_run_id="root-bound-run-settlement",
            units=(LongTaskUnitSpec(id="draft:1:scene-1", position=0),),
            budget_limits=LongTaskBudgetLimits(
                max_invocation_attempts=8,
                max_input_tokens=10_000,
                max_output_tokens=10_000,
                max_reasoning_tokens=10_000,
            ),
        ),
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, status, prompt, model_attempt_count, input_tokens, "
        "output_tokens, reasoning_tokens) VALUES "
        "('run-bound-part', 'done', '', 1, 120, 30, 5)"
    )
    executor = object.__new__(ScreenplayTaskUnitExecutor)
    executor._db = screenplay_db
    executor._runtime = object()
    executor._long_tasks = long_tasks

    async def task_view(_context):
        return {
            "id": task.id,
            "projectId": "project-bound-run-settlement",
            "targetRole": "screenplayDraft",
            "units": [{
                "id": "draft:1:scene-1",
                "kind": "generate_draft_scene",
                "input": {"sceneId": "scene-1"},
                "partContractKey": "draft_scene",
            }],
        }

    class Delegate:
        async def execute(self, *, bind_run, **_kwargs):
            await bind_run("run-bound-part")
            return {"sceneId": "scene-1", "sceneText": "正文"}

    class Parts:
        async def write_host_part(self, **_kwargs):
            current = await long_tasks.load(task.id)
            assert current is not None
            assert current.usage.invocation_count == 1
            return SimpleNamespace(
                output_ref="artifact://scene-1",
                run_id="run-bound-part",
                content_digest="sha256:scene-1",
                validation_receipt={},
            )

    executor._task_view = task_view
    executor._delegate = Delegate()
    executor._parts = Parts()
    bound = []

    async def bind_run(run_id):
        bound.append(run_id)

    result = await executor.execute(SimpleNamespace(
        task=SimpleNamespace(id=task.id),
        unit=SimpleNamespace(
            id="draft:1:scene-1",
            semantic_key="draft:1:scene-1",
        ),
        bind_run=bind_run,
    ))

    assert bound == ["run-bound-part"]
    assert result.run_id == "run-bound-part"


@pytest.mark.asyncio
async def test_run_usage_projects_all_same_run_invocations_and_excludes_foreign_run(
    screenplay_db,
):
    operation, _turn_id, _command, _finalizer = await _finalization_fixture(
        screenplay_db
    )
    for run_id in ("usage-root", "usage-foreign"):
        await screenplay_db.execute(
            "INSERT INTO ai_agent_runs (id, status, prompt) "
            "VALUES (?, 'running', '')",
            [run_id],
        )
    for run_id, sequence in (
        ("usage-root", 1),
        ("usage-root", 2),
        ("usage-foreign", 1),
    ):
        await screenplay_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json, event_id, sequence, source, "
            "kind, channel, visibility, source_event_key) VALUES "
            "(?, 'provider.usage', ?, ?, ?, 'provider', 'provider.usage', "
            "'diagnostic', 'private', ?)",
            [
                run_id,
                json.dumps({
                    "inputTokens": 10,
                    "outputTokens": 4,
                    "reasoningOutputTokens": 2,
                }),
                f"event-{run_id}-{sequence}",
                sequence,
                f"usage:{run_id}:{sequence}",
            ],
        )
    projector = ScreenplayAgentRootCompletionProjector(screenplay_db)

    async with screenplay_db.transaction(cancellation_linearizable=True):
        await projector._project_operation_usage("usage-root", operation.id)
        await projector._project_operation_usage("usage-root", operation.id)

    stored = await SqliteScreenplayOperationRepository(screenplay_db).load(
        operation.id
    )
    assert stored is not None
    assert stored.usage == OperationUsage(
        invocation_count=2,
        input_tokens=20,
        output_tokens=8,
        reasoning_tokens=4,
    )
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_operation_usage "
        "WHERE operation_id = ?",
        [operation.id],
    ) == {"count": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "terminal_status",
        "business_status",
        "expected_task_status",
        "expected_unit_status",
    ),
    (
        (RunStatus.FAILED, "failed", "failed", "failed"),
        (RunStatus.BLOCKED, "failed", "canceled", "canceled"),
        (RunStatus.CANCELED, "canceled", "canceled", "canceled"),
    ),
)
async def test_non_success_root_projects_usage_and_business_terminal_once(
    screenplay_db,
    terminal_status,
    business_status,
    expected_task_status,
    expected_unit_status,
):
    operation, turn_id, _command, _finalizer = await _finalization_fixture(
        screenplay_db
    )
    turn = await screenplay_db.fetch_one(
        "SELECT project_id, session_id, command_id FROM screenplay_agent_turns "
        "WHERE id = ?",
        [turn_id],
    )
    root_run_id = f"usage-terminal-{terminal_status.value}"
    await screenplay_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, mode, prompt, binding_namespace, "
        "binding_aggregate_id, binding_command_id, binding_attributes_json) "
        "VALUES (?, ?, 'running', 'agent', '', ?, ?, ?, ?)",
        [
            root_run_id,
            turn["session_id"],
            "screenplay.conversation_turn",
            turn["project_id"],
            turn["command_id"],
            json.dumps({
                "agentProfile": "screenplay",
                "domainNamespace": "purrtypos.screenplay",
            }),
        ],
    )
    await screenplay_db.execute(
        "UPDATE screenplay_agent_turns SET planner_run_id = ? WHERE id = ?",
        [root_run_id, turn_id],
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json, event_id, turn_id, sequence, "
        "source, kind, channel, visibility, source_event_key) VALUES "
        "(?, 'run.started', '{\"status\":\"running\"}', ?, ?, 1, "
        "'runtime', 'run.lifecycle', 'lifecycle', 'public', ?)",
        [root_run_id, f"event-{root_run_id}", turn_id, f"run:{root_run_id}:running"],
    )
    await screenplay_db.execute(
        "UPDATE ai_agent_long_tasks SET created_by_run_id = ?, "
        "status = 'running', total_units = 1, completed_units = 0, "
        "failed_units = 0 WHERE id = 'task-atomic-finalizer'",
        [root_run_id],
    )
    await screenplay_db.execute(
        "DELETE FROM ai_agent_long_task_units "
        "WHERE task_id = 'task-atomic-finalizer'"
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status, worker_id, "
        "lease_expires_at_ms) VALUES "
        "('task-atomic-finalizer', 'terminal-unit', 'terminal-unit', 1, "
        "'claimed', 'terminal-worker', 9999999999999)"
    )
    for sequence in (2, 3):
        await screenplay_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json, event_id, sequence, source, "
            "kind, channel, visibility, source_event_key) VALUES "
            "(?, 'provider.usage', ?, ?, ?, 'provider', 'provider.usage', "
            "'diagnostic', 'private', ?)",
            [
                root_run_id,
                json.dumps({"inputTokens": 11, "outputTokens": 5}),
                f"usage-event-{root_run_id}-{sequence}",
                sequence,
                f"usage:{root_run_id}:{sequence}",
            ],
        )
    if terminal_status is RunStatus.CANCELED:
        await SqliteScreenplayOperationRepository(
            screenplay_db
        ).request_cancel(turn_id, idempotency_key=f"cancel-{root_run_id}")
    commit = RunCommit(
        terminal_status=terminal_status,
        error=("provider failed" if terminal_status is RunStatus.FAILED else None),
    )
    projector = ScreenplayAgentRootCompletionProjector(screenplay_db)

    for _attempt in range(2):
        async with screenplay_db.transaction(cancellation_linearizable=True):
            await projector.project(root_run_id, commit)

    stored = await SqliteScreenplayOperationRepository(screenplay_db).load(
        operation.id
    )
    assert stored is not None
    assert stored.status.value == business_status
    assert stored.usage == OperationUsage(
        invocation_count=2,
        input_tokens=22,
        output_tokens=10,
        reasoning_tokens=0,
    )
    assert await screenplay_db.fetch_one(
        "SELECT status FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    ) == {"status": business_status}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_operation_usage "
        "WHERE operation_id = ?",
        [operation.id],
    ) == {"count": 1}
    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_long_tasks "
        "WHERE id = 'task-atomic-finalizer'"
    ) == {"status": expected_task_status}
    assert await screenplay_db.fetch_one(
        "SELECT status, worker_id, lease_expires_at_ms, error_code "
        "FROM ai_agent_long_task_units WHERE task_id = "
        "'task-atomic-finalizer' AND unit_id = 'terminal-unit'"
    ) == {
        "status": expected_unit_status,
        "worker_id": None,
        "lease_expires_at_ms": None,
        "error_code": (
            "provider failed" if terminal_status is RunStatus.FAILED else None
        ),
    }


@pytest.mark.asyncio
async def test_failed_root_rolls_back_long_task_when_business_projection_fails(
    screenplay_db,
):
    operation, turn_id, _command, _finalizer = await _finalization_fixture(
        screenplay_db
    )
    turn = await screenplay_db.fetch_one(
        "SELECT project_id, session_id, command_id FROM screenplay_agent_turns "
        "WHERE id = ?",
        [turn_id],
    )
    root_run_id = "run-failed-root-rollback"
    await screenplay_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, mode, prompt, binding_namespace, "
        "binding_aggregate_id, binding_command_id, binding_attributes_json) "
        "VALUES (?, ?, 'running', 'agent', '', ?, ?, ?, ?)",
        [
            root_run_id,
            turn["session_id"],
            "screenplay.conversation_turn",
            turn["project_id"],
            turn["command_id"],
            json.dumps({
                "agentProfile": "screenplay",
                "domainNamespace": "purrtypos.screenplay",
            }),
        ],
    )
    await screenplay_db.execute(
        "UPDATE screenplay_agent_turns SET planner_run_id = ? WHERE id = ?",
        [root_run_id, turn_id],
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json, event_id, turn_id, sequence, "
        "source, kind, channel, visibility, source_event_key) VALUES "
        "(?, 'run.started', '{}', ?, ?, 1, 'runtime', 'run.lifecycle', "
        "'lifecycle', 'public', ?)",
        [root_run_id, f"event-{root_run_id}", turn_id, f"run:{root_run_id}:running"],
    )
    await screenplay_db.execute(
        "UPDATE ai_agent_long_tasks SET created_by_run_id = ?, "
        "status = 'running', total_units = 1, completed_units = 0, "
        "failed_units = 0 WHERE id = 'task-atomic-finalizer'",
        [root_run_id],
    )
    await screenplay_db.execute(
        "DELETE FROM ai_agent_long_task_units "
        "WHERE task_id = 'task-atomic-finalizer'"
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status, worker_id, "
        "lease_expires_at_ms) VALUES ('task-atomic-finalizer', "
        "'rollback-unit', 'rollback-unit', 1, 'claimed', "
        "'rollback-worker', 9999999999999)"
    )
    await screenplay_db.execute(
        "CREATE TRIGGER reject_failed_operation BEFORE UPDATE OF status ON "
        "screenplay_agent_operations WHEN NEW.id = '" + operation.id + "' "
        "AND NEW.status = 'failed' "
        "BEGIN SELECT RAISE(ABORT, 'reject failed operation'); END"
    )

    with pytest.raises(
        ScreenplayAgentRootCompletionError,
        match="could not be committed",
    ):
        async with screenplay_db.transaction(cancellation_linearizable=True):
            await ScreenplayAgentRootCompletionProjector(screenplay_db).project(
                root_run_id,
                RunCommit(
                    terminal_status=RunStatus.FAILED,
                    error="execution_lease_expired",
                ),
            )

    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_long_tasks "
        "WHERE id = 'task-atomic-finalizer'"
    ) == {"status": "running"}
    assert await screenplay_db.fetch_one(
        "SELECT status, worker_id, lease_expires_at_ms FROM "
        "ai_agent_long_task_units WHERE task_id = 'task-atomic-finalizer'"
    ) == {
        "status": "claimed",
        "worker_id": "rollback-worker",
        "lease_expires_at_ms": 9999999999999,
    }
    assert await screenplay_db.fetch_one(
        "SELECT status FROM screenplay_agent_operations WHERE id = ?",
        [operation.id],
    ) == {"status": "running"}
    assert await screenplay_db.fetch_one(
        "SELECT status FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    ) == {"status": "running"}


@pytest.mark.asyncio
async def test_orphan_recovery_pauses_recoverable_screenplay_task_and_usage(
    screenplay_db,
):
    operation, turn_id, _command, _finalizer = await _finalization_fixture(
        screenplay_db
    )
    turn = await screenplay_db.fetch_one(
        "SELECT project_id, session_id, command_id FROM screenplay_agent_turns "
        "WHERE id = ?",
        [turn_id],
    )
    assert turn is not None
    root_run_id = "run-orphan-screenplay-failure"
    await screenplay_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, mode, prompt, binding_namespace, "
        "binding_aggregate_id, binding_command_id, binding_attributes_json, "
        "execution_owner_id, heartbeat_at_ms, lease_expires_at_ms) "
        "VALUES (?, ?, 'running', 'agent', '', ?, ?, ?, ?, "
        "'dead-screenplay-worker', 1, 2)",
        [
            root_run_id,
            turn["session_id"],
            "screenplay.conversation_turn",
            turn["project_id"],
            turn["command_id"],
            json.dumps({
                "agentProfile": "screenplay",
                "domainNamespace": "purrtypos.screenplay",
            }),
        ],
    )
    await screenplay_db.execute(
        "UPDATE screenplay_agent_turns SET planner_run_id = ? WHERE id = ?",
        [root_run_id, turn_id],
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json, event_id, turn_id, sequence, "
        "source, kind, channel, visibility, source_event_key) VALUES "
        "(?, 'run.started', '{\"status\":\"running\"}', ?, ?, 1, "
        "'runtime', 'run.lifecycle', 'lifecycle', 'public', ?)",
        [root_run_id, f"event-{root_run_id}", turn_id, f"run:{root_run_id}:running"],
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json, event_id, turn_id, sequence, source, "
        "kind, channel, visibility, source_event_key) VALUES "
        "(?, 'provider.usage', ?, ?, ?, 2, 'provider', 'provider.usage', "
        "'diagnostic', 'private', ?)",
        [
            root_run_id,
            json.dumps({"inputTokens": 17, "outputTokens": 6}),
            f"usage-event-{root_run_id}",
            turn_id,
            f"usage:{root_run_id}",
        ],
    )
    await screenplay_db.execute(
        "UPDATE ai_agent_long_tasks SET created_by_run_id = ?, "
        "status = 'running', total_units = 1, completed_units = 0, "
        "failed_units = 0 WHERE id = 'task-atomic-finalizer'",
        [root_run_id],
    )
    await screenplay_db.execute(
        "DELETE FROM ai_agent_long_task_runs "
        "WHERE task_id = 'task-atomic-finalizer'"
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_long_task_runs (task_id, run_id, relation) "
        "VALUES ('task-atomic-finalizer', ?, 'created')",
        [root_run_id],
    )
    await screenplay_db.execute(
        "DELETE FROM ai_agent_long_task_units "
        "WHERE task_id = 'task-atomic-finalizer'"
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status, worker_id, "
        "lease_expires_at_ms) VALUES "
        "('task-atomic-finalizer', 'orphan-unit', 'orphan-unit', 1, "
        "'claimed', 'dead-unit-worker', 9999999999999)"
    )
    composition = create_agent_composition(screenplay_db)
    recovery = AgentOrphanRecoveryService(screenplay_db, composition)
    monitor = asyncio.create_task(monitor_orphaned_runs(
        recover_orphans=recovery.recover,
        poll_interval_seconds=0.01,
    ))
    try:
        await asyncio.sleep(0.05)
        assert await screenplay_db.fetch_one(
            "SELECT status FROM ai_agent_runs WHERE id = ?",
            [root_run_id],
        ) == {"status": "running"}
        await screenplay_db.execute(
            "UPDATE ai_agent_long_task_units SET lease_expires_at_ms = 1 "
            "WHERE task_id = 'task-atomic-finalizer' "
            "AND unit_id = 'orphan-unit'"
        )
        for _ in range(100):
            root = await screenplay_db.fetch_one(
                "SELECT status FROM ai_agent_runs WHERE id = ?",
                [root_run_id],
            )
            if root == {"status": "canceled"}:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("periodic orphan monitor did not settle Root")
        assert await recovery.recover() == ()
    finally:
        monitor.cancel()
        with pytest.raises(asyncio.CancelledError):
            await monitor
        await composition.shutdown()

    stored = await SqliteScreenplayOperationRepository(screenplay_db).load(
        operation.id
    )
    assert stored is not None and stored.status.value == "paused"
    assert stored.usage == OperationUsage(
        invocation_count=1,
        input_tokens=17,
        output_tokens=6,
        reasoning_tokens=0,
    )
    assert await screenplay_db.fetch_one(
        "SELECT status FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    ) == {"status": "paused"}
    assert await screenplay_db.fetch_one(
        "SELECT source_event_key FROM ai_agent_run_events WHERE run_id = ? "
        "AND source_event_key = ?",
        [root_run_id, f"run:{root_run_id}:canceled"],
    ) == {"source_event_key": f"run:{root_run_id}:canceled"}
    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_long_tasks "
        "WHERE id = 'task-atomic-finalizer'"
    ) == {"status": "paused"}
    assert await screenplay_db.fetch_one(
        "SELECT status, worker_id, lease_expires_at_ms, error_code FROM "
        "ai_agent_long_task_units WHERE task_id = 'task-atomic-finalizer' "
        "AND unit_id = 'orphan-unit'"
    ) == {
        "status": "pending",
        "worker_id": None,
        "lease_expires_at_ms": None,
        "error_code": "durable_task_interrupted",
    }
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ? "
        "AND source_event_key = ?",
        [root_run_id, f"run:{root_run_id}:canceled"],
    ) == {"count": 1}


@pytest.mark.asyncio
async def test_incompatible_model_resume_keeps_operation_paused(screenplay_db):
    operation, _turn_id, _command, _finalizer = await _finalization_fixture(
        screenplay_db
    )
    operations = SqliteScreenplayOperationRepository(screenplay_db)
    paused = await operations.pause(
        operation.id,
        code="model_task_mode_incompatible",
        message="change model",
        command_id="pause-before-model-change",
    )
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id="resume-preflight-test",
        unit_executor_factory=lambda _runtime: _PausedUnitExecutor(),
        projects=ScreenplayV2ProjectService(screenplay_db),
    )
    request = ResumeScreenplayOperationRequest.model_validate({
        "expectedOperationRevision": paused.revision,
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://api.moonshot.cn/v1",
            "options": {
                "model": "kimi-k3",
                "model_profile": "moonshot:kimi-k3",
                "thinking": {"type": "enabled"},
            },
            "contextWindow": "1m",
        },
    })

    with pytest.raises(AppError, match="model_capability_incompatible") as error:
        await service.prepare_resume(
            paused.id,
            idempotency_key="resume-incompatible-model",
            request=request,
        )

    assert error.value.status_code == 409
    unchanged = await operations.load(paused.id)
    assert unchanged is not None
    assert unchanged.status.value == "paused"
    assert unchanged.revision == paused.revision


async def _paused_continuation_fixture(db):
    operation, turn_id, _command, _finalizer = await _finalization_fixture(db)
    turn = await db.fetch_one(
        "SELECT project_id, session_id FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    )
    source_root_run_id = "run-paused-continuation-source"
    await db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, mode, prompt, binding_namespace, "
        "binding_aggregate_id, binding_command_id, binding_attributes_json) "
        "VALUES (?, ?, 'canceled', 'agent', '', ?, ?, ?, ?)",
        [
            source_root_run_id,
            turn["session_id"],
            "screenplay.conversation_turn",
            turn["project_id"],
            "command-atomic-finalizer",
            json.dumps({
                "agentProfile": "screenplay",
                "domainNamespace": "purrtypos.screenplay",
            }),
        ],
    )
    await db.execute(
        "UPDATE screenplay_agent_turns SET planner_run_id = ? WHERE id = ?",
        [source_root_run_id, turn_id],
    )
    await db.execute(
        "UPDATE ai_agent_long_tasks SET created_by_run_id = ?, "
        "status = 'paused' WHERE id = 'task-atomic-finalizer'",
        [source_root_run_id],
    )
    await db.execute(
        "DELETE FROM ai_agent_long_task_units "
        "WHERE task_id = 'task-atomic-finalizer'"
    )
    operations = SqliteScreenplayOperationRepository(db)
    paused = await operations.pause(
        operation.id,
        code="checkpoint_requires_resume",
        message="resume",
        command_id="pause-for-continuation-reservation",
    )
    return paused, turn_id, source_root_run_id, turn


@pytest.mark.asyncio
async def test_explicit_cancel_wins_over_paused_long_task_projection(
    screenplay_db,
):
    paused, turn_id, root_run_id, _turn = await _paused_continuation_fixture(
        screenplay_db
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json, event_id, turn_id, sequence, "
        "source, kind, channel, visibility, source_event_key) VALUES "
        "(?, 'run.started', '{\"status\":\"running\"}', ?, ?, 1, "
        "'runtime', 'run.lifecycle', 'lifecycle', 'public', ?)",
        [root_run_id, f"event-{root_run_id}", turn_id, f"run:{root_run_id}:running"],
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status) VALUES "
        "('task-atomic-finalizer', 'paused-unit', 'paused-unit', 1, 'blocked')"
    )
    operations = SqliteScreenplayOperationRepository(screenplay_db)
    receipt = await operations.request_cancel(
        turn_id,
        idempotency_key="explicit-cancel-paused-root",
    )

    async with screenplay_db.transaction(cancellation_linearizable=True):
        await ScreenplayAgentRootCompletionProjector(screenplay_db).project(
            root_run_id,
            RunCommit(terminal_status=RunStatus.CANCELED),
        )

    stored = await operations.load(paused.id)
    assert stored is not None and stored.status.value == "canceled"
    assert await screenplay_db.fetch_one(
        "SELECT status, cancel_receipt_id FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    ) == {"status": "canceled", "cancel_receipt_id": receipt.id}
    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_long_tasks WHERE id = 'task-atomic-finalizer'"
    ) == {"status": "canceled"}
    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_long_task_units WHERE task_id = "
        "'task-atomic-finalizer' AND unit_id = 'paused-unit'"
    ) == {"status": "canceled"}


@pytest.mark.asyncio
async def test_generic_root_cancel_overrides_live_screenplay_pause(
    screenplay_db,
):
    paused, turn_id, root_run_id, _turn = await _paused_continuation_fixture(
        screenplay_db
    )
    await screenplay_db.execute(
        "UPDATE ai_agent_runs SET status = 'running' WHERE id = ?",
        [root_run_id],
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json, event_id, turn_id, sequence, "
        "source, kind, channel, visibility, source_event_key) VALUES "
        "(?, 'run.started', '{\"status\":\"running\"}', ?, ?, 1, "
        "'runtime', 'run.lifecycle', 'lifecycle', 'public', ?)",
        [
            root_run_id,
            f"event-live-{root_run_id}",
            turn_id,
            f"run:{root_run_id}:running",
        ],
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_long_task_units "
        "(task_id, unit_id, semantic_key, position, status) VALUES "
        "('task-atomic-finalizer', 'live-paused-unit', 'live-paused-unit', "
        "1, 'blocked')"
    )
    composition = create_agent_composition(screenplay_db)
    try:
        result = await AgentCancellationService(
            screenplay_db,
            composition,
        ).cancel(root_run_id)
    finally:
        await composition.shutdown()

    assert result is not None and result["cancellationStatus"] == "completed"
    stored = await SqliteScreenplayOperationRepository(screenplay_db).load(
        paused.id
    )
    assert stored is not None and stored.status.value == "canceled"
    assert await screenplay_db.fetch_one(
        "SELECT status FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    ) == {"status": "canceled"}
    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_long_tasks WHERE id = 'task-atomic-finalizer'"
    ) == {"status": "canceled"}
    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_long_task_units WHERE task_id = "
        "'task-atomic-finalizer' AND unit_id = 'live-paused-unit'"
    ) == {"status": "canceled"}


@pytest.mark.asyncio
async def test_continuation_start_reservation_has_one_cross_connection_winner(
    screenplay_db,
    tmp_path,
):
    paused, turn_id, source_root_run_id, turn = (
        await _paused_continuation_fixture(screenplay_db)
    )
    snapshot = {"digest": "sha256:" + "a" * 64}
    command_id = "resume-cross-connection"
    await SqliteScreenplayOperationRepository(screenplay_db).resume_with_model(
        paused.id,
        command_id=command_id,
        expected_revision=paused.revision,
        capability_snapshot=snapshot,
    )
    second_db = DatabaseConnection(tmp_path)
    await second_db.init()
    try:
        first, second = await asyncio.gather(
            SqliteScreenplayOperationRepository(
                screenplay_db
            ).claim_continuation_start(
                command_id=command_id,
                operation_id=paused.id,
                turn_id=turn_id,
                source_root_run_id=source_root_run_id,
                session_id=int(turn["session_id"]),
                project_id=str(turn["project_id"]),
                owner_id="resume-owner-one",
            ),
            SqliteScreenplayOperationRepository(second_db).claim_continuation_start(
                command_id=command_id,
                operation_id=paused.id,
                turn_id=turn_id,
                source_root_run_id=source_root_run_id,
                session_id=int(turn["session_id"]),
                project_id=str(turn["project_id"]),
                owner_id="resume-owner-two",
            ),
        )
    finally:
        await second_db.close()

    assert sorted((first["_acquired"], second["_acquired"])) == [False, True]
    assert first["identity_digest"] == second["identity_digest"]


@pytest.mark.asyncio
async def test_prepare_resume_cross_service_dispatches_exactly_one_root(
    screenplay_db,
    tmp_path,
):
    paused, _turn_id, _source_root_run_id, _turn = (
        await _paused_continuation_fixture(screenplay_db)
    )
    second_db = DatabaseConnection(tmp_path)
    await second_db.init()
    request = ResumeScreenplayOperationRequest.model_validate({
        "expectedOperationRevision": paused.revision,
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://api.deepseek.com/v1",
            "options": {
                "model": "deepseek-v4-flash",
                "model_profile": "deepseek:deepseek-v4-flash",
            },
            "contextWindow": "128k",
        },
    })
    first_service = ScreenplayAgentService(
        screenplay_db,
        owner_id="resume-service-one",
        projects=ScreenplayV2ProjectService(screenplay_db),
    )
    second_service = ScreenplayAgentService(
        second_db,
        owner_id="resume-service-two",
        projects=ScreenplayV2ProjectService(second_db),
    )
    try:
        first, second = await asyncio.gather(
            first_service.prepare_resume(
                paused.id,
                idempotency_key="resume-cross-service-command",
                request=request,
            ),
            second_service.prepare_resume(
                paused.id,
                idempotency_key="resume-cross-service-command",
                request=request,
            ),
        )
    finally:
        await second_db.close()

    assert sorted((first["dispatchRequired"], second["dispatchRequired"])) == [
        False,
        True,
    ]
    assert first["continuationRootRunId"] is None
    assert second["continuationRootRunId"] is None


@pytest.mark.asyncio
async def test_continuation_reservation_rejects_identity_mismatch_and_reclaims_expiry(
    screenplay_db,
):
    paused, turn_id, source_root_run_id, turn = (
        await _paused_continuation_fixture(screenplay_db)
    )
    operations = SqliteScreenplayOperationRepository(screenplay_db)
    command_id = "resume-expiry-and-identity"
    await operations.resume_with_model(
        paused.id,
        command_id=command_id,
        expected_revision=paused.revision,
        capability_snapshot={"digest": "sha256:" + "d" * 64},
    )
    first = await operations.claim_continuation_start(
        command_id=command_id,
        operation_id=paused.id,
        turn_id=turn_id,
        source_root_run_id=source_root_run_id,
        session_id=int(turn["session_id"]),
        project_id=str(turn["project_id"]),
        owner_id="first-owner",
    )
    with pytest.raises(ValueError, match="identity conflicts"):
        await operations.claim_continuation_start(
            command_id=command_id,
            operation_id=paused.id,
            turn_id=turn_id,
            source_root_run_id="different-source-root",
            session_id=int(turn["session_id"]),
            project_id=str(turn["project_id"]),
            owner_id="attacker",
        )
    await screenplay_db.execute(
        "UPDATE screenplay_agent_operation_commands SET "
        "continuation_lease_expires_at_ms = 0 WHERE command_id = ?",
        [command_id],
    )
    recovered = await operations.claim_continuation_start(
        command_id=command_id,
        operation_id=paused.id,
        turn_id=turn_id,
        source_root_run_id=source_root_run_id,
        session_id=int(turn["session_id"]),
        project_id=str(turn["project_id"]),
        owner_id="recovery-owner",
    )

    assert first["_acquired"] is True
    assert recovered["_acquired"] is True
    assert int(recovered["continuation_epoch"]) == (
        int(first["continuation_epoch"]) + 1
    )


@pytest.mark.asyncio
async def test_lost_continuation_starter_does_not_fail_winner_business_state(
    screenplay_db,
):
    paused, turn_id, source_root_run_id, turn = (
        await _paused_continuation_fixture(screenplay_db)
    )
    operations = SqliteScreenplayOperationRepository(screenplay_db)
    command_id = "resume-lost-starter-benign"
    resumed = await operations.resume_with_model(
        paused.id,
        command_id=command_id,
        expected_revision=paused.revision,
        capability_snapshot={"digest": "sha256:" + "e" * 64},
    )
    first = await operations.claim_continuation_start(
        command_id=command_id,
        operation_id=paused.id,
        turn_id=turn_id,
        source_root_run_id=source_root_run_id,
        session_id=int(turn["session_id"]),
        project_id=str(turn["project_id"]),
        owner_id="old-starter",
    )
    await screenplay_db.execute(
        "UPDATE screenplay_agent_operation_commands SET "
        "continuation_lease_expires_at_ms = 0 WHERE command_id = ?",
        [command_id],
    )
    winner = await operations.claim_continuation_start(
        command_id=command_id,
        operation_id=paused.id,
        turn_id=turn_id,
        source_root_run_id=source_root_run_id,
        session_id=int(turn["session_id"]),
        project_id=str(turn["project_id"]),
        owner_id="new-starter",
    )
    lifecycle = _ScreenplayContinuationRunLifecycle(
        screenplay_db,
        SqliteScreenplayAgentRepository(screenplay_db, owner_id="old-starter"),
        operations,
        turn_id,
        source_root_run_id=source_root_run_id,
        continuation_command=command_id,
        reservation=first,
    )
    with pytest.raises(ContinuationStartLost, match="reservation was lost") as lost:
        await lifecycle.before_submit()
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id="old-starter",
        projects=object(),
    )

    await service._settle_execution_exception(turn_id, lost.value)

    current = await operations.load(resumed.id)
    persisted_turn = await screenplay_db.fetch_one(
        "SELECT status FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    )
    assert current is not None and current.status.value == "running"
    assert persisted_turn == {"status": "running"}
    assert int(winner["continuation_epoch"]) == int(first["continuation_epoch"]) + 1


@pytest.mark.asyncio
async def test_continuation_root_begin_binds_receipt_and_turn_atomically(
    screenplay_db,
):
    paused, turn_id, source_root_run_id, turn = (
        await _paused_continuation_fixture(screenplay_db)
    )
    operations = SqliteScreenplayOperationRepository(screenplay_db)
    command_id = "resume-atomic-root-begin"
    await operations.resume_with_model(
        paused.id,
        command_id=command_id,
        expected_revision=paused.revision,
        capability_snapshot={"digest": "sha256:" + "b" * 64},
    )
    reservation = await operations.claim_continuation_start(
        command_id=command_id,
        operation_id=paused.id,
        turn_id=turn_id,
        source_root_run_id=source_root_run_id,
        session_id=int(turn["session_id"]),
        project_id=str(turn["project_id"]),
        owner_id="continuation-owner",
    )
    await screenplay_db.execute(
        "INSERT INTO screenplay_checkpoint_plans "
        "(operation_id, task_id, checkpoint_key, root_run_id, status, "
        "input_digest, outcome, error_code) "
        "VALUES (?, 'task-atomic-finalizer', 'episode:4', ?, 'paused', ?, "
        "'requires_reresolution', 'scope_changed')",
        [paused.id, source_root_run_id, "sha256:" + "e" * 64],
    )
    params = RunCreateParams(
        session_id=int(turn["session_id"]),
        prompt="continue",
        mode="agent",
        turn_id=turn_id,
        binding=RunBinding(
            namespace="screenplay.conversation_turn",
            aggregate_id=str(turn["project_id"]),
            command_id=command_id,
            attributes={
                "agentProfile": "screenplay",
                "domainNamespace": "purrtypos.screenplay",
                "continuationOf": source_root_run_id,
                "operationId": paused.id,
                "continuationOwner": "continuation-owner",
                "continuationEpoch": int(reservation["continuation_epoch"]),
                "continuationIdentityDigest": reservation["identity_digest"],
            },
        ),
    )
    repository = SqliteAgentOutputRepository(
        screenplay_db,
        run_repository=SqliteRunRepository(screenplay_db),
        run_begin_projector=ScreenplayContinuationBeginProjector(screenplay_db),
    )
    begun, _output = await repository.begin_run_lifecycle(
        params,
        AgentEvent(
            type=CoreEventType.RUN_STARTED,
            payload={"status": RunStatus.RUNNING.value},
        ),
    )

    assert await screenplay_db.fetch_one(
        "SELECT planner_run_id AS root_run_id FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    ) == {"root_run_id": begun.run_id}
    assert await screenplay_db.fetch_one(
        "SELECT continuation_status, continuation_root_run_id FROM "
        "screenplay_agent_operation_commands WHERE command_id = ?",
        [command_id],
    ) == {
        "continuation_status": "bound",
        "continuation_root_run_id": begun.run_id,
    }
    assert await screenplay_db.fetch_one(
        "SELECT root_run_id, status, outcome, error_code FROM "
        "screenplay_checkpoint_plans WHERE operation_id = ? AND "
        "checkpoint_key = 'episode:4'",
        [paused.id],
    ) == {
        "root_run_id": begun.run_id,
        "status": "continuation_retry",
        "outcome": "requires_reresolution",
        "error_code": "scope_changed",
    }
    with pytest.raises(ContinuationStartLost):
        await repository.begin_run_lifecycle(
            params,
            AgentEvent(
                type=CoreEventType.RUN_STARTED,
                payload={"status": RunStatus.RUNNING.value},
            ),
        )
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE "
        "binding_command_id = ?",
        [command_id],
    ) == {"count": 1}


@pytest.mark.asyncio
async def test_truncate_cancel_fence_rejects_concurrent_continuation_begin(
    screenplay_db,
):
    paused, turn_id, source_root_run_id, turn = (
        await _paused_continuation_fixture(screenplay_db)
    )
    operations = SqliteScreenplayOperationRepository(screenplay_db)
    command_id = "resume-racing-truncate"
    await operations.resume_with_model(
        paused.id,
        command_id=command_id,
        expected_revision=paused.revision,
        capability_snapshot={"digest": "sha256:" + "1" * 64},
    )
    reservation = await operations.claim_continuation_start(
        command_id=command_id,
        operation_id=paused.id,
        turn_id=turn_id,
        source_root_run_id=source_root_run_id,
        session_id=int(turn["session_id"]),
        project_id=str(turn["project_id"]),
        owner_id="continuation-racing-truncate",
    )
    await operations.request_cancel(
        turn_id,
        idempotency_key="truncate-race-cancel",
    )
    params = RunCreateParams(
        session_id=int(turn["session_id"]),
        prompt="continue",
        mode="agent",
        turn_id=turn_id,
        binding=RunBinding(
            namespace="screenplay.conversation_turn",
            aggregate_id=str(turn["project_id"]),
            command_id=command_id,
            attributes={
                "agentProfile": "screenplay",
                "domainNamespace": "purrtypos.screenplay",
                "continuationOf": source_root_run_id,
                "operationId": paused.id,
                "continuationOwner": "continuation-racing-truncate",
                "continuationEpoch": int(reservation["continuation_epoch"]),
                "continuationIdentityDigest": reservation["identity_digest"],
            },
        ),
    )
    repository = SqliteAgentOutputRepository(
        screenplay_db,
        run_repository=SqliteRunRepository(screenplay_db),
        run_begin_projector=ScreenplayContinuationBeginProjector(screenplay_db),
    )

    with pytest.raises(
        ContractViolationError,
        match="continuation identity conflicts",
    ):
        await repository.begin_run_lifecycle(
            params,
            AgentEvent(
                type=CoreEventType.RUN_STARTED,
                payload={"status": RunStatus.RUNNING.value},
            ),
        )

    assert await screenplay_db.fetch_one(
        "SELECT planner_run_id AS root_run_id FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    ) == {"root_run_id": source_root_run_id}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE "
        "binding_command_id = ?",
        [command_id],
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_continuation_begin_failure_rolls_back_root_receipt_and_turn(
    screenplay_db,
):
    paused, turn_id, source_root_run_id, turn = (
        await _paused_continuation_fixture(screenplay_db)
    )
    operations = SqliteScreenplayOperationRepository(screenplay_db)
    command_id = "resume-rollback-root-begin"
    await operations.resume_with_model(
        paused.id,
        command_id=command_id,
        expected_revision=paused.revision,
        capability_snapshot={"digest": "sha256:" + "c" * 64},
    )
    reservation = await operations.claim_continuation_start(
        command_id=command_id,
        operation_id=paused.id,
        turn_id=turn_id,
        source_root_run_id=source_root_run_id,
        session_id=int(turn["session_id"]),
        project_id=str(turn["project_id"]),
        owner_id="rollback-owner",
    )
    await screenplay_db.execute(
        "INSERT INTO screenplay_checkpoint_plans "
        "(operation_id, task_id, checkpoint_key, root_run_id, status, "
        "input_digest, outcome, error_code) "
        "VALUES (?, 'task-atomic-finalizer', 'episode:4', ?, 'paused', ?, "
        "'requires_reresolution', 'scope_changed')",
        [paused.id, source_root_run_id, "sha256:" + "f" * 64],
    )

    class RejectAfterProjection:
        async def project(self, run_id, params):
            await ScreenplayContinuationBeginProjector(screenplay_db).project(
                run_id,
                params,
            )
            raise RuntimeError("injected begin projection failure")

    repository = SqliteAgentOutputRepository(
        screenplay_db,
        run_repository=SqliteRunRepository(screenplay_db),
        run_begin_projector=RejectAfterProjection(),
    )
    params = RunCreateParams(
        session_id=int(turn["session_id"]),
        prompt="continue",
        mode="agent",
        turn_id=turn_id,
        binding=RunBinding(
            namespace="screenplay.conversation_turn",
            aggregate_id=str(turn["project_id"]),
            command_id=command_id,
            attributes={
                "agentProfile": "screenplay",
                "domainNamespace": "purrtypos.screenplay",
                "continuationOf": source_root_run_id,
                "operationId": paused.id,
                "continuationOwner": "rollback-owner",
                "continuationEpoch": int(reservation["continuation_epoch"]),
                "continuationIdentityDigest": reservation["identity_digest"],
            },
        ),
    )
    with pytest.raises(RuntimeError, match="injected begin projection failure"):
        await repository.begin_run_lifecycle(
            params,
            AgentEvent(
                type=CoreEventType.RUN_STARTED,
                payload={"status": RunStatus.RUNNING.value},
            ),
        )

    assert await screenplay_db.fetch_one(
        "SELECT planner_run_id AS root_run_id FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    ) == {"root_run_id": source_root_run_id}
    assert await screenplay_db.fetch_one(
        "SELECT continuation_status, continuation_root_run_id FROM "
        "screenplay_agent_operation_commands WHERE command_id = ?",
        [command_id],
    ) == {
        "continuation_status": "starting",
        "continuation_root_run_id": None,
    }
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE "
        "binding_command_id = ?",
        [command_id],
    ) == {"count": 0}
    assert await screenplay_db.fetch_one(
        "SELECT root_run_id, status FROM screenplay_checkpoint_plans "
        "WHERE operation_id = ? AND checkpoint_key = 'episode:4'",
        [paused.id],
    ) == {
        "root_run_id": source_root_run_id,
        "status": "paused",
    }
    assert await operations.release_continuation_start(
        command_id=command_id,
        owner_id="rollback-owner",
        epoch=int(reservation["continuation_epoch"]),
    ) is True
    retry = await operations.claim_continuation_start(
        command_id=command_id,
        operation_id=paused.id,
        turn_id=turn_id,
        source_root_run_id=source_root_run_id,
        session_id=int(turn["session_id"]),
        project_id=str(turn["project_id"]),
        owner_id="retry-owner",
    )
    assert retry["_acquired"] is True
    assert int(retry["continuation_epoch"]) == (
        int(reservation["continuation_epoch"]) + 1
    )


@pytest.mark.asyncio
async def test_cancel_request_is_durable_canonical_and_idempotent(screenplay_db):
    operation, turn_id, _command, _finalizer = await _finalization_fixture(
        screenplay_db
    )
    operations = SqliteScreenplayOperationRepository(screenplay_db)

    first = await operations.request_cancel(
        turn_id,
        idempotency_key="cancel-command-1",
    )
    replay = await operations.request_cancel(
        turn_id,
        idempotency_key="cancel-command-1",
    )
    another_command = await operations.request_cancel(
        turn_id,
        idempotency_key="cancel-command-2",
    )

    assert replay == first
    assert another_command.id == first.id
    assert first.operation_id == operation.id
    assert first.turn_id == turn_id
    assert first.terminal_status == "cancel_requested"
    requested = await operations.load(operation.id)
    assert requested is not None
    assert requested.cancel_receipt_id == first.id
    assert requested.cancel_requested_at_ms is not None

    settled = await operations.settle_cancel(turn_id, receipt_id=first.id)
    assert settled.id == first.id
    assert settled.terminal_status == "canceled"
    assert (await operations.load(operation.id)).status.value == "canceled"
    assert await screenplay_db.fetch_one(
        "SELECT status, assistant_content FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    ) == {"status": "canceled", "assistant_content": ""}


@pytest.mark.asyncio
async def test_pause_after_resume_records_a_new_lifecycle_occurrence(screenplay_db):
    operation, _turn_id, _command, _finalizer = await _finalization_fixture(
        screenplay_db
    )
    operations = SqliteScreenplayOperationRepository(screenplay_db)
    first = await operations.pause(
        operation.id,
        code="screenplay_task_paused",
        message="任务已暂停，可恢复后继续。",
        command_id="pause-before-resume",
    )
    await screenplay_db.execute(
        "UPDATE screenplay_agent_operations SET status = 'running', "
        "revision = revision + 1 WHERE id = ?",
        [operation.id],
    )

    second = await operations.pause(
        operation.id,
        code="screenplay_task_paused",
        message="任务已暂停，可恢复后继续。",
        command_id="pause-after-resume",
    )
    replay = await operations.pause(
        operation.id,
        code="screenplay_task_paused",
        message="任务已暂停，可恢复后继续。",
        command_id="pause-after-resume",
    )

    assert second.status.value == "paused"
    assert second.revision == first.revision + 2
    assert replay.revision == second.revision
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_operation_commands "
        "WHERE operation_id = ? AND command_type = 'pause'",
        [operation.id],
    ) == {"count": 2}
    digests = await screenplay_db.fetch_all(
        "SELECT request_digest FROM screenplay_agent_operation_commands "
        "WHERE operation_id = ? AND command_type = 'pause'",
        [operation.id],
    )
    assert len({row["request_digest"] for row in digests}) == 2

    with pytest.raises(ValueError, match="command conflicts"):
        await operations.pause(
            operation.id,
            code="different_error",
            message="different message",
            command_id="pause-after-resume",
        )


@pytest.mark.asyncio
async def test_cancel_idempotency_key_cannot_be_reused_for_another_turn(
    screenplay_db,
):
    _operation, turn_id, _command, _finalizer = await _finalization_fixture(
        screenplay_db
    )
    turn = await screenplay_db.fetch_one(
        "SELECT project_id, session_id FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    )
    operations = SqliteScreenplayOperationRepository(screenplay_db)
    requested = await operations.request_cancel(
        turn_id,
        idempotency_key="shared-cancel-key",
    )
    await operations.settle_cancel(turn_id, receipt_id=requested.id)
    await screenplay_db.execute(
        "INSERT INTO screenplay_agent_turns "
        "(id, project_id, session_id, command_id, status, user_content) "
        "VALUES ('turn-other-cancel', ?, ?, 'other-cancel', 'queued', '停止')",
        [turn["project_id"], turn["session_id"]],
    )

    with pytest.raises(ValueError, match="command conflicts"):
        await operations.request_cancel(
            "turn-other-cancel",
            idempotency_key="shared-cancel-key",
        )


@pytest.mark.asyncio
async def test_finalization_commit_wins_before_cancel_request(screenplay_db):
    operation, turn_id, command, finalizer = await _finalization_fixture(
        screenplay_db
    )
    finalization = await finalizer.finalize(command)
    operations = SqliteScreenplayOperationRepository(screenplay_db)

    requested = await operations.request_cancel(
        turn_id,
        idempotency_key="cancel-after-finalization",
    )
    settled = await operations.settle_cancel(turn_id, receipt_id=requested.id)

    assert requested.terminal_status == "succeeded"
    assert settled.terminal_status == "succeeded"
    stored = await operations.load(operation.id)
    assert stored.status.value == "succeeded"
    assert stored.result_revision_id == finalization.revision_id
    assert await screenplay_db.fetch_one(
        "SELECT status FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    ) == {"status": "completed"}


@pytest.mark.asyncio
async def test_cancel_request_commit_wins_before_finalization(screenplay_db):
    operation, turn_id, command, finalizer = await _finalization_fixture(
        screenplay_db
    )
    operations = SqliteScreenplayOperationRepository(screenplay_db)
    requested = await operations.request_cancel(
        turn_id,
        idempotency_key="cancel-before-finalization",
    )

    with pytest.raises(ValueError, match="cancel was requested"):
        await finalizer.finalize(command)

    settled = await operations.settle_cancel(turn_id, receipt_id=requested.id)
    assert settled.terminal_status == "canceled"
    assert (await operations.load(operation.id)).status.value == "canceled"
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revisions "
        "WHERE agent_task_id = 'task-atomic-finalizer'"
    ) == {"count": 0}


class _PausedUnitExecutor:
    async def execute(self, context, signal=None):
        del context, signal
        raise ModelGatewayError(
            "selected protocol is incompatible",
            code="provider_bad_request",
            retryable=False,
        )

    def classify_failure(self, error):
        return classify_screenplay_run_failure(error)


class _PauseAfterOneUnitExecutor(_UnitExecutor):
    async def execute(self, context, signal=None):
        if self.calls:
            raise ModelGatewayError(
                "selected protocol is incompatible",
                code="provider_bad_request",
                retryable=False,
            )
        return await super().execute(context, signal)

    def classify_failure(self, error):
        return classify_screenplay_run_failure(error)


class _ExplodingUnitExecutor:
    async def execute(self, context, signal=None):
        del context, signal
        raise RuntimeError("injected screenplay unit failure")

    def classify_failure(self, error):
        return classify_screenplay_run_failure(error)


class _BlockingUnitExecutor:
    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def execute(self, context, signal=None):
        del context
        assert signal is not None
        self.entered.set()
        await signal.wait()
        raise asyncio.CancelledError


@pytest.mark.asyncio
async def test_paused_operation_retains_session_control_until_terminal(
    screenplay_db,
):
    projects = ScreenplayV2ProjectService(screenplay_db)
    workspace = await projects.create_project(
        command_id="create-operation-control-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Operation control",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    project_id = workspace["project"]["id"]
    session = await projects.ensure_current_session(project_id)
    for suffix in ("first", "second"):
        await screenplay_db.execute(
            "INSERT INTO screenplay_agent_turns "
            "(id, project_id, session_id, command_id, status, user_content) "
            "VALUES (?, ?, ?, ?, 'completed', ?)",
            [
                f"operation-turn-{suffix}",
                project_id,
                session["id"],
                f"operation-command-{suffix}",
                "生成剧本",
            ],
        )
    operations = SqliteScreenplayOperationRepository(screenplay_db)
    first_command = ScreenplayOperationCreateCommand(
        turn_id="operation-turn-first",
        project_id=project_id,
        session_id=session["id"],
        target_role="screenplayDraft",
        requirements_json={"intent": {"action": "create"}},
        manifest_digest="sha256:first",
    )
    second_command = ScreenplayOperationCreateCommand(
        turn_id="operation-turn-second",
        project_id=project_id,
        session_id=session["id"],
        target_role="screenplayDraft",
        requirements_json={"intent": {"action": "create"}},
        manifest_digest="sha256:second",
    )
    first = await operations.create(first_command)
    await operations.pause(
        first.id,
        code="model_output_truncated",
        message="需要恢复",
        command_id="pause-first-operation",
    )

    with pytest.raises(ValueError, match="active Operation"):
        await operations.create(second_command)

    await operations.fail(
        first.id,
        code="user_abandoned",
        message="不再恢复",
        command_id="fail-first-operation",
    )
    second = await operations.create(second_command)
    assert second.status.value == "queued"


@pytest.mark.asyncio
async def test_service_cancel_settles_root_task_operation_and_turn(
    screenplay_db,
    monkeypatch,
):
    projects = ScreenplayV2ProjectService(screenplay_db)
    workspace = await projects.create_project(
        command_id="create-cancel-control-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Cancel control",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    plan = {
        "needsTodos": True,
        "title": "生成原作分析",
        "taskSpec": {
            "goal": "生成原作分析",
            "operation": "create",
            "instruction": "生成原作分析",
            "deliverable": "sourceAnalysis",
            "target": {"screenplay": {
                "version": 1,
                "scope": {"kind": "current_stage"},
                "stepBindings": [
                    {"stepId": "read", "phase": "evidence"},
                    {"stepId": "create", "phase": "creation"},
                    {"stepId": "deliver", "phase": "delivery"},
                ],
            }},
        },
        "todos": [
            {"id": "read", "title": "读取", "type": "analyze", "executor": "model", "riskLevel": "read"},
            {"id": "create", "title": "生成", "type": "write", "executor": "model", "riskLevel": "write"},
            {"id": "deliver", "title": "交付", "type": "write", "executor": "model", "riskLevel": "write"},
        ],
    }
    gateway = _ScriptedPlannerGateway([[
        ModelStreamChunk(content_delta=json.dumps(plan, ensure_ascii=False)),
        ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
    ]])
    monkeypatch.setattr(
        agent_composition,
        "ProviderModelGateway",
        lambda *_args, **_kwargs: gateway,
    )
    monkeypatch.setattr(
        composition_factory,
        "build_screenplay_agent_profile",
        lambda *, db, **_kwargs: ScreenplayAgentProfile(
            db,
            resolver=_SourceAnalysisResolver(),
        ),
    )
    composition = create_agent_composition(screenplay_db)
    executor = _BlockingUnitExecutor()
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id=composition.execution_owner_id,
        composition=composition,
        unit_executor_factory=lambda _runtime: executor,
        projects=projects,
    )
    request = SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session["id"],
        "content": "开始后等待取消。",
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://api.deepseek.com/v1",
            "options": {
                "model": "deepseek-v4-flash",
                "model_profile": "deepseek:deepseek-v4-flash",
            },
            "contextWindow": "128k",
        },
    })
    turn = await service.submit_turn(
        command_id="cancel-active-turn",
        project_id=workspace["project"]["id"],
        request=request,
    )
    execution = service.dispatch_turn(turn["id"], request.runtime)
    await asyncio.wait_for(executor.entered.wait(), timeout=2)

    first = await service.cancel_turn(
        turn["id"],
        idempotency_key="cancel-active-command",
    )
    draining = await screenplay_db.fetch_one(
        "SELECT t.status AS turn_status, o.status AS operation_status, "
        "t.cancel_receipt_id FROM screenplay_agent_turns AS t "
        "JOIN screenplay_agent_operations AS o ON o.turn_id = t.id "
        "WHERE t.id = ?",
        [turn["id"]],
    )
    assert first["terminalStatus"] == "cancel_requested"
    assert draining == {
        "turn_status": "running",
        "operation_status": "running",
        "cancel_receipt_id": first["cancelReceiptId"],
    }
    replay = await service.cancel_turn(
        turn["id"],
        idempotency_key="cancel-active-command",
    )
    with suppress(asyncio.CancelledError):
        await execution
    settled = await service.cancel_turn(
        turn["id"],
        idempotency_key="cancel-active-command",
    )
    await composition.shutdown()

    assert replay == first
    assert settled["terminalStatus"] == "canceled"
    operation = await screenplay_db.fetch_one(
        "SELECT status, long_task_id, cancel_receipt_id "
        "FROM screenplay_agent_operations WHERE turn_id = ?",
        [turn["id"]],
    )
    assert operation["status"] == "canceled"
    assert operation["cancel_receipt_id"] == settled["cancelReceiptId"]
    assert await screenplay_db.fetch_one(
        "SELECT status, assistant_content FROM screenplay_agent_turns WHERE id = ?",
        [turn["id"]],
    ) == {"status": "canceled", "assistant_content": ""}
    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_long_tasks WHERE id = ?",
        [operation["long_task_id"]],
    ) == {"status": "canceled"}
    snapshot = await service.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )
    root_run_id = snapshot["turns"][0]["rootRunId"]
    assert root_run_id
    assert (
        await screenplay_db.fetch_one(
            "SELECT cancel_requested_at_ms FROM ai_agent_runs WHERE id = ?",
            [root_run_id],
        )
    )["cancel_requested_at_ms"] is not None
    assert snapshot["operations"][0]["status"] == "canceled"
    assert snapshot["operations"][0]["resultRevisionId"] is None
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE "
        "id = ? AND (status = 'running' OR "
        "execution_owner_id IS NOT NULL OR lease_expires_at_ms IS NOT NULL)",
        [root_run_id],
    ) == {"count": 0}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_task_units WHERE "
        "task_id = ? AND (status IN ('claimed', 'running') OR "
        "worker_id IS NOT NULL OR lease_expires_at_ms IS NOT NULL)",
        [operation["long_task_id"]],
    ) == {"count": 0}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_delegations WHERE "
        "run_id = ? AND status IN ('queued', 'running')",
        [root_run_id],
    ) == {"count": 0}



@pytest.mark.asyncio
async def test_screenplay_answer_turn_does_not_create_a_durable_task(
    screenplay_db,
    monkeypatch,
):
    projects = ScreenplayV2ProjectService(screenplay_db)
    workspace = await projects.create_project(
        command_id="create-answer-screenplay-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Answer screenplay",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    gateway = _ScriptedPlannerGateway([
        [
            ModelStreamChunk(content_delta=json.dumps({
                "needsTodos": True,
                "title": "说明当前阶段",
                "goal": "回答项目当前进度",
                "taskSpec": {
                    "goal": "回答项目当前进度",
                    "operation": "answer",
                    "instruction": "解释当前阶段",
                    "target": {"screenplay": {
                        "version": 1,
                        "scope": {"kind": "current_stage"},
                        "stepBindings": [{
                            "stepId": "answer-current-stage",
                            "phase": "delivery",
                        }],
                    }},
                },
                "todos": [{
                    "id": "answer-current-stage",
                    "title": "说明当前阶段",
                    "type": "review",
                    "executor": "model",
                    "dependsOn": [],
                    "riskLevel": "read",
                }],
            }, ensure_ascii=False)),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
        [
            ModelStreamChunk(content_delta="当前处于创作简报阶段。"),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
    ])
    monkeypatch.setattr(
        agent_composition,
        "ProviderModelGateway",
        lambda *_args, **_kwargs: gateway,
    )
    composition = create_agent_composition(screenplay_db)
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id=composition.execution_owner_id,
        composition=composition,
        projects=projects,
    )
    request = SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session["id"],
        "content": "现在进行到哪一步了？",
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://api.deepseek.com/v1",
            "options": {
                "model": "deepseek-v4-flash",
                "model_profile": "deepseek:deepseek-v4-flash",
            },
            "contextWindow": "128k",
        },
    })
    turn = await service.submit_turn(
        command_id="answer-current-stage",
        project_id=workspace["project"]["id"],
        request=request,
    )

    try:
        await service.execute_turn(turn["id"], request.runtime)
    finally:
        await composition.shutdown()

    snapshot = await service.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )
    assert snapshot["tasks"] == []
    assert snapshot["operations"] == []
    debug_events = await screenplay_db.fetch_all(
        "SELECT event_type, "
        "json_extract(payload_json, '$.stage') AS stage, "
        "json_extract(payload_json, '$.outcome') AS outcome, "
        "json_extract(payload_json, '$.details.errorType') AS error_type, "
        "json_extract(payload_json, '$.details.reasonCode') AS reason_code, "
        "json_extract(payload_json, '$.details.validationReason') AS validation_reason, "
        "json_extract(payload_json, '$.details.error') AS detail_error, "
        "json_extract(payload_json, '$.error') AS error "
        "FROM ai_agent_run_events "
        "WHERE event_type IN ('agentRunTrace', 'runFailed') ORDER BY id"
    )
    assert snapshot["turns"][0]["status"] == "completed", (
        snapshot["turns"][0]["error"],
        debug_events,
    )
    assert snapshot["turns"][0]["assistantContent"] == "当前处于创作简报阶段。"
    root_run_id = snapshot["turns"][0]["rootRunId"]
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs"
    ) == {"count": 1}
    run_columns = {
        row["name"]
        for row in await screenplay_db.fetch_all(
            "PRAGMA table_info(ai_agent_runs)"
        )
    }
    assert {
        "delegation_id",
        "agent_role",
        "run_depth",
    }.isdisjoint(run_columns)
    assert {"parent_run_id", "root_run_id", "agent_id"}.issubset(
        run_columns
    )
    assert await screenplay_db.fetch_one(
        "SELECT root_run_id, parent_run_id FROM ai_agent_runs WHERE id = ?",
        [root_run_id],
    ) == {"root_run_id": root_run_id, "parent_run_id": None}
    canonical_events = await screenplay_db.fetch_all(
        "SELECT turn_id, sequence FROM ai_agent_run_events "
        "WHERE run_id = ? AND event_id IS NOT NULL ORDER BY sequence",
        [root_run_id],
    )
    assert canonical_events
    assert {event["turn_id"] for event in canonical_events} == {turn["id"]}
    sequences = [event["sequence"] for event in canonical_events]
    assert sequences == list(range(1, len(sequences) + 1))
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_operations"
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_screenplay_formal_turn_records_tool_child_run_under_root(
    screenplay_db,
    monkeypatch,
):
    projects = ScreenplayV2ProjectService(screenplay_db)
    workspace = await projects.create_project(
        command_id="create-formal-root-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Formal root screenplay",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    plan = {
        "needsTodos": True,
        "title": "连续创作三集",
        "goal": "完成接下来三集剧本",
        "taskSpec": {
            "goal": "完成接下来三集剧本",
            "operation": "create",
            "instruction": "连续写完后面三集",
            "deliverable": "screenplayDraft",
            "target": {"screenplay": {
                "version": 1,
                "scope": {"kind": "next_episodes", "count": 3},
                "stepBindings": [
                    {"stepId": "collect-evidence", "phase": "evidence"},
                    {"stepId": "draft-next-three", "phase": "creation"},
                    {"stepId": "deliver-next-three", "phase": "delivery"},
                ],
            }},
        },
        "todos": [
            {
                "id": "collect-evidence",
                "title": "读取创作依据",
                "type": "analyze",
                "executor": "model",
                "dependsOn": [],
                "riskLevel": "read",
            },
            {
                "id": "draft-next-three",
                "title": "创作接下来三集",
                "type": "write",
                "executor": "model",
                "dependsOn": [],
                "riskLevel": "write",
            },
            {
                "id": "deliver-next-three",
                "title": "交付三集候选稿",
                "type": "write",
                "executor": "model",
                "dependsOn": [],
                "riskLevel": "write",
            },
        ],
    }
    gateway = _ScriptedPlannerGateway([
        [
            ModelStreamChunk(content_delta=json.dumps(plan, ensure_ascii=False)),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
        [
            ModelStreamChunk(
                content_delta="我先读取项目信息，再完成当前场景。",
                tool_call_deltas=(ToolCallDelta(
                    index=0,
                    id="call-read-project-for-scene",
                    type="function",
                    name="inspectScreenplayProject",
                    arguments_fragment="{}",
                ),),
                finish_reason=ModelFinishReason.TOOL_CALLS,
            ),
        ],
        [
            ModelStreamChunk(content_delta="第 4 集场景正文。"),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
    ])
    monkeypatch.setattr(
        agent_composition,
        "ProviderModelGateway",
        lambda *_args, **_kwargs: gateway,
    )
    monkeypatch.setattr(
        composition_factory,
        "build_screenplay_agent_profile",
        lambda *, db, **_kwargs: ScreenplayAgentProfile(
            db,
            resolver=_Resolver(),
        ),
    )
    class CheckpointPlanner:
        def __init__(self):
            self.calls = []

        async def revise(self, value, signal=None):
            del signal
            self.calls.append(value)
            if len(self.calls) == 1:
                return ScreenplayCheckpointDecision(
                    ScreenplayCheckpointOutcome.REVISED,
                    plan=replace(
                        value.current_plan,
                        steps=tuple(
                            replace(
                                step,
                                title="交付三集连续候选稿",
                                description="根据首集检查点继续交付",
                            )
                            if step.id == "deliver-next-three"
                            else step
                            for step in value.current_plan.steps
                        ),
                    ),
                )
            return ScreenplayCheckpointDecision(
                ScreenplayCheckpointOutcome.UNCHANGED,
            )

    checkpoint_planner = CheckpointPlanner()
    composition = create_agent_composition(screenplay_db)
    request = SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session["id"],
        "content": "不要只写下一集，连续写完后面三集。",
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://api.deepseek.com/v1",
            "options": {
                "model": "deepseek-v4-flash",
                "model_profile": "deepseek:deepseek-v4-flash",
                "max_tokens": 4096,
            },
            "contextWindow": "128k",
        },
    })
    executor = _ToolCallingUnitExecutor(
        screenplay_db,
        composition=composition,
        runtime=request.runtime,
        turn_id="pending-formal-turn",
    )
    executor.checkpoint_planner = checkpoint_planner
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id=composition.execution_owner_id,
        composition=composition,
        unit_executor_factory=lambda _runtime: executor,
        projects=projects,
    )
    turn = await service.submit_turn(
        command_id="formal-root-next-three",
        project_id=workspace["project"]["id"],
        request=request,
    )
    executor._turn_id = turn["id"]

    try:
        await service.execute_turn(turn["id"], request.runtime)
    finally:
        await composition.shutdown()

    snapshot = await service.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )
    projected_turn = snapshot["turns"][0]
    root_run_id = projected_turn["rootRunId"]
    assert projected_turn["status"] == "completed", json.dumps(
        snapshot,
        ensure_ascii=False,
        default=str,
    )
    assert projected_turn["assistantContent"] == (
        "第 4 至 6 集候选稿已经完成。可以在候选稿区域查看并继续编辑。"
    )
    assert len(snapshot["operations"]) == 1
    operation = snapshot["operations"][0]
    task = snapshot["tasks"][0]
    assert task["rootRunId"] == root_run_id
    assert operation["status"] == "succeeded"
    assert operation["taskId"] == task["id"]
    assert operation["resultRevisionId"] == task["resultRevisionId"]
    assert operation["finalizationReceiptId"]
    assert task["status"] == "completed"
    assert task["completedUnits"] == 13
    assert all(unit["status"] == "completed" for unit in task["units"])
    assert task["resultRevision"]["id"] == task["resultRevisionId"]
    assert await screenplay_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name IN ('screenplay_agent_jobs', 'screenplay_agent_job_steps')"
    ) == []
    persisted_runs = await screenplay_db.fetch_all(
        "SELECT id, binding_namespace FROM ai_agent_runs ORDER BY create_time, id"
    )
    assert {row["binding_namespace"] for row in persisted_runs} == {
        "screenplay.conversation_turn",
        "screenplay.agent.task",
    }
    child_run_id = next(
        row["id"] for row in persisted_runs
        if row["binding_namespace"] == "screenplay.agent.task"
    )
    run_columns = {
        row["name"]
        for row in await screenplay_db.fetch_all(
            "PRAGMA table_info(ai_agent_runs)"
        )
    }
    assert {
        "delegation_id",
        "agent_role",
        "run_depth",
    }.isdisjoint(run_columns)
    assert {"parent_run_id", "root_run_id", "agent_id"}.issubset(
        run_columns
    )
    run_scopes = await screenplay_db.fetch_all(
        "SELECT id, root_run_id, parent_run_id FROM ai_agent_runs "
        "WHERE id IN (?, ?) ORDER BY id",
        [root_run_id, child_run_id],
    )
    assert run_scopes == sorted(
        [
            {
                "id": root_run_id,
                "root_run_id": root_run_id,
                "parent_run_id": None,
            },
            {
                "id": child_run_id,
                "root_run_id": child_run_id,
                "parent_run_id": None,
            },
        ],
        key=lambda item: item["id"],
    )
    candidate_events = await SqliteAgentOutputRepository(
        screenplay_db,
        run_repository=SqliteRunRepository(screenplay_db),
    ).list_events(root_run_id, after_sequence=0)
    assert not any(
        event.visibility.value == "public"
        and event.kind.value == "provider.content_delta"
        for event in candidate_events
    )
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifacts "
        "WHERE created_by_run_id = ? AND status = 'finalized'",
        [root_run_id],
    ) == {"count": task["completedUnits"] - 1}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifacts "
        "WHERE created_by_run_id = ? AND status = 'finalized'",
        [child_run_id],
    ) == {"count": 2}
    child_events = await SqliteAgentOutputRepository(
        screenplay_db,
        run_repository=SqliteRunRepository(screenplay_db),
    ).list_events(child_run_id, after_sequence=0)
    assert any(
        event.visibility.value == "public"
        and event.kind.value == "provider.content_delta"
        and event.channel.value == "commentary"
        for event in child_events
    )
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events AS started "
        "JOIN ai_agent_run_events AS finished "
        "ON finished.run_id = started.run_id "
        "AND finished.event_type = 'operation.finished' "
        "AND json_extract(finished.payload_json, '$.operationId') = "
        "json_extract(started.payload_json, '$.operationId') "
        "WHERE started.run_id = ? "
        "AND started.event_type = 'operation.started' "
        "AND json_extract(started.payload_json, '$.kind') = 'tool' "
        "AND json_extract(started.payload_json, "
        "'$.display.labelParams.toolName') = 'inspectScreenplayProject' "
        "AND json_extract(finished.payload_json, '$.status') = 'succeeded'",
        [child_run_id],
    ) == {"count": 1}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_outbox_events "
        "WHERE aggregate_id = ? AND event_type = 'screenplay.candidate.ready'",
        [task["resultRevisionId"]],
    ) == {"count": 1}
    public_candidate_leaks = await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events "
        "WHERE run_id = ? AND visibility = 'public' "
        "AND (event_type = 'run.validated_result' "
        "OR payload_json LIKE '%screenplay.candidate.ready%')",
        [root_run_id],
    )
    assert public_candidate_leaks == {"count": 0}
    run = await screenplay_db.fetch_one(
        "SELECT id, final_response FROM ai_agent_runs WHERE id = ?",
        [root_run_id],
    )
    assert run == {
        "id": root_run_id,
        "final_response": projected_turn["assistantContent"],
    }
    root_events = await SqliteAgentOutputRepository(
        screenplay_db,
        run_repository=SqliteRunRepository(screenplay_db),
    ).list_events(root_run_id, after_sequence=0, limit=200)
    public_chunks = [
        chunk
        for event in root_events
        if (chunk := canonical_output_to_sse_chunk(event)) is not None
    ]
    dispatch_chunks = [
        chunk for chunk in public_chunks
        if chunk["payload"].get("eventType") == "long_task.dispatched"
    ]
    assert len(dispatch_chunks) == 1
    dispatch_data = dispatch_chunks[0]["payload"]["data"]
    assert dispatch_data["taskId"] == task["id"]
    assert {"taskTitle", "units", "plannerStepId"}.isdisjoint(dispatch_data)
    assert not any(
        chunk["payload"].get("eventType") == "long_task.progress"
        for chunk in public_chunks
    )
    private_progress_sequences = [
        event.sequence for event in root_events
        if event.visibility.value == "private"
        and event.payload.get("eventType") == "long_task.progress"
    ]
    assert private_progress_sequences
    assert all(
        sequence not in {chunk["sequence"] for chunk in public_chunks}
        for sequence in private_progress_sequences
    )
    done_updates = [
        chunk["payload"]["data"]
        for chunk in public_chunks
        if chunk["payload"].get("eventType") == "run.todo_updated"
        and chunk["payload"]["data"].get("step", {}).get("status") == "done"
    ]
    expected_done = [
        {
            "step_id": step_id,
            "status": "done",
            "result_summary": "Durable execution completed this Planner step.",
        }
        for step_id in (
            "collect-evidence",
            "draft-next-three",
            "deliver-next-three",
        )
    ]
    assert [
        {
            "step_id": update["step_id"],
            "status": update["step"]["status"],
            "result_summary": update["step"]["result_summary"],
        }
        for update in done_updates
    ] == expected_done
    assert await screenplay_db.fetch_all(
        "SELECT step_id, status, result_summary FROM ai_agent_run_todos "
        "WHERE run_id = ? ORDER BY sort",
        [root_run_id],
    ) == expected_done
    assert public_chunks[-2]["payload"]["data"]["step_id"] == (
        "deliver-next-three"
    )
    assert public_chunks[-1]["kind"] == "run.lifecycle"
    assert public_chunks[-1]["payload"] == {
        "status": "done",
        "final_response": projected_turn["assistantContent"],
    }
    assert not any(
        chunk["kind"] == "provider.content_delta"
        and chunk["channel"] == "final"
        for chunk in public_chunks
    )
    assert [call.checkpoint_key for call in checkpoint_planner.calls] == [
        "episode:4",
        "episode:5",
        "episode:6",
    ]
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_checkpoint_plans "
        "WHERE status = 'applied'"
    ) == {"count": 3}
    revision_events = await screenplay_db.fetch_all(
        "SELECT json_extract(payload_json, '$.planRevision.identity') AS identity "
        "FROM ai_agent_run_events WHERE run_id = ? "
        "AND event_type = 'run.todos_updated' "
        "AND json_extract(payload_json, '$.planRevision.identity') IS NOT NULL "
        "ORDER BY id",
        [root_run_id],
    )
    assert [row["identity"] for row in revision_events] == [
        "episode:4",
        "episode:5",
        "episode:6",
    ]
    assert await screenplay_db.fetch_one(
        "SELECT title, description FROM ai_agent_run_todos "
        "WHERE run_id = ? AND step_id = 'deliver-next-three'",
        [root_run_id],
    ) == {
        "title": "交付三集连续候选稿",
        "description": "根据首集检查点继续交付",
    }
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_revisions "
        "WHERE agent_task_id = ?",
        [task["id"]],
    ) == {"count": 1}

    removed = await service.truncate_from_turn(turn["id"])
    assert removed["deletedTaskIds"] == [task["id"]]
    assert removed["deletedOperationIds"] == [operation["id"]]
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_tasks"
    ) == {"count": 0}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_checkpoint_plans"
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_checkpoint_scope_change_pauses_then_explicit_continuation_replans(
    screenplay_db,
    monkeypatch,
):
    projects = ScreenplayV2ProjectService(screenplay_db)
    workspace = await projects.create_project(
        command_id="create-checkpoint-pause-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Checkpoint pause",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    plan = {
        "needsTodos": True,
        "title": "创作下一集",
        "goal": "完成下一集候选稿",
        "taskSpec": {
            "goal": "完成下一集候选稿",
            "operation": "create",
            "instruction": "创作下一集",
            "deliverable": "screenplayDraft",
            "target": {"screenplay": {
                "version": 1,
                "scope": {"kind": "next_episodes", "count": 1},
                "stepBindings": [
                    {"stepId": "evidence", "phase": "evidence"},
                    {"stepId": "draft", "phase": "creation"},
                    {"stepId": "deliver", "phase": "delivery"},
                ],
            }},
        },
        "todos": [
            {"id": "evidence", "title": "读取依据", "type": "analyze", "executor": "model"},
            {"id": "draft", "title": "创作本集", "type": "write", "executor": "model"},
            {"id": "deliver", "title": "交付候选稿", "type": "write", "executor": "model"},
        ],
    }
    gateway = _ScriptedPlannerGateway([
        [
            ModelStreamChunk(content_delta=json.dumps(plan, ensure_ascii=False)),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
        [
            ModelStreamChunk(content_delta=json.dumps({
                "protocol": "screenplay.checkpoint-plan.v1",
                "outcome": "requires_reresolution",
                "code": "checkpoint_scope_requires_reresolution",
            })),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
        [
            ModelStreamChunk(content_delta=json.dumps({
                "protocol": "screenplay.checkpoint-plan.v1",
                "outcome": "unchanged",
            })),
            ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
        ],
    ])
    monkeypatch.setattr(
        agent_composition,
        "ProviderModelGateway",
        lambda *_args, **_kwargs: gateway,
    )
    monkeypatch.setattr(
        composition_factory,
        "build_screenplay_agent_profile",
        lambda *, db, **_kwargs: ScreenplayAgentProfile(
            db,
            resolver=_SingleDraftResolver(),
        ),
    )
    executor = _UnitExecutor(screenplay_db)
    composition = create_agent_composition(screenplay_db)
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id=composition.execution_owner_id,
        composition=composition,
        unit_executor_factory=lambda _runtime: executor,
        projects=projects,
    )
    request = SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session["id"],
        "content": "创作下一集。",
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://api.deepseek.com/v1",
            "options": {"model": "deepseek-v4-flash"},
            "contextWindow": "128k",
        },
    })
    executor.checkpoint_planner = ScreenplayCheckpointPlanner(
        ScreenplayStructuredCallService(
            screenplay_db,
            composition=composition,
        ),
        runtime=request.runtime,
    )
    turn = await service.submit_turn(
        command_id="checkpoint-pause-turn",
        project_id=workspace["project"]["id"],
        request=request,
    )
    try:
        await service.execute_turn(turn["id"], request.runtime)
        snapshot = await service.get_snapshot(
            project_id=workspace["project"]["id"],
            session_id=session["id"],
        )
        old_root_run_id = snapshot["turns"][0]["rootRunId"]
        resumed = await service.prepare_resume(
            snapshot["operations"][0]["id"],
            idempotency_key="resume-checkpoint-scope-change",
            request=ResumeScreenplayOperationRequest.model_validate({
                "expectedOperationRevision": snapshot["operations"][0][
                    "revision"
                ],
                "runtime": request.runtime.model_dump(mode="json"),
            }),
        )
        await service.execute_resumed_operation(
            resumed["operationId"],
            request.runtime,
            continuation_command="resume-checkpoint-scope-change",
        )
        completed = await service.get_snapshot(
            project_id=workspace["project"]["id"],
            session_id=session["id"],
        )
    finally:
        await composition.shutdown()

    assert snapshot["turns"][0]["status"] == "paused"
    assert snapshot["operations"][0]["status"] == "paused"
    assert snapshot["tasks"][0]["status"] == "paused"
    assert snapshot["operations"][0]["error"]["code"] == (
        "checkpoint_scope_requires_reresolution"
    )
    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [old_root_run_id],
    ) == {"status": "canceled"}
    run_rows = await screenplay_db.fetch_all(
        "SELECT id, status, binding_command_id FROM ai_agent_runs ORDER BY rowid"
    )
    assert len(run_rows) == 2, run_rows
    assert completed["turns"][0]["status"] == "completed"
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE "
        "binding_namespace = 'screenplay.checkpoint_plan'",
    ) == {"count": 0}
    assert await screenplay_db.fetch_one(
        "SELECT status, outcome, error_code FROM screenplay_checkpoint_plans"
    ) == {
        "status": "applied",
        "outcome": "unchanged",
        "error_code": None,
    }
    new_root_run_id = completed["turns"][0]["rootRunId"]
    assert new_root_run_id != old_root_run_id
    assert completed["operations"][0]["status"] == "succeeded"
    assert completed["tasks"][0]["status"] == "completed"
    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [new_root_run_id],
    ) == {"status": "done"}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ? "
        "AND payload_json LIKE '%resume-checkpoint-scope-change%'",
        [old_root_run_id],
    ) == {"count": 0}
    continuation_events = await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ? "
        "AND payload_json LIKE '%resume-checkpoint-scope-change%'",
        [new_root_run_id],
    )
    assert int(continuation_events["count"]) > 0


@pytest.mark.asyncio
async def test_formal_root_retries_the_complete_business_projection_transaction(
    screenplay_db,
    monkeypatch,
):
    class RejectAfterScreenplayProjection:
        def __init__(self):
            self.calls = 0

        async def project(self, run_id, commit):
            binding = await screenplay_db.fetch_one(
                "SELECT binding_namespace FROM ai_agent_runs WHERE id = ?",
                [run_id],
            )
            if (
                commit.terminal_status is not None
                and binding == {"binding_namespace": "screenplay.conversation_turn"}
                and commit.terminal_status.value == "done"
            ):
                self.calls += 1
                if self.calls == 1:
                    raise RunCommitProjectionError(
                        "injected later Root projection failure",
                        retryable=True,
                    )

    projects = ScreenplayV2ProjectService(screenplay_db)
    workspace = await projects.create_project(
        command_id="create-atomic-root-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Atomic formal root",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    plan = {
        "needsTodos": True,
        "title": "创作剧本",
        "taskSpec": {
            "goal": "创作剧本",
            "operation": "create",
            "instruction": "创作剧本",
            "deliverable": "screenplayDraft",
            "target": {"screenplay": {
                "version": 1,
                "scope": {"kind": "next_episodes", "count": 3},
                "stepBindings": [
                    {"stepId": "read", "phase": "evidence"},
                    {"stepId": "create", "phase": "creation"},
                    {"stepId": "deliver", "phase": "delivery"},
                ],
            }},
        },
        "todos": [
            {"id": "read", "title": "读取", "type": "analyze", "executor": "model", "riskLevel": "read"},
            {"id": "create", "title": "生成", "type": "write", "executor": "model", "riskLevel": "write"},
            {"id": "deliver", "title": "交付", "type": "write", "executor": "model", "riskLevel": "write"},
        ],
    }
    gateway = _ScriptedPlannerGateway([[
        ModelStreamChunk(content_delta=json.dumps(plan, ensure_ascii=False)),
        ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
    ]])
    monkeypatch.setattr(
        agent_composition,
        "ProviderModelGateway",
        lambda *_args, **_kwargs: gateway,
    )
    monkeypatch.setattr(
        composition_factory,
        "build_screenplay_agent_profile",
        lambda *, db, **_kwargs: ScreenplayAgentProfile(
            db,
            resolver=_Resolver(),
        ),
    )
    rejecting_projector = RejectAfterScreenplayProjection()
    composition = create_agent_composition(
        screenplay_db,
        run_commit_projector=rejecting_projector,
    )
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id=composition.execution_owner_id,
        composition=composition,
        unit_executor_factory=lambda _runtime: _UnitExecutor(screenplay_db),
        projects=projects,
    )
    request = SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session["id"],
        "content": "连续创作后面三集",
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://api.deepseek.com/v1",
            "options": {
                "model": "deepseek-v4-flash",
                "model_profile": "deepseek:deepseek-v4-flash",
            },
            "contextWindow": "128k",
        },
    })
    turn = await service.submit_turn(
        command_id="formal-atomic-root",
        project_id=workspace["project"]["id"],
        request=request,
    )

    try:
        await service.execute_turn(turn["id"], request.runtime)
        snapshot = await service.get_snapshot(
            project_id=workspace["project"]["id"],
            session_id=session["id"],
        )
        projected_turn = snapshot["turns"][0]
        root = await screenplay_db.fetch_one(
            "SELECT status FROM ai_agent_runs WHERE id = ?",
            [projected_turn["rootRunId"]],
        )
        terminal_events = await screenplay_db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_run_events "
            "WHERE run_id = ? AND kind = 'run.lifecycle' "
            "AND json_extract(payload_json, '$.status') = 'done'",
            [projected_turn["rootRunId"]],
        )
        revision_count = await screenplay_db.fetch_one(
            "SELECT COUNT(*) AS count FROM screenplay_revisions "
            "WHERE agent_task_id = ?",
            [snapshot["tasks"][0]["id"]],
        )
    finally:
        await composition.shutdown()
    assert rejecting_projector.calls == 2
    assert root == {"status": "done"}
    assert terminal_events == {"count": 1}
    assert projected_turn["status"] == "completed"
    assert snapshot["operations"][0]["status"] == "succeeded"
    assert revision_count == {"count": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("executor_factory", "turn_status", "operation_status", "run_status"),
    (
        (lambda db: _PauseAfterOneUnitExecutor(db), "paused", "paused", "canceled"),
        (lambda db: _ExplodingUnitExecutor(), "failed", "failed", "failed"),
    ),
)
async def test_screenplay_formal_root_settles_non_success_terminal_states(
    screenplay_db,
    monkeypatch,
    executor_factory,
    turn_status,
    operation_status,
    run_status,
):
    class RejectFirstMatchingTerminal:
        def __init__(self):
            self.calls = 0

        async def project(self, run_id, commit):
            binding = await screenplay_db.fetch_one(
                "SELECT binding_namespace FROM ai_agent_runs WHERE id = ?",
                [run_id],
            )
            if (
                binding == {"binding_namespace": "screenplay.conversation_turn"}
                and commit.terminal_status is not None
                and commit.terminal_status.value == run_status
            ):
                self.calls += 1
                if self.calls == 1:
                    raise RunCommitProjectionError(
                        "injected non-success projection failure",
                        retryable=True,
                    )

    projects = ScreenplayV2ProjectService(screenplay_db)
    workspace = await projects.create_project(
        command_id=f"create-{turn_status}-root-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Formal terminal screenplay",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    plan = {
        "needsTodos": True,
        "title": "生成原作分析",
        "taskSpec": {
            "goal": "生成原作分析",
            "operation": "create",
            "instruction": "生成原作分析",
            "deliverable": "screenplayDraft",
            "target": {"screenplay": {
                "version": 1,
                "scope": {"kind": "next_episodes", "count": 1},
                "stepBindings": [
                    {"stepId": "read", "phase": "evidence"},
                    {"stepId": "create", "phase": "creation"},
                    {"stepId": "deliver", "phase": "delivery"},
                ],
            }},
        },
        "todos": [
            {"id": "read", "title": "读取", "type": "analyze", "executor": "model", "riskLevel": "read"},
            {"id": "create", "title": "生成", "type": "write", "executor": "model", "riskLevel": "write"},
            {"id": "deliver", "title": "交付", "type": "write", "executor": "model", "riskLevel": "write"},
        ],
    }
    gateway = _ScriptedPlannerGateway([[
        ModelStreamChunk(content_delta=json.dumps(plan, ensure_ascii=False)),
        ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
    ]])
    monkeypatch.setattr(agent_composition, "ProviderModelGateway", lambda *_a, **_k: gateway)
    monkeypatch.setattr(
        composition_factory,
        "build_screenplay_agent_profile",
        lambda *, db, **_kwargs: ScreenplayAgentProfile(
            db,
            resolver=_SingleDraftResolver(),
        ),
    )
    rejecting_projector = RejectFirstMatchingTerminal()
    composition = create_agent_composition(
        screenplay_db,
        run_commit_projector=rejecting_projector,
    )
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id=composition.execution_owner_id,
        composition=composition,
        unit_executor_factory=lambda _runtime: executor_factory(screenplay_db),
        projects=projects,
    )
    request = SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session["id"],
        "content": "生成原作分析",
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://api.deepseek.com/v1",
            "options": {"model": "deepseek-v4-flash", "model_profile": "deepseek:deepseek-v4-flash"},
            "contextWindow": "128k",
        },
    })
    turn = await service.submit_turn(
        command_id=f"formal-{turn_status}-root",
        project_id=workspace["project"]["id"],
        request=request,
    )
    try:
        await service.execute_turn(turn["id"], request.runtime)
        initial_snapshot = await service.get_snapshot(
            project_id=workspace["project"]["id"],
            session_id=session["id"],
        )
        old_root_run_id = initial_snapshot["turns"][0]["rootRunId"]
        if turn_status == "paused":
            service._unit_executor_factory = (
                lambda _runtime: _UnitExecutor(screenplay_db)
            )
            resumed = await service.prepare_resume(
                initial_snapshot["operations"][0]["id"],
                idempotency_key="resume-formal-paused-root",
                request=ResumeScreenplayOperationRequest.model_validate({
                    "expectedOperationRevision": initial_snapshot["operations"][0][
                        "revision"
                    ],
                    "runtime": request.runtime.model_dump(mode="json"),
                }),
            )
            await service.execute_resumed_operation(
                resumed["operationId"],
                request.runtime,
                continuation_command="resume-formal-paused-root",
            )
    finally:
        await composition.shutdown()
    snapshot = initial_snapshot
    assert rejecting_projector.calls == 2
    if turn_status == "paused":
        completed = await service.get_snapshot(
            project_id=workspace["project"]["id"],
            session_id=session["id"],
        )
        new_root_run_id = completed["turns"][0]["rootRunId"]
        assert new_root_run_id != old_root_run_id, completed
        assert completed["turns"][0]["status"] == "completed", await screenplay_db.fetch_one(
            "SELECT status, error_json FROM screenplay_agent_turns WHERE id = ?",
            [turn["id"]],
        )
        assert completed["operations"][0]["status"] == "succeeded"
        assert await screenplay_db.fetch_one(
            "SELECT status FROM ai_agent_runs WHERE id = ?",
            [old_root_run_id],
        ) == {"status": "canceled"}
        assert await screenplay_db.fetch_one(
            "SELECT status FROM ai_agent_runs WHERE id = ?",
            [new_root_run_id],
        ) == {"status": "done"}
        replayed = await service.prepare_resume(
            initial_snapshot["operations"][0]["id"],
            idempotency_key="resume-formal-paused-root",
            request=ResumeScreenplayOperationRequest.model_validate({
                "expectedOperationRevision": initial_snapshot["operations"][0][
                    "revision"
                ],
                "runtime": request.runtime.model_dump(mode="json"),
            }),
        )
        assert replayed["continuationRootRunId"] == new_root_run_id
        assert replayed["dispatchRequired"] is False
        assert await screenplay_db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE "
            "binding_namespace = 'screenplay.conversation_turn' AND "
            "binding_command_id = ?",
            ["resume-formal-paused-root"],
        ) == {"count": 1}
        assert await screenplay_db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ? "
            "AND payload_json LIKE '%resume-formal-paused-root%'",
            [old_root_run_id],
        ) == {"count": 0}
        continuation_events = await screenplay_db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ? "
            "AND payload_json LIKE '%resume-formal-paused-root%'",
            [new_root_run_id],
        )
        assert int(continuation_events["count"]) > 0
        assert await screenplay_db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ? "
            "AND event_type = 'run.todos_updated'",
            [new_root_run_id],
        ) == {"count": 1}
        assert await screenplay_db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_long_task_units "
            "WHERE task_id = ? AND attempt > 1",
            [completed["tasks"][0]["id"]],
        ) == {"count": 1}
        completed_first_attempt = await screenplay_db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_long_task_units "
            "WHERE task_id = ? AND status = 'completed' AND attempt = 1",
            [completed["tasks"][0]["id"]],
        )
        assert int(completed_first_attempt["count"]) >= 1
    snapshot = await service.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    ) if turn_status != "paused" else snapshot
    assert snapshot["turns"][0]["status"] == turn_status
    assert snapshot["operations"][0]["status"] == operation_status
    run = await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [snapshot["turns"][0]["rootRunId"]],
    )
    assert run == {"status": run_status}
