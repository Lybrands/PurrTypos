from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI

from agent_core.contracts import AgentRunResult, RunStatus
from agent_core.events import AgentEvent
from application.screenplay_conversation_service import (
    ScreenplayConversationService,
)
from application.agent_composition import set_agent_composition
from application.screenplay_v2_service import ScreenplayV2ProjectService
from database.connection import DatabaseConnection
from dependencies import clear_db, set_db
from exceptions import AppError
from infrastructure.persistence.sqlite_screenplay_conversation_repository import (
    SqliteScreenplayConversationRepository,
)
from infrastructure.persistence.sqlite_screenplay_v2_repository import (
    SqliteScreenplayV2Repository,
)
from schemas.screenplay_conversation import (
    SubmitScreenplayConversationTurnRequest,
)
from schemas.screenplay_v2 import CreateScreenplayV2ProjectRequest
from routers.screenplay_v2 import router as screenplay_v2_router
from tests.support.asgi_sse import request_json, start_asgi_request


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


class _LeaseStore:
    def __init__(self) -> None:
        self.canceled: list[str] = []

    async def request_cancellation(self, run_id: str) -> bool:
        self.canceled.append(run_id)
        return True


class _Composition:
    def __init__(self, owner_id: str = "conversation-test-owner") -> None:
        self.execution_owner_id = owner_id
        self.execution_lease_store = _LeaseStore()
        self.tasks = []

    def track_background_run(self, task) -> None:
        self.tasks.append(task)


class _Runner:
    def __init__(self, updates) -> None:
        self.updates = tuple(updates)
        self.calls = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        for update in self.updates:
            yield update


async def _project_and_session(db: DatabaseConnection):
    projects = ScreenplayV2ProjectService(db)
    workspace = await projects.create_project(
        command_id="create-conversation-project",
        request=CreateScreenplayV2ProjectRequest.model_validate({
            "title": "Conversation v2",
            "format": "singleEpisode",
            "source": {"type": "original"},
            "brief": {"approach": "人物驱动", "premise": "一次重逢"},
        }),
    )
    session = await projects.ensure_current_session(workspace["project"]["id"])
    return workspace, session


def _request(**overrides) -> SubmitScreenplayConversationTurnRequest:
    payload = {
        "sessionId": 1,
        "content": "现在的故事核心是什么？",
        "runtime": {
            "apiKey": "top-secret-key",
            "baseURL": "https://provider.example/v1",
            "apiProvider": "openai",
            "locale": "zh-CN",
            "options": {"model": "conversation-model", "temperature": 0.3},
            "contextWindow": "128k",
        },
    }
    payload.update(overrides)
    return SubmitScreenplayConversationTurnRequest.model_validate(payload)


async def test_read_only_turn_persists_canonical_history_without_operation_or_secret(
    temp_db: DatabaseConnection,
):
    workspace, session = await _project_and_session(temp_db)
    runner = _Runner((
        AgentEvent(type="run.started", run_id="run-read-only", payload={}),
        AgentEvent(
            type="model.delta",
            run_id="run-read-only",
            payload={"delta": "故事核心是重新建立信任。"},
        ),
        AgentRunResult(
            run_id="run-read-only",
            status=RunStatus.DONE,
            final_response="故事核心是重新建立信任。",
        ),
    ))
    composition = _Composition()
    service = ScreenplayConversationService(
        temp_db,
        composition,
        runner=runner,
    )
    request = _request(sessionId=session["id"])

    turn = await service.submit_turn(
        command_id="conversation-read-only-1",
        project_id=workspace["project"]["id"],
        request=request,
    )
    await service.execute_turn(turn["id"], request.runtime)

    snapshot = await service.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )
    persisted = snapshot["turns"][0]
    assert persisted["route"] == "read_only"
    assert persisted["status"] == "completed"
    assert persisted["assistantContent"] == "故事核心是重新建立信任。"
    assert persisted["operationId"] is None
    assert persisted["runId"] == "run-read-only"
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_operations"
    ) == {"count": 0}
    raw_profile = await temp_db.fetch_one(
        "SELECT runtime_profile_json FROM screenplay_conversation_turns "
        "WHERE id = ?",
        [turn["id"]],
    )
    encoded_profile = str(raw_profile["runtime_profile_json"])
    assert "top-secret-key" not in encoded_profile
    assert "provider.example" not in encoded_profile
    assert json.loads(encoded_profile)["model"] == "conversation-model"
    assert runner.calls[0]["body"].enableAgentTools is False
    assert runner.calls[0]["body"].chatAgentMode == "ask"
    assert runner.calls[0]["body"].screenplayTaskIntent == "chat"


async def test_formal_turn_creates_exactly_one_operation_in_the_same_command(
    temp_db: DatabaseConnection,
):
    workspace, session = await _project_and_session(temp_db)
    composition = _Composition()
    composition.database = temp_db
    runner = _Runner(())
    service = ScreenplayConversationService(
        temp_db,
        composition,
        runner=runner,
    )
    request = _request(
        sessionId=session["id"],
        content="生成正式创作简报",
        operation={
            "expectedProjectRevision": workspace["project"]["revision"],
            "targetRole": "creativeBrief",
            "intent": {
                "type": "generate",
                "instruction": "生成正式创作简报",
            },
        },
    )

    first = await service.submit_turn(
        command_id="conversation-operation-1",
        project_id=workspace["project"]["id"],
        request=request,
    )
    replay = await service.submit_turn(
        command_id="conversation-operation-1",
        project_id=workspace["project"]["id"],
        request=request,
    )

    assert first["id"] == replay["id"]
    assert first["route"] == "operation"
    assert first["operationId"] == replay["operationId"]
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_conversation_turns"
    ) == {"count": 1}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_operations"
    ) == {"count": 1}
    operation = await ScreenplayV2ProjectService(temp_db).get_operation(
        first["operationId"]
    )
    assert operation["intent"]["conversation"] == {
        "sessionId": session["id"],
        "userMessageId": first["id"],
    }

    changed = _request(
        sessionId=session["id"],
        content="不同内容",
        operation=request.operation.model_dump(mode="json"),
    )
    with pytest.raises(AppError, match="Idempotency-Key"):
        await service.submit_turn(
            command_id="conversation-operation-1",
            project_id=workspace["project"]["id"],
            request=changed,
        )


async def test_formal_execution_rebuilds_request_from_persisted_operation(
    temp_db: DatabaseConnection,
):
    workspace, session = await _project_and_session(temp_db)
    runner = _Runner((AgentRunResult(
        run_id="run-persisted-operation",
        status=RunStatus.DONE,
        final_response="正式交付任务已完成",
    ),))
    service = ScreenplayConversationService(
        temp_db,
        _Composition(),
        runner=runner,
    )
    request = _request(
        sessionId=session["id"],
        content="生成三集正文",
        operation={
            "expectedProjectRevision": workspace["project"]["revision"],
            "targetRole": "creativeBrief",
            "intent": {
                "type": "generate",
                "scope": {"mode": "next_3_episodes", "sceneCount": 7},
                "instruction": "持久化后的正式任务",
            },
        },
    )
    turn = await service.submit_turn(
        command_id="conversation-persisted-operation",
        project_id=workspace["project"]["id"],
        request=request,
    )

    # Only credentials/runtime configuration cross the recovery boundary.
    # Formal intent is reconstructed from the persisted Operation.
    await service.execute_turn(turn["id"], request.runtime)

    body = runner.calls[0]["body"]
    assert body.screenplayOperationId == turn["operationId"]
    assert body.screenplayTaskIntent == "stage_deliverable"
    assert body.screenplayDraftScope == "next_3_episodes"
    assert body.screenplayDraftSceneCount == 7


async def test_resume_command_is_idempotent_without_persisting_credentials(
    temp_db: DatabaseConnection,
):
    workspace, session = await _project_and_session(temp_db)
    service = ScreenplayConversationService(
        temp_db,
        _Composition(),
        runner=_Runner(()),
    )
    request = _request(sessionId=session["id"])
    turn = await service.submit_turn(
        command_id="conversation-to-resume",
        project_id=workspace["project"]["id"],
        request=request,
    )
    dispatched: list[str] = []
    service.dispatch_turn = (  # type: ignore[method-assign]
        lambda turn_id, runtime: dispatched.append(turn_id)
    )

    first = await service.resume_turn(
        turn["id"],
        command_id="resume-conversation-once",
        runtime=request.runtime,
    )
    replay = await service.resume_turn(
        turn["id"],
        command_id="resume-conversation-once",
        runtime=request.runtime,
    )

    assert first["id"] == replay["id"] == turn["id"]
    assert dispatched == [turn["id"], turn["id"]]
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_command_receipts "
        "WHERE command_type = 'resumeConversationTurn'"
    ) == {"count": 1}
    receipt = await temp_db.fetch_one(
        "SELECT response_json FROM screenplay_command_receipts "
        "WHERE command_type = 'resumeConversationTurn'"
    )
    assert "top-secret-key" not in str(receipt["response_json"])

    changed_runtime = request.runtime.model_copy(deep=True)
    changed_runtime.options["model"] = "different-model"
    with pytest.raises(AppError, match="Idempotency-Key"):
        await service.resume_turn(
            turn["id"],
            command_id="resume-conversation-once",
            runtime=changed_runtime,
        )


async def test_restart_recovery_releases_turn_and_resumes_paused_operation(
    temp_db: DatabaseConnection,
):
    workspace, session = await _project_and_session(temp_db)
    request = _request(
        sessionId=session["id"],
        operation={
            "expectedProjectRevision": workspace["project"]["revision"],
            "targetRole": "creativeBrief",
            "intent": {
                "type": "generate",
                "instruction": "恢复正式任务",
            },
        },
    )
    service = ScreenplayConversationService(
        temp_db,
        _Composition("new-process-owner"),
        runner=_Runner(()),
    )
    turn = await service.submit_turn(
        command_id="conversation-before-restart",
        project_id=workspace["project"]["id"],
        request=request,
    )
    await temp_db.execute(
        "UPDATE screenplay_conversation_turns SET status = 'running', "
        "execution_owner_id = 'dead-process', lease_expires_at_ms = 9999999999999 "
        "WHERE id = ?",
        [turn["id"]],
    )
    await temp_db.execute(
        "UPDATE screenplay_operations SET status = 'running' WHERE id = ?",
        [turn["operationId"]],
    )

    await SqliteScreenplayV2Repository(
        temp_db
    ).recover_operations_after_restart()
    recovered = await SqliteScreenplayConversationRepository(
        temp_db,
        owner_id="startup-recovery",
    ).recover_after_restart()
    assert recovered == (turn["id"],)

    dispatched: list[str] = []
    service.dispatch_turn = (  # type: ignore[method-assign]
        lambda turn_id, runtime: dispatched.append(turn_id)
    )
    resumed = await service.resume_turn(
        turn["id"],
        command_id="resume-after-restart",
        runtime=request.runtime,
    )

    operation = await ScreenplayV2ProjectService(temp_db).get_operation(
        turn["operationId"]
    )
    assert resumed["status"] == "queued"
    assert resumed["retryable"] is True
    assert operation["status"] == "queued"
    assert dispatched == [turn["id"]]
    events = await service.list_events(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
        after=0,
        limit=100,
    )
    assert any(
        event["type"] == "screenplay.conversation.turn_recovery_required"
        for event in events["events"]
    )


async def test_operation_validation_failure_rolls_back_turn_and_receipts(
    temp_db: DatabaseConnection,
):
    workspace, session = await _project_and_session(temp_db)
    service = ScreenplayConversationService(
        temp_db,
        _Composition(),
        runner=_Runner(()),
    )
    request = _request(
        sessionId=session["id"],
        operation={
            "expectedProjectRevision": 999,
            "targetRole": "creativeBrief",
            "intent": {"type": "generate"},
        },
    )

    with pytest.raises(AppError, match="刷新"):
        await service.submit_turn(
            command_id="conversation-invalid-operation",
            project_id=workspace["project"]["id"],
            request=request,
        )

    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_conversation_turns"
    ) == {"count": 0}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_operations"
    ) == {"count": 0}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM screenplay_command_receipts "
        "WHERE command_id LIKE 'conversation-invalid-operation%'"
    ) == {"count": 0}


async def test_turn_execution_claim_prevents_duplicate_model_runs(
    temp_db: DatabaseConnection,
):
    workspace, session = await _project_and_session(temp_db)
    runner = _Runner((AgentRunResult(
        run_id="run-once",
        status=RunStatus.DONE,
        final_response="只执行一次",
    ),))
    service = ScreenplayConversationService(
        temp_db,
        _Composition(),
        runner=runner,
    )
    request = _request(sessionId=session["id"])
    turn = await service.submit_turn(
        command_id="conversation-claim-once",
        project_id=workspace["project"]["id"],
        request=request,
    )

    await service.execute_turn(turn["id"], request.runtime)
    await service.execute_turn(turn["id"], request.runtime)

    assert len(runner.calls) == 1
    snapshot = await service.get_snapshot(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
    )
    assert snapshot["turns"][0]["assistantContent"] == "只执行一次"


async def test_conversation_event_cursor_is_forward_only(
    temp_db: DatabaseConnection,
):
    workspace, session = await _project_and_session(temp_db)
    runner = _Runner((
        AgentEvent(type="run.started", run_id="run-cursor", payload={}),
        AgentEvent(
            type="model.delta",
            run_id="run-cursor",
            payload={"delta": "第一段"},
        ),
        AgentEvent(
            type="model.delta",
            run_id="run-cursor",
            payload={"delta": "第二段"},
        ),
        AgentRunResult(
            run_id="run-cursor",
            status=RunStatus.DONE,
            final_response="第一段第二段",
        ),
    ))
    service = ScreenplayConversationService(
        temp_db,
        _Composition(),
        runner=runner,
    )
    request = _request(sessionId=session["id"])
    turn = await service.submit_turn(
        command_id="conversation-cursor",
        project_id=workspace["project"]["id"],
        request=request,
    )
    await service.execute_turn(turn["id"], request.runtime)

    first = await service.list_events(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
        after=0,
        limit=2,
    )
    second = await service.list_events(
        project_id=workspace["project"]["id"],
        session_id=session["id"],
        after=first["nextCursor"],
        limit=20,
    )

    assert first["hasMore"] is True
    assert second["events"]
    cursors = [
        *(event["cursor"] for event in first["events"]),
        *(event["cursor"] for event in second["events"]),
    ]
    assert cursors == sorted(set(cursors))
    assert second["nextCursor"] > first["nextCursor"]


async def test_native_conversation_http_contract_uses_snapshot_and_cursor_events(
    temp_db: DatabaseConnection,
    monkeypatch,
):
    workspace, session = await _project_and_session(temp_db)
    composition = _Composition()
    composition.database = temp_db
    set_db(temp_db)
    set_agent_composition(composition)  # type: ignore[arg-type]
    dispatched: list[str] = []

    def _dispatch(_service, turn_id, runtime):
        del runtime
        dispatched.append(turn_id)
        return None

    monkeypatch.setattr(ScreenplayConversationService, "dispatch_turn", _dispatch)
    app = FastAPI()
    app.include_router(screenplay_v2_router, prefix="/api")
    try:
        live_response = start_asgi_request(
            app,
            method="POST",
            path=(
                "/api/screenplay/v2/projects/"
                f"{workspace['project']['id']}/conversation/turns"
            ),
            headers={"Idempotency-Key": "http-conversation-turn"},
            json_body={
                "sessionId": session["id"],
                "content": "解释当前阶段",
                "runtime": {
                    "apiKey": "secret",
                    "apiProvider": "openai",
                    "options": {"model": "http-test-model"},
                },
            },
        )
        await live_response.wait_started()
        response = await live_response.finish()
        assert response.status_code == 202
        turn = response.json()["data"]
        assert dispatched == [turn["id"]]

        snapshot = await request_json(
            app,
            method="GET",
            path=(
                "/api/screenplay/v2/projects/"
                f"{workspace['project']['id']}/conversation/snapshot"
                f"?sessionId={session['id']}"
            ),
            json_body=None,
        )
        assert snapshot.status_code == 200
        assert snapshot.json()["data"]["turns"][0]["userContent"] == "解释当前阶段"

        events = await request_json(
            app,
            method="GET",
            path=(
                "/api/screenplay/v2/projects/"
                f"{workspace['project']['id']}/conversation/events"
                f"?sessionId={session['id']}&after=0&follow=false"
            ),
            json_body=None,
        )
        assert events.status_code == 200
        page = events.json()["data"]
        assert page["events"][0]["type"] == "screenplay.conversation.turn_queued"
        assert page["nextCursor"] == page["events"][0]["cursor"]
    finally:
        set_agent_composition(None)
        clear_db(temp_db)


async def test_sse_disconnect_removes_only_the_subscriber(
    temp_db: DatabaseConnection,
):
    workspace, session = await _project_and_session(temp_db)
    composition = _Composition()
    composition.database = temp_db
    service = ScreenplayConversationService(
        temp_db,
        composition,
        runner=_Runner(()),
    )
    request = _request(sessionId=session["id"])
    turn = await service.submit_turn(
        command_id="conversation-disconnect",
        project_id=workspace["project"]["id"],
        request=request,
    )
    set_db(temp_db)
    set_agent_composition(composition)  # type: ignore[arg-type]
    app = FastAPI()
    app.include_router(screenplay_v2_router, prefix="/api")
    live = start_asgi_request(
        app,
        method="GET",
        path=(
            "/api/screenplay/v2/projects/"
            f"{workspace['project']['id']}/conversation/events"
            f"?sessionId={session['id']}&after=0"
        ),
    )
    try:
        await live.wait_started()
        event = await live.next_sse_json()
        assert event["turnId"] == turn["id"]
        await live.aclose()

        snapshot = await service.get_snapshot(
            project_id=workspace["project"]["id"],
            session_id=session["id"],
        )
        assert snapshot["turns"][0]["status"] == "queued"
        assert composition.execution_lease_store.canceled == []
    finally:
        await live.aclose()
        set_agent_composition(None)
        clear_db(temp_db)
