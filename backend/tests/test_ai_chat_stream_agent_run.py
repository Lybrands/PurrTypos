from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db
from routers.ai import chat_stream
from schemas.ai import ChatStreamRequest

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def reset_provider_capability_cache():
    from services.provider_capability_cache import clear_provider_capability_cache

    clear_provider_capability_cache()
    try:
        yield
    finally:
        clear_provider_capability_cache()


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        yield db
    finally:
        await db.close()


class _Request:
    async def is_disconnected(self) -> bool:
        return False


class _DisconnectedRequest:
    async def is_disconnected(self) -> bool:
        return True


async def _collect_sse_events(response, limit: int = 20) -> list[dict]:
    events: list[dict] = []
    async for raw in response.body_iterator:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        stripped = text.strip()
        if stripped.startswith("{"):
            events.append(json.loads(stripped))
            if len(events) >= limit or any(evt.get("done") for evt in events):
                break
            continue
        for line in text.splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[len("data: "):].strip()
            if payload and payload != "[DONE]":
                events.append(json.loads(payload))
        if len(events) >= limit or any(evt.get("done") for evt in events):
            break
    return events


async def test_tool_round_streams_approval_event_before_tool_task_finishes(
    monkeypatch: pytest.MonkeyPatch,
):
    """Regression: a waiting approval must not be buffered until completion."""
    from routers.ai import _run_agent_tool_round

    release_tool = asyncio.Event()

    async def _run_tools(_calls, _ctx, send_chunk, signal):
        send_chunk({"toolApprovalRequired": {"approvalId": "a1"}})
        await release_tool.wait()
        return [{"tool_call_id": "call-1", "content": '{"success": true}'}]

    monkeypatch.setattr("services.tool_executor.run_tools", _run_tools)
    round_stream = _run_agent_tool_round(
        [{"id": "call-1", "function": {"name": "deleteCharacter", "arguments": "{}"}}],
        {},
        "",
        "",
    )

    progress, continuation = await anext(round_stream)
    assert progress == {"toolApprovalRequired": {"approvalId": "a1"}}
    assert continuation is None

    release_tool.set()
    final_event, continuation = await anext(round_stream)
    assert "toolResults" in final_event
    assert continuation is not None


async def test_chat_stream_emits_agent_run_started_before_done(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    def _skills():
        return [
            {
                "name": "getChapterContent",
                "description": "读取章节",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "searchMemories",
                "description": "search memories",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        ]

    monkeypatch.setattr("services.tool_router.get_api_skill_items", _skills)

    async def _plan(**_kwargs):
        return {
            "title": "检查章节",
            "steps": [
                {
                    "id": "read",
                    "title": "读取章节",
                    "type": "read",
                    "executor": "tool",
                    "suggestedTools": ["getChapterContent"],
                },
                {"id": "answer", "title": "生成建议", "type": "review", "executor": "model"},
                {
                    "id": "later-memory",
                    "title": "后续检查记忆",
                    "type": "read",
                    "executor": "tool",
                    "suggestedTools": ["searchMemories"],
                },
            ],
        }

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _plan)

    async def _stream():
        yield {
            "choices": [
                {
                    "delta": {"content": "完成"},
                    "finish_reason": "stop",
                }
            ]
        }

    captured_params: list[dict] = []

    async def _create_chat_stream(*_args, **_kwargs):
        captured_params.append(_args[2])
        return {"stream": _stream(), "model": "mock-model"}

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "帮我检查前三章节奏并给出修改建议"}],
            apiKey="k",
            options={"model": "mock-model"},
            enableAgentTools=True,
            bookId="book1",
            chatAgentMode="agent",
        ),
        _Request(),
    )

    events = await _collect_sse_events(response)
    event_keys = [next(iter(evt.keys())) for evt in events]

    assert "agentRunStarted" in event_keys
    assert "done" not in event_keys
    assert "error" in event_keys
    assert "结构化工具调用" in events[-1]["error"]
    budget = next(evt["contextBudget"] for evt in events if "contextBudget" in evt)
    assert budget["projectedTotalTokens"] <= budget["windowTokens"]
    sent_tool_names = {
        tool["function"]["name"]
        for tool in captured_params[0].get("tools", [])
    }
    assert sent_tool_names == {"getChapterContent"}
    assert captured_params[0]["tool_choice"] == "required"
    run_id = next(evt["agentRunStarted"]["runId"] for evt in events if "agentRunStarted" in evt)
    from services.agent_run_store import get_run_events

    persisted = await get_run_events(temp_db, run_id)
    trace_stages = {
        event["payload"].get("stage")
        for event in persisted
        if event["eventType"] == "agentRunTrace"
    }
    assert {"planner", "context_budget", "model_round", "terminal"}.issubset(trace_stages)


async def test_chat_stream_emits_model_planned_todos(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    def _skills():
        return [
            {
                "name": "getChapterContent",
                "description": "读取章节",
                "parameters": {"type": "object", "properties": {}, "required": []},
            }
        ]

    monkeypatch.setattr("services.tool_router.get_api_skill_items", _skills)

    async def _plan(**_kwargs):
        return {
            "title": "检查节奏",
            "steps": [
                {
                    "id": "read-context",
                    "title": "读取章节上下文",
                    "type": "read",
                    "executor": "tool",
                    "suggestedTools": ["getChapterContent"],
                },
                {"id": "review", "title": "分析节奏问题", "type": "analyze", "executor": "model"},
            ],
        }

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _plan)

    async def _stream():
        yield {
            "choices": [
                {
                    "delta": {
                        "content": "开始继续"
                    },
                    "finish_reason": None,
                }
            ]
        }
        yield {
            "choices": [
                {
                    "delta": {"content": "完成"},
                    "finish_reason": "stop",
                }
            ]
        }

    async def _create_chat_stream(*_args, **_kwargs):
        return {"stream": _stream(), "model": "mock-model"}

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "帮我检查前三章节奏并给出修改建议"}],
            apiKey="k",
            options={"model": "mock-model"},
            enableAgentTools=True,
            bookId="book1",
            chatAgentMode="agent",
        ),
        _Request(),
    )

    events = await _collect_sse_events(response)
    deltas = "".join(evt.get("delta", "") for evt in events)
    todos_event = next(evt["agentRunTodosUpdated"] for evt in events if "agentRunTodosUpdated" in evt)

    assert deltas == ""
    assert "结构化工具调用" in events[-1]["error"]
    assert [step["title"] for step in todos_event["steps"]] == ["读取章节上下文", "分析节奏问题"]
    assert [step["status"] for step in todos_event["steps"]] == ["running", "pending"]


@pytest.mark.parametrize("core_runtime", [False, True], ids=["legacy", "core"])
async def test_chat_stream_reports_upstream_read_error_without_raw_traceback(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
    core_runtime: bool,
):
    monkeypatch.setattr("config.AGENT_CORE_RUNTIME_ENABLED", core_runtime)
    async def _stream():
        yield {
            "choices": [
                {
                    "delta": {"content": "已输出一部分"},
                    "finish_reason": None,
                }
            ]
        }
        raise httpx.ReadError("stream interrupted")

    async def _create_chat_stream(*_args, **_kwargs):
        return {"stream": _stream(), "model": "mock-model"}

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "继续写"}],
            apiKey="k",
            options={"model": "mock-model"},
            enableAgentTools=False,
            bookId="book1",
            chatAgentMode="ask",
        ),
        _Request(),
    )

    events = await _collect_sse_events(response)

    first_delta = next(evt["delta"] for evt in events if "delta" in evt)
    assert first_delta == "已输出一部分"
    assert "模型服务流式响应中断" in events[-1]["error"]
    assert "ReadError" not in events[-1]["error"]


@pytest.mark.parametrize("core_runtime", [False, True], ids=["legacy", "core"])
async def test_agent_stream_interruption_persists_failed_terminal_trace(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
    core_runtime: bool,
):
    monkeypatch.setattr("config.AGENT_CORE_RUNTIME_ENABLED", core_runtime)
    monkeypatch.setattr("services.tool_router.get_api_skill_items", lambda: [])

    async def _plan(**_kwargs):
        return {
            "title": "answer",
            "steps": [
                {"id": "answer", "title": "answer", "type": "review", "executor": "model"},
            ],
        }

    async def _stream():
        yield {"choices": [{"delta": {"content": "partial"}, "finish_reason": None}]}
        raise httpx.ReadError("socket reset")

    async def _create_chat_stream(*_args, **_kwargs):
        return {"stream": _stream(), "model": "mock"}

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _plan)
    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "answer with agent trace"}],
            apiKey="k",
            options={"model": "mock"},
            enableAgentTools=True,
            bookId="book1",
            chatAgentMode="agent",
        ),
        _Request(),
    )
    events = await _collect_sse_events(response)
    run_id = next(event["agentRunStarted"]["runId"] for event in events if "agentRunStarted" in event)
    from services.agent_run_store import get_run, get_run_events

    assert (await get_run(temp_db, run_id) or {})["status"] == "failed"
    traces = await get_run_events(temp_db, run_id)
    assert any(
        event["payload"].get("stage") == "stream"
        and event["payload"].get("outcome") == "interrupted"
        for event in traces
    )
    assert "socket reset" not in events[-1]["error"]


async def test_agent_runtime_exception_returns_safe_message(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("services.tool_router.get_api_skill_items", lambda: [])

    async def _plan(**_kwargs):
        return {
            "title": "answer",
            "steps": [
                {"id": "answer", "title": "answer", "type": "review", "executor": "model"},
            ],
        }

    async def _create_chat_stream(*_args, **_kwargs):
        raise RuntimeError("private upstream detail")

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _plan)
    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "answer with safe failure"}],
            apiKey="k",
            options={"model": "mock"},
            enableAgentTools=True,
            bookId="book1",
            chatAgentMode="agent",
        ),
        _Request(),
    )
    events = await _collect_sse_events(response)
    run_id = next(event["agentRunStarted"]["runId"] for event in events if "agentRunStarted" in event)
    from services.agent_run_store import get_run

    assert (await get_run(temp_db, run_id) or {})["status"] == "failed"
    assert "private upstream detail" not in events[-1]["error"]
    assert "安全停止" in events[-1]["error"]


async def test_chat_stream_executes_sequential_tool_steps_before_final_answer(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "services.tool_router.get_api_skill_items",
        lambda: [
            {"name": "readA", "description": "read A", "parameters": {"type": "object", "properties": {}}},
            {"name": "readB", "description": "read B", "parameters": {"type": "object", "properties": {}}},
        ],
    )

    async def _plan(**_kwargs):
        return {
            "title": "sequential reads",
            "steps": [
                {"id": "read-a", "title": "read A", "type": "read", "executor": "tool", "suggestedTools": ["readA"]},
                {"id": "read-b", "title": "read B", "type": "read", "executor": "tool", "suggestedTools": ["readB"]},
                {"id": "answer", "title": "answer", "type": "review", "executor": "model"},
            ],
        }

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _plan)
    provider_params: list[dict] = []

    async def _stream_tool(name: str, index: int):
        yield {
            "choices": [{
                "delta": {"tool_calls": [{
                    "index": 0,
                    "id": f"call-{index}",
                    "type": "function",
                    "function": {"name": name, "arguments": "{}"},
                }]},
                "finish_reason": "tool_calls",
            }],
        }

    async def _stream_answer():
        yield {"choices": [{"delta": {"content": "final answer"}, "finish_reason": "stop"}]}

    async def _create_chat_stream(*args, **_kwargs):
        names = [tool["function"]["name"] for tool in args[2].get("tools", [])]
        provider_params.append({"tools": names, "tool_choice": args[2].get("tool_choice")})
        call_index = len(provider_params)
        if call_index == 1:
            return {"stream": _stream_tool("readA", call_index), "model": "mock"}
        if call_index == 2:
            return {"stream": _stream_tool("readB", call_index), "model": "mock"}
        return {"stream": _stream_answer(), "model": "mock"}

    async def _tool_round(valid_calls, *_args, **_kwargs):
        call_id = valid_calls[0]["id"]
        yield {"toolResults": []}, [
            {"role": "assistant", "tool_calls": valid_calls},
            {"role": "tool", "tool_call_id": call_id, "content": "ok"},
        ]

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)
    monkeypatch.setattr("routers.ai._run_agent_tool_round", _tool_round)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "run sequential reads"}],
            apiKey="k",
            options={"model": "mock"},
            enableAgentTools=True,
            bookId="book1",
            chatAgentMode="agent",
        ),
        _Request(),
    )
    events = await _collect_sse_events(response)

    assert provider_params == [
        {"tools": ["readA"], "tool_choice": "required"},
        {"tools": ["readB"], "tool_choice": "required"},
        {"tools": [], "tool_choice": None},
    ]
    assert "".join(event.get("delta", "") for event in events) == "final answer"
    assert any("agentRunCompleted" in event for event in events)


async def test_planner_failure_stops_before_main_model_call(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "services.tool_router.get_api_skill_items",
        lambda: [{
            "name": "getChapterContent",
            "description": "read",
            "parameters": {"type": "object", "properties": {}},
        }],
    )

    async def _broken_plan(**_kwargs):
        raise RuntimeError("provider rejected planner options")

    main_model_called = False

    async def _create_chat_stream(*_args, **_kwargs):
        nonlocal main_model_called
        main_model_called = True
        raise AssertionError("main model must not run after planner failure")

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _broken_plan)
    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "read current chapter"}],
            apiKey="k",
            options={"model": "mock"},
            enableAgentTools=True,
            bookId="book1",
            chatAgentMode="agent",
        ),
        _Request(),
    )
    events = await _collect_sse_events(response)

    assert main_model_called is False
    assert any("agentRunFailed" in event for event in events)
    assert "计划生成失败" in events[-1]["error"]


async def test_tool_choice_provider_rejection_falls_back_without_losing_guard(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "services.tool_router.get_api_skill_items",
        lambda: [{
            "name": "read",
            "description": "read",
            "parameters": {"type": "object", "properties": {}},
        }],
    )

    async def _plan(**_kwargs):
        return {
            "title": "read then answer",
            "steps": [
                {"id": "read", "title": "read", "type": "read", "executor": "tool", "suggestedTools": ["read"]},
                {"id": "answer", "title": "answer", "type": "review", "executor": "model"},
            ],
        }

    class _UnsupportedToolChoice(RuntimeError):
        status_code = 400

    calls: list[dict] = []

    async def _tool_stream():
        yield {"choices": [{
            "delta": {"tool_calls": [{
                "index": 0,
                "id": "call-1",
                "type": "function",
                "function": {"name": "read", "arguments": "{}"},
            }]},
            "finish_reason": "tool_calls",
        }]}

    async def _answer_stream():
        yield {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}

    async def _create_chat_stream(*args, **_kwargs):
        calls.append({
            "tools": [tool["function"]["name"] for tool in args[2].get("tools", [])],
            "tool_choice": args[2].get("tool_choice"),
        })
        if len(calls) == 1:
            raise _UnsupportedToolChoice("tool_choice is unsupported")
        if len(calls) in {2, 4}:
            return {"stream": _tool_stream(), "model": "mock"}
        return {"stream": _answer_stream(), "model": "mock"}

    async def _tool_round(valid_calls, *_args, **_kwargs):
        yield {"toolResults": []}, [
            {"role": "assistant", "tool_calls": valid_calls},
            {"role": "tool", "tool_call_id": valid_calls[0]["id"], "content": "ok"},
        ]

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _plan)
    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)
    monkeypatch.setattr("routers.ai._run_agent_tool_round", _tool_round)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "read then answer"}],
            apiKey="k",
            options={"model": "mock"},
            enableAgentTools=True,
            bookId="book1",
            chatAgentMode="agent",
        ),
        _Request(),
    )
    events = await _collect_sse_events(response)

    second_response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "read then answer again"}],
            apiKey="k",
            options={"model": "mock"},
            enableAgentTools=True,
            bookId="book1",
            chatAgentMode="agent",
        ),
        _Request(),
    )
    second_events = await _collect_sse_events(second_response)

    assert calls == [
        {"tools": ["read"], "tool_choice": "required"},
        {"tools": ["read"], "tool_choice": None},
        {"tools": [], "tool_choice": None},
        {"tools": ["read"], "tool_choice": None},
        {"tools": [], "tool_choice": None},
    ]
    assert "".join(event.get("delta", "") for event in events) == "done"
    assert any("agentRunCompleted" in event for event in events)
    assert "".join(event.get("delta", "") for event in second_events) == "done"
    assert any("agentRunCompleted" in event for event in second_events)


async def test_tool_result_error_fails_run_without_advancing_plan(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "services.tool_router.get_api_skill_items",
        lambda: [{
            "name": "read",
            "description": "read",
            "parameters": {"type": "object", "properties": {}},
        }],
    )

    async def _plan(**_kwargs):
        return {
            "title": "read",
            "steps": [
                {"id": "read", "title": "read", "type": "read", "executor": "tool", "suggestedTools": ["read"]},
                {"id": "answer", "title": "answer", "type": "review", "executor": "model"},
            ],
        }

    async def _tool_stream():
        yield {"choices": [{
            "delta": {"tool_calls": [{
                "index": 0,
                "id": "call-1",
                "type": "function",
                "function": {"name": "read", "arguments": "{}"},
            }]},
            "finish_reason": "tool_calls",
        }]}

    provider_calls = 0

    async def _create_chat_stream(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return {"stream": _tool_stream(), "model": "mock"}

    async def _failed_tool_round(valid_calls, *_args, **_kwargs):
        yield {"toolResults": []}, [
            {"role": "assistant", "tool_calls": valid_calls},
            {"role": "tool", "tool_call_id": valid_calls[0]["id"], "content": '{"error":"database unavailable"}'},
        ]

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _plan)
    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)
    monkeypatch.setattr("routers.ai._run_agent_tool_round", _failed_tool_round)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "read current chapter"}],
            apiKey="k",
            options={"model": "mock"},
            enableAgentTools=True,
            bookId="book1",
            chatAgentMode="agent",
        ),
        _Request(),
    )
    events = await _collect_sse_events(response)
    run_id = next(event["agentRunStarted"]["runId"] for event in events if "agentRunStarted" in event)
    from services.agent_run_store import get_run, get_run_events

    assert provider_calls == 1
    assert "工具执行失败" in events[-1]["error"]
    assert (await get_run(temp_db, run_id) or {})["status"] == "failed"
    traces = await get_run_events(temp_db, run_id)
    assert any(
        event["payload"].get("stage") == "tool_round"
        and event["payload"].get("outcome") == "failed"
        for event in traces
    )


async def test_tool_approval_rejection_is_declined_not_operational_failure():
    from routers.ai import _classify_tool_round_messages

    outcome, error = _classify_tool_round_messages([{
        "role": "tool",
        "content": '{"success":false,"approvalStatus":"rejected","error":"not approved"}',
    }])

    assert outcome == "declined"
    assert error is None


async def test_disconnected_agent_run_is_not_left_running(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("services.tool_router.get_api_skill_items", lambda: [])

    async def _plan(**_kwargs):
        return {
            "title": "answer",
            "steps": [
                {"id": "answer", "title": "answer", "type": "review", "executor": "model"},
            ],
        }

    main_model_called = False

    async def _create_chat_stream(*_args, **_kwargs):
        nonlocal main_model_called
        main_model_called = True
        raise AssertionError("disconnect should stop before the main model call")

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _plan)
    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "answer this"}],
            apiKey="k",
            options={"model": "mock"},
            enableAgentTools=True,
            bookId="book1",
            chatAgentMode="agent",
        ),
        _DisconnectedRequest(),
    )
    events = await _collect_sse_events(response)
    run_id = next(event["agentRunStarted"]["runId"] for event in events if "agentRunStarted" in event)
    from services.agent_run_store import get_run

    assert main_model_called is False
    assert (await get_run(temp_db, run_id) or {})["status"] == "canceled"


async def test_chat_stream_reserves_anthropic_effective_thinking_output(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    captured_params: list[dict] = []

    async def _stream():
        yield {
            "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
        }

    async def _create_chat_stream(*args, **_kwargs):
        captured_params.append(args[2])
        return {"stream": _stream(), "model": "mock-model"}

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "think"}],
            apiKey="k",
            apiProvider="anthropic",
            options={
                "model": "mock-model",
                "max_tokens": 1024,
                "thinking": {"type": "enabled"},
            },
            contextWindow="200k",
        ),
        _Request(),
    )

    events = await _collect_sse_events(response)
    budget = next(evt["contextBudget"] for evt in events if "contextBudget" in evt)

    assert captured_params[0]["max_tokens"] == 1024
    assert budget["outputReserveTokens"] == 3072


async def test_oversized_tool_result_stops_before_second_provider_call(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "services.tool_router.get_api_skill_items",
        lambda: [{
            "name": "getChapterContent",
            "description": "read",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }],
    )
    monkeypatch.setattr(
        "services.task_planner.should_request_task_plan",
        lambda **_kwargs: False,
    )

    provider_calls = 0

    async def _tool_call_stream():
        yield {
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "call-1",
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

    async def _create_chat_stream(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return {"stream": _tool_call_stream(), "model": "mock-model"}

    async def _oversized_round(valid_calls, *_args, **_kwargs):
        yield {"toolResults": []}, [
            {"role": "assistant", "content": "", "tool_calls": valid_calls},
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": "x" * 800_000,
            },
        ]

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)
    monkeypatch.setattr("routers.ai._run_agent_tool_round", _oversized_round)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "read chapter"}],
            apiKey="k",
            options={"model": "mock-model"},
            enableAgentTools=True,
            bookId="book1",
            chatAgentMode="ask",
            contextWindow="200k",
        ),
        _Request(),
    )

    events = await _collect_sse_events(response)

    assert provider_calls == 1
    assert any("工具结果超过" in evt.get("error", "") for evt in events)


async def test_initial_context_overflow_marks_started_agent_run_failed(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "services.tool_router.get_api_skill_items",
        lambda: [{
            "name": "getChapterContent",
            "description": "read",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }],
    )

    async def _plan(**_kwargs):
        return {
            "title": "read",
            "steps": [{
                "id": "read",
                "title": "read",
                "type": "read",
                "executor": "tool",
                "suggestedTools": ["getChapterContent"],
            }],
        }

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _plan)

    provider_called = False

    async def _create_chat_stream(*_args, **_kwargs):
        nonlocal provider_called
        provider_called = True
        raise AssertionError("provider must not be called after preflight overflow")

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "x" * 100_000}],
            apiKey="k",
            options={"model": "mock-model"},
            enableAgentTools=True,
            bookId="book1",
            chatAgentMode="agent",
            contextWindow="32k",
        ),
        _Request(),
    )

    events = await _collect_sse_events(response)

    assert provider_called is False
    assert any("agentRunFailed" in evt for evt in events)
    assert any("超过了所配置的模型窗口" in evt.get("error", "") for evt in events)


async def test_last_tool_round_is_not_executed_without_a_followup_model_turn(
    temp_db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "services.tool_router.get_api_skill_items",
        lambda: [{
            "name": "getChapterContent",
            "description": "read",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }],
    )
    monkeypatch.setattr(
        "services.task_planner.should_request_task_plan",
        lambda **_kwargs: False,
    )

    provider_calls = 0
    executed_rounds = 0

    async def _tool_call_stream(call_index: int):
        yield {
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": f"call-{call_index}",
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

    async def _create_chat_stream(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return {
            "stream": _tool_call_stream(provider_calls),
            "model": "mock-model",
        }

    async def _small_round(valid_calls, *_args, **_kwargs):
        nonlocal executed_rounds
        executed_rounds += 1
        call_id = valid_calls[0]["id"]
        yield {"toolResults": []}, [
            {"role": "assistant", "content": "", "tool_calls": valid_calls},
            {"role": "tool", "tool_call_id": call_id, "content": "ok"},
        ]

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)
    monkeypatch.setattr("routers.ai._run_agent_tool_round", _small_round)

    response = await chat_stream(
        ChatStreamRequest(
            sessionId=1,
            messages=[{"role": "user", "content": "keep reading"}],
            apiKey="k",
            options={"model": "mock-model"},
            enableAgentTools=True,
            bookId="book1",
            chatAgentMode="ask",
            contextWindow="200k",
        ),
        _Request(),
    )

    events = await _collect_sse_events(response)

    assert provider_calls == 6
    assert executed_rounds == 5
    assert any("最大工具轮次" in evt.get("error", "") for evt in events)
