"""Human-in-the-Loop approval tests for protected Agent tool execution."""

from __future__ import annotations

import asyncio
import json

import pytest

from services.tool_approval_service import (
    ToolApprovalStatus,
    request_tool_approval,
    resolve_tool_approval,
)
from services.tool_executor import TOOL_HANDLERS, ToolResult, run_tools
from services.tool_policy import TOOL_POLICIES, ToolExecutionMode, policy_coverage


@pytest.mark.asyncio
async def test_approval_broker_emits_request_and_resumes_after_approval():
    events: list[dict] = []
    result_task = asyncio.create_task(request_tool_approval(
        tool_name="deleteCharacter",
        args={"characterId": 7},
        policy=TOOL_POLICIES["deleteCharacter"],
        send_chunk=events.append,
        signal=None,
        timeout_seconds=1,
    ))
    await asyncio.sleep(0)

    request = events[0]["toolApprovalRequired"]
    assert request["toolName"] == "deleteCharacter"
    assert request["riskLevel"] == "destructive"
    assert resolve_tool_approval(request["approvalId"], True) is ToolApprovalStatus.APPROVED

    result = await result_task
    assert result.approved
    assert resolve_tool_approval(request["approvalId"], True) is None


@pytest.mark.asyncio
async def test_rejected_protected_tool_never_calls_its_handler():
    calls: list[dict] = []
    events: list[dict] = []
    original = TOOL_HANDLERS["deleteCharacter"]

    async def _fake_handler(ctx, args, send_chunk):
        calls.append(args)
        return ToolResult('{"success": true}')

    TOOL_HANDLERS["deleteCharacter"] = _fake_handler
    try:
        task = asyncio.create_task(run_tools(
            [{"id": "call-1", "function": {"name": "deleteCharacter", "arguments": '{"characterId": 7}'}}],
            ctx={},
            send_chunk=events.append,
        ))
        await asyncio.sleep(0)
        approval_id = events[0]["toolApprovalRequired"]["approvalId"]
        assert resolve_tool_approval(approval_id, False) is ToolApprovalStatus.REJECTED

        result = await task
        payload = json.loads(result[0]["content"])
        assert calls == []
        assert payload["approvalStatus"] == "rejected"
    finally:
        TOOL_HANDLERS["deleteCharacter"] = original


@pytest.mark.asyncio
async def test_approved_protected_tool_executes_exactly_once():
    calls: list[dict] = []
    events: list[dict] = []
    original = TOOL_HANDLERS["deleteCharacter"]

    async def _fake_handler(ctx, args, send_chunk):
        calls.append(args)
        return ToolResult('{"success": true}')

    TOOL_HANDLERS["deleteCharacter"] = _fake_handler
    try:
        task = asyncio.create_task(run_tools(
            [{"id": "call-1", "function": {"name": "deleteCharacter", "arguments": '{"characterId": 7}'}}],
            ctx={},
            send_chunk=events.append,
        ))
        await asyncio.sleep(0)
        approval_id = events[0]["toolApprovalRequired"]["approvalId"]
        assert resolve_tool_approval(approval_id, True) is ToolApprovalStatus.APPROVED

        result = await task
        assert calls == [{"characterId": 7}]
        assert result[0]["content"] == '{"success": true}'
    finally:
        TOOL_HANDLERS["deleteCharacter"] = original


def test_each_registered_tool_has_an_explicit_execution_policy():
    missing, orphaned = policy_coverage(TOOL_HANDLERS)
    assert not missing
    assert not orphaned
    assert TOOL_POLICIES["editChapterContent"].mode is ToolExecutionMode.PROPOSE
    assert TOOL_POLICIES["deleteCharacter"].mode is ToolExecutionMode.CONFIRM
