from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

from application.screenplay_agent_service import (
    PlannedScreenplayIntent,
    ResolvedScreenplayTask,
    ScreenplayAgentService,
)
from application.screenplay_candidate_assembler import (
    ScreenplayCandidateAssembler,
)
from application.screenplay_part_artifacts import ScreenplayPartArtifactQuery
from application.screenplay_v2_service import ScreenplayV2ProjectService
from database.connection import DatabaseConnection
from domains.screenplay_agent import (
    ScreenplayIntent,
    ScreenplayIntentAction,
    ScreenplayIntentScope,
    ScreenplayOperationCreateCommand,
)
from domains.screenplay_agent.contracts import ScreenplayScopeKind
from infrastructure.persistence.sqlite_screenplay_operation_repository import (
    SqliteScreenplayOperationRepository,
)
from infrastructure.persistence import run_store
from infrastructure.persistence.sqlite_screenplay_operation_finalizer import (
    ScreenplayOperationFinalizationCommand,
    SqliteScreenplayOperationFinalizer,
)
from purra.long_tasks import LongTaskUnitResult
from purra.errors import ModelGatewayError
from domains.screenplay_agent.recovery import classify_screenplay_run_failure
from schemas.screenplay_agent import SubmitScreenplayAgentTurnRequest
from schemas.screenplay_v2 import CreateScreenplayV2ProjectRequest


@pytest_asyncio.fixture
async def screenplay_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


class _Planner:
    def __init__(self, intent: ScreenplayIntent) -> None:
        self.intent = intent

    async def plan(self, **_kwargs):
        return PlannedScreenplayIntent(self.intent, "run-screenplay-planner")


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


class _UnitExecutor:
    def __init__(self, db) -> None:
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


class _BlockingUnitExecutor:
    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def execute(self, context, signal=None):
        del context, signal
        self.entered.set()
        await asyncio.Event().wait()
        raise AssertionError("canceled screenplay unit resumed")


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
async def test_service_cancel_settles_active_task_operation_and_turn(screenplay_db):
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
    intent = ScreenplayIntent(
        action=ScreenplayIntentAction.CREATE,
        instruction="完成接下来三集",
        scope=ScreenplayIntentScope(
            kind=ScreenplayScopeKind.NEXT_EPISODES,
            count=3,
        ),
        requested_deliverable="screenplayDraft",
    )
    executor = _BlockingUnitExecutor()
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id="screenplay-cancel-test",
        planner=_Planner(intent),
        resolver=_Resolver(),
        unit_executor_factory=lambda _runtime: executor,
        projects=projects,
    )
    request = SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session["id"],
        "content": "开始后等待取消。",
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "options": {"model": "fixture-model"},
        },
    })
    turn = await service.submit_turn(
        command_id="cancel-active-turn",
        project_id=workspace["project"]["id"],
        request=request,
    )
    execution = service.dispatch_turn(turn["id"], request.runtime)
    await asyncio.wait_for(executor.entered.wait(), timeout=2)
    active_operation = await screenplay_db.fetch_one(
        "SELECT long_task_id FROM screenplay_agent_operations WHERE turn_id = ?",
        [turn["id"]],
    )
    bound_run_id = await run_store.create_run(
        screenplay_db,
        session_id=session["id"],
        prompt="bound cancel fixture",
        mode="agent",
    )
    await screenplay_db.execute(
        "UPDATE ai_agent_long_task_units SET status = 'running', run_id = ? "
        "WHERE task_id = ? AND status = 'claimed'",
        [bound_run_id, active_operation["long_task_id"]],
    )

    first = await service.cancel_turn(
        turn["id"],
        idempotency_key="cancel-active-command",
    )
    replay = await service.cancel_turn(
        turn["id"],
        idempotency_key="cancel-active-command",
    )
    try:
        await execution
    except asyncio.CancelledError:
        pass

    assert replay == first
    assert first["terminalStatus"] == "canceled"
    operation = await screenplay_db.fetch_one(
        "SELECT status, long_task_id, cancel_receipt_id "
        "FROM screenplay_agent_operations WHERE turn_id = ?",
        [turn["id"]],
    )
    assert operation["status"] == "canceled"
    assert operation["cancel_receipt_id"] == first["cancelReceiptId"]
    assert await screenplay_db.fetch_one(
        "SELECT status, assistant_content FROM screenplay_agent_turns WHERE id = ?",
        [turn["id"]],
    ) == {"status": "canceled", "assistant_content": ""}
    assert await screenplay_db.fetch_one(
        "SELECT status FROM ai_agent_long_tasks WHERE id = ?",
        [operation["long_task_id"]],
    ) == {"status": "canceled"}
    assert {
        row["status"] for row in await screenplay_db.fetch_all(
            "SELECT status FROM ai_agent_long_task_units WHERE task_id = ?",
            [operation["long_task_id"]],
        )
    } == {"canceled"}
    assert (
        await screenplay_db.fetch_one(
            "SELECT cancel_requested_at_ms FROM ai_agent_runs WHERE id = ?",
            [bound_run_id],
        )
    )["cancel_requested_at_ms"] is not None
    canceled_snapshot = await service.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )
    projected_operation = canceled_snapshot["operations"][0]
    assert projected_operation["status"] == "canceled"
    assert projected_operation["cancelReceiptId"] == first["cancelReceiptId"]
    assert projected_operation["cancelRequestedAt"] is not None
    assert projected_operation["resultRevisionId"] is None
    assert projected_operation["finalizationReceiptId"] is None
    assert projected_operation["resultRevision"] is None


@pytest.mark.asyncio
async def test_screenplay_answer_turn_does_not_create_a_durable_task(screenplay_db):
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
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id="screenplay-answer-test",
        planner=_Planner(ScreenplayIntent(
            action=ScreenplayIntentAction.ANSWER,
            instruction="解释当前阶段",
            reply="当前处于创作简报阶段。",
        )),
        resolver=_Resolver(),
        unit_executor_factory=lambda _runtime: None,
        projects=projects,
    )
    request = SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session["id"],
        "content": "现在进行到哪一步了？",
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://provider.example/v1",
            "options": {"model": "fixture-model"},
            "contextWindow": "128k",
        },
    })
    turn = await service.submit_turn(
        command_id="answer-current-stage",
        project_id=workspace["project"]["id"],
        request=request,
    )

    await service.execute_turn(turn["id"], request.runtime)

    snapshot = await service.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )
    assert snapshot["tasks"] == []
    assert snapshot["turns"][0]["status"] == "completed"
    assert snapshot["turns"][0]["assistantContent"] == "当前处于创作简报阶段。"
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_agent_operations"
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_screenplay_execution_uses_purra_task_without_job_state(
    screenplay_db,
):
    projects = ScreenplayV2ProjectService(screenplay_db)
    workspace = await projects.create_project(
        command_id="create-durable-screenplay-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Durable screenplay",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    intent = ScreenplayIntent(
        action=ScreenplayIntentAction.CREATE,
        instruction="完成接下来三集",
        scope=ScreenplayIntentScope(
            kind=ScreenplayScopeKind.NEXT_EPISODES,
            count=3,
        ),
        requested_deliverable="screenplayDraft",
    )
    executor = _UnitExecutor(screenplay_db)
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id="screenplay-durable-test",
        planner=_Planner(intent),
        resolver=_Resolver(),
        unit_executor_factory=lambda _runtime: executor,
        projects=projects,
    )
    request = SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session["id"],
        "content": "不要只写下一集，连续写完后面三集。",
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://provider.example/v1",
            "options": {"model": "fixture-model"},
            "contextWindow": "128k",
        },
    })
    turn = await service.submit_turn(
        command_id="durable-next-three",
        project_id=workspace["project"]["id"],
        request=request,
    )

    await service.execute_turn(turn["id"], request.runtime)

    snapshot = await service.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )
    task = snapshot["tasks"][0]
    revision_id = snapshot["turns"][0]["resultRevisionId"]
    assert snapshot["turns"][0]["taskId"] == task["id"]
    assert str(revision_id).startswith("sprev_")
    assert snapshot["turns"][0]["assistantContent"] == (
        "第 4 至 6 集候选稿已经完成。可以在候选稿区域查看并继续编辑。"
    )
    operation = await screenplay_db.fetch_one(
        "SELECT * FROM screenplay_agent_operations WHERE turn_id = ?",
        [turn["id"]],
    )
    assert operation is not None
    assert operation["status"] == "succeeded"
    assert operation["long_task_id"] == task["id"]
    assert operation["target_role"] == "screenplayDraft"
    assert operation["result_revision_id"] == revision_id
    assert operation["finalization_receipt_id"]
    assert str(operation["manifest_digest"]).startswith("sha256:")
    legacy_turn_state = await screenplay_db.fetch_one(
        "SELECT operation_id, task_id, target_role, result_revision_id, error_json "
        "FROM screenplay_agent_turns WHERE id = ?",
        [turn["id"]],
    )
    assert legacy_turn_state == {
        "operation_id": operation["id"],
        "task_id": None,
        "target_role": None,
        "result_revision_id": None,
        "error_json": None,
    }
    assert task["status"] == "completed"
    assert task["resultRevision"]["id"] == revision_id
    assert task["resultRevision"]["agentTaskId"] == task["id"]
    projected_operation = snapshot["operations"][0]
    assert projected_operation["status"] == "succeeded"
    assert projected_operation["taskId"] == task["id"]
    assert projected_operation["resultRevisionId"] == revision_id
    assert projected_operation["finalizationReceiptId"]
    assert projected_operation["cancelReceiptId"] is None
    assert projected_operation["resultRevision"]["id"] == revision_id
    assert projected_operation["parts"] == task["units"]
    expected_ids = [
        "evidence:4",
        "draft:4:ep04_s01",
        "episode:4:metadata",
        "episode:4:validation",
        "evidence:5",
        "draft:5:ep05_s01",
        "episode:5:metadata",
        "episode:5:validation",
        "evidence:6",
        "draft:6:ep06_s01",
        "episode:6:metadata",
        "episode:6:validation",
        "compose-final-response",
    ]
    assert [unit["id"] for unit in task["units"]] == expected_ids
    def output_ref(unit_id: str) -> str:
        return executor.output_refs[unit_id]
    assert executor.calls == [
        ("evidence:4", {}),
        ("draft:4:ep04_s01", {"evidence:4": output_ref("evidence:4")}),
        ("episode:4:metadata", {"draft:4:ep04_s01": output_ref("draft:4:ep04_s01")}),
        ("episode:4:validation", {"episode:4:metadata": output_ref("episode:4:metadata")}),
        ("evidence:5", {"episode:4:validation": output_ref("episode:4:validation")}),
        ("draft:5:ep05_s01", {"evidence:5": output_ref("evidence:5")}),
        ("episode:5:metadata", {"draft:5:ep05_s01": output_ref("draft:5:ep05_s01")}),
        ("episode:5:validation", {"episode:5:metadata": output_ref("episode:5:metadata")}),
        ("evidence:6", {"episode:5:validation": output_ref("episode:5:validation")}),
        ("draft:6:ep06_s01", {"evidence:6": output_ref("evidence:6")}),
        ("episode:6:metadata", {"draft:6:ep06_s01": output_ref("draft:6:ep06_s01")}),
        ("episode:6:validation", {"episode:6:metadata": output_ref("episode:6:metadata")}),
        (
            "compose-final-response",
            {
                f"episode:{number}:validation": output_ref(f"episode:{number}:validation")
                for number in (4, 5, 6)
            },
        ),
    ]
    assert await screenplay_db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name IN ('screenplay_agent_jobs', 'screenplay_agent_job_steps')"
    ) == []
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_tasks"
    ) == {"count": 1}
    chunks = [
        json.loads(row["chunk_json"])
        for row in await screenplay_db.fetch_all(
            "SELECT chunk_json FROM screenplay_agent_chunks ORDER BY id"
        )
    ]
    assert not any("commentaryDelta" in chunk for chunk in chunks)
    progress = [
        chunk["longTaskProgress"]
        for chunk in chunks
        if "longTaskProgress" in chunk
    ]
    assert progress
    assert progress[-1]["status"] == "completed"
    assert progress[-1]["completedUnits"] == 13
    assert [unit["title"] for unit in progress[-1]["units"]] == [
        "整理第 4 集创作依据",
        "创作第 4 集场景 ep04_s01",
        "整理第 4 集连续性",
        "校验第 4 集完整性",
        "整理第 5 集创作依据",
        "创作第 5 集场景 ep05_s01",
        "整理第 5 集连续性",
        "校验第 5 集完整性",
        "整理第 6 集创作依据",
        "创作第 6 集场景 ep06_s01",
        "整理第 6 集连续性",
        "校验第 6 集完整性",
        "整理最终答复",
    ]
    assert any(
        any(unit["status"] == "claimed" for unit in snapshot["units"])
        for snapshot in progress
    )
    assert all(unit["status"] == "completed" for unit in progress[-1]["units"])

    removed = await service.truncate_from_turn(turn["id"])
    assert removed["deletedTaskIds"] == [task["id"]]
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_tasks"
    ) == {"count": 0}
    assert await screenplay_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_work_items"
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_recoverable_exhaustion_pauses_turn_without_formal_assistant_final(
    screenplay_db,
):
    projects = ScreenplayV2ProjectService(screenplay_db)
    workspace = await projects.create_project(
        command_id="create-paused-screenplay-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Paused screenplay",
            "format": "series",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "意外重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    service = ScreenplayAgentService(
        screenplay_db,
        owner_id="screenplay-paused-test",
        planner=_Planner(ScreenplayIntent(
            action=ScreenplayIntentAction.CREATE,
            instruction="完成接下来三集",
            scope=ScreenplayIntentScope(
                kind=ScreenplayScopeKind.NEXT_EPISODES,
                count=3,
            ),
            requested_deliverable="screenplayDraft",
        )),
        resolver=_Resolver(),
        unit_executor_factory=lambda _runtime: _PausedUnitExecutor(),
        projects=projects,
    )
    request = SubmitScreenplayAgentTurnRequest.model_validate({
        "sessionId": session["id"],
        "content": "连续写完后面三集。",
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "baseURL": "https://provider.example/v1",
            "options": {"model": "fixture-model"},
            "contextWindow": "128k",
        },
    })
    turn = await service.submit_turn(
        command_id="pause-incompatible-protocol",
        project_id=workspace["project"]["id"],
        request=request,
    )

    await service.execute_turn(turn["id"], request.runtime)

    snapshot = await service.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )
    assert snapshot["turns"][0]["status"] == "paused"
    assert snapshot["turns"][0]["assistantContent"] == ""
    assert snapshot["tasks"][0]["status"] == "paused"
    assert snapshot["tasks"][0]["units"][0]["status"] == "blocked"
    events = await service.list_events(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
        after=0,
        limit=100,
    )
    assert any(
        event["type"] == "screenplay.agent.task.paused"
        for event in events["events"]
    )
    assert not any(
        event["type"] == "screenplay.agent.task.failed"
        for event in events["events"]
    )
    chunks = [
        json.loads(row["chunk_json"])
        for row in await screenplay_db.fetch_all(
            "SELECT chunk_json FROM screenplay_agent_chunks ORDER BY id"
        )
    ]
    assert not any(chunk.get("delta") for chunk in chunks)
    assert chunks[-1] == {
        "done": True,
        "finalResponseExpected": False,
    }
