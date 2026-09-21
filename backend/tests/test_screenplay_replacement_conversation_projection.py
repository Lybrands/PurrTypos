from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic import SecretStr

from agents.screenplay.composition import (
    create_isolated_screenplay_replacement_composition,
)
from agents.screenplay.conversation_projection import (
    SCREENPLAY_REPLACEMENT_ROOT_BINDING,
    ScreenplayReplacementConversationStore,
    ScreenplayReplacementRootProjectionError,
    ScreenplayReplacementRunBeginProjector,
    ScreenplayReplacementRunCancellationProjector,
    ScreenplayReplacementRunCommitProjector,
    ScreenplayReplacementTurnLifecycle,
)
from agents.screenplay.conversation_query import (
    ScreenplayReplacementConversationQuery,
)
from agents.screenplay.conversation_service import (
    ScreenplayReplacementConversationService,
)
from agents.shared.implementation import AgentKind, replacement_implementation
from agents.shared.implementation_store import SqliteAgentImplementationStore
from agents.screenplay.entry_service import ScreenplayReplacementExecutionService
from agents.screenplay.ordinary_service import ScreenplayReplacementOrdinaryService
from agents.screenplay.recovery_service import (
    ScreenplayReplacementContinuationLifecycle,
    ScreenplayReplacementRecoveryError,
    ScreenplayReplacementRecoveryService,
)
from application.model_runtime import runtime_from_settings
from database.connection import DatabaseConnection
from agents.screenplay.contracts import ScreenplayStageCommand
from exceptions import AppError
from infrastructure.persistence.run_store import create_run
from purra.contracts import RunBinding, RunCreateParams, RunStatus
from purra.ports import RunCommit
from purra.run_control import RunCancellationReceipt


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_kind, source_book_id, source_scope_json) "
        "VALUES ('project-conversation', '会话投影', 'book', 'book-conversation', ?)",
        [json.dumps({"schemaVersion": 1, "mode": "whole_book"})],
    )
    await db.execute(
        "INSERT INTO ai_sessions (id, title, scope, screenplay_project_id) "
        "VALUES (7, '剧本会话', 'screenplay', 'project-conversation')"
    )
    await db.execute(
        "INSERT INTO books (id, title) VALUES ('book-conversation', '原作')"
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES ('outline-conversation', '正文', 'writing', 'book-conversation')"
    )
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title, sort) "
        "VALUES ('chapter-conversation', 'outline-conversation', '第一章', 1)"
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) "
        "VALUES ('chapter-conversation', '雨夜停电。')"
    )
    try:
        yield db
    finally:
        await db.close()


async def _runtime(db):
    config = {
        "id": "conversation-model",
        "apiProvider": "openai",
        "name": "fixture",
        "apiKey": "fixture-key",
        "baseUrl": "http://example.test/v1",
        "contextWindow": "128k",
        "profileMaxGenerationTokens": 8192,
        "maxGenerationTokens": 4096,
        "supportsThinking": False,
        "thinkingOnly": False,
    }
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ["ai_model_configs", json.dumps([config])],
    )
    persisted = runtime_from_settings(config)
    return SimpleNamespace(
        apiKey=SecretStr(config["apiKey"]),
        modelConfigId=config["id"],
        apiProvider=persisted.apiProvider,
        baseURL=persisted.baseURL,
        contextWindow=persisted.contextWindow,
        options=persisted.options,
        locale="zh-CN",
    )


@pytest.mark.asyncio
async def test_replacement_query_reads_terminal_legacy_turn_without_runtime_profile(
    temp_db,
) -> None:
    await temp_db.execute(
        "INSERT INTO screenplay_agent_turns "
        "(id, project_id, session_id, command_id, status, user_content, "
        "assistant_content, intent_json, runtime_profile_json, implementation_id, "
        "attempt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "turn-legacy-terminal",
            "project-conversation",
            7,
            "command-legacy-terminal",
            "completed",
            "读取旧会话",
            "这是冻结实现留下的历史回复。",
            json.dumps({"kind": "ordinary"}),
            json.dumps({"model": "historical-model"}),
            "legacy-frozen-2026-09-12",
            1,
        ],
    )

    snapshot = await ScreenplayReplacementConversationQuery(
        temp_db,
        output_repository=object(),
    ).snapshot(project_id="project-conversation", session_id=7)

    assert snapshot["tasks"] == []
    assert snapshot["operations"] == []
    assert snapshot["turns"] == [{
        "id": "turn-legacy-terminal",
        "commandId": "command-legacy-terminal",
        "projectId": "project-conversation",
        "sessionId": 7,
        "status": "completed",
        "userContent": "读取旧会话",
        "stageCommand": None,
        "assistantContent": "这是冻结实现留下的历史回复。",
        "runtimeProfile": {"model": "historical-model"},
        "intent": {"kind": "ordinary"},
        "rootRunId": None,
        "taskId": None,
        "cancelReceiptId": None,
        "cancelRequestedAt": None,
        "error": None,
        "createdAt": snapshot["turns"][0]["createdAt"],
        "updatedAt": snapshot["turns"][0]["updatedAt"],
    }]


def _command():
    return ScreenplayStageCommand.from_mapping({
        "kind": "stage_action",
        "action": "create",
        "targetRole": "sourceAnalysis",
        "scope": {"kind": "current_stage"},
    })


async def _admit(db):
    runtime = await _runtime(db)
    composition = create_isolated_screenplay_replacement_composition(db)
    entry = ScreenplayReplacementExecutionService(db, composition)
    request = await entry.build_request(
        project_id="project-conversation",
        session_id=7,
        turn_id="turn-conversation",
        command_id="command-conversation",
        prompt="分析原作",
        stage_command=_command(),
        runtime=runtime,
    )
    store = ScreenplayReplacementConversationStore(db, owner_id="owner-1")
    turn = await store.admit(
        request=request,
        command_id="command-conversation",
        user_content="分析原作",
        stage_command=_command().to_mapping(),
    )
    return composition, store, turn, request


async def _admit_ordinary(db):
    runtime = await _runtime(db)
    composition = create_isolated_screenplay_replacement_composition(db)
    request = await ScreenplayReplacementOrdinaryService(
        db, composition
    ).build_request(
        project_id="project-conversation",
        session_id=7,
        turn_id="turn-ordinary",
        command_id="command-ordinary",
        prompt="当前项目是什么状态？",
        runtime=runtime,
    )
    store = ScreenplayReplacementConversationStore(
        db, owner_id="owner-ordinary"
    )
    turn = await store.admit_ordinary(
        request=request,
        command_id="command-ordinary",
        user_content="当前项目是什么状态？",
    )
    return composition, store, turn, request


@pytest.mark.asyncio
async def test_replacement_turn_admission_is_idempotent_and_secret_free(temp_db) -> None:
    composition, store, turn, request = await _admit(temp_db)
    try:
        replay = await store.admit(
            request=request,
            command_id="command-conversation",
            user_content="分析原作",
            stage_command=_command().to_mapping(),
        )
    finally:
        await composition.shutdown()

    assert replay["id"] == turn["id"]
    assert turn["status"] == "queued"
    operation = await temp_db.fetch_one(
        "SELECT status, target_role, requirements_json "
        "FROM screenplay_agent_operations WHERE id = ?",
        [turn["operationId"]],
    )
    assert operation["status"] == "queued"
    assert operation["target_role"] == "sourceAnalysis"
    assert "fixture-key" not in operation["requirements_json"]
    assert await temp_db.fetch_one(
        "SELECT implementation_id FROM screenplay_agent_turns WHERE id = ?",
        [turn["id"]],
    ) == {"implementation_id": "purra-native"}


@pytest.mark.asyncio
async def test_duplicate_initial_dispatch_cannot_fail_the_winning_claim(
    temp_db,
) -> None:
    composition, store, turn, _request = await _admit(temp_db)
    winner = ScreenplayReplacementTurnLifecycle(
        temp_db, store, turn_id=turn["id"]
    )
    loser = ScreenplayReplacementTurnLifecycle(
        temp_db, store, turn_id=turn["id"]
    )
    try:
        await winner.validate()
        await loser.validate()
        await winner.before_submit()
        with pytest.raises(ValueError, match="claim was lost"):
            await loser.before_submit()
        await loser.on_start_failed("duplicate_dispatch")
        persisted = await temp_db.fetch_one(
            "SELECT status, execution_owner_id FROM screenplay_agent_turns "
            "WHERE id = ?",
            [turn["id"]],
        )
    finally:
        await composition.shutdown()

    assert persisted["status"] == "planning"
    assert persisted["execution_owner_id"] == (
        winner.run_binding_attributes()["turnExecutionOwner"]
    )


@pytest.mark.asyncio
async def test_queued_turn_cancel_is_atomic_idempotent_and_terminal(temp_db) -> None:
    composition, store, turn, _request = await _admit(temp_db)
    try:
        receipt = await store.request_cancel(
            turn["id"], command_id="cancel-conversation"
        )
        replay = await store.request_cancel(
            turn["id"], command_id="cancel-conversation"
        )
    finally:
        await composition.shutdown()

    assert receipt == replay
    assert receipt["status"] == "canceled"
    assert receipt["operationStatus"] == "canceled"
    assert receipt["rootRunId"] is None
    assert receipt["receiptId"].startswith("spacancel_")


@pytest.mark.asyncio
async def test_cancel_does_not_relabel_completed_turn(temp_db) -> None:
    composition, store, turn, _request = await _admit(temp_db)
    await temp_db.execute(
        "UPDATE screenplay_agent_turns SET status = 'completed' WHERE id = ?",
        [turn["id"]],
    )
    await temp_db.execute(
        "UPDATE screenplay_agent_operations SET status = 'succeeded' WHERE id = ?",
        [turn["operationId"]],
    )
    try:
        receipt = await store.request_cancel(
            turn["id"], command_id="cancel-completed"
        )
    finally:
        await composition.shutdown()

    persisted = await temp_db.fetch_one(
        "SELECT status, cancel_requested_at_ms, cancel_receipt_id "
        "FROM screenplay_agent_turns WHERE id = ?",
        [turn["id"]],
    )
    assert receipt["status"] == "completed"
    assert persisted == {
        "status": "completed",
        "cancel_requested_at_ms": None,
        "cancel_receipt_id": None,
    }


@pytest.mark.asyncio
async def test_truncate_cancels_queued_tail_before_deleting_it(temp_db) -> None:
    composition, _store, turn, _request = await _admit(temp_db)
    try:
        service = ScreenplayReplacementConversationService(
            temp_db, composition, owner_id="truncate-owner"
        )
        result = await service.truncate_from_turn(turn["id"])
    finally:
        await composition.shutdown()

    assert result["deletedTurnIds"] == [turn["id"]]
    assert result["deletedOperationIds"] == [turn["operationId"]]
    assert await temp_db.fetch_one(
        "SELECT id FROM screenplay_agent_turns WHERE id = ?", [turn["id"]]
    ) is None
    assert await temp_db.fetch_one(
        "SELECT command_id FROM screenplay_agent_cancel_commands "
        "WHERE turn_id = ?", [turn["id"]]
    ) is None


@pytest.mark.asyncio
async def test_truncate_keeps_rows_while_root_has_foreign_live_lease(temp_db) -> None:
    composition, store, turn, _request = await _admit(temp_db)
    lifecycle = ScreenplayReplacementTurnLifecycle(
        temp_db, store, turn_id=turn["id"]
    )
    await lifecycle.validate()
    binding = RunBinding(
        namespace=SCREENPLAY_REPLACEMENT_ROOT_BINDING,
        aggregate_id="project-conversation",
        command_id="command-conversation",
        attributes={
            **lifecycle.run_binding_attributes(),
            "agentProfile": "screenplay.purra-native.v1",
            "domainNamespace": "purrtypos.screenplay",
        },
    )
    try:
        await lifecycle.before_submit()
        await create_run(
            temp_db, run_id="run-truncate-live", session_id=7,
            prompt="分析原作", mode="screenplay", binding=binding,
        )
        await SqliteAgentImplementationStore(temp_db).bind(
            "run-truncate-live",
            replacement_implementation(AgentKind.SCREENPLAY),
        )
        async with temp_db.transaction(cancellation_linearizable=True):
            await ScreenplayReplacementRunBeginProjector(temp_db).project(
                "run-truncate-live",
                RunCreateParams(
                    session_id=7, prompt="分析原作", mode="screenplay",
                    turn_id=turn["id"], binding=binding,
                ),
            )
        await temp_db.execute(
            "UPDATE ai_agent_runs SET execution_owner_id = 'foreign-owner', "
            "lease_expires_at_ms = 9999999999999 WHERE id = 'run-truncate-live'"
        )
        service = ScreenplayReplacementConversationService(
            temp_db, composition, owner_id="truncate-owner"
        )
        with pytest.raises(AppError, match="still active") as captured:
            await service.truncate_from_turn(turn["id"])
    finally:
        await composition.shutdown()

    assert captured.value.status_code == 409
    persisted = await temp_db.fetch_one(
        "SELECT status, cancel_requested_at_ms FROM screenplay_agent_turns "
        "WHERE id = ?", [turn["id"]]
    )
    assert persisted["status"] == "running"
    assert persisted["cancel_requested_at_ms"] is not None


@pytest.mark.asyncio
async def test_truncate_fences_and_detaches_superseded_root_events(temp_db) -> None:
    composition, _store, turn, _request = await _admit(temp_db)
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, prompt, root_run_id) "
        "VALUES ('run-truncate-source', 7, 'canceled', '分析原作', "
        "'run-truncate-source')"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, prompt, root_run_id) "
        "VALUES ('run-truncate-continuation', 7, 'done', '继续', "
        "'run-truncate-continuation')"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, turn_id, root_run_id) VALUES "
        "('run-truncate-source', 'run.lifecycle', ?, 'run-truncate-source')",
        [turn["id"]],
    )
    await temp_db.execute(
        "UPDATE screenplay_agent_turns SET status = 'completed', "
        "planner_run_id = 'run-truncate-continuation' WHERE id = ?",
        [turn["id"]],
    )
    await temp_db.execute(
        "UPDATE screenplay_agent_operations SET status = 'succeeded' WHERE id = ?",
        [turn["operationId"]],
    )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET execution_owner_id = 'foreign-owner', "
        "lease_expires_at_ms = 9999999999999 "
        "WHERE id = 'run-truncate-source'"
    )
    service = ScreenplayReplacementConversationService(
        temp_db, composition, owner_id="truncate-owner"
    )
    try:
        with pytest.raises(AppError, match="still active") as captured:
            await service.truncate_from_turn(turn["id"])
        await temp_db.execute(
            "UPDATE ai_agent_runs SET execution_owner_id = NULL, "
            "lease_expires_at_ms = NULL WHERE id = 'run-truncate-source'"
        )
        await service.truncate_from_turn(turn["id"])
        replay = await ScreenplayReplacementConversationQuery(
            temp_db,
            output_repository=composition.output_journal,
        ).list_chunks(
            project_id="project-conversation", session_id=7, after=0, limit=500
        )
    finally:
        await composition.shutdown()

    assert captured.value.status_code == 409
    assert await temp_db.fetch_one(
        "SELECT id FROM screenplay_agent_turns WHERE id = ?", [turn["id"]]
    ) is None
    assert await temp_db.fetch_all(
        "SELECT id, session_id FROM ai_agent_runs WHERE id IN "
        "('run-truncate-source', 'run-truncate-continuation') ORDER BY id"
    ) == [
        {"id": "run-truncate-continuation", "session_id": None},
        {"id": "run-truncate-source", "session_id": None},
    ]
    assert all(chunk["turnId"] != turn["id"] for chunk in replay["chunks"])


@pytest.mark.asyncio
async def test_truncate_preserves_accepted_revision_and_detaches_deleted_task(
    temp_db,
) -> None:
    composition, _store, turn, _request = await _admit(temp_db)
    deliverable = await temp_db.fetch_one(
        "SELECT id FROM screenplay_deliverables WHERE project_id = ? "
        "AND role = 'sourceAnalysis'",
        ["project-conversation"],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, total_units) "
        "VALUES ('task-truncate-accepted', 'purrtypos.screenplay', "
        "'screenplay.purra-native', 'project-conversation', "
        "'audit-root', 'completed', 1)"
    )
    await temp_db.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, "
        "created_by, agent_task_id) VALUES ('revision-truncate-accepted', ?, ?, "
        "1, 'sha256:accepted', 'agent', 'task-truncate-accepted')",
        ["project-conversation", deliverable["id"]],
    )
    await temp_db.execute(
        "INSERT INTO screenplay_project_heads "
        "(project_id, deliverable_id, revision_id) VALUES (?, ?, ?)",
        [
            "project-conversation", deliverable["id"],
            "revision-truncate-accepted",
        ],
    )
    await temp_db.execute(
        "UPDATE screenplay_agent_turns SET status = 'completed', "
        "task_id = 'task-truncate-accepted', "
        "result_revision_id = 'revision-truncate-accepted' WHERE id = ?",
        [turn["id"]],
    )
    await temp_db.execute(
        "UPDATE screenplay_agent_operations SET status = 'succeeded', "
        "long_task_id = 'task-truncate-accepted', "
        "result_revision_id = 'revision-truncate-accepted' WHERE id = ?",
        [turn["operationId"]],
    )
    try:
        service = ScreenplayReplacementConversationService(
            temp_db, composition, owner_id="truncate-owner"
        )
        result = await service.truncate_from_turn(turn["id"])
    finally:
        await composition.shutdown()

    assert result["deletedTaskIds"] == ["task-truncate-accepted"]
    assert await temp_db.fetch_one(
        "SELECT id FROM ai_agent_long_tasks WHERE id = 'task-truncate-accepted'"
    ) is None
    assert await temp_db.fetch_one(
        "SELECT agent_task_id FROM screenplay_revisions "
        "WHERE id = 'revision-truncate-accepted'"
    ) == {"agent_task_id": None}
    assert await temp_db.fetch_one(
        "SELECT revision_id FROM screenplay_project_heads WHERE project_id = ? "
        "AND deliverable_id = ?",
        ["project-conversation", deliverable["id"]],
    ) == {"revision_id": "revision-truncate-accepted"}


@pytest.mark.asyncio
async def test_root_cancellation_projector_fences_turn_operation_and_task(
    temp_db,
) -> None:
    composition, store, turn, _request = await _admit(temp_db)
    lifecycle = ScreenplayReplacementTurnLifecycle(
        temp_db, store, turn_id=turn["id"]
    )
    await lifecycle.validate()
    attributes = {
        **lifecycle.run_binding_attributes(),
        "agentProfile": "screenplay.purra-native.v1",
        "domainNamespace": "purrtypos.screenplay",
    }
    binding = RunBinding(
        namespace=SCREENPLAY_REPLACEMENT_ROOT_BINDING,
        aggregate_id="project-conversation",
        command_id="command-conversation",
        attributes=attributes,
    )
    try:
        await lifecycle.before_submit()
        await create_run(
            temp_db, run_id="run-conversation", session_id=7,
            prompt="分析原作", mode="screenplay", binding=binding,
        )
        async with temp_db.transaction(cancellation_linearizable=True):
            await ScreenplayReplacementRunBeginProjector(temp_db).project(
                "run-conversation",
                RunCreateParams(
                    session_id=7, prompt="分析原作", mode="screenplay",
                    turn_id=turn["id"], binding=binding,
                ),
            )
        await temp_db.execute(
            "INSERT INTO ai_agent_long_tasks "
            "(id, namespace, kind, owner_id, created_by_run_id, status, "
            "total_units) VALUES ('task-conversation', 'purrtypos.screenplay', "
            "'screenplay.purra-native', 'project-conversation', "
            "'run-conversation', 'running', 1)"
        )
        async with temp_db.transaction(cancellation_linearizable=True):
            await ScreenplayReplacementRunCancellationProjector(temp_db).project(
                "run-conversation",
                RunCancellationReceipt(
                    run_id="run-conversation",
                    status=RunStatus.RUNNING,
                    cancellation_epoch=1,
                    newly_requested=True,
                    draining=True,
                ),
            )
    finally:
        await composition.shutdown()

    projected = await store.cancel_view(turn["id"])
    task = await temp_db.fetch_one(
        "SELECT status, cancel_requested_at_ms FROM ai_agent_long_tasks "
        "WHERE id = 'task-conversation'"
    )
    assert projected["status"] == "running"
    assert projected["operationStatus"] == "running"
    assert projected["receiptId"] == "run-cancel:run-conversation:1"
    assert projected["cancelRequestedAtMs"] is not None
    assert task["status"] == "running"
    assert task["cancel_requested_at_ms"] is not None


@pytest.mark.asyncio
async def test_conversation_cancel_terminalizes_unowned_root_through_purra(
    temp_db,
) -> None:
    composition, store, turn, _request = await _admit(temp_db)
    lifecycle = ScreenplayReplacementTurnLifecycle(
        temp_db, store, turn_id=turn["id"]
    )
    await lifecycle.validate()
    attributes = {
        **lifecycle.run_binding_attributes(),
        "agentProfile": "screenplay.purra-native.v1",
        "domainNamespace": "purrtypos.screenplay",
    }
    binding = RunBinding(
        namespace=SCREENPLAY_REPLACEMENT_ROOT_BINDING,
        aggregate_id="project-conversation",
        command_id="command-conversation",
        attributes=attributes,
    )
    try:
        await lifecycle.before_submit()
        await create_run(
            temp_db, run_id="run-conversation", session_id=7,
            prompt="分析原作", mode="screenplay", binding=binding,
        )
        await SqliteAgentImplementationStore(temp_db).bind(
            "run-conversation",
            replacement_implementation(AgentKind.SCREENPLAY),
        )
        async with temp_db.transaction(cancellation_linearizable=True):
            await ScreenplayReplacementRunBeginProjector(temp_db).project(
                "run-conversation",
                RunCreateParams(
                    session_id=7, prompt="分析原作", mode="screenplay",
                    turn_id=turn["id"], binding=binding,
                ),
            )
        service = ScreenplayReplacementConversationService(
            temp_db,
            composition,
            owner_id="owner-1",
        )
        receipt = await service.cancel_turn(
            turn["id"], command_id="cancel-conversation"
        )
        replay = await service.cancel_turn(
            turn["id"], command_id="cancel-conversation"
        )
    finally:
        await composition.shutdown()

    run = await temp_db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = 'run-conversation'"
    )
    assert receipt["status"] == "canceled"
    assert receipt["operationStatus"] == "canceled"
    assert receipt["runCancellation"]["status"] == "canceled"
    assert replay["status"] == "canceled"
    assert run == {"status": "canceled"}


@pytest.mark.asyncio
async def test_ordinary_conversation_cancel_terminalizes_without_operation(
    temp_db,
) -> None:
    composition, store, turn, _request = await _admit_ordinary(temp_db)
    lifecycle = ScreenplayReplacementTurnLifecycle(
        temp_db, store, turn_id=turn["id"]
    )
    await lifecycle.validate()
    binding = RunBinding(
        namespace=SCREENPLAY_REPLACEMENT_ROOT_BINDING,
        aggregate_id="project-conversation",
        command_id="command-ordinary",
        attributes={
            **lifecycle.run_binding_attributes(),
            "interactionKind": "ordinary",
            "agentProfile": "screenplay.purra-native.v1",
            "domainNamespace": "purrtypos.screenplay",
        },
    )
    try:
        await lifecycle.before_submit()
        await create_run(
            temp_db, run_id="run-ordinary-cancel", session_id=7,
            prompt="当前项目是什么状态？", mode="screenplay_ordinary",
            binding=binding,
        )
        await SqliteAgentImplementationStore(temp_db).bind(
            "run-ordinary-cancel",
            replacement_implementation(AgentKind.SCREENPLAY),
        )
        async with temp_db.transaction(cancellation_linearizable=True):
            await ScreenplayReplacementRunBeginProjector(temp_db).project(
                "run-ordinary-cancel",
                RunCreateParams(
                    session_id=7, prompt="当前项目是什么状态？",
                    mode="screenplay_ordinary", turn_id=turn["id"],
                    binding=binding,
                ),
            )
        service = ScreenplayReplacementConversationService(
            temp_db, composition, owner_id="ordinary-cancel-owner"
        )
        receipt = await service.cancel_turn(
            turn["id"], command_id="cancel-ordinary-running"
        )
    finally:
        await composition.shutdown()

    assert receipt["status"] == "canceled"
    assert receipt["operationId"] is None
    assert receipt["runCancellation"]["terminalized"] is True


@pytest.mark.asyncio
async def test_continuation_begin_rebinds_paused_turn_to_new_root(temp_db) -> None:
    composition, store, turn, _request = await _admit(temp_db)
    lifecycle = ScreenplayReplacementTurnLifecycle(
        temp_db, store, turn_id=turn["id"]
    )
    await lifecycle.validate()
    initial_attributes = {
        **lifecycle.run_binding_attributes(),
        "agentProfile": "screenplay.purra-native.v1",
        "domainNamespace": "purrtypos.screenplay",
    }
    initial_binding = RunBinding(
        namespace=SCREENPLAY_REPLACEMENT_ROOT_BINDING,
        aggregate_id="project-conversation",
        command_id="command-conversation",
        attributes=initial_attributes,
    )
    try:
        await lifecycle.before_submit()
        await create_run(
            temp_db, run_id="run-source", session_id=7,
            prompt="分析原作", mode="screenplay", binding=initial_binding,
        )
        async with temp_db.transaction(cancellation_linearizable=True):
            await ScreenplayReplacementRunBeginProjector(temp_db).project(
                "run-source",
                RunCreateParams(
                    session_id=7, prompt="分析原作", mode="screenplay",
                    turn_id=turn["id"], binding=initial_binding,
                ),
            )
        await temp_db.execute(
            "UPDATE ai_agent_runs SET status = 'canceled' WHERE id = 'run-source'"
        )
        await temp_db.execute(
            "INSERT INTO ai_agent_long_tasks "
            "(id, namespace, kind, owner_id, created_by_run_id, status, "
            "total_units) VALUES ('task-resume', 'purrtypos.screenplay', "
            "'screenplay.purra-native', 'project-conversation', "
            "'run-source', 'paused', 1)"
        )
        await temp_db.execute(
            "INSERT INTO ai_agent_long_task_runs (task_id, run_id, relation) "
            "VALUES ('task-resume', 'run-source', 'created')"
        )
        await temp_db.execute(
            "UPDATE screenplay_agent_turns SET status = 'paused', "
            "task_id = 'task-resume' WHERE id = ?",
            [turn["id"]],
        )
        await temp_db.execute(
            "UPDATE screenplay_agent_operations SET status = 'paused', "
            "long_task_id = 'task-resume', revision = revision + 1 WHERE id = ?",
            [turn["operationId"]],
        )
        operation = await temp_db.fetch_one(
            "SELECT revision, requirements_json FROM screenplay_agent_operations "
            "WHERE id = ?",
            [turn["operationId"]],
        )
        requirements = json.loads(operation["requirements_json"])
        reservation = await store.reserve_resume(
            operation_id=turn["operationId"],
            command_id="resume-conversation",
            expected_operation_revision=operation["revision"],
            runtime_binding=requirements["runtimeBinding"],
            turn_id=turn["id"],
            source_run_id="run-source",
            project_id="project-conversation",
            session_id=7,
        )
        assert reservation["dispatchRequired"] is True
        replay = await store.reserve_resume(
            operation_id=turn["operationId"],
            command_id="resume-conversation",
            expected_operation_revision=operation["revision"],
            runtime_binding=requirements["runtimeBinding"],
            turn_id=turn["id"],
            source_run_id="run-source",
            project_id="project-conversation",
            session_id=7,
        )
        assert replay["dispatchRequired"] is False
        await temp_db.execute(
            "UPDATE screenplay_agent_operation_commands SET "
            "continuation_lease_expires_at_ms = 1 WHERE command_id = ?",
            ["resume-conversation"],
        )
        reclaimed = await store.reserve_resume(
            operation_id=turn["operationId"],
            command_id="resume-conversation",
            expected_operation_revision=operation["revision"],
            runtime_binding=requirements["runtimeBinding"],
            turn_id=turn["id"],
            source_run_id="run-source",
            project_id="project-conversation",
            session_id=7,
        )
        assert reclaimed["dispatchRequired"] is True
        reserved = await store.load_resume_reservation("resume-conversation")
        assert reserved is not None
        assert reserved["continuation_epoch"] == 2
        continuation = ScreenplayReplacementContinuationLifecycle(
            temp_db,
            composition.long_task_repository,
            turn_id=turn["id"],
            operation_id=turn["operationId"],
            task_id="task-resume",
            source_run_id="run-source",
            expected_operation_revision=operation["revision"],
            owner_id="owner-1",
            continuation_epoch=reserved["continuation_epoch"],
            continuation_identity_digest=reserved["continuation_identity_digest"],
        )
        competing = ScreenplayReplacementContinuationLifecycle(
            temp_db,
            composition.long_task_repository,
            turn_id=turn["id"],
            operation_id=turn["operationId"],
            task_id="task-resume",
            source_run_id="run-source",
            expected_operation_revision=operation["revision"],
            owner_id="owner-2",
            continuation_epoch=reserved["continuation_epoch"],
            continuation_identity_digest=reserved["continuation_identity_digest"],
        )
        await continuation.validate()
        await competing.validate()
        continuation_attributes = {
            **continuation.run_binding_attributes(),
            "agentProfile": "screenplay.purra-native.v1",
            "domainNamespace": "purrtypos.screenplay",
        }
        continuation_binding = RunBinding(
            namespace=SCREENPLAY_REPLACEMENT_ROOT_BINDING,
            aggregate_id="project-conversation",
            command_id="resume-conversation",
            attributes=continuation_attributes,
        )
        await continuation.before_submit()
        with pytest.raises(ScreenplayReplacementRecoveryError, match="claim was lost"):
            await competing.before_submit()
        await competing.on_start_failed("competing_resume_lost")
        claimed = await temp_db.fetch_one(
            "SELECT status, execution_owner_id FROM screenplay_agent_turns "
            "WHERE id = ?",
            [turn["id"]],
        )
        task = await composition.long_task_repository.load("task-resume")
        assert claimed == {
            "status": "planning",
            "execution_owner_id": "owner-1",
        }
        assert task.status.value == "running"
        await create_run(
            temp_db, run_id="run-continuation", session_id=7,
            prompt="继续", mode="screenplay", binding=continuation_binding,
        )
        async with temp_db.transaction(cancellation_linearizable=True):
            await ScreenplayReplacementRunBeginProjector(temp_db).project(
                "run-continuation",
                RunCreateParams(
                    session_id=7, prompt="继续", mode="screenplay",
                    turn_id=turn["id"], binding=continuation_binding,
                ),
            )
        await continuation.on_run_started("run-continuation")
    finally:
        await composition.shutdown()

    projected = await store.load_turn(turn["id"])
    operation = await temp_db.fetch_one(
        "SELECT status, long_task_id FROM screenplay_agent_operations WHERE id = ?",
        [turn["operationId"]],
    )
    assert projected["status"] == "running"
    assert projected["rootRunId"] == "run-continuation"
    assert projected["attempt"] == 2
    assert operation == {"status": "running", "long_task_id": "task-resume"}


@pytest.mark.asyncio
async def test_restart_recovers_expired_pre_run_claim(temp_db) -> None:
    composition, store, turn, _request = await _admit(temp_db)
    lifecycle = ScreenplayReplacementTurnLifecycle(
        temp_db, store, turn_id=turn["id"]
    )
    await lifecycle.validate()
    try:
        await lifecycle.before_submit()
        await temp_db.execute(
            "UPDATE screenplay_agent_turns SET lease_expires_at_ms = 1 WHERE id = ?",
            [turn["id"]],
        )
        recovery = ScreenplayReplacementRecoveryService(
            temp_db,
            composition,
            entry_service=ScreenplayReplacementExecutionService(
                temp_db, composition
            ),
        )
        recovered = await recovery.recover_stale_admissions(timestamp_ms=2)
    finally:
        await composition.shutdown()

    assert recovered == (turn["id"],)
    assert (await store.load_turn(turn["id"]))["status"] == "queued"


@pytest.mark.asyncio
async def test_replacement_recovery_ignores_legacy_turns(temp_db) -> None:
    composition, store, turn, _request = await _admit(temp_db)
    lifecycle = ScreenplayReplacementTurnLifecycle(
        temp_db, store, turn_id=turn["id"]
    )
    await lifecycle.validate()
    try:
        await lifecycle.before_submit()
        await temp_db.execute(
            "UPDATE screenplay_agent_turns SET implementation_id = ?, "
            "lease_expires_at_ms = 1 WHERE id = ?",
            ["legacy-frozen-2026-09-12", turn["id"]],
        )
        recovery = ScreenplayReplacementRecoveryService(
            temp_db,
            composition,
            entry_service=ScreenplayReplacementExecutionService(
                temp_db, composition
            ),
        )
        recovered = await recovery.recover_stale_admissions(timestamp_ms=2)
    finally:
        await composition.shutdown()

    assert recovered == ()
    assert (await store.load_turn(turn["id"]))["status"] == "planning"


@pytest.mark.asyncio
async def test_restart_repauses_interrupted_continuation_claim(temp_db) -> None:
    composition, store, turn, _request = await _admit(temp_db)
    await create_run(
        temp_db,
        run_id="run-stale-source",
        session_id=7,
        prompt="分析原作",
        mode="screenplay",
    )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET status = 'canceled' "
        "WHERE id = 'run-stale-source'"
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, total_units) "
        "VALUES ('task-stale-resume', 'purrtypos.screenplay', "
        "'screenplay.purra-native', 'project-conversation', "
        "'run-stale-source', 'paused', 1)"
    )
    await temp_db.execute(
        "UPDATE screenplay_agent_turns SET status = 'paused', attempt = 1, "
        "planner_run_id = 'run-stale-source', task_id = 'task-stale-resume' "
        "WHERE id = ?",
        [turn["id"]],
    )
    await temp_db.execute(
        "UPDATE screenplay_agent_operations SET status = 'paused', "
        "long_task_id = 'task-stale-resume', revision = 2 WHERE id = ?",
        [turn["operationId"]],
    )
    lifecycle = ScreenplayReplacementContinuationLifecycle(
        temp_db,
        composition.long_task_repository,
        turn_id=turn["id"],
        operation_id=turn["operationId"],
        task_id="task-stale-resume",
        source_run_id="run-stale-source",
        expected_operation_revision=2,
        owner_id="stale-resume-owner",
        continuation_epoch=1,
        continuation_identity_digest="stale-resume-test",
    )
    try:
        await lifecycle.validate()
        await lifecycle.before_submit()
        await temp_db.execute(
            "UPDATE screenplay_agent_turns SET lease_expires_at_ms = 1 WHERE id = ?",
            [turn["id"]],
        )
        recovery = ScreenplayReplacementRecoveryService(
            temp_db,
            composition,
            entry_service=ScreenplayReplacementExecutionService(
                temp_db, composition
            ),
        )
        recovered = await recovery.recover_stale_admissions(timestamp_ms=2)
        task = await composition.long_task_repository.load("task-stale-resume")
    finally:
        await composition.shutdown()

    assert recovered == (turn["id"],)
    assert (await store.load_turn(turn["id"]))["status"] == "paused"
    assert task.status.value == "paused"


@pytest.mark.asyncio
async def test_begin_and_terminal_projectors_attach_task_and_revision_atomically(
    temp_db,
) -> None:
    composition, store, turn, _request = await _admit(temp_db)
    lifecycle = ScreenplayReplacementTurnLifecycle(
        temp_db, store, turn_id=turn["id"]
    )
    await lifecycle.validate()
    attributes = {
        **lifecycle.run_binding_attributes(),
        "agentProfile": "screenplay.purra-native.v1",
        "domainNamespace": "purrtypos.screenplay",
    }
    binding = RunBinding(
        namespace=SCREENPLAY_REPLACEMENT_ROOT_BINDING,
        aggregate_id="project-conversation",
        command_id="command-conversation",
        attributes=attributes,
    )
    params = RunCreateParams(
        session_id=7,
        prompt="分析原作",
        mode="screenplay",
        turn_id=turn["id"],
        binding=binding,
    )
    try:
        await lifecycle.before_submit()
        await create_run(
            temp_db,
            run_id="run-conversation",
            session_id=7,
            prompt="分析原作",
            mode="screenplay",
            binding=binding,
        )
        async with temp_db.transaction(cancellation_linearizable=True):
            await ScreenplayReplacementRunBeginProjector(temp_db).project(
                "run-conversation", params
            )
        await lifecycle.on_run_started("run-conversation")
        await _seed_completed_result(temp_db, turn)
        query = ScreenplayReplacementConversationQuery(
            temp_db,
            output_repository=composition.output_journal,
        )
        running_snapshot = await query.snapshot(
            project_id="project-conversation", session_id=7
        )
        async with temp_db.transaction(cancellation_linearizable=True):
            await ScreenplayReplacementRunCommitProjector(temp_db).project(
                "run-conversation",
                RunCommit(
                    terminal_status=RunStatus.DONE,
                    final_response="screenplay-host-result-v1://final",
                    validated_result="screenplay-host-result-v1://final",
                ),
            )
        await lifecycle.on_run_finished(SimpleNamespace(status=RunStatus.DONE))
        snapshot = await query.snapshot(
            project_id="project-conversation", session_id=7
        )
    finally:
        await composition.shutdown()

    projected = await store.load_turn(turn["id"])
    operation = await temp_db.fetch_one(
        "SELECT status, long_task_id, result_revision_id "
        "FROM screenplay_agent_operations WHERE id = ?",
        [turn["operationId"]],
    )
    assert projected["status"] == "completed"
    assert projected["rootRunId"] == "run-conversation"
    assert projected["taskId"] == "task-conversation"
    assert projected["resultRevisionId"] == "revision-conversation"
    assert projected["assistantContent"] == (
        "原作分析候选版本已生成，等待你审阅和接受。"
    )
    assert running_snapshot["tasks"][0]["id"] == "task-conversation"
    assert running_snapshot["tasks"][0]["status"] == "running"
    assert running_snapshot["operations"][0]["taskId"] == "task-conversation"
    assert operation == {
        "status": "succeeded",
        "long_task_id": "task-conversation",
        "result_revision_id": "revision-conversation",
    }
    assert snapshot["turns"][0]["status"] == "completed"
    assert snapshot["tasks"][0]["resultRevision"]["status"] == "candidate"
    assert snapshot["operations"][0]["finalizationReceiptId"] == (
        "scope-conversation"
    )
    assert snapshot["operations"][0]["resultRevision"]["id"] == (
        "revision-conversation"
    )


@pytest.mark.asyncio
async def test_missing_revision_rolls_back_terminal_product_projection(temp_db) -> None:
    composition, store, turn, _request = await _admit(temp_db)
    lifecycle = ScreenplayReplacementTurnLifecycle(
        temp_db, store, turn_id=turn["id"]
    )
    await lifecycle.validate()
    attributes = {
        **lifecycle.run_binding_attributes(),
        "agentProfile": "screenplay.purra-native.v1",
        "domainNamespace": "purrtypos.screenplay",
    }
    binding = RunBinding(
        namespace=SCREENPLAY_REPLACEMENT_ROOT_BINDING,
        aggregate_id="project-conversation",
        command_id="command-conversation",
        attributes=attributes,
    )
    try:
        await lifecycle.before_submit()
        await create_run(
            temp_db, run_id="run-conversation", session_id=7,
            prompt="分析原作", mode="screenplay", binding=binding,
        )
        params = RunCreateParams(
            session_id=7, prompt="分析原作", mode="screenplay",
            turn_id=turn["id"], binding=binding,
        )
        async with temp_db.transaction(cancellation_linearizable=True):
            await ScreenplayReplacementRunBeginProjector(temp_db).project(
                "run-conversation", params
            )
        await temp_db.execute(
            "INSERT INTO ai_agent_long_tasks "
            "(id, namespace, kind, owner_id, created_by_run_id, status, "
            "total_units, completed_units) VALUES "
            "('task-conversation', 'purrtypos.screenplay', 'screenplay.purra-native', "
            "'project-conversation', 'run-conversation', 'completed', 1, 1)"
        )
        with pytest.raises(ScreenplayReplacementRootProjectionError):
            async with temp_db.transaction(cancellation_linearizable=True):
                await ScreenplayReplacementRunCommitProjector(temp_db).project(
                    "run-conversation",
                    RunCommit(terminal_status=RunStatus.DONE),
                )
    finally:
        await composition.shutdown()

    assert (await store.load_turn(turn["id"]))["status"] == "running"
    assert await temp_db.fetch_one(
        "SELECT status FROM screenplay_agent_operations WHERE id = ?",
        [turn["operationId"]],
    ) == {"status": "running"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("run_status", "explicit_cancel", "task_status", "expected"),
    (
        (RunStatus.FAILED, False, None, "failed"),
        (RunStatus.CANCELED, False, None, "failed"),
        (RunStatus.CANCELED, True, None, "canceled"),
        (RunStatus.CANCELED, False, "paused", "paused"),
    ),
)
async def test_terminal_projector_preserves_failure_cancel_and_pause_semantics(
    temp_db,
    run_status,
    explicit_cancel,
    task_status,
    expected,
) -> None:
    composition, store, turn, _request = await _admit(temp_db)
    lifecycle = ScreenplayReplacementTurnLifecycle(
        temp_db, store, turn_id=turn["id"]
    )
    await lifecycle.validate()
    attributes = {
        **lifecycle.run_binding_attributes(),
        "agentProfile": "screenplay.purra-native.v1",
        "domainNamespace": "purrtypos.screenplay",
    }
    binding = RunBinding(
        namespace=SCREENPLAY_REPLACEMENT_ROOT_BINDING,
        aggregate_id="project-conversation",
        command_id="command-conversation",
        attributes=attributes,
    )
    try:
        await lifecycle.before_submit()
        await create_run(
            temp_db, run_id="run-conversation", session_id=7,
            prompt="分析原作", mode="screenplay", binding=binding,
        )
        async with temp_db.transaction(cancellation_linearizable=True):
            await ScreenplayReplacementRunBeginProjector(temp_db).project(
                "run-conversation",
                RunCreateParams(
                    session_id=7, prompt="分析原作", mode="screenplay",
                    turn_id=turn["id"], binding=binding,
                ),
            )
        if explicit_cancel:
            await temp_db.execute(
                "UPDATE screenplay_agent_turns SET cancel_requested_at_ms = 1 "
                "WHERE id = ?",
                [turn["id"]],
            )
        if task_status is not None:
            await temp_db.execute(
                "INSERT INTO ai_agent_long_tasks "
                "(id, namespace, kind, owner_id, created_by_run_id, status, "
                "total_units) VALUES ('task-conversation', 'purrtypos.screenplay', "
                "'screenplay.purra-native', 'project-conversation', "
                "'run-conversation', ?, 1)",
                [task_status],
            )
        async with temp_db.transaction(cancellation_linearizable=True):
            await ScreenplayReplacementRunCommitProjector(temp_db).project(
                "run-conversation",
                RunCommit(
                    terminal_status=run_status,
                    error=(
                        "fixture terminal"
                        if run_status is RunStatus.FAILED
                        else None
                    ),
                ),
            )
        await lifecycle.on_run_finished(SimpleNamespace(status=run_status))
    finally:
        await composition.shutdown()

    assert (await store.load_turn(turn["id"]))["status"] == expected
    assert await temp_db.fetch_one(
        "SELECT status FROM screenplay_agent_operations WHERE id = ?",
        [turn["operationId"]],
    ) == {"status": expected}


async def _seed_completed_result(db, turn) -> None:
    await db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, status, total_units, "
        "completed_units, usage_json) VALUES "
        "('task-conversation', 'purrtypos.screenplay', 'screenplay.purra-native', "
        "'project-conversation', 'run-conversation', 'completed', 1, 1, ?)",
        [json.dumps({"invocationCount": 3, "inputTokens": 10})],
    )
    deliverable = await db.fetch_one(
        "SELECT id FROM screenplay_deliverables "
        "WHERE project_id = 'project-conversation' AND role = 'sourceAnalysis'"
    )
    await db.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, created_by, "
        "agent_task_id) VALUES "
        "('revision-conversation', 'project-conversation', ?, 1, 'digest', "
        "'screenplay_agent_task', 'task-conversation')",
        [deliverable["id"]],
    )
    await db.execute(
        "INSERT INTO screenplay_replacement_projection_receipts "
        "(task_id, operation_scope_id, project_id, target_role, revision_id, "
        "input_receipt_digest, output_digest, projected_by_run_id) VALUES "
        "('task-conversation', 'scope-conversation', 'project-conversation', "
        "'sourceAnalysis', 'revision-conversation', 'input', 'output', "
        "'run-conversation')"
    )
