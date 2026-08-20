from __future__ import annotations

from fastapi import FastAPI
import pytest

import routers.screenplay_conversations as conversation_routes
import application.agent_composition as agent_composition
from routers.screenplay_v2 import router as screenplay_router
from tests.support.asgi_sse import start_asgi_request


pytestmark = pytest.mark.asyncio


class _Service:
    def __init__(self) -> None:
        self.submissions = []
        self.dispatched = []

    async def submit_turn(self, **kwargs):
        self.submissions.append(kwargs)
        request = kwargs["request"]
        return {
            "id": "spaturn-route",
            "projectId": kwargs["project_id"],
            "sessionId": request.sessionId,
            "status": "queued",
            "userContent": request.content,
            "assistantContent": "",
            "runtimeProfile": {"model": request.runtime.options["model"]},
            "intent": None,
            "rootRunId": None,
            "taskId": None,
            "error": None,
        }

    def dispatch_turn(self, turn_id, runtime):
        self.dispatched.append((turn_id, runtime.options["model"]))

def _body(**extra):
    return {
        "sessionId": 7,
        "content": "不要只做下一集，直接创作后面三集。",
        "runtime": {
            "apiKey": "secret",
            "apiProvider": "openai",
            "options": {"model": "route-model"},
        },
        **extra,
    }


async def test_production_unit_executor_has_no_tool_and_tool_model_paths(
    monkeypatch,
):
    captured_service = {}
    captured_executor = {}

    class Composition:
        execution_owner_id = "route-composition-owner"
        output_processor = object()

        @staticmethod
        def track_background_run(_task):
            return None

    composition = Composition()

    class CapturingService:
        def __init__(self, _db, **kwargs):
            captured_service.update(kwargs)

    class CapturingExecutor:
        def __init__(self, _db, **kwargs):
            captured_executor.update(kwargs)

    monkeypatch.setattr(conversation_routes, "get_db", lambda: object())
    monkeypatch.setattr(
        agent_composition,
        "get_agent_composition",
        lambda: composition,
    )
    monkeypatch.setattr(
        conversation_routes,
        "ScreenplayAgentService",
        CapturingService,
    )
    monkeypatch.setattr(
        conversation_routes,
        "ScreenplayV2ProjectService",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        conversation_routes,
        "ScreenplayCandidateModelService",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        conversation_routes,
        "ScreenplayTaskUnitExecutor",
        CapturingExecutor,
    )

    conversation_routes._service()
    captured_service["unit_executor_factory"](object())

    assert captured_service["composition"] is composition
    assert "planner" not in captured_service
    assert "resolver" not in captured_service
    assert captured_executor["composition"] is composition
    assert captured_executor["candidate_model_service"] is not None


async def _post(app, body):
    response = start_asgi_request(
        app,
        method="POST",
        path="/api/screenplay/v2/projects/project-1/conversation/turns",
        headers={"Idempotency-Key": "route-command"},
        json_body=body,
    )
    await response.wait_started()
    return await response.finish()


async def test_turn_endpoint_passes_only_user_text_to_the_rewritten_service(monkeypatch):
    service = _Service()
    monkeypatch.setattr(conversation_routes, "_service", lambda: service)
    app = FastAPI()
    app.include_router(screenplay_router, prefix="/api")

    response = await _post(app, _body())

    assert response.status_code == 202
    assert service.submissions[0]["request"].content == _body()["content"]
    assert service.dispatched == [("spaturn-route", "route-model")]


async def test_turn_endpoint_accepts_a_typed_stage_command(monkeypatch):
    service = _Service()
    monkeypatch.setattr(conversation_routes, "_service", lambda: service)
    app = FastAPI()
    app.include_router(screenplay_router, prefix="/api")

    response = await _post(app, _body(stageCommand={
        "kind": "stage_action",
        "action": "review",
        "targetRole": "review",
        "scope": {"kind": "current_stage"},
    }))

    assert response.status_code == 202
    command = service.submissions[0]["request"].stageCommand
    assert command is not None
    assert command.targetRole == "review"
    assert command.scope.kind == "current_stage"


@pytest.mark.parametrize("stage_command", [
    {
        "kind": "stage_action",
        "action": "answer",
        "targetRole": "screenplayDraft",
        "scope": {"kind": "current_stage"},
    },
    {
        "kind": "stage_action",
        "action": "review",
        "targetRole": "screenplayDraft",
        "scope": {"kind": "current_stage"},
    },
    {
        "kind": "stage_action",
        "action": "create",
        "targetRole": "review",
        "scope": {"kind": "current_stage"},
    },
    {
        "kind": "stage_action",
        "action": "create",
        "targetRole": "screenplayDraft",
        "scope": {"kind": "next_episodes"},
    },
    {
        "kind": "stage_action",
        "action": "create",
        "targetRole": "screenplayDraft",
        "scope": {"kind": "all_remaining", "count": 2},
    },
])
async def test_turn_endpoint_rejects_invalid_stage_commands(
    monkeypatch,
    stage_command,
):
    service = _Service()
    monkeypatch.setattr(conversation_routes, "_service", lambda: service)
    app = FastAPI()
    app.include_router(screenplay_router, prefix="/api")

    response = await _post(app, _body(stageCommand=stage_command))

    assert response.status_code == 422
    assert service.submissions == []


async def test_turn_endpoint_rejects_the_removed_frontend_operation_contract(monkeypatch):
    service = _Service()
    monkeypatch.setattr(conversation_routes, "_service", lambda: service)
    app = FastAPI()
    app.include_router(screenplay_router, prefix="/api")

    response = await _post(app, _body(operation={
        "targetRole": "screenplayDraft",
        "intent": {"type": "continue"},
    }))

    assert response.status_code == 422
    assert service.submissions == []


async def test_cancel_turn_reuses_receipt_for_the_same_idempotency_key(monkeypatch):
    class CancelService:
        def __init__(self) -> None:
            self.receipts = {}
            self.calls = []

        async def cancel_turn(self, turn_id, *, idempotency_key):
            self.calls.append((turn_id, idempotency_key))
            receipt = self.receipts.setdefault(
                (turn_id, idempotency_key),
                f"cancel-receipt-{len(self.receipts) + 1}",
            )
            return {
                "id": turn_id,
                "status": "canceled",
                "cancelReceiptId": receipt,
            }

    service = CancelService()
    monkeypatch.setattr(conversation_routes, "_service", lambda: service)

    first = await conversation_routes.cancel_screenplay_conversation_turn(
        "spaturn-route",
        "cancel-command-1",
    )
    second = await conversation_routes.cancel_screenplay_conversation_turn(
        "spaturn-route",
        "cancel-command-1",
    )

    assert first["data"]["cancelReceiptId"] == "cancel-receipt-1"
    assert second == first
    assert service.calls == [
        ("spaturn-route", "cancel-command-1"),
        ("spaturn-route", "cancel-command-1"),
    ]


async def test_resume_operation_preflights_then_dispatches_selected_runtime(monkeypatch):
    class ResumeService:
        def __init__(self) -> None:
            self.prepared = []
            self.dispatched = []

        async def prepare_resume(self, operation_id, *, idempotency_key, request):
            self.prepared.append((
                operation_id,
                idempotency_key,
                request.expectedOperationRevision,
                request.runtime.options["model"],
            ))
            return {
                "operationId": operation_id,
                "status": "running",
                "revision": request.expectedOperationRevision + 1,
                "dispatchRequired": True,
            }

        def dispatch_resumed_operation(
            self,
            operation_id,
            runtime,
            *,
            continuation_command,
        ):
            self.dispatched.append((
                operation_id,
                runtime.options["model"],
                continuation_command,
            ))

    service = ResumeService()
    monkeypatch.setattr(conversation_routes, "_service", lambda: service)

    result = await conversation_routes.resume_screenplay_operation(
        "operation-1",
        conversation_routes.ResumeScreenplayOperationRequest.model_validate({
            "expectedOperationRevision": 4,
            "runtime": _body()["runtime"],
        }),
        "resume-command-1",
    )

    assert result["data"]["revision"] == 5
    assert service.prepared == [(
        "operation-1", "resume-command-1", 4, "route-model",
    )]
    assert service.dispatched == [(
        "operation-1", "route-model", "resume-command-1",
    )]


async def test_resume_operation_replay_does_not_redispatch_terminal_operation(
    monkeypatch,
):
    class ResumeService:
        dispatched = False

        async def prepare_resume(self, operation_id, *, idempotency_key, request):
            del idempotency_key, request
            return {
                "operationId": operation_id,
                "status": "running",
                "revision": 8,
                "dispatchRequired": False,
            }

        def dispatch_resumed_operation(
            self,
            operation_id,
            runtime,
            *,
            continuation_command,
        ):
            del operation_id, runtime, continuation_command
            self.dispatched = True

    service = ResumeService()
    monkeypatch.setattr(conversation_routes, "_service", lambda: service)

    result = await conversation_routes.resume_screenplay_operation(
        "operation-1",
        conversation_routes.ResumeScreenplayOperationRequest.model_validate({
            "expectedOperationRevision": 4,
            "runtime": _body()["runtime"],
        }),
        "resume-command-1",
    )

    assert result["data"]["status"] == "running"
    assert service.dispatched is False


async def test_conversation_sse_streams_only_canonical_agent_output(
    monkeypatch,
):
    class _Chunks:
        def __init__(self, _db, *, output_repository) -> None:
            assert output_repository is canonical_output

        async def list_chunks(self, *, after, **_kwargs):
            chunks = [] if after >= 2 else [{
                "cursor": 2,
                "runId": "run-route",
                "turnId": "spaturn-route",
                "taskId": None,
                "userContent": "创作下一集",
                "model": "route-model",
                "turnCreatedAt": "2026-08-09 00:00:00",
                "chunk": {"commentaryDelta": "正在理解请求"},
                "createdAt": "2026-08-09 00:00:00",
            }]
            return {
                "chunks": chunks,
                "nextCursor": chunks[-1]["cursor"] if chunks else after,
                "hasMore": False,
            }

    canonical_output = object()
    monkeypatch.setattr(
        agent_composition,
        "get_agent_composition",
        lambda: type("Composition", (), {
            "output_journal": canonical_output,
        })(),
    )
    monkeypatch.setattr(
        conversation_routes,
        "ScreenplayCanonicalOutputQuery",
        _Chunks,
    )
    monkeypatch.setattr(conversation_routes, "get_db", lambda: object())
    app = FastAPI()
    app.include_router(screenplay_router, prefix="/api")
    response = start_asgi_request(
        app,
        method="GET",
        path=(
            "/api/screenplay/v2/projects/project-1/conversation/events"
            "?sessionId=7&chunkAfter=0"
        ),
    )
    try:
        await response.wait_started()
        chunk_page = await response.next_sse_json()
    finally:
        await response.aclose()

    assert chunk_page == {
        "kind": "agent_chunks",
        "chunks": [{
            "cursor": 2,
            "runId": "run-route",
            "turnId": "spaturn-route",
            "taskId": None,
            "userContent": "创作下一集",
            "model": "route-model",
            "turnCreatedAt": "2026-08-09 00:00:00",
            "chunk": {"commentaryDelta": "正在理解请求"},
            "createdAt": "2026-08-09 00:00:00",
        }],
        "nextCursor": 2,
        "hasMore": False,
    }


async def test_conversation_sse_announces_empty_chunk_replay_completion(
    monkeypatch,
):
    class _Chunks:
        def __init__(self, _db, *, output_repository) -> None:
            assert output_repository is canonical_output

        async def list_chunks(self, *, after, **_kwargs):
            return {
                "chunks": [],
                "nextCursor": after,
                "hasMore": False,
            }

    canonical_output = object()
    monkeypatch.setattr(
        agent_composition,
        "get_agent_composition",
        lambda: type("Composition", (), {
            "output_journal": canonical_output,
        })(),
    )
    monkeypatch.setattr(
        conversation_routes,
        "ScreenplayCanonicalOutputQuery",
        _Chunks,
    )
    monkeypatch.setattr(conversation_routes, "get_db", lambda: object())
    app = FastAPI()
    app.include_router(screenplay_router, prefix="/api")
    response = start_asgi_request(
        app,
        method="GET",
        path=(
            "/api/screenplay/v2/projects/project-1/conversation/events"
            "?sessionId=7&chunkAfter=0"
        ),
    )
    try:
        await response.wait_started()
        chunk_page = await response.next_sse_json()
    finally:
        await response.aclose()

    assert chunk_page == {
        "kind": "agent_chunks",
        "chunks": [],
        "nextCursor": 0,
        "hasMore": False,
    }


async def test_conversation_sse_advances_past_unbound_canonical_output(
    monkeypatch,
):
    class _Chunks:
        def __init__(self, _db, *, output_repository) -> None:
            assert output_repository is canonical_output

        async def list_chunks(self, *, after, **_kwargs):
            return {
                "chunks": [],
                "nextCursor": 7 if after < 7 else after,
                "hasMore": False,
            }

    canonical_output = object()
    monkeypatch.setattr(
        agent_composition,
        "get_agent_composition",
        lambda: type("Composition", (), {
            "output_journal": canonical_output,
        })(),
    )
    monkeypatch.setattr(
        conversation_routes,
        "ScreenplayCanonicalOutputQuery",
        _Chunks,
    )
    monkeypatch.setattr(conversation_routes, "get_db", lambda: object())
    app = FastAPI()
    app.include_router(screenplay_router, prefix="/api")
    response = start_asgi_request(
        app,
        method="GET",
        path=(
            "/api/screenplay/v2/projects/project-1/conversation/events"
            "?sessionId=7&chunkAfter=0"
        ),
    )
    try:
        await response.wait_started()
        chunk_page = await response.next_sse_json()
    finally:
        await response.aclose()

    assert chunk_page == {
        "kind": "agent_chunks",
        "chunks": [],
        "nextCursor": 7,
        "hasMore": False,
    }


async def test_chunk_delivery_keeps_every_canonical_event_live():
    items = [
        {"cursor": 1, "chunk": {"kind": "operation.started"}},
        {"cursor": 2, "chunk": {"kind": "provider.content_delta"}},
        {"cursor": 3, "chunk": {"kind": "provider.content_delta"}},
        {"cursor": 4, "chunk": {"kind": "operation.finished"}},
    ]

    pages = conversation_routes._chunk_delivery_pages({
        "chunks": items,
        "nextCursor": 4,
        "hasMore": False,
    })

    assert [len(page["chunks"]) for page in pages] == [1, 1, 1, 1]
    assert [page["nextCursor"] for page in pages] == [1, 2, 3, 4]
    assert [page["hasMore"] for page in pages] == [True, True, True, False]


async def test_chunk_delivery_advances_a_cursor_without_visible_chunks():
    pages = conversation_routes._chunk_delivery_pages({
        "chunks": [],
        "nextCursor": 7,
        "hasMore": False,
    })

    assert pages == ({
        "kind": "agent_chunks",
        "chunks": [],
        "nextCursor": 7,
        "hasMore": False,
    },)


async def test_chunk_replay_keeps_persisted_history_in_one_batch():
    items = [
        {"cursor": 1, "chunk": {"kind": "operation.started"}},
        {"cursor": 2, "chunk": {"kind": "provider.content_delta"}},
        {"cursor": 3, "chunk": {"kind": "operation.finished"}},
    ]

    page = conversation_routes._chunk_replay_page({
        "chunks": items,
        "nextCursor": 3,
        "hasMore": True,
    })

    assert page == {
        "kind": "agent_chunks",
        "chunks": items,
        "nextCursor": 3,
        "hasMore": True,
    }


async def test_conversation_sse_disconnect_only_detaches_subscription(monkeypatch):
    class Request:
        calls = 0

        async def is_disconnected(self):
            self.calls += 1
            return self.calls > 1

    class Chunks:
        def __init__(self, *_args, **_kwargs):
            pass

        async def list_chunks(self, **_kwargs):
            return {"chunks": [], "nextCursor": 0, "hasMore": False}

    class Composition:
        output_journal = object()

    monkeypatch.setattr(conversation_routes, "get_db", lambda: object())
    monkeypatch.setattr(
        agent_composition,
        "get_agent_composition",
        lambda: Composition(),
    )
    monkeypatch.setattr(
        conversation_routes,
        "ScreenplayCanonicalOutputQuery",
        Chunks,
    )

    response = await conversation_routes.stream_screenplay_conversation_events(
        Request(),
        "project-1",
        7,
        0,
        100,
    )
    events = [item async for item in response.body_iterator]

    assert len(events) == 1
    assert "agent_chunks" in str(events[0])
