from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI

from application.agent_composition import set_agent_composition
from application.composition_factory import create_agent_composition
from database.connection import DatabaseConnection
from dependencies import set_db
from domains.writing.agent_roles import build_writing_agent_role_registry
from infrastructure.persistence.run_store import create_run
from purra.contracts import RunBinding
import routers.ai as ai_routes
from routers.ai import router as ai_router
from tests.support.asgi_sse import (
    decode_sse_json,
    request_json,
    start_asgi_request,
)


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def receipt_app(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) "
        "VALUES (7, 'book-1', 'chapter-1')"
    )
    set_db(db)
    composition = create_agent_composition(db)
    set_agent_composition(composition)
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")
    try:
        yield app, db
    finally:
        await composition.shutdown()
        set_agent_composition(None)
        await db.close()


def request_body(request_id: str = "chat-route-1") -> dict:
    return {
        "streamId": request_id,
        "requestReceiptVersion": 1,
        "messages": [{"role": "user", "content": "继续写作"}],
        "apiKey": "test-key",
        "baseURL": "https://provider.test/v1/",
        "apiProvider": "openai",
        "locale": "zh-CN",
        "sessionId": 7,
        "options": {
            "model": "deepseek-v4-flash",
            "model_profile": "deepseek:deepseek-v4-flash",
        },
        "enableAgentTools": True,
        "bookId": "book-1",
        "chapterId": "chapter-1",
        "chatAgentMode": "agent",
        "contextWindow": "200k",
    }


async def test_writing_routes_resolve_roles_from_persisted_profile_identity():
    registry = build_writing_agent_role_registry()
    seen_profiles: list[tuple[str, str]] = []

    class _Composition:
        def agent_role_registry_for_persisted_profile(
            self,
            *,
            profile_id,
            domain_namespace,
        ):
            seen_profiles.append((profile_id, domain_namespace))
            return registry

    resolved = await ai_routes._persisted_run_role_registry(
        _Composition(),
        None,
        {
            "id": "writing-run",
            "binding_attributes_json": (
                '{"agentProfile":"writing",'
                '"domainNamespace":"purrtypos.writing"}'
            ),
        },
    )

    assert resolved is registry
    assert seen_profiles == [("writing", "purrtypos.writing")]


async def test_reserve_route_replays_response_loss_and_rejects_changed_input(
    receipt_app,
):
    app, _db = receipt_app
    first = await request_json(
        app,
        method="PUT",
        path="/api/ai/chat/requests/chat-route-1",
        json_body=request_body(),
    )
    replay = await request_json(
        app,
        method="PUT",
        path="/api/ai/chat/requests/chat-route-1",
        json_body=request_body(),
    )
    changed = request_body()
    changed["messages"] = [{"role": "user", "content": "不同请求"}]
    conflict = await request_json(
        app,
        method="PUT",
        path="/api/ai/chat/requests/chat-route-1",
        json_body=changed,
    )

    assert first.status_code == 200
    assert replay.json() == first.json()
    assert first.json()["data"]["status"] == "accepted"
    assert conflict.status_code == 409


async def test_explicit_validation_failure_creates_no_receipt(receipt_app):
    app, db = receipt_app
    invalid = request_body("chat-invalid")
    invalid["options"] = {}
    response = await request_json(
        app,
        method="PUT",
        path="/api/ai/chat/requests/chat-invalid",
        json_body=invalid,
    )

    assert response.status_code == 400
    assert await db.fetch_one(
        "SELECT request_id FROM ai_writing_chat_requests "
        "WHERE request_id = 'chat-invalid'"
    ) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("bookId", "other-book"), ("chapterId", "other-chapter")],
)
async def test_reserve_rejects_scope_that_does_not_own_the_session(
    receipt_app,
    field,
    value,
):
    app, db = receipt_app
    invalid = request_body(f"chat-wrong-{field}")
    invalid[field] = value

    response = await request_json(
        app,
        method="PUT",
        path=f"/api/ai/chat/requests/chat-wrong-{field}",
        json_body=invalid,
    )

    assert response.status_code == 409
    assert await db.fetch_one(
        "SELECT request_id FROM ai_writing_chat_requests "
        "WHERE request_id = ?",
        [f"chat-wrong-{field}"],
    ) is None


async def test_request_cancel_is_idempotent_before_run(receipt_app):
    app, _db = receipt_app
    await request_json(
        app,
        method="PUT",
        path="/api/ai/chat/requests/chat-stop-before-run",
        json_body=request_body("chat-stop-before-run"),
    )
    first = await request_json(
        app,
        method="POST",
        path="/api/ai/chat/requests/chat-stop-before-run/cancel",
        json_body={},
    )
    second = await request_json(
        app,
        method="POST",
        path="/api/ai/chat/requests/chat-stop-before-run/cancel",
        json_body={},
    )

    assert first.json()["data"]["status"] == "canceled"
    assert second.json()["data"]["revision"] == first.json()["data"]["revision"]


async def test_enhanced_post_cannot_bypass_request_reservation(receipt_app):
    app, db = receipt_app
    response = await request_json(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=request_body("chat-not-reserved"),
    )

    assert response.status_code == 409
    assert await db.fetch_one(
        "SELECT id FROM ai_agent_runs WHERE binding_command_id = ?",
        ["chat-not-reserved"],
    ) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chatAgentMode", None),
        ("sessionId", None),
        ("streamId", None),
        ("apiKey", ""),
        ("options", {}),
    ],
)
async def test_reserved_v1_post_cannot_downgrade_before_claim(
    receipt_app,
    field,
    value,
):
    app, db = receipt_app
    request_id = f"chat-no-downgrade-{field}"
    original = request_body(request_id)
    reserved = await request_json(
        app,
        method="PUT",
        path=f"/api/ai/chat/requests/{request_id}",
        json_body=original,
    )
    assert reserved.status_code == 200
    changed = {**original, field: value}

    response = await request_json(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=changed,
    )

    assert response.status_code in {400, 409}
    assert await db.fetch_one(
        "SELECT status FROM ai_writing_chat_requests WHERE request_id = ?",
        [request_id],
    ) == {"status": "accepted"}
    assert await db.fetch_one(
        "SELECT id FROM ai_agent_runs WHERE binding_command_id = ?",
        [request_id],
    ) is None


async def test_request_id_replay_cannot_change_api_key_credential(receipt_app):
    app, db = receipt_app
    request_id = "chat-api-key-binding"
    original = request_body(request_id)
    await request_json(
        app,
        method="PUT",
        path=f"/api/ai/chat/requests/{request_id}",
        json_body=original,
    )

    changed = {**original, "apiKey": "different-secret"}
    response = await request_json(
        app,
        method="PUT",
        path=f"/api/ai/chat/requests/{request_id}",
        json_body=changed,
    )

    assert response.status_code == 409
    stored = await db.fetch_one(
        "SELECT request_digest FROM ai_writing_chat_requests WHERE request_id = ?",
        [request_id],
    )
    assert "key" not in stored["request_digest"]
    assert "different-secret" not in stored["request_digest"]


async def test_post_claim_binds_one_run_and_bound_cancel_is_applied_once(
    receipt_app,
    monkeypatch,
):
    app, db = receipt_app
    request_id = "chat-route-bound"
    calls = 0
    cancel_calls: list[str] = []

    async def fake_stream_composed_agent(**kwargs):
        nonlocal calls
        calls += 1
        lifecycle = kwargs["run_binding_lifecycle"]
        await lifecycle.validate()
        await lifecycle.before_submit()
        run_id = await create_run(
            db,
            session_id=7,
            prompt="继续写作",
            mode="agent",
            binding=RunBinding(
                namespace="writing.chat.request",
                aggregate_id="7",
                command_id=request_id,
            ),
        )
        await lifecycle.on_run_started(run_id)
        yield {"runId": run_id, "kind": "run.started"}

    async def fake_cancel_agent_run(run_id: str):
        cancel_calls.append(run_id)
        return {"success": True}

    monkeypatch.setattr(
        ai_routes,
        "_stream_composed_agent",
        fake_stream_composed_agent,
    )
    monkeypatch.setattr(ai_routes, "cancel_agent_run", fake_cancel_agent_run)
    await request_json(
        app,
        method="PUT",
        path=f"/api/ai/chat/requests/{request_id}",
        json_body=request_body(request_id),
    )

    first = await request_json(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=request_body(request_id),
    )
    replay = await request_json(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=request_body(request_id),
    )
    first_frames = decode_sse_json(first.content)
    replay_frames = decode_sse_json(replay.content)
    receipt = await db.fetch_one(
        "SELECT status, run_id FROM ai_writing_chat_requests "
        "WHERE request_id = ?",
        [request_id],
    )

    assert calls == 1
    assert receipt["status"] == "run_bound"
    assert first_frames[0]["runId"] == receipt["run_id"]
    assert replay_frames == [{
        "requestReceipt": {
            "requestId": request_id,
            "sessionId": 7,
            "status": "run_bound",
            "runId": receipt["run_id"],
            "cancelRequested": False,
            "rejectionCode": None,
            "revision": 3,
        },
    }]

    await request_json(
        app,
        method="POST",
        path=f"/api/ai/chat/requests/{request_id}/cancel",
        json_body={},
    )
    await request_json(
        app,
        method="POST",
        path=f"/api/ai/chat/requests/{request_id}/cancel",
        json_body={},
    )
    assert cancel_calls == [receipt["run_id"]]


async def test_cancel_during_prepare_prevents_run_submission(
    receipt_app,
    monkeypatch,
):
    app, db = receipt_app
    request_id = "chat-cancel-during-prepare"
    prepare_started = asyncio.Event()
    finish_prepare = asyncio.Event()
    submissions = 0

    async def fake_stream_composed_agent(**kwargs):
        nonlocal submissions
        lifecycle = kwargs["run_binding_lifecycle"]
        await lifecycle.validate()
        prepare_started.set()
        await finish_prepare.wait()
        await lifecycle.before_submit()
        submissions += 1
        if False:
            yield {}

    monkeypatch.setattr(
        ai_routes,
        "_stream_composed_agent",
        fake_stream_composed_agent,
    )
    await request_json(
        app,
        method="PUT",
        path=f"/api/ai/chat/requests/{request_id}",
        json_body=request_body(request_id),
    )
    stream = start_asgi_request(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=request_body(request_id),
    )
    await stream.wait_started()
    await prepare_started.wait()

    await request_json(
        app,
        method="POST",
        path=f"/api/ai/chat/requests/{request_id}/cancel",
        json_body={},
    )
    finish_prepare.set()
    terminal = await stream.next_sse_json()
    await stream.finish()

    assert terminal["requestResult"]["status"] == "canceled"
    assert terminal["aborted"] is True
    assert submissions == 0
    assert await db.fetch_one(
        "SELECT id FROM ai_agent_runs WHERE binding_command_id = ?",
        [request_id],
    ) is None


async def test_response_start_disconnect_keeps_claimed_request_owned(
    receipt_app,
    monkeypatch,
):
    app, db = receipt_app
    request_id = "chat-disconnect-before-start"
    execution_owned = asyncio.Event()
    finish_prepare = asyncio.Event()

    async def fake_stream_composed_agent(**kwargs):
        lifecycle = kwargs["run_binding_lifecycle"]
        await lifecycle.validate()
        execution_owned.set()
        await finish_prepare.wait()
        await lifecycle.before_submit()
        run_id = await create_run(
            db,
            session_id=7,
            prompt="disconnect",
            mode="agent",
            binding=RunBinding(
                namespace="writing.chat.request",
                aggregate_id="7",
                command_id=request_id,
            ),
        )
        await lifecycle.on_run_started(run_id)
        if False:
            yield {}

    monkeypatch.setattr(
        ai_routes,
        "_stream_composed_agent",
        fake_stream_composed_agent,
    )
    await request_json(
        app,
        method="PUT",
        path=f"/api/ai/chat/requests/{request_id}",
        json_body=request_body(request_id),
    )
    stream = start_asgi_request(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=request_body(request_id),
    )
    stream.fail_next_response_start()

    await execution_owned.wait()
    finish_prepare.set()
    for _attempt in range(20):
        receipt = await db.fetch_one(
            "SELECT status, run_id FROM ai_writing_chat_requests "
            "WHERE request_id = ?",
            [request_id],
        )
        if receipt["status"] == "run_bound":
            break
        await asyncio.sleep(0)
    await stream.aclose()

    assert receipt["status"] == "run_bound"
    assert receipt["run_id"]
