from __future__ import annotations

import json

import pytest

from agent_core.contracts import (
    ExecutionState,
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolCall,
)
from agent_core.events import CoreEventType
from agent_core.ports import ToolExecutionGateway
from application.event_sinks import CallbackEventSink
from application.legacy_tool_execution_gateway import LegacyToolExecutionGateway


def _request(*, allowed=frozenset({"readA"}), state=None):
    return ToolBatchRequest(
        run_id="run-1",
        calls=(ToolCall(id="call-a", name="readA", arguments_json="{}"),),
        allowed_tool_names=allowed,
        state=state or ExecutionState(),
    )


@pytest.mark.asyncio
async def test_legacy_tool_gateway_streams_typed_progress_and_restores_authorization(
    monkeypatch,
):
    captured = {}

    async def _run_tools(calls, context, send_chunk=None, signal=None):
        captured.update({
            "calls": calls,
            "context": dict(context),
            "signal": signal,
        })
        send_chunk({"toolReadCacheMask": [True]})
        send_chunk({"toolApprovalRequired": {
            "approvalId": "approval-1",
            "toolName": "readA",
            "title": "Read",
            "riskLevel": "read",
            "summary": "{}",
        }})
        send_chunk({"toolIndexCompleted": 0, "toolFromCache": True})
        return [{
            "tool_call_id": "call-a",
            "content": json.dumps({"success": True}, ensure_ascii=False),
        }]

    monkeypatch.setattr(
        "services.tool_executor.run_tools",
        _run_tools,
    )
    state = ExecutionState(domain={"allowedToolNames": {"previous"}, "value": 1})
    events = []
    gateway = LegacyToolExecutionGateway()

    result = await gateway.execute_batch(
        _request(state=state),
        CallbackEventSink(events.append),
    )

    assert isinstance(gateway, ToolExecutionGateway)
    assert captured["context"]["allowedToolNames"] == {"readA"}
    assert captured["calls"][0]["function"] == {"name": "readA", "arguments": "{}"}
    assert state.domain["allowedToolNames"] == {"previous"}
    assert result.outcome is ToolBatchOutcome.COMPLETED
    assert result.cache_hits == (True,)
    assert result.results[0].tool_name == "readA"
    assert [event.type for event in events] == [
        "tool.read_cache_mask",
        CoreEventType.APPROVAL_REQUESTED,
        CoreEventType.TOOL_CALL_COMPLETED,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected", "error"),
    [
        (
            {"success": False, "approvalStatus": "rejected", "error": "not approved"},
            ToolBatchOutcome.DECLINED,
            None,
        ),
        (
            {"success": False, "approvalStatus": "canceled", "error": "canceled"},
            ToolBatchOutcome.CANCELED,
            "approval_canceled",
        ),
        (
            {"success": False, "approvalStatus": "timed_out", "error": "timeout"},
            ToolBatchOutcome.FAILED,
            "approval_unavailable",
        ),
        (
            {"success": False, "error": "handler failed"},
            ToolBatchOutcome.FAILED,
            "tool_execution_failed",
        ),
    ],
)
async def test_legacy_tool_gateway_classifies_existing_result_protocol(
    monkeypatch,
    payload,
    expected,
    error,
):
    async def _run_tools(calls, context, send_chunk=None, signal=None):
        return [{
            "tool_call_id": "call-a",
            "content": json.dumps(payload, ensure_ascii=False),
        }]

    monkeypatch.setattr(
        "services.tool_executor.run_tools",
        _run_tools,
    )
    result = await LegacyToolExecutionGateway().execute_batch(
        _request(),
        CallbackEventSink(lambda _event: None),
    )

    assert result.outcome is expected
    assert result.error == error


@pytest.mark.asyncio
async def test_legacy_tool_gateway_rejects_unauthorized_batch_before_old_executor(
    monkeypatch,
):
    async def _unexpected(*_args, **_kwargs):
        raise AssertionError("old executor must not receive unauthorized calls")

    monkeypatch.setattr(
        "services.tool_executor.run_tools",
        _unexpected,
    )
    state = ExecutionState(domain={"value": 1})
    result = await LegacyToolExecutionGateway().execute_batch(
        _request(allowed=frozenset(), state=state),
        CallbackEventSink(lambda _event: None),
    )

    assert result.outcome is ToolBatchOutcome.REJECTED
    assert result.error == "tool_not_authorized"
    assert "allowedToolNames" not in state.domain


@pytest.mark.asyncio
async def test_legacy_tool_gateway_restores_authorization_when_progress_sink_fails(
    monkeypatch,
):
    async def _run_tools(calls, context, send_chunk=None, signal=None):
        send_chunk({"toolIndexCompleted": 0})
        return [{
            "tool_call_id": "call-a",
            "content": json.dumps({"success": True}),
        }]

    async def _reject_progress(_event):
        raise RuntimeError("progress sink failed")

    monkeypatch.setattr("services.tool_executor.run_tools", _run_tools)
    previous_authorization = {"previous"}
    state = ExecutionState(domain={"allowedToolNames": previous_authorization})

    with pytest.raises(RuntimeError, match="progress sink failed"):
        await LegacyToolExecutionGateway().execute_batch(
            _request(state=state),
            CallbackEventSink(_reject_progress),
        )

    assert state.domain["allowedToolNames"] is previous_authorization
