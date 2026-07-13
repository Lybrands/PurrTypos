"""Replay a deterministic tape through the current legacy Agent runtime."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from routers.ai import chat_stream
from schemas.ai import ChatStreamRequest
from services.agent_run_store import get_run, get_run_events, get_run_todos
from services.provider_capability_cache import clear_provider_capability_cache


class ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


class DisconnectedRequest:
    async def is_disconnected(self) -> bool:
        return True


async def collect_sse_events(response) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for raw in response.body_iterator:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        stripped = text.strip()
        if stripped.startswith("{"):
            events.append(json.loads(stripped))
        else:
            for line in text.splitlines():
                if not line.startswith("data: "):
                    continue
                payload = line[len("data: "):].strip()
                if payload and payload != "[DONE]":
                    events.append(json.loads(payload))
        if any(event.get("done") or event.get("error") for event in events):
            break
    return events


async def replay_legacy_runtime_tape(
    case: dict[str, Any],
    *,
    db,
    monkeypatch,
    core_runtime: bool = False,
) -> dict[str, Any]:
    clear_provider_capability_cache()
    monkeypatch.setattr("config.AGENT_CORE_RUNTIME_ENABLED", core_runtime)
    skills = [
        {
            "name": name,
            "description": f"Tape tool {name}",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }
        for name in case.get("skills", [])
    ]
    monkeypatch.setattr("services.tool_router.get_api_skill_items", lambda: skills)

    async def _plan(**kwargs):
        expected_user_text = next(
            str(message.get("content") or "")
            for message in reversed(case["request"]["messages"])
            if message.get("role") == "user"
        )
        assert kwargs["user_text"] == expected_user_text, case["id"]
        assert kwargs["chat_agent_mode"] == case["request"].get("chatAgentMode", "agent")
        assert set(kwargs["available_tool_names"]) == set(case.get("skills", []))
        assert kwargs.get("signal") is not None
        return case["plan"]

    monkeypatch.setattr("services.task_planner.generate_model_task_plan", _plan)

    model_rounds = list(case["modelRounds"])
    captured_scopes: list[list[str]] = []
    model_index = 0

    async def _create_chat_stream(*args, **_kwargs):
        nonlocal model_index
        if model_index >= len(model_rounds):
            raise AssertionError(f"{case['id']}: unexpected extra model round")
        messages = args[1]
        params = args[2]
        visible_scope = [
            str((tool.get("function") or {}).get("name") or "")
            for tool in params.get("tools", [])
        ]
        captured_scopes.append(visible_scope)
        assert params.get("tool_choice") == (
            "required" if visible_scope else None
        ), case["id"]
        expected_user_text = next(
            str(message.get("content") or "")
            for message in reversed(case["request"]["messages"])
            if message.get("role") == "user"
        )
        assert any(
            message.get("role") == "user"
            and str(message.get("content") or "") == expected_user_text
            for message in messages
        ), case["id"]
        if model_index > 0:
            previous = tool_rounds[model_index - 1]
            payloads = previous.get("resultPayloads", [])
            tail = messages[-(len(payloads) + 1):]
            assistant = tail[0]
            assert assistant.get("role") == "assistant", case["id"]
            assert [
                str((call.get("function") or {}).get("name") or "")
                for call in assistant.get("tool_calls", [])
            ] == previous["tools"], case["id"]
            for tool_message, payload in zip(tail[1:], payloads, strict=True):
                assert tool_message.get("role") == "tool", case["id"]
                assert str(tool_message.get("tool_call_id") or ""), case["id"]
                assert json.loads(tool_message.get("content") or "{}") == payload, case["id"]
        chunks = model_rounds[model_index]
        model_index += 1

        async def _stream():
            for chunk in chunks:
                yield chunk

        return {"stream": _stream(), "model": "tape-model"}

    monkeypatch.setattr("services.ai_provider.create_chat_stream", _create_chat_stream)

    tool_rounds = list(case.get("toolRounds", []))
    tool_index = 0
    executed_tools: list[str] = []

    async def _run_tools(calls, _ctx, send_chunk=None, signal=None):
        nonlocal tool_index
        if tool_index >= len(tool_rounds):
            raise AssertionError(f"{case['id']}: unexpected extra tool round")
        recorded = tool_rounds[tool_index]
        tool_index += 1
        names = [str((call.get("function") or {}).get("name") or "") for call in calls]
        executed_tools.extend(names)
        assert names == recorded["tools"], case["id"]
        assert _ctx.get("bookId") == case["request"].get("bookId"), case["id"]
        assert _ctx.get("chapterId") == case["request"].get("chapterId"), case["id"]
        assert set(_ctx.get("allowedToolNames") or set()) == set(recorded["tools"]), case["id"]
        assert signal is not None, case["id"]
        for progress in recorded.get("progress", []):
            if send_chunk:
                send_chunk(progress)
        payloads = recorded.get("resultPayloads", [])
        assert len(payloads) == len(calls), case["id"]
        return [
            {
                "tool_call_id": call.get("id"),
                "content": json.dumps(payload, ensure_ascii=False),
            }
            for call, payload in zip(calls, payloads, strict=True)
        ]

    monkeypatch.setattr("services.tool_executor.run_tools", _run_tools)

    request_data = dict(case["request"])
    response = await chat_stream(
        ChatStreamRequest(
            messages=request_data["messages"],
            apiKey="tape-key",
            apiProvider=request_data.get("apiProvider", "openai"),
            options={"model": "tape-model", **request_data.get("options", {})},
            sessionId=request_data.get("sessionId", 1),
            enableAgentTools=request_data.get("enableAgentTools", True),
            bookId=request_data.get("bookId"),
            chapterId=request_data.get("chapterId"),
            currentChapterTitle=request_data.get("currentChapterTitle"),
            writingChapters=request_data.get("writingChapters"),
            availableOutlines=request_data.get("availableOutlines"),
            associatedChapterIds=request_data.get("associatedChapterIds"),
            associatedOutlineIds=request_data.get("associatedOutlineIds"),
            selectedMemoryIds=request_data.get("selectedMemoryIds"),
            selectedForeshadowingIds=request_data.get("selectedForeshadowingIds"),
            chatAgentMode=request_data.get("chatAgentMode", "agent"),
            contextWindow=request_data.get("contextWindow", "32k"),
        ),
        DisconnectedRequest() if request_data.get("disconnect") else ConnectedRequest(),
    )
    events = await collect_sse_events(response)
    run_id = next(
        event["agentRunStarted"]["runId"]
        for event in events
        if "agentRunStarted" in event
    )
    run = await get_run(db, run_id) or {}
    todos = await get_run_todos(db, run_id)
    persisted = await get_run_events(db, run_id)
    traces = [
        event["payload"]
        for event in persisted
        if event["eventType"] == "agentRunTrace"
    ]

    assert model_index == len(model_rounds), case["id"]
    assert tool_index == len(tool_rounds), case["id"]
    return {
        "eventKeys": [_event_key(event) for event in events],
        "sseDigest": _sse_digest(events),
        "modelCalls": model_index,
        "visibleToolScopes": captured_scopes,
        "toolSequence": executed_tools,
        "runStatus": run.get("status"),
        "todos": [
            {"id": todo.get("id"), "status": todo.get("status")}
            for todo in todos
        ],
        "traceSequence": [
            f"{trace.get('stage')}:{trace.get('outcome')}"
            for trace in traces
        ],
        "finalText": "".join(str(event.get("delta") or "") for event in events),
        "errors": [str(event["error"]) for event in events if event.get("error")],
        "domainEffects": [
            key
            for event in events
            for key in ("proposedChapterDiff", "toolApprovalRequired")
            if key in event
        ],
    }


def _event_key(event: dict[str, Any]) -> str:
    precedence = (
        "agentRunStarted",
        "agentRunTodosUpdated",
        "agentRunTodoUpdated",
        "contextBudget",
        "thinkingDelta",
        "delta",
        "toolCalls",
        "toolIndexCompleted",
        "toolResults",
        "proposedChapterDiff",
        "toolApprovalRequired",
        "agentRunCompleted",
        "agentRunBlocked",
        "agentRunFailed",
        "agentRunCanceled",
        "done",
        "error",
    )
    return next((key for key in precedence if key in event), sorted(event)[0])


def _sse_digest(events: list[dict[str, Any]]) -> str:
    normalized = _normalize_dynamic(events)
    canonical = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_dynamic(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            item_key: _normalize_dynamic(item_value, item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_normalize_dynamic(item, key) for item in value]
    if key == "runId":
        return "<RUN_ID>"
    if key == "approvalId":
        return "<APPROVAL_ID>"
    return value
