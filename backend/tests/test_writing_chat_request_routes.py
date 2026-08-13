from __future__ import annotations

import asyncio
import json
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
from schemas.ai import ChatStreamRequest
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


def _lexical(text: str) -> str:
    return json.dumps({
        "root": {
            "children": [{
                "type": "paragraph",
                "children": [{"type": "text", "text": text}],
            }],
        },
    }, ensure_ascii=False)


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


async def test_replan_silently_discards_completed_step_rewrites_in_one_root(
    receipt_app,
    monkeypatch,
):
    app, db = receipt_app
    request_id = "chat-replan-history"
    chapter_text = "弄堂尽头没有雨，只有旧木门被风推得轻响。"
    await db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-1", "重规划历史测试书"],
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES (?, ?, 'writing', ?)",
        ["writing-history", "写作目录", "book-1"],
    )
    await db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, level, sort) VALUES (?, ?, ?, 1, 1)",
        ["chapter-1", "writing-history", "第一章：弄堂"],
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        ["chapter-1", _lexical(chapter_text)],
    )
    planner_round = 0

    async def _planner(_key, messages, _options, _provider, signal=None):
        nonlocal planner_round
        planner_round += 1
        assert signal is not None
        payload = json.loads(messages[1]["content"])
        if planner_round == 1:
            content = {
                "needsTodos": True,
                "title": "深化弄堂氛围",
                "goal": "读取章节后提出氛围策略",
                "todos": [
                    {
                        "id": "inspect-current-chapter",
                        "title": "检查当前章节",
                        "type": "read",
                        "executor": "tool",
                        "expectedTools": ["getChapterContent"],
                        "riskLevel": "read",
                    },
                    {
                        "id": "propose-atmosphere-rewrite",
                        "title": "提出氛围改写",
                        "type": "review",
                        "executor": "model",
                        "expectedTools": [],
                        "riskLevel": "read",
                    },
                ],
            }
        else:
            assert planner_round == 2
            execution = payload["executionState"]
            assert execution["completedSteps"][0]["id"] == (
                "inspect-current-chapter"
            )
            assert chapter_text in json.dumps(
                execution["recentToolObservations"],
                ensure_ascii=False,
            )
            # Approved contract deviation: Core rejects this completed-id
            # rewrite by retaining immutable history, without surfacing a
            # planner validation error to the Writing layer.
            content = {
                "needsTodos": True,
                "title": "深化弄堂氛围",
                "goal": "根据新证据调整未完成策略",
                "todos": [
                    {
                        "id": "inspect-current-chapter",
                        "title": "伪造已完成步骤标题",
                        "type": "review",
                        "executor": "model",
                        "expectedTools": [],
                        "riskLevel": "read",
                    },
                    {
                        "id": "shape-wind-sound-atmosphere",
                        "title": "围绕风声调整弄堂氛围",
                        "type": "review",
                        "executor": "model",
                        "expectedTools": [],
                        "riskLevel": "read",
                    },
                ],
            }
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps(content, ensure_ascii=False),
            },
            "model": "planner-model",
            "finish_reason": "stop",
        }

    runtime_round = 0

    async def _runtime(_key, messages, options, _provider, signal=None):
        nonlocal runtime_round
        runtime_round += 1
        assert signal is not None

        async def _stream():
            if runtime_round == 1:
                assert [
                    item["function"]["name"]
                    for item in options.get("tools", [])
                ] == ["getChapterContent"]
                yield {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call-read-history",
                                "type": "function",
                                "function": {
                                    "name": "getChapterContent",
                                    "arguments": "{}",
                                },
                            }],
                        },
                        "finish_reason": "tool_calls",
                    }],
                }
                return
            assert not options.get("tools")
            yield {
                "choices": [{
                    "delta": {"content": "应围绕风推木门的轻响深化氛围。"},
                    "finish_reason": "stop",
                }],
            }

        return {"stream": _stream(), "model": "route-model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _planner,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _runtime,
    )
    body = request_body(request_id)
    body["messages"] = [{"role": "user", "content": "深化弄堂氛围"}]
    body["currentChapterTitle"] = "第一章：弄堂"
    body.pop("streamId")
    body.pop("requestReceiptVersion")
    frames = [
        chunk
        async for chunk in ai_routes._stream_composed_agent(
            body=ChatStreamRequest.model_validate(body),
            api_key="test-key",
            provider_options={
                "model": "deepseek-v4-flash",
                "baseURL": "https://provider.test/v1/",
                "max_tokens": 2_048,
            },
            signal=asyncio.Event(),
        )
    ]
    plans = [
        frame["payload"]["data"]
        for frame in frames
        if frame.get("kind") == "runtime.event"
        and frame.get("payload", {}).get("eventType") == "run.todos_updated"
    ]
    observed_runs = await db.fetch_all(
        "SELECT id, status, final_response FROM ai_agent_runs"
    )
    assert planner_round == 2
    assert len(plans) >= 2
    assert [
        (
            step["id"],
            step["title"],
            step["type"],
            step["executor"],
            step["status"],
        )
        for step in plans[-1]["steps"]
    ] == [
        (
            "inspect-current-chapter",
            "检查当前章节",
            "read",
            "tool",
            "done",
        ),
        (
            "shape-wind-sound-atmosphere",
            "围绕风声调整弄堂氛围",
            "review",
            "model",
            "running",
        ),
    ]
    assert "伪造已完成步骤标题" not in json.dumps(
        plans,
        ensure_ascii=False,
    )
    root_run_id = observed_runs[0]["id"]
    runs = await db.fetch_all(
        "SELECT id, status, parent_run_id FROM ai_agent_runs"
    )
    assert runs == [{
        "id": root_run_id,
        "status": "done",
        "parent_run_id": None,
    }]


async def test_agent_edit_persists_candidate_receipt_without_applying_article(
    receipt_app,
    monkeypatch,
):
    _app, db = receipt_app
    original_article = _lexical("正式正文：木门在风里轻响。")
    proposed_text = "候选正文：风钻过弄堂，旧木门发出一声轻响。"
    await db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-1", "候选稿边界测试书"],
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES (?, ?, 'writing', ?)",
        ["writing-candidate", "写作目录", "book-1"],
    )
    await db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, level, sort) VALUES (?, ?, ?, 1, 1)",
        ["chapter-1", "writing-candidate", "第一章：弄堂"],
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        ["chapter-1", original_article],
    )

    async def _planner(_key, _messages, _options, _provider, signal=None):
        assert signal is not None
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "needsTodos": True,
                    "title": "深化弄堂氛围",
                    "goal": "提出一版可由用户审阅的章节候选稿",
                    "todos": [{
                        "id": "propose-chapter-edit",
                        "title": "提出章节候选改写",
                        "type": "write",
                        "executor": "tool",
                        "expectedTools": ["editChapterContent"],
                        "riskLevel": "write",
                    }],
                }, ensure_ascii=False),
            },
            "model": "planner-model",
            "finish_reason": "stop",
        }

    runtime_round = 0
    runtime_tools: list[list[str]] = []

    async def _runtime(_key, _messages, options, _provider, signal=None):
        nonlocal runtime_round
        runtime_round += 1
        assert signal is not None
        available = [
            item["function"]["name"]
            for item in options.get("tools", [])
        ]
        runtime_tools.append(available)

        async def _stream():
            if runtime_round == 1:
                assert available == ["getChapterContent"], available
                yield {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call-read-candidate",
                                "type": "function",
                                "function": {
                                    "name": "getChapterContent",
                                    "arguments": "{}",
                                },
                            }],
                        },
                        "finish_reason": "tool_calls",
                    }],
                }
            elif runtime_round == 2:
                assert available == ["editChapterContent"], available
                yield {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call-edit-candidate",
                                "type": "function",
                                "function": {
                                    "name": "editChapterContent",
                                    "arguments": json.dumps({
                                        "chapterId": "chapter-1",
                                        "content": proposed_text,
                                    }, ensure_ascii=False),
                                },
                            }],
                        },
                        "finish_reason": "tool_calls",
                    }],
                }
            else:
                assert available == []
                yield {
                    "choices": [{
                        "delta": {"content": "候选稿已提交，等待用户确认。"},
                        "finish_reason": "stop",
                    }],
                }

        return {"stream": _stream(), "model": "route-model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _planner,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _runtime,
    )
    body = request_body("chat-candidate-first")
    body["messages"] = [{"role": "user", "content": "深化弄堂氛围"}]
    body["currentChapterTitle"] = "第一章：弄堂"
    body.pop("streamId")
    body.pop("requestReceiptVersion")
    chunks = [
        chunk
        async for chunk in ai_routes._stream_composed_agent(
            body=ChatStreamRequest.model_validate(body),
            api_key="test-key",
            provider_options={
                "model": "deepseek-v4-flash",
                "baseURL": "https://provider.test/v1/",
                "max_tokens": 2_048,
            },
            signal=asyncio.Event(),
        )
    ]
    run = await db.fetch_one(
        "SELECT id, status FROM ai_agent_runs ORDER BY create_time DESC LIMIT 1"
    )
    receipt = await db.fetch_one(
        "SELECT tool_name, content, effects_json FROM ai_agent_tool_receipts "
        "WHERE run_id = ? AND tool_call_id = ?",
        [run["id"], "call-edit-candidate"],
    )
    stored_article = await db.fetch_one(
        "SELECT content FROM articles WHERE chapter_id = ?",
        ["chapter-1"],
    )

    assert runtime_round == 3, runtime_tools
    assert chunks[-1]["runResult"]["status"] == "done"
    assert run["status"] == "done"
    assert receipt["tool_name"] == "editChapterContent"
    assert json.loads(receipt["content"]) == {
        "success": True,
        "message": "已向用户提交差异预览，需用户在编辑器接受/拒绝后才会写入正文",
        "chapterId": "chapter-1",
        "pendingUserApproval": True,
    }
    assert json.loads(receipt["effects_json"]) == [{
        "type": "writing.proposed_chapter_diff",
        "payload": {
            "chapterId": "chapter-1",
            "beforeText": "正式正文：木门在风里轻响。",
            "proposedText": proposed_text,
            "source": "ai_tool_edit",
        },
    }]
    assert stored_article == {"content": original_article}


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
    assert first_frames[0] == {
        "requestReceipt": {
            "requestId": request_id,
            "sessionId": 7,
            "status": "run_bound",
            "runId": receipt["run_id"],
            "cancelRequested": False,
            "rejectionCode": None,
            "revision": 3,
        },
    }
    assert first_frames[1]["runId"] == receipt["run_id"]
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
