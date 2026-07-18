from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI

from application.agent_composition import (
    AgentComposition,
    set_agent_composition,
)
from database.connection import DatabaseConnection
from routers.ai import router as ai_router
from tests.support.asgi_sse import (
    ASGIResponse,
    decode_sse_json,
    request_json,
    start_asgi_request,
)


FAILED_MESSAGE = "Agent 计划格式无效，已安全停止。"
BLOCKED_MESSAGE = "Agent 未完成全部计划步骤，已安全停止。"
TERMINAL_KEYS = {
    "agentRunCompleted",
    "agentRunBlocked",
    "agentRunFailed",
    "agentRunCanceled",
}


@pytest_asyncio.fixture
async def composed_app(
    tmp_path: Path,
):
    db = DatabaseConnection(tmp_path)
    await db.init()
    composition = AgentComposition(db)
    set_agent_composition(composition)

    app = FastAPI()
    app.include_router(ai_router, prefix="/api")
    try:
        yield app, composition, db
    finally:
        await composition.shutdown()
        set_agent_composition(None)
        await db.close()


def _chat_request(prompt: str) -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": prompt}],
        "apiKey": "wire-key",
        "baseURL": "https://provider.test/v1/",
        "apiProvider": "openai",
        "options": {"model": "wire-model"},
        "enableAgentTools": True,
        "bookId": "book-wire",
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


def _assert_sse_wire(response: ASGIResponse) -> list[dict[str, Any]]:
    assert response.status_code == 200
    assert response.headers["content-type"].split(";", 1)[0] == "text/event-stream"
    assert response.content.startswith(b"data: ")
    assert re.search(rb"\r?\n\r?\n\Z", response.content)
    return decode_sse_json(response.content)


def _assert_exact_two_item_writing_policy(
    messages: list[dict[str, Any]],
    *,
    user_prompt: str,
    atomic_continuity: bool = False,
) -> str:
    evidence_blocks = [
        message
        for message in messages
        if message.get("role") == "system"
        and "exactReviewItemCount=2" in str(message.get("content") or "")
    ]
    assert len(evidence_blocks) == 1
    policy = evidence_blocks[0]
    assert policy["role"] == "system"
    assert all(
        "context_name" not in message and "untrusted" not in message
        for message in messages
    )
    assert policy["content"].count("exactReviewItemCount=2") == 1
    assert "不得先用总表枚举更多问题" in policy["content"]
    assert "不得在补充、其他或可选项中展开第 N+1 项" in policy["content"]
    assert "材料较短或可能只是片段、梗概、节拍时" in policy["content"]
    assert "完整性目标未知时，把扩写写成条件化" in policy["content"]
    if atomic_continuity:
        assert "atomicContinuityItems=true" in policy["content"]
        assert "地点、钥匙、人物关系等多个维度" in policy["content"]
        assert "以大纲为准和保留正文两种建议" in policy["content"]
        assert "从首次回答起必须严格使用以下四个物理行" in policy["content"]
        assert "N. 【维度名】" in policy["content"]
        assert "严格互逆的 A↔B 替换" in policy["content"]
    else:
        assert "atomicContinuityItems=true" not in policy["content"]

    user_indexes = [
        index
        for index, message in enumerate(messages)
        if message.get("role") == "user"
        and message.get("content") == user_prompt
    ]
    assert len(user_indexes) == 1
    assert messages.index(policy) < user_indexes[0]
    return str(policy["content"])


def _normalize_dynamic_ids(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = deepcopy(events)
    run_ids: dict[str, str] = {}
    approval_ids: dict[str, str] = {}

    def _walk(value: Any, key: str | None = None) -> Any:
        if key == "runId" and isinstance(value, str):
            return run_ids.setdefault(value, f"<run-{len(run_ids) + 1}>")
        if key == "approvalId" and isinstance(value, str):
            return approval_ids.setdefault(
                value,
                f"<approval-{len(approval_ids) + 1}>",
            )
        if isinstance(value, dict):
            return {item_key: _walk(item, item_key) for item_key, item in value.items()}
        if isinstance(value, list):
            return [_walk(item) for item in value]
        return value

    return [_walk(event) for event in normalized]


def _event_name(event: dict[str, Any]) -> str:
    for key in (
        "agentRunStarted",
        "agentRunTodosUpdated",
        "agentRunTodoUpdated",
        "contextBudget",
        "delta",
        "toolCalls",
        "toolApprovalRequired",
        "toolApprovalResolved",
        "agentDelegationCreated",
        "agentDelegationUpdated",
        "toolIndexCompleted",
        "toolResults",
        "agentRunCompleted",
        "agentRunBlocked",
        "agentRunFailed",
        "agentRunCanceled",
        "done",
        "error",
    ):
        if key in event:
            return key
    raise AssertionError(f"unclassified SSE event: {event!r}")


@pytest.mark.asyncio
async def test_composed_parent_streams_live_child_agent_lifecycle(
    composed_app,
    monkeypatch: pytest.MonkeyPatch,
):
    app, composition, _db = composed_app
    planner_calls: list[str] = []
    runtime_calls: list[str] = []

    def _has_child_role_instruction(messages: list[dict[str, Any]]) -> bool:
        return any(
            message.get("role") == "system"
            and "read-only research sub-agent" in str(message.get("content") or "")
            for message in messages
        )

    def _is_child_planning_call(messages: list[dict[str, Any]]) -> bool:
        return any(
            message.get("role") == "user"
            and "核验三条关键证据" in str(message.get("content") or "")
            for message in messages
        )

    async def _planner(_key, messages, _options, _provider, signal=None):
        assert signal is not None
        if _is_child_planning_call(messages):
            planner_calls.append("child")
            content = {
                "needsTodos": False,
                "reason": "the child can answer from supplied context",
            }
        elif "parent" in planner_calls:
            planner_calls.append("parent-replan")
            content = {
                "needsTodos": False,
                "reason": "the delegated evidence is now available",
            }
        else:
            planner_calls.append("parent")
            content = {
                "needsTodos": True,
                "title": "并行研究后综合",
                "goal": "让研究 Agent 提供独立证据",
                "todos": [{
                    "id": "delegate-research",
                    "title": "委派独立研究",
                    "type": "analyze",
                    "executor": "tool",
                    "expectedTools": ["delegateToAgents"],
                    "riskLevel": "write",
                }],
            }
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps(content, ensure_ascii=False),
            },
            "model": "planner-model",
        }

    async def _runtime(_key, messages, options, _provider, signal=None):
        assert signal is not None
        child = _has_child_role_instruction(messages)
        tool_names = [
            item["function"]["name"]
            for item in options.get("tools", [])
        ]

        async def _stream():
            if child:
                runtime_calls.append("child")
                assert tool_names == []
                yield {
                    "choices": [{
                        "delta": {"content": "子 Agent 已核验三条证据。"},
                        "finish_reason": "stop",
                    }],
                }
                return
            if tool_names:
                runtime_calls.append("parent-delegate")
                assert tool_names == ["delegateToAgents"]
                yield {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call-delegate",
                                "type": "function",
                                "function": {
                                    "name": "delegateToAgents",
                                    "arguments": json.dumps({
                                        "delegations": [{
                                            "agentRole": "researcher",
                                            "objective": "核验三条关键证据",
                                            "input": {"scope": "current request"},
                                        }],
                                    }, ensure_ascii=False),
                                },
                            }],
                        },
                        "finish_reason": "tool_calls",
                    }],
                }
                return
            runtime_calls.append("parent-final")
            tool_message = next(
                message for message in reversed(messages)
                if message.get("role") == "tool"
            )
            result = json.loads(tool_message["content"])
            assert result["state"] == "ready"
            assert result["counts"]["done"] == 1
            assert result["results"][0]["summary"] == "子 Agent 已核验三条证据。"
            yield {
                "choices": [{
                    "delta": {"content": "父 Agent 已根据子 Agent 结果完成综合。"},
                    "finish_reason": "stop",
                }],
            }

        return {"stream": _stream(), "model": "wire-model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _planner,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _runtime,
    )

    live = start_asgi_request(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=_chat_request("请先让独立研究者核验，再综合回答。"),
    )
    await live.wait_started()
    response = await live.finish()
    events = _assert_sse_wire(response)

    created = [
        event["agentDelegationCreated"]
        for event in events
        if "agentDelegationCreated" in event
    ]
    updated = [
        event["agentDelegationUpdated"]
        for event in events
        if "agentDelegationUpdated" in event
    ]
    assert len(created) == 1, events
    assert [item["status"] for item in updated] == [
        "claimed",
        "running",
        "done",
    ], updated
    delegation_id = created[0]["delegationId"]
    assert created[0]["agentTitle"] == "研究 Agent"
    assert all(item["delegationId"] == delegation_id for item in updated)
    assert all(item["agentTitle"] == "研究 Agent" for item in updated)
    assert updated[1]["childRunId"]
    assert updated[2]["childRunId"] == updated[1]["childRunId"]
    assert updated[2]["resultSummary"] == "子 Agent 已核验三条证据。"
    assert planner_calls == ["parent", "child", "parent-replan"]
    assert runtime_calls == ["parent-delegate", "child", "parent-final"]
    assert sum(event.get("done") is True for event in events) == 1

    parent_run_id = created[0]["runId"]
    snapshot = await composition.checkpoint_store.load(
        parent_run_id,
        after_event_id=0,
        limit=100,
    )
    assert snapshot is not None
    assert len(snapshot.delegations) == 1
    assert snapshot.delegations[0].status.value == "done"


def _assert_terminal_exclusive(
    events: list[dict[str, Any]],
    *,
    terminal: str,
    result: str,
) -> None:
    terminals = [
        key
        for event in events
        for key in TERMINAL_KEYS
        if key in event
    ]
    assert terminals == [terminal]
    assert sum(event.get("done") is True for event in events) == (result == "done")
    assert sum(bool(event.get("error")) for event in events) == (result == "error")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("book_id", "enable_agent_tools"),
    [
        (None, True),
        ("   ", True),
        ("book-wire", False),
    ],
    ids=["no-book-context", "blank-book-context", "agent-tools-disabled"],
)
async def test_composed_core_handles_unscoped_direct_response_requests(
    composed_app,
    monkeypatch: pytest.MonkeyPatch,
    book_id: str | None,
    enable_agent_tools: bool,
):
    app, _composition, _db = composed_app
    provider_calls = 0

    async def _planner_must_not_run(*_args, **_kwargs):
        raise AssertionError("unscoped ask request must use direct response")

    async def _direct_response(
        _key,
        messages,
        options,
        _provider,
        signal=None,
    ):
        nonlocal provider_calls
        provider_calls += 1
        assert signal is not None
        assert "tools" not in options
        assert "tool_choice" not in options
        assert any(
            message.get("role") == "user"
            and message.get("content") == "请直接解释这个概念，不需要调用工具。"
            for message in messages
        )
        if book_id is None or not str(book_id).strip():
            assert any(
                    message.get("role") == "system"
                and "未绑定任何作品或章节" in str(message.get("content") or "")
                and "不得猜测" in str(message.get("content") or "")
                for message in messages
            )

        async def _stream():
            yield {
                "choices": [{
                    "delta": {"content": "这是一个直接回答。"},
                    "finish_reason": "stop",
                }],
            }

        return {"stream": _stream(), "model": "wire-model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _planner_must_not_run,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _direct_response,
    )
    request_body = _chat_request("请直接解释这个概念，不需要调用工具。")
    request_body["chatAgentMode"] = "ask"
    request_body["enableAgentTools"] = enable_agent_tools
    if book_id is None:
        request_body.pop("bookId")
    else:
        request_body["bookId"] = book_id

    live = start_asgi_request(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=request_body,
    )
    await live.wait_started()
    response = await live.finish()
    events = _normalize_dynamic_ids(_assert_sse_wire(response))

    assert provider_calls == 1, events
    assert [_event_name(event) for event in events] == [
        "agentRunStarted",
        "contextBudget",
        "delta",
        "agentRunCompleted",
        "done",
    ]
    assert events[2] == {"delta": "这是一个直接回答。"}
    _assert_terminal_exclusive(
        events,
        terminal="agentRunCompleted",
        result="done",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("book_id", "enable_agent_tools", "binding_marker"),
    [
        (None, True, "未绑定任何作品或章节"),
        ("book-wire", False, "无法调用工具访问书籍内容"),
    ],
)
async def test_unavailable_current_chapter_can_refuse_without_item_repair(
    composed_app,
    monkeypatch: pytest.MonkeyPatch,
    book_id: str | None,
    enable_agent_tools: bool,
    binding_marker: str,
):
    app, _composition, _db = composed_app
    provider_calls = 0
    refusal = "当前未绑定作品或章节，请先选择章节或提供原文。"

    async def _planner_must_not_run(*_args, **_kwargs):
        raise AssertionError("unscoped request must not invoke the planner")

    async def _refuse(_key, messages, options, _provider, signal=None):
        nonlocal provider_calls
        provider_calls += 1
        assert signal is not None
        assert "tools" not in options
        assert any(
                message.get("role") == "system"
            and binding_marker in str(message.get("content") or "")
            for message in messages
        )
        assert not any(
            "exactReviewItemCount=" in str(message.get("content") or "")
            for message in messages
        )

        async def _stream():
            yield {
                "choices": [{
                    "delta": {"content": refusal},
                    "finish_reason": "stop",
                }],
            }

        return {"stream": _stream(), "model": "wire-model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _planner_must_not_run,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _refuse,
    )
    request_body = _chat_request(
        "读取当前章节，找出两处不一致并给出最小修改建议。"
    )
    request_body["chatAgentMode"] = "ask"
    request_body["enableAgentTools"] = enable_agent_tools
    if book_id is None:
        request_body.pop("bookId")
    else:
        request_body["bookId"] = book_id

    live = start_asgi_request(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=request_body,
    )
    await live.wait_started()
    response = await live.finish()
    events = _assert_sse_wire(response)

    assert provider_calls == 1, events
    assert "".join(str(event.get("delta") or "") for event in events) == refusal
    done_events = [event for event in events if event.get("done") is True]
    assert len(done_events) == 1


@pytest.mark.asyncio
async def test_composed_planning_invalid_has_failed_asgi_sse_snapshot(
    composed_app,
    monkeypatch: pytest.MonkeyPatch,
):
    app, _composition, db = composed_app

    async def _invalid_plan(*_args, **_kwargs):
        return {
            "message": {"role": "assistant", "content": "not-json"},
            "model": "planner-model",
        }

    async def _model_must_not_run(*_args, **_kwargs):
        raise AssertionError("runtime model stream must not start after invalid planning")

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _invalid_plan,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _model_must_not_run,
    )

    live = start_asgi_request(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=_chat_request("请根据全书设定制定一个完整的修改计划"),
    )
    await live.wait_started()
    response = await live.finish()
    raw_events = _assert_sse_wire(response)
    run_id = next(
        event["agentRunStarted"]["runId"]
        for event in raw_events
        if "agentRunStarted" in event
    )
    events = _normalize_dynamic_ids(raw_events)

    assert events == [
        {
            "agentRunStarted": {
                "runId": "<run-1>",
                "status": "running",
                "title": "To-dos",
                "goal": None,
            },
        },
        {
            "agentRunFailed": {
                "runId": "<run-1>",
                "status": "failed",
                "error": "planning_invalid",
            },
        },
        {"error": FAILED_MESSAGE},
    ]
    _assert_terminal_exclusive(events, terminal="agentRunFailed", result="error")
    from infrastructure.persistence.run_store import get_run

    persisted_run = await get_run(db, run_id)
    assert persisted_run is not None
    assert persisted_run["model_provider"] == "openai"
    assert persisted_run["model_name"] == "wire-model"
    assert persisted_run["context_window"] == 200_000
    assert len(persisted_run["endpoint_digest"]) == 64
    assert len(persisted_run["request_profile_digest"]) == 64


@pytest.mark.asyncio
async def test_composed_unfinished_planned_tool_has_blocked_asgi_sse_snapshot(
    composed_app,
    monkeypatch: pytest.MonkeyPatch,
):
    app, composition, _db = composed_app

    async def _planned_read(*_args, **_kwargs):
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "needsTodos": True,
                    "title": "读取人物",
                    "goal": "基于真实设定回答",
                    "todos": [{
                        "id": "read-characters",
                        "title": "读取人物设定",
                        "type": "read",
                        "executor": "tool",
                        "expectedTools": ["getBookCharacters"],
                        "riskLevel": "read",
                    }],
                }, ensure_ascii=False),
            },
            "model": "planner-model",
        }

    model_calls = 0

    async def _skip_planned_tool(
        _key,
        _messages,
        options,
        _provider,
        signal=None,
    ):
        nonlocal model_calls
        model_calls += 1
        assert signal is not None
        assert options.get("tool_choice") is None
        assert [
            item["function"]["name"] for item in options.get("tools", [])
        ] == ["getBookCharacters"]

        async def _stream():
                yield {
                    "choices": [{
                        "delta": {
                            "reasoning_content": "PRIVATE",
                            "content": "未读取设定就直接结束",
                        },
                    "finish_reason": "stop",
                }],
            }

        return {"stream": _stream(), "model": "wire-model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _planned_read,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _skip_planned_tool,
    )
    capability_key = composition.provider_capabilities.key(
        api_provider="openai",
        base_url="https://provider.test/v1",
        model="wire-model",
        thinking_enabled=False,
    )
    composition.provider_capabilities.mark_required_tool_choice_unsupported(
        capability_key
    )

    live = start_asgi_request(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=_chat_request("请先读取全部人物设定再分析主角冲突"),
    )
    await live.wait_started()
    response = await live.finish()
    events = _normalize_dynamic_ids(_assert_sse_wire(response))

    assert model_calls == 2
    assert [_event_name(event) for event in events] == [
        "agentRunStarted",
        "agentRunTodosUpdated",
        "contextBudget",
        "agentRunTodoUpdated",
        "agentRunFailed",
        "error",
    ]
    assert events[1]["agentRunTodosUpdated"]["runId"] == "<run-1>"
    assert events[1]["agentRunTodosUpdated"]["steps"][0]["status"] == "running"
    assert events[3]["agentRunTodoUpdated"] == {
        "runId": "<run-1>",
        "stepId": "read-characters",
        "step": {
            "id": "read-characters",
            "title": "读取人物设定",
            "type": "read",
            "executor": "tool",
            "status": "failed",
            "riskLevel": "read",
            "suggestedTools": ["getBookCharacters"],
            "description": None,
            "resultSummary": None,
            "error": "missing_required_tool_call",
        },
        "status": "failed",
    }
    assert events[4] == {
        "agentRunFailed": {
            "runId": "<run-1>",
            "status": "failed",
            "error": "missing_required_tool_call",
        },
    }
    assert events[5] == {
        "error": "当前计划步骤必须调用工具，但模型未返回结构化调用。",
    }
    assert not any("delta" in event or "thinkingDelta" in event for event in events)
    _assert_terminal_exclusive(events, terminal="agentRunFailed", result="error")


@pytest.mark.asyncio
async def test_composed_read_continuation_keeps_writing_evidence_policy(
    composed_app,
    monkeypatch: pytest.MonkeyPatch,
):
    app, _composition, db = composed_app
    user_prompt = (
        "读取当前章节，给出一个 150 字以内的摘要和两个可改进之处。"
        "只分析，不要保存、删除或修改任何内容。"
    )
    chapter_text = (
        "雨夜，林澈在北门找到一把银色钥匙。她告诉同伴，自己从未见过守门人周砚。"
        "钟楼在午夜敲了十二下，她借火把照亮门锁，随后独自进入城内。"
    )
    await db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-wire", "证据约束测试书"],
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES (?, ?, 'writing', ?)",
        ["writing-wire", "写作目录", "book-wire"],
    )
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title, level, sort) "
        "VALUES (?, ?, ?, 1, 1)",
        ["chapter-wire", "writing-wire", "第一章：北门雨夜"],
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        ["chapter-wire", _lexical(chapter_text)],
    )

    async def _planned_read(*_args, **_kwargs):
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "needsTodos": True,
                    "title": "读取并分析当前章",
                    "goal": "基于原文完成事实摘要和改进建议",
                    "todos": [
                        {
                            "id": "read-current-chapter",
                            "title": "读取当前章",
                            "type": "read",
                            "executor": "tool",
                            "expectedTools": ["getChapterContent"],
                            "riskLevel": "read",
                        },
                        {
                            "id": "summarize-current-chapter",
                            "title": "摘要并提出建议",
                            "type": "review",
                            "executor": "model",
                            "expectedTools": [],
                            "riskLevel": "read",
                        },
                    ],
                }, ensure_ascii=False),
            },
            "model": "planner-model",
        }

    model_round = 0
    continuation_messages: list[dict[str, Any]] = []
    policy_rounds: list[str] = []
    invalid_response = (
        "摘要（150字以内）：雨夜中，林澈在北门找到银色钥匙，告诉同伴自己从未见过"
        "周砚；午夜钟响后，她借火把照亮门锁并独自入城。\n"
        "1. 若希望这段文字独立交代行动动机，可在“独自进入城内”附近补一句直接原因；"
        "若这是梗概或有意留白，可保持现状。\n"
        "2. 若目标是增强雨夜氛围，可在“借火把照亮门锁”附近补一个局部光影细节；"
        "这属于可选增强，并非原文已有事实。"
    )
    repaired_response = (
        "摘要（150字以内）：林澈雨夜找到钥匙，称从未见过周砚，随后独自入城。\n"
        "1. 若希望这段文字独立交代行动动机，可在“独自进入城内”附近补一句直接原因；"
        "若这是梗概或有意留白，可保持现状。\n"
        "2. 若目标是增强雨夜氛围，可在“借火把照亮门锁”附近补一个局部光影细节；"
        "这属于可选增强，并非原文已有事实。"
    )

    async def _model_stream(
        _key,
        messages,
        options,
        _provider,
        signal=None,
    ):
        nonlocal model_round, continuation_messages
        model_round += 1
        assert signal is not None
        policy_rounds.append(_assert_exact_two_item_writing_policy(
            messages,
            user_prompt=user_prompt,
        ))

        async def _stream():
            nonlocal continuation_messages
            if model_round == 1:
                assert [
                    item["function"]["name"]
                    for item in options.get("tools", [])
                ] == ["getChapterContent"]
                yield {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call-read-chapter",
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

            assert options.get("tools") is None
            continuation_messages = list(messages)
            assistant_index, assistant_call = next(
                (index, message)
                for index, message in enumerate(messages)
                if message.get("role") == "assistant"
                and message.get("tool_calls")
            )
            assert assistant_call["tool_calls"][0]["function"]["name"] == (
                "getChapterContent"
            )
            tool_index, tool_message = next(
                (index, message)
                for index, message in enumerate(messages)
                if message.get("role") == "tool"
            )
            policy_index = next(
                index
                for index, message in enumerate(messages)
                if "exactReviewItemCount=2" in str(message.get("content") or "")
            )
            user_index = next(
                index
                for index, message in enumerate(messages)
                if message.get("role") == "user"
                and message.get("content") == user_prompt
            )
            assert policy_index < user_index < assistant_index < tool_index
            tool_result = json.loads(tool_message["content"])
            assert tool_result["plainText"] == chapter_text
            if model_round == 2:
                yield {
                    "choices": [{
                        "delta": {
                            "content": invalid_response,
                        },
                        "finish_reason": "stop",
                    }],
                }
                return

            assert model_round == 3
            assert messages[-2]["role"] == "assistant"
            assert messages[-2]["content"] == invalid_response
            assert messages[-1]["role"] == "system"
            assert "摘要正文按非空白可见字符计数不得超过 150 字" in (
                messages[-1]["content"]
            )
            assert "不得声称未经宿主验证的实际精确字数" in (
                messages[-1]["content"]
            )
            assert "归一化口径为 57 个字符" in messages[-1]["content"]
            assert "内部明显压缩目标约 34 字以内" in messages[-1]["content"]
            assert "摘要只写 1 至 2 句" in messages[-1]["content"]
            assert "不得沿用来源句序逐句轻改" in messages[-1]["content"]
            yield {
                "choices": [{
                    "delta": {
                        "content": repaired_response,
                    },
                    "finish_reason": "stop",
                }],
            }

        return {"stream": _stream(), "model": "wire-model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _planned_read,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _model_stream,
    )
    request_body = _chat_request(user_prompt)
    request_body.update({
        "chapterId": "chapter-wire",
        "currentChapterTitle": "第一章：北门雨夜",
        "writingChapters": [{
            "id": "chapter-wire",
            "title": "第一章：北门雨夜",
        }],
    })

    live = start_asgi_request(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=request_body,
    )
    await live.wait_started()
    response = await live.finish()
    events = _assert_sse_wire(response)

    run_id = next(
        event["agentRunStarted"]["runId"]
        for event in events
        if "agentRunStarted" in event
    )
    assert model_round == 3
    assert len(policy_rounds) == 3
    assert policy_rounds == [policy_rounds[0]] * 3
    assert policy_rounds[0].count("summaryMaxCharacters=150") == 1
    assert "摘要前后不得重复展示或逐句改写完整原文" in policy_rounds[0]
    assert "不得声称摘要实际为某个精确字数" in policy_rounds[0]
    assert continuation_messages
    assert any("toolResults" in event for event in events)
    visible_text = "".join(str(event.get("delta") or "") for event in events)
    assert visible_text == repaired_response
    assert invalid_response not in visible_text
    assert chapter_text not in visible_text
    assert not re.search(r"摘要[（(]\s*[1-9][0-9]*\s*字\s*[）)]", visible_text)
    assert not re.search(r"(?:共|实际|计数为)\s*[1-9][0-9]*\s*字", visible_text)
    assert re.findall(r"(?m)^([1-9][0-9]*)\.\s", visible_text) == ["1", "2"]
    assert visible_text.count("若") >= 3
    assert "情节过于简略" not in visible_text
    assert "缺乏逻辑支撑" not in visible_text
    stored_run = await db.fetch_one(
        "SELECT status, final_response FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )
    assert stored_run == {
        "status": "done",
        "final_response": repaired_response,
    }
    assert events[-1] == {"done": True, "model": "wire-model"}


@pytest.mark.asyncio
async def test_composed_complete_selected_outline_repairs_redundant_read_plan(
    composed_app,
    monkeypatch: pytest.MonkeyPatch,
):
    app, _composition, db = composed_app
    user_prompt = (
        "对照当前章节与关联大纲，找出两处不一致，并给出最小修改建议。"
        "先分析再建议，不要自动写入。"
    )
    chapter_text = (
        "午夜雨夜，林澈找到一把北门银色钥匙。她告诉同伴，自己从未见过守门人周砚。"
        "钟楼在午夜敲了十二下，她借火把照亮门锁，随后独自进入城内。"
    )
    outline_text = (
        "- 本章发生在晴朗清晨。\n"
        "- 林澈使用随身的南门蓝色铜钥匙入城。\n"
        "- 林澈与兄长周砚已合作三年，当天由周砚在门口接应。\n"
        "- 林澈惧怕明火，现场只使用冷光石。"
    )
    await db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-wire", "已注入大纲约束测试书"],
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES (?, ?, 'writing', ?)",
        ["writing-wire", "写作目录", "book-wire"],
    )
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title, level, sort) "
        "VALUES (?, ?, ?, 1, 1)",
        ["chapter-wire", "writing-wire", "第一章：北门雨夜"],
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        ["chapter-wire", _lexical(chapter_text)],
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id, markdown_content) "
        "VALUES (?, ?, 'chapter', ?, ?)",
        ["outline-wire", "第一章情节大纲", "book-wire", outline_text],
    )

    planner_round = 0
    judge_round = 0
    planner_available_tools: list[str] = []

    async def _planned_compare(
        _key,
        messages,
        _options,
        _provider,
        signal=None,
    ):
        nonlocal planner_round, judge_round, planner_available_tools
        if (
            messages
            and messages[0].get("role") == "system"
            and "independent semantic-diff judge" in messages[0].get("content", "")
        ):
            judge_round += 1
            assert _options.get("tools") is None
            assert _options["temperature"] == 0
            judge_payload = json.loads(messages[1]["content"])
            assert all(
                item["outlineValue"] in item["outlineSourceExcerpt"]
                and item["chapterValue"] in item["chapterSourceExcerpt"]
                for item in judge_payload["items"]
            )
            if judge_round == 1:
                assert judge_payload["items"][0]["outlineValue"] == "晴朗清晨"
                assert judge_payload["items"][1]["chapterValue"] == "北门银色钥匙"
                verdict_items = [
                    {
                        "number": 1,
                        "changedDimensions": [
                            {
                                "id": "weather.condition",
                                "outlineEvidence": "晴朗",
                                "chapterEvidence": "雨",
                            },
                            {
                                "id": "time.daypart",
                                "outlineEvidence": "清晨",
                                "chapterEvidence": "午夜",
                            },
                        ],
                        "claimedDimensionMatches": False,
                        "uncertainties": [],
                        "confidence": 0.98,
                    },
                    {
                        "number": 2,
                        "changedDimensions": [
                            {
                                "id": "location.entry",
                                "outlineEvidence": "南门",
                                "chapterEvidence": "北门",
                            },
                            {
                                "id": "prop.color",
                                "outlineEvidence": "蓝色",
                                "chapterEvidence": "银色",
                            },
                        ],
                        "claimedDimensionMatches": False,
                        "uncertainties": [],
                        "confidence": 0.97,
                    },
                ]
            else:
                assert judge_round == 2
                assert judge_payload["items"][0]["outlineValue"] == "清晨"
                verdict_items = [
                    {
                        "number": 1,
                        "changedDimensions": [{
                            "id": "time.daypart",
                            "outlineEvidence": "清晨",
                            "chapterEvidence": "午夜",
                        }],
                        "claimedDimensionMatches": True,
                        "uncertainties": [],
                        "confidence": 0.98,
                    },
                    {
                        "number": 2,
                        "changedDimensions": [{
                            "id": "character.relationship",
                            "outlineEvidence": "合作三年",
                            "chapterEvidence": "从未见过",
                        }],
                        "claimedDimensionMatches": True,
                        "uncertainties": [],
                        "confidence": 0.97,
                    },
                ]
            return {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "schemaVersion": 1,
                        "items": verdict_items,
                    }, ensure_ascii=False),
                },
                "model": "judge-model",
            }
        planner_round += 1
        assert signal is not None
        planner_payload = json.loads(messages[1]["content"])
        planner_available_tools = planner_payload["availableTools"]
        assert "getChapterContent" in planner_available_tools
        assert "queryOutline" not in planner_available_tools

        if planner_round == 1:
            content = {
                "needsTodos": True,
                "title": "对照章节与关联大纲",
                "goal": "读取章节后检查与已选大纲的一致性",
                "todos": [
                    {
                        "id": "read-current-chapter",
                        "title": "读取当前章节",
                        "type": "read",
                        "executor": "tool",
                        "expectedTools": ["getChapterContent"],
                        "riskLevel": "read",
                    },
                    {
                        "id": "reread-selected-outline",
                        "title": "重新读取关联大纲",
                        "type": "read",
                        "executor": "model",
                        "expectedTools": [],
                        "riskLevel": "read",
                    },
                    {
                        "id": "compare-evidence",
                        "title": "比较章节与大纲",
                        "type": "review",
                        "executor": "model",
                        "expectedTools": [],
                        "riskLevel": "read",
                    },
                ],
            }
        elif planner_round == 2:
            assert messages[-1]["role"] == "user"
            assert "read steps are reserved" in messages[-1]["content"]
            content = {
                "needsTodos": True,
                "title": "对照章节与关联大纲",
                "goal": "读取章节后使用已注入大纲检查一致性",
                "todos": [
                    {
                        "id": "read-current-chapter",
                        "title": "读取当前章节",
                        "type": "read",
                        "executor": "tool",
                        "expectedTools": ["getChapterContent"],
                        "riskLevel": "read",
                    },
                    {
                        "id": "compare-evidence",
                        "title": "比较章节与已注入大纲",
                        "type": "review",
                        "executor": "model",
                        "expectedTools": [],
                        "riskLevel": "read",
                    },
                ],
            }
        else:
            assert planner_round == 3
            execution_state = planner_payload["executionState"]
            assert execution_state["revision"] == 1
            assert execution_state["completedSteps"][0]["id"] == (
                "read-current-chapter"
            )
            assert execution_state["recentToolObservations"]
            content = {
                "needsTodos": True,
                "title": "对照章节与关联大纲",
                "goal": "使用已获取章节和已注入大纲完成一致性检查",
                "todos": [{
                    "id": "compare-evidence",
                    "title": "比较章节与已注入大纲",
                    "type": "review",
                    "executor": "model",
                    "expectedTools": [],
                    "riskLevel": "read",
                }],
            }
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps(content, ensure_ascii=False),
            },
            "model": "planner-model",
        }

    runtime_round = 0
    final_messages: list[dict[str, Any]] = []
    policy_rounds: list[str] = []
    invalid_response = (
        "1. 【环境变化】\n"
        "- 证据：大纲“晴朗清晨”；正文“午夜雨夜”。\n"
        "- 若以大纲为准：仅把正文的“午夜雨夜”替换为“晴朗清晨”。\n"
        "- 若保留正文：仅把大纲的“晴朗清晨”替换为“午夜雨夜”。\n"
        "2. 【入城条件】\n"
        "- 证据：大纲“南门蓝色铜钥匙”；正文“北门银色钥匙”。\n"
        "- 若以大纲为准：仅把正文的“北门银色钥匙”替换为“南门蓝色铜钥匙”。\n"
        "- 若保留正文：仅把大纲的“南门蓝色铜钥匙”替换为“北门银色钥匙”。"
    )
    repaired_response = (
        "1. 【时间】\n"
        "- 证据：大纲“清晨”；正文“午夜”。\n"
        "- 若以大纲为准：仅把正文的“午夜”替换为“清晨”。\n"
        "- 若保留正文：仅把大纲的“清晨”替换为“午夜”。\n"
        "2. 【人物关系】\n"
        "- 证据：大纲“合作三年”；正文“从未见过”。\n"
        "- 若以大纲为准：仅把正文的“从未见过”替换为“合作三年”。\n"
        "- 若保留正文：仅把大纲的“合作三年”替换为“从未见过”。"
    )

    async def _model_stream(
        _key,
        messages,
        options,
        _provider,
        signal=None,
    ):
        nonlocal runtime_round, final_messages
        runtime_round += 1
        assert signal is not None
        policy_rounds.append(_assert_exact_two_item_writing_policy(
            messages,
            user_prompt=user_prompt,
            atomic_continuity=True,
        ))

        async def _stream():
            nonlocal final_messages
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
                                "id": "call-read-chapter-for-compare",
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

            assert options.get("tools") is None
            if runtime_round == 2:
                assistant_index = next(
                    index
                    for index, message in enumerate(messages)
                    if message.get("role") == "assistant"
                    and message.get("tool_calls")
                )
                tool_index = next(
                    index
                    for index, message in enumerate(messages)
                    if message.get("role") == "tool"
                )
                policy_index = next(
                    index
                    for index, message in enumerate(messages)
                    if "exactReviewItemCount=2" in str(message.get("content") or "")
                )
                user_index = next(
                    index
                    for index, message in enumerate(messages)
                    if message.get("role") == "user"
                    and message.get("content") == user_prompt
                )
                assert policy_index < user_index < assistant_index < tool_index
                yield {
                    "choices": [{
                        "delta": {"content": invalid_response},
                        "finish_reason": "stop",
                    }],
                }
                return

            assert runtime_round == 3
            final_messages = list(messages)
            yield {
                "choices": [{
                    "delta": {"content": repaired_response},
                    "finish_reason": "stop",
                }],
            }

        return {"stream": _stream(), "model": "wire-model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _planned_compare,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _model_stream,
    )
    request_body = _chat_request(user_prompt)
    request_body.update({
        "chapterId": "chapter-wire",
        "currentChapterTitle": "第一章：北门雨夜",
        "writingChapters": [{
            "id": "chapter-wire",
            "title": "第一章：北门雨夜",
        }],
        "associatedOutlineIds": ["outline-wire"],
        "availableOutlines": [
            {"id": "outline-wire", "title": "第一章情节大纲"},
        ],
    })

    live = start_asgi_request(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=request_body,
    )
    await live.wait_started()
    response = await live.finish()
    raw_events = _assert_sse_wire(response)
    run_id = next(
        event["agentRunStarted"]["runId"]
        for event in raw_events
        if "agentRunStarted" in event
    )
    assert planner_round == 3
    assert judge_round == 2
    assert final_messages[-2]["role"] == "assistant"
    assert final_messages[-2]["content"] == invalid_response
    assert final_messages[-1]["role"] == "system"
    repair_guidance = final_messages[-1]["content"]
    assert "独立语义评审未通过" in repair_guidance
    assert "第1项实际改变了零个或多个独立维度" in repair_guidance
    assert "第2项实际改变了零个或多个独立维度" in repair_guidance
    assert "恰好 2 个四行检查项" in repair_guidance
    assert "复合表达中未变化的属性" in repair_guidance
    assert "标题必须准确命名唯一变化" in repair_guidance
    assert "两个修改方向只做同一个 A↔B 替换" in repair_guidance
    assert runtime_round == 3
    trace_rows = await db.fetch_all(
        "SELECT payload_json FROM ai_agent_run_events "
        "WHERE run_id = ? AND event_type = 'agentRunTrace' ORDER BY id ASC",
        [run_id],
    )
    judge_traces = [
        json.loads(row["payload_json"])
        for row in trace_rows
        if "response_judge_" in row["payload_json"]
    ]
    assert [trace["outcome"] for trace in judge_traces] == [
        "response_judge_rejected",
        "response_judge_passed",
    ]
    assert all(trace["details"]["judgeIndex"] == 0 for trace in judge_traces)
    assert all("durationMs" in trace for trace in judge_traces)
    assert invalid_response not in json.dumps(judge_traces, ensure_ascii=False)
    assert len(policy_rounds) == 3
    assert policy_rounds == [policy_rounds[0]] * 3
    assert planner_available_tools and "queryOutline" not in planner_available_tools
    assert final_messages
    retrieval_message = next(
        message for message in final_messages
        if "本章发生在晴朗清晨" in str(message.get("content") or "")
    )
    assert "本章发生在晴朗清晨" in retrieval_message["content"]
    assert "南门蓝色铜钥匙入城" in retrieval_message["content"]
    assert "林澈与兄长周砚已合作三年" in retrieval_message["content"]
    final_tool_message = next(
        message for message in reversed(final_messages)
        if message.get("role") == "tool"
    )
    assert json.loads(final_tool_message["content"])["plainText"] == chapter_text
    tool_calls = [
        call
        for event in raw_events
        for call in event.get("toolCalls", [])
    ]
    tool_results = [
        result
        for event in raw_events
        for result in event.get("toolResults", [])
    ]
    assert [call["function"]["name"] for call in tool_calls] == [
        "getChapterContent",
    ]
    assert [result["name"] for result in tool_results] == [
        "getChapterContent",
    ]
    todo_steps = next(
        event["agentRunTodosUpdated"]["steps"]
        for event in raw_events
        if "agentRunTodosUpdated" in event
    )
    assert [step["suggestedTools"] for step in todo_steps] == [
        ["getChapterContent"],
        [],
    ]
    assert "queryOutline" not in json.dumps(
        [tool_calls, tool_results, todo_steps],
        ensure_ascii=False,
    )
    stored_todos = await db.fetch_all(
        "SELECT step_id, status FROM ai_agent_run_todos "
        "WHERE run_id = ? ORDER BY sort ASC",
        [run_id],
    )
    assert stored_todos == [
        {"step_id": "read-current-chapter", "status": "done"},
        {"step_id": "compare-evidence", "status": "done"},
    ]
    visible_text = "".join(
        str(event.get("delta") or "")
        for event in raw_events
    )
    assert visible_text == repaired_response
    assert "违规合并草稿" not in visible_text
    assert re.findall(r"(?m)^([1-9][0-9]*)\.\s", visible_text) == ["1", "2"]
    first_item, second_item = visible_text.split("\n2. ", 1)
    assert "天气" not in first_item
    assert "地点" not in first_item
    assert "钥匙" not in first_item
    assert "接应" not in second_item
    assert "南门" not in second_item
    assert "钥匙" not in second_item
    stored_run = await db.fetch_one(
        "SELECT status, final_response FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )
    assert stored_run == {
        "status": "done",
        "final_response": repaired_response,
    }
    assert raw_events[-1] == {"done": True, "model": "wire-model"}


@pytest.mark.asyncio
async def test_composed_reject_uses_real_http_endpoint_and_replay_fails(
    composed_app,
    monkeypatch: pytest.MonkeyPatch,
):
    app, _composition, db = composed_app
    model_round = 0
    continuation_tool_result: dict[str, Any] | None = None
    await db.execute(
        "INSERT INTO characters (id, book_id, name) VALUES (?, ?, ?)",
        [7, "book-wire", "拒绝后保留的人物"],
    )

    async def _planned_delete(*_args, **_kwargs):
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "needsTodos": True,
                    "title": "安全删除人物",
                    "goal": "经用户确认后删除并报告结果",
                    "todos": [
                        {
                            "id": "delete-character",
                            "title": "删除人物",
                            "type": "write",
                            "executor": "tool",
                            "expectedTools": ["deleteCharacter"],
                            "riskLevel": "destructive",
                        },
                        {
                            "id": "report-result",
                            "title": "报告审批结果",
                            "type": "review",
                            "executor": "model",
                            "expectedTools": [],
                            "riskLevel": "read",
                        },
                    ],
                }, ensure_ascii=False),
            },
            "model": "planner-model",
        }

    async def _model_stream(
        _key,
        messages,
        options,
        _provider,
        signal=None,
    ):
        nonlocal model_round, continuation_tool_result
        model_round += 1
        assert signal is not None
        assert bool(options.get("tools")) is (model_round == 1)

        async def _stream():
            nonlocal continuation_tool_result
            if model_round == 1:
                yield {
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call-delete",
                                "type": "function",
                                "function": {
                                    "name": "deleteCharacter",
                                    "arguments": '{"characterId":7}',
                                },
                            }],
                        },
                        "finish_reason": "tool_calls",
                    }],
                }
                return

            tool_message = next(
                message for message in reversed(messages)
                if message.get("role") == "tool"
            )
            continuation_tool_result = json.loads(tool_message["content"])
            if model_round == 2:
                guidance = messages[-1]
                assert guidance["role"] == "system"
                assert "user rejected" in guidance["content"]
                yield {
                    "choices": [{
                        "delta": {"content": "<tool_"},
                        "finish_reason": None,
                    }],
                }
                yield {
                    "choices": [{
                        "delta": {
                            "content": (
                                "call>\n<function=deleteCharacter>\n"
                                "<parameter=characterId>7</parameter>\n"
                                "</function>\n</tool_call>"
                            ),
                        },
                        "finish_reason": "stop",
                    }],
                }
                return

            guidance = messages[-1]
            assert guidance["role"] == "system"
            assert "not shown to the user" in guidance["content"]
            yield {
                "choices": [{
                    "delta": {
                        "reasoning_content": (
                            "正在删除并重试 <tool_call><function=deleteCharacter>"
                        ),
                        "content": (
                            "已找到人物，正在删除。删除失败，请检查权限或联系管理员。"
                        ),
                    },
                    "finish_reason": "stop",
                }],
            }

        return {"stream": _stream(), "model": "wire-model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_no_stream",
        _planned_delete,
    )
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _model_stream,
    )

    live = start_asgi_request(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=_chat_request("请安全删除这个人物"),
    )
    await live.wait_started()
    while True:
        event = await live.next_sse_json()
        approval = event.get("toolApprovalRequired")
        if approval:
            break

    approval_id = approval["approvalId"]
    resolved = await request_json(
        app,
        method="POST",
        path=f"/api/ai/tool-approvals/{approval_id}",
        json_body={"approved": False},
    )
    assert resolved.status_code == 200
    assert resolved.headers["content-type"].startswith("application/json")
    assert resolved.json() == {
        "success": True,
        "data": {"status": "rejected"},
    }

    replay = await request_json(
        app,
        method="POST",
        path=f"/api/ai/tool-approvals/{approval_id}",
        json_body={"approved": False},
    )
    assert replay.status_code == 200
    assert replay.json() == {
        "success": False,
        "error": "确认请求不存在、已过期或已被处理。",
    }

    response = await live.finish()
    raw_events = _assert_sse_wire(response)
    run_id = next(
        event["agentRunStarted"]["runId"]
        for event in raw_events
        if "agentRunStarted" in event
    )
    events = _normalize_dynamic_ids(raw_events)
    names = [_event_name(event) for event in events]

    assert names == [
        "agentRunStarted",
        "agentRunTodosUpdated",
        "contextBudget",
        "toolCalls",
        "toolApprovalRequired",
        "toolApprovalResolved",
        "toolIndexCompleted",
        "toolResults",
        "agentRunTodoUpdated",
        "agentRunTodoUpdated",
        "delta",
        "agentRunTodoUpdated",
        "agentRunCompleted",
        "done",
    ]
    requested = events[4]["toolApprovalRequired"]
    resolved_event = events[5]["toolApprovalResolved"]
    assert requested["runId"] == resolved_event["runId"] == "<run-1>"
    assert requested["approvalId"] == resolved_event["approvalId"] == "<approval-1>"
    assert requested["toolName"] == resolved_event["toolName"] == "deleteCharacter"
    assert resolved_event["status"] == "rejected"
    tool_result = json.loads(events[7]["toolResults"][0]["content"])
    assert tool_result["success"] is False
    assert tool_result["errorCode"] == "approval_rejected"
    assert continuation_tool_result == tool_result
    declined_todo = events[8]["agentRunTodoUpdated"]
    assert declined_todo["stepId"] == "delete-character"
    assert declined_todo["step"]["status"] == "blocked"
    assert declined_todo["step"]["resultSummary"] == (
        "User declined approval; the planned tool was not executed."
    )
    assert declined_todo["step"]["error"] == "approval_rejected"
    assert "Planned tool step completed." not in str(declined_todo)
    assert events[9]["agentRunTodoUpdated"]["stepId"] == "report-result"
    assert events[9]["agentRunTodoUpdated"]["step"]["status"] == "running"
    assert events[10] == {
        "delta": "您已拒绝审批；操作未执行，相关数据仍保留。",
    }
    assert events[11]["agentRunTodoUpdated"]["stepId"] == "report-result"
    assert events[11]["agentRunTodoUpdated"]["step"]["status"] == "done"
    delete_statuses = [
        event["agentRunTodoUpdated"]["step"]["status"]
        for event in events
        if event.get("agentRunTodoUpdated", {}).get("stepId")
        == "delete-character"
    ]
    assert delete_statuses == ["blocked"]
    visible_text = "".join(
        str(event.get("delta") or "") for event in events
    )
    assert "<tool_call" not in visible_text
    assert "<function=" not in visible_text
    assert "<parameter=" not in visible_text
    assert "deleteCharacter" not in visible_text
    assert "正在删除" not in visible_text
    assert "删除失败" not in visible_text
    assert "权限" not in visible_text
    assert "联系管理员" not in visible_text
    assert not any("thinkingDelta" in event for event in events)
    assert events[-1] == {"done": True, "model": "wire-model"}
    assert model_round == 3
    character = await db.fetch_one(
        "SELECT id FROM characters WHERE id = ? AND book_id = ?",
        [7, "book-wire"],
    )
    assert character == {"id": 7}
    stored_run = await db.fetch_one(
        "SELECT status, final_response FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )
    assert stored_run == {
        "status": "done",
        "final_response": "您已拒绝审批；操作未执行，相关数据仍保留。",
    }
    stored_todos = await db.fetch_all(
        "SELECT step_id, status, result_summary, error "
        "FROM ai_agent_run_todos WHERE run_id = ? ORDER BY sort ASC",
        [run_id],
    )
    assert stored_todos == [
        {
            "step_id": "delete-character",
            "status": "blocked",
            "result_summary": (
                "User declined approval; the planned tool was not executed."
            ),
            "error": "approval_rejected",
        },
        {
            "step_id": "report-result",
            "status": "done",
            "result_summary": "Final response covered this model step.",
            "error": None,
        },
    ]
    _assert_terminal_exclusive(events, terminal="agentRunCompleted", result="done")


@pytest.mark.asyncio
async def test_composed_disconnect_wins_before_late_approval_resolve(
    composed_app,
    monkeypatch: pytest.MonkeyPatch,
):
    app, composition, db = composed_app
    from infrastructure.writing.tools.handlers import character_tools

    await db.execute(
        "INSERT INTO characters (book_id, name) VALUES (?, ?)",
        ["book-wire", "不可删除的人物"],
    )
    character = await db.fetch_one(
        "SELECT id, book_id, name FROM characters WHERE book_id = ?",
        ["book-wire"],
    )
    assert character is not None
    character_id = int(character["id"])

    delete_calls: list[int] = []
    original_delete = character_tools.delete_character

    async def _record_delete(target_db, target_id):
        delete_calls.append(int(target_id))
        await original_delete(target_db, target_id)

    monkeypatch.setattr(character_tools, "delete_character", _record_delete)
    model_round = 0

    async def _request_dangerous_tool(
        _key,
        _messages,
        _options,
        _provider,
        signal=None,
    ):
        nonlocal model_round
        model_round += 1
        assert signal is not None

        async def _stream():
            yield {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": "call-disconnect-delete",
                            "type": "function",
                            "function": {
                                "name": "deleteCharacter",
                                "arguments": json.dumps({
                                    "characterId": character_id,
                                }),
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
            }

        return {"stream": _stream(), "model": "wire-model"}

    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _request_dangerous_tool,
    )

    live = start_asgi_request(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=_chat_request("删除人物"),
    )
    await live.wait_started()
    while True:
        event = await live.next_sse_json()
        approval = event.get("toolApprovalRequired")
        if approval:
            break

    approval_id = str(approval["approvalId"])
    run_id = str(approval["runId"])
    assert composition._approval_gateway.pending_count(run_id) == 1
    assert composition._approval_runs == {approval_id: run_id}

    await live.disconnect()
    disconnected = await live.wait_closed(timeout=1.0)
    assert disconnected.status_code == 200
    assert disconnected.headers["content-type"].startswith("text/event-stream")

    # Waiting for the stream task first makes this a deterministic
    # disconnect-wins ordering rather than a scheduler-dependent race.
    assert composition._approval_gateway.pending_count(run_id) == 0
    assert composition._approval_runs == {}
    late_resolve = await request_json(
        app,
        method="POST",
        path=f"/api/ai/tool-approvals/{approval_id}",
        json_body={"approved": True},
    )
    assert late_resolve.status_code == 200
    assert late_resolve.json() == {
        "success": False,
        "error": "确认请求不存在、已过期或已被处理。",
    }

    assert delete_calls == []
    assert model_round == 1
    persisted_character = await db.fetch_one(
        "SELECT id FROM characters WHERE id = ? AND book_id = ?",
        [character_id, "book-wire"],
    )
    assert persisted_character is not None
    run = await db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )
    assert run is not None and run["status"] == "canceled"
    terminal_events = await db.fetch_all(
        "SELECT event_type FROM ai_agent_run_events "
        "WHERE run_id = ? AND event_type IN (?, ?, ?, ?)",
        [
            run_id,
            "run.completed",
            "run.blocked",
            "run.failed",
            "run.canceled",
        ],
    )
    assert [item["event_type"] for item in terminal_events] == ["run.canceled"]

    # Once disconnect is observed the transport wrapper drains Core cleanup
    # without attempting another socket write. Only pre-disconnect frames are
    # visible; the canceled terminal remains durable in SQLite.
    delivered_events = _normalize_dynamic_ids(
        decode_sse_json(disconnected.content)
    )
    assert [_event_name(item) for item in delivered_events] == [
        "agentRunStarted",
        "contextBudget",
        "toolCalls",
        "toolApprovalRequired",
    ]
    assert not any(TERMINAL_KEYS.intersection(item) for item in delivered_events)
    assert not any(item.get("done") or item.get("error") for item in delivered_events)


@pytest.mark.asyncio
async def test_composed_send_side_disconnect_cleans_pending_approval_and_run(
    composed_app,
    monkeypatch: pytest.MonkeyPatch,
):
    app, composition, db = composed_app
    from infrastructure.writing.tools.handlers import character_tools
    from routers.ai import _AgentEventSourceResponse

    await db.execute(
        "INSERT INTO characters (book_id, name) VALUES (?, ?)",
        ["book-wire", "send-side-disconnect"],
    )
    character = await db.fetch_one(
        "SELECT id FROM characters WHERE book_id = ?",
        ["book-wire"],
    )
    assert character is not None
    character_id = int(character["id"])
    delete_calls: list[int] = []
    original_delete = character_tools.delete_character

    async def _record_delete(target_db, target_id):
        delete_calls.append(int(target_id))
        await original_delete(target_db, target_id)

    async def _request_dangerous_tool(
        _key,
        _messages,
        _options,
        _provider,
        signal=None,
    ):
        assert signal is not None

        async def _stream():
            yield {
                "choices": [{
                    "delta": {
                        "tool_calls": [{
                            "index": 0,
                            "id": "call-send-side-delete",
                            "type": "function",
                            "function": {
                                "name": "deleteCharacter",
                                "arguments": json.dumps({
                                    "characterId": character_id,
                                }),
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
            }

        return {"stream": _stream(), "model": "wire-model"}

    monkeypatch.setattr(character_tools, "delete_character", _record_delete)
    monkeypatch.setattr(
        "infrastructure.models.provider_router.create_chat_stream",
        _request_dangerous_tool,
    )
    monkeypatch.setattr(
        _AgentEventSourceResponse,
        "DEFAULT_PING_INTERVAL",
        0.01,
    )

    live = start_asgi_request(
        app,
        method="POST",
        path="/api/ai/chat/stream",
        json_body=_chat_request("删除人物"),
    )
    await live.wait_started()
    while True:
        event = await live.next_sse_json()
        approval = event.get("toolApprovalRequired")
        if approval:
            break

    approval_id = str(approval["approvalId"])
    run_id = str(approval["runId"])
    assert composition._approval_gateway.pending_count(run_id) == 1
    live.fail_next_body_send()

    response = await live.wait_closed(timeout=1)

    assert response.status_code == 200
    assert composition._approval_gateway.pending_count(run_id) == 0
    assert composition._approval_runs == {}
    assert delete_calls == []
    assert await db.fetch_one(
        "SELECT id FROM characters WHERE id = ?",
        [character_id],
    ) == {"id": character_id}
    run = await db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )
    assert run == {"status": "canceled"}
    terminal_events = await db.fetch_all(
        "SELECT event_type FROM ai_agent_run_events "
        "WHERE run_id = ? AND event_type IN (?, ?, ?, ?)",
        [
            run_id,
            "run.completed",
            "run.blocked",
            "run.failed",
            "run.canceled",
        ],
    )
    assert terminal_events == [{"event_type": "run.canceled"}]
    stale = await request_json(
        app,
        method="POST",
        path=f"/api/ai/tool-approvals/{approval_id}",
        json_body={"approved": True},
    )
    assert stale.json()["success"] is False
