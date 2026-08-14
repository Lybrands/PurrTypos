from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

import pytest
import pytest_asyncio

from application.screenplay_agent_service import (
    ScreenplayAgentService,
    _ACTIVE_TASKS,
    _ScreenplayContinuationRunLifecycle,
)
from application.agent_cancellation_service import AgentCancellationService
from application.screenplay_task_resolver import ResolvedScreenplayTask
from application.screenplay_agent_profile import ScreenplayAgentProfileExtension
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
from application.screenplay_part_artifacts import ScreenplayPartArtifactQuery
from application.screenplay_v2_service import ScreenplayV2ProjectService
from database.connection import DatabaseConnection
from domains.screenplay_agent import (
    ContinuationStartLost,
    OperationUsage,
    ScreenplayIntentAction,
    ScreenplayOperationCreateCommand,
)
from infrastructure.persistence.sqlite_screenplay_operation_repository import (
    SqliteScreenplayOperationRepository,
)
from infrastructure.persistence.sqlite_screenplay_agent_repository import (
    SqliteScreenplayAgentRepository,
)
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from infrastructure.persistence.sqlite_work_item_repository import (
    SqliteWorkItemRepository,
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
    RunBinding,
    RunCreateParams,
    RunStatus,
)
from purra.events import AgentEvent, CoreEventType
from purra.ports import RunCommit
from purra.api import AgentCore
from purra.errors import (
    ContractViolationError,
    ModelGatewayError,
    RunCommitProjectionError,
)
from purra.long_tasks import LongTaskUnitResult, RecipeLongTaskDispatcher
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
from infrastructure.persistence.run_execution_store import SqliteExecutionLeaseStore
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
        self._leases = SqliteExecutionLeaseStore(db)

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


async def test_screenplay_profile_dispatcher_registers_only_the_injected_executor(
    screenplay_db,
):
    executor = _UnitExecutor(screenplay_db)
    dispatcher = ScreenplayAgentProfileExtension(
        screenplay_db,
        owner_id="screenplay-profile-dispatcher-test",
    ).create_long_task_dispatcher(
        work_item_repository=SqliteWorkItemRepository(screenplay_db),
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
        "SELECT status FROM ai_agent_run_cancellations WHERE root_run_id = ?",
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
                    "(root_run_id, cancellation_epoch, status, requested_at_ms, "
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
async def test_truncate_cancels_complete_root_tree_before_business_delete(
    screenplay_db,
):
    identity = await _answer_projection_fixture(screenplay_db, "truncate_tree")
    root_run_id = identity["rootRunId"]
    child_run_id = "run-truncate-host-child"
    await screenplay_db.execute(
        "UPDATE ai_agent_runs SET execution_owner_id = 'foreign-worker', "
        "lease_expires_at_ms = 0 WHERE id = ?",
        [root_run_id],
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, status, mode, prompt, binding_namespace, binding_aggregate_id, "
        "binding_command_id, binding_attributes_json, parent_run_id, "
        "root_run_id, agent_role, run_depth, execution_owner_id, "
        "lease_expires_at_ms) VALUES (?, 'running', 'agent', '', "
        "'screenplay.checkpoint_plan', ?, 'checkpoint-child', '{}', ?, ?, "
        "'screenplay-part', 1, 'foreign-child-worker', 0)",
        [
            child_run_id,
            identity["projectId"],
            root_run_id,
            root_run_id,
        ],
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_delegations "
        "(id, parent_run_id, root_run_id, child_run_id, agent_role, "
        "objective, status, worker_id, claim_expires_at_ms) VALUES "
        "('delegation-truncate', ?, ?, ?, 'screenplay-part', 'test', "
        "'claimed', 'foreign-delegation-worker', 9999999999999)",
        [root_run_id, root_run_id, child_run_id],
    )
    await screenplay_db.execute(
        "INSERT INTO ai_agent_host_child_runs "
        "(host_child_key, identity_digest, contract_json, attempt_key, "
        "run_id) VALUES ('host-child-truncate', 'identity-truncate', '{}', "
        "'attempt-truncate', ?)",
        [child_run_id],
    )
    composition = create_agent_composition(screenplay_db)
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id=composition.execution_owner_id,
        composition=composition,
        projects=object(),
    )
    try:
        removed = await service.truncate_from_turn(identity["turnId"])
    finally:
        await composition.shutdown()

    assert removed["deletedTurnIds"] == [identity["turnId"]]
    assert await screenplay_db.fetch_one(
        "SELECT id FROM screenplay_agent_turns WHERE id = ?",
        [identity["turnId"]],
    ) is None
    assert await screenplay_db.fetch_all(
        "SELECT id, status, execution_owner_id, lease_expires_at_ms "
        "FROM ai_agent_runs WHERE id IN (?, ?) ORDER BY id",
        [root_run_id, child_run_id],
    ) == [
        {
            "id": root_run_id,
            "status": "canceled",
            "execution_owner_id": None,
            "lease_expires_at_ms": None,
        },
        {
            "id": child_run_id,
            "status": "canceled",
            "execution_owner_id": None,
            "lease_expires_at_ms": None,
        },
    ]
    assert await screenplay_db.fetch_one(
        "SELECT status, worker_id, claim_expires_at_ms FROM "
        "ai_agent_delegations WHERE id = 'delegation-truncate'"
    ) == {
        "status": "canceled",
        "worker_id": None,
        "claim_expires_at_ms": None,
    }
    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_run_cancellations WHERE root_run_id = ?",
        [root_run_id],
    ) == {"status": "completed"}
    assert await screenplay_db.fetch_one(
        "SELECT host_child_key FROM ai_agent_host_child_runs WHERE "
        "host_child_key = 'host-child-truncate'"
    ) == {"host_child_key": "host-child-truncate"}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_cancel_commands "
        "WHERE turn_id = ?",
        [identity["turnId"]],
    ) == {"count": 0}


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
async def test_root_usage_projects_actual_lineage_once_and_excludes_foreign_run(
    screenplay_db,
):
    operation, _turn_id, _command, _finalizer = await _finalization_fixture(
        screenplay_db
    )
    for run_id, root_id in (
        ("usage-root", None),
        ("usage-child", "usage-root"),
        ("usage-foreign", "another-root"),
    ):
        await screenplay_db.execute(
            "INSERT INTO ai_agent_runs (id, status, prompt, root_run_id) "
            "VALUES (?, 'running', '', ?)",
            [run_id, root_id],
        )
        await screenplay_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json, event_id, sequence, source, "
            "kind, channel, visibility, source_event_key) VALUES "
            "(?, 'provider.usage', ?, ?, 1, 'provider', 'provider.usage', "
            "'diagnostic', 'private', ?)",
            [
                run_id,
                json.dumps({
                    "inputTokens": 10,
                    "outputTokens": 4,
                    "reasoningOutputTokens": 2,
                }),
                f"event-{run_id}",
                f"usage:{run_id}",
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
    ) == {"count": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_status", "task_status", "business_status"),
    (
        (RunStatus.FAILED, "failed", "failed"),
        (RunStatus.CANCELED, "canceled", "canceled"),
    ),
)
async def test_non_success_root_projects_usage_and_business_terminal_once(
    screenplay_db,
    terminal_status,
    task_status,
    business_status,
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
        "binding_aggregate_id, binding_command_id, binding_attributes_json, "
        "root_run_id) VALUES (?, ?, 'running', 'agent', '', ?, ?, ?, ?, ?)",
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
            root_run_id,
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
        "INSERT INTO ai_agent_long_tasks "
        "(id, work_item_id, namespace, kind, owner_id, created_by_run_id, "
        "status, total_units) VALUES "
        "('task-atomic-finalizer', 'work-terminal', 'purrtypos.screenplay', "
        "'recipe', ?, ?, ?, 1)",
        [turn["project_id"], root_run_id, task_status],
    )
    for run_id, lineage_root in (
        (root_run_id, root_run_id),
        (f"{root_run_id}-child", root_run_id),
    ):
        if run_id != root_run_id:
            await screenplay_db.execute(
                "INSERT INTO ai_agent_runs "
                "(id, status, prompt, parent_run_id, root_run_id, run_depth) "
                "VALUES (?, 'done', '', ?, ?, 1)",
                [run_id, root_run_id, lineage_root],
            )
        await screenplay_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json, event_id, sequence, source, "
            "kind, channel, visibility, source_event_key) VALUES "
            "(?, 'provider.usage', ?, ?, 2, 'provider', 'provider.usage', "
            "'diagnostic', 'private', ?)",
            [
                run_id,
                json.dumps({"inputTokens": 11, "outputTokens": 5}),
                f"usage-event-{run_id}",
                f"usage:{run_id}",
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
    ) == {"count": 2}


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
        "binding_aggregate_id, binding_command_id, root_run_id) "
        "VALUES (?, ?, 'canceled', 'agent', '', ?, ?, ?, ?)",
        [
            source_root_run_id,
            turn["session_id"],
            "screenplay.conversation_turn",
            turn["project_id"],
            "command-atomic-finalizer",
            source_root_run_id,
        ],
    )
    await db.execute(
        "UPDATE screenplay_agent_turns SET planner_run_id = ? WHERE id = ?",
        [source_root_run_id, turn_id],
    )
    await db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, work_item_id, namespace, kind, owner_id, created_by_run_id, "
        "status, total_units) VALUES (?, ?, ?, 'recipe', ?, ?, 'paused', 1)",
        [
            "task-atomic-finalizer",
            "work-paused-continuation",
            "purrtypos.screenplay",
            turn["project_id"],
            source_root_run_id,
        ],
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
        "SELECT planner_run_id FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    ) == {"planner_run_id": begun.run_id}
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
        "SELECT planner_run_id FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    ) == {"planner_run_id": source_root_run_id}
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
        "SELECT planner_run_id FROM screenplay_agent_turns WHERE id = ?",
        [turn_id],
    ) == {"planner_run_id": source_root_run_id}
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
        "build_screenplay_profile_extension",
        lambda *, db, **_kwargs: ScreenplayAgentProfileExtension(
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
        "(id = ? OR root_run_id = ?) AND (status = 'running' OR "
        "execution_owner_id IS NOT NULL OR lease_expires_at_ms IS NOT NULL)",
        [root_run_id, root_run_id],
    ) == {"count": 0}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_task_units WHERE "
        "task_id = ? AND (status IN ('claimed', 'running') OR "
        "worker_id IS NOT NULL OR lease_expires_at_ms IS NOT NULL)",
        [operation["long_task_id"]],
    ) == {"count": 0}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_delegations WHERE "
        "root_run_id = ? AND status IN ('queued', 'claimed', 'running')",
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
    assert snapshot["turns"][0]["plannerRunId"] == root_run_id
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs"
    ) == {"count": 1}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs "
        "WHERE parent_run_id IS NOT NULL"
    ) == {"count": 0}
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
        "SELECT COUNT(*) AS count FROM ai_agent_run_events "
        "WHERE json_extract(payload_json, '$.phase') = "
        "'screenplay_intent_planning'"
    ) == {"count": 0}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_operations"
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_screenplay_formal_turn_finishes_on_the_same_root_run(
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
        "build_screenplay_profile_extension",
        lambda *, db, **_kwargs: ScreenplayAgentProfileExtension(
            db,
            resolver=_Resolver(),
        ),
    )
    executor = _UnitExecutor(screenplay_db)

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
    executor.checkpoint_planner = checkpoint_planner
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
        "content": "不要只写下一集，连续写完后面三集。",
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
        command_id="formal-root-next-three",
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
    projected_turn = snapshot["turns"][0]
    root_run_id = projected_turn["rootRunId"]
    assert projected_turn["status"] == "completed", (projected_turn, snapshot)
    assert projected_turn["assistantContent"] == (
        "第 4 至 6 集候选稿已经完成。可以在候选稿区域查看并继续编辑。"
    )
    assert projected_turn["plannerRunId"] == root_run_id
    assert len(snapshot["operations"]) == 1
    operation = snapshot["operations"][0]
    task = snapshot["tasks"][0]
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
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs"
    ) == {"count": 1}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs "
        "WHERE parent_run_id IS NOT NULL"
    ) == {"count": 0}
    run = await screenplay_db.fetch_one(
        "SELECT id, final_response FROM ai_agent_runs WHERE id = ?",
        [root_run_id],
    )
    assert run == {
        "id": root_run_id,
        "final_response": projected_turn["assistantContent"],
    }
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
        "SELECT COUNT(*) AS count FROM ai_agent_work_items"
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
        "build_screenplay_profile_extension",
        lambda *, db, **_kwargs: ScreenplayAgentProfileExtension(
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
        old_root_event_count = await screenplay_db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ?",
            [old_root_run_id],
        )
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
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs "
        "WHERE parent_run_id IS NULL",
    ) == {"count": 2}
    assert completed["turns"][0]["status"] == "completed"
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE "
        "binding_namespace = 'screenplay.checkpoint_plan'",
    ) == {"count": 2}
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
        "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ?",
        [old_root_run_id],
    ) == old_root_event_count


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
        "build_screenplay_profile_extension",
        lambda *, db, **_kwargs: ScreenplayAgentProfileExtension(
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
        "build_screenplay_profile_extension",
        lambda *, db, **_kwargs: ScreenplayAgentProfileExtension(
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
        old_root_event_count = await screenplay_db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ?",
            [old_root_run_id],
        )
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
            "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ?",
            [old_root_run_id],
        ) == old_root_event_count
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
