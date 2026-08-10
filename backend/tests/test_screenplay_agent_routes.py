from __future__ import annotations

from fastapi import FastAPI
import pytest

import routers.screenplay_conversations as conversation_routes
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
            "plannerRunId": None,
            "taskId": None,
            "error": None,
        }

    def dispatch_turn(self, turn_id, runtime):
        self.dispatched.append((turn_id, runtime.options["model"]))

    async def list_events(self, *, after, **_kwargs):
        events = [] if after >= 1 else [{
            "cursor": 1,
            "turnId": "spaturn-route",
            "taskId": None,
            "type": "screenplay.agent.turn.planning",
            "payload": {},
        }]
        return {
            "events": events,
            "nextCursor": events[-1]["cursor"] if events else after,
            "hasMore": False,
        }


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


async def test_conversation_sse_interleaves_business_and_shared_agent_chunks(
    monkeypatch,
):
    service = _Service()

    class _Chunks:
        def __init__(self, _db) -> None:
            pass

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

    monkeypatch.setattr(conversation_routes, "_service", lambda: service)
    monkeypatch.setattr(
        conversation_routes,
        "ScreenplayAgentChunkStore",
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
            "?sessionId=7&after=0&chunkAfter=0&follow=true"
        ),
    )
    try:
        await response.wait_started()
        business = await response.next_sse_json()
        chunk_page = await response.next_sse_json()
    finally:
        await response.aclose()

    assert business["type"] == "screenplay.agent.turn.planning"
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
    service = _Service()

    class _Chunks:
        def __init__(self, _db) -> None:
            pass

        async def list_chunks(self, *, after, **_kwargs):
            return {
                "chunks": [],
                "nextCursor": after,
                "hasMore": False,
            }

    monkeypatch.setattr(conversation_routes, "_service", lambda: service)
    monkeypatch.setattr(
        conversation_routes,
        "ScreenplayAgentChunkStore",
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
            "?sessionId=7&after=0&chunkAfter=0&follow=true"
        ),
    )
    try:
        await response.wait_started()
        await response.next_sse_json()
        chunk_page = await response.next_sse_json()
    finally:
        await response.aclose()

    assert chunk_page == {
        "kind": "agent_chunks",
        "chunks": [],
        "nextCursor": 0,
        "hasMore": False,
    }


async def test_chunk_delivery_keeps_diagnostics_batched_and_public_text_live():
    items = [
        {"cursor": 1, "chunk": {"reasoningDelta": "a"}},
        {"cursor": 2, "chunk": {"modelContentDelta": "b"}},
        {"cursor": 3, "chunk": {"commentaryDelta": "核"}},
        {"cursor": 4, "chunk": {"commentaryDelta": "对"}},
        {"cursor": 5, "chunk": {"modelContentDelta": "c"}},
    ]

    pages = conversation_routes._chunk_delivery_pages({
        "chunks": items,
        "nextCursor": 5,
        "hasMore": False,
    })

    assert [len(page["chunks"]) for page in pages] == [2, 1, 1, 1]
    assert [page["nextCursor"] for page in pages] == [2, 3, 4, 5]
    assert [page["hasMore"] for page in pages] == [True, True, True, False]


async def test_chunk_replay_keeps_persisted_history_in_one_batch():
    items = [
        {"cursor": 1, "chunk": {"reasoningDelta": "a"}},
        {"cursor": 2, "chunk": {"commentaryDelta": "核"}},
        {"cursor": 3, "chunk": {"commentaryDelta": "对"}},
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
