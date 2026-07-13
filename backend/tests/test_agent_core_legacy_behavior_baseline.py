"""Legacy behaviors that must survive the Agent Core extraction."""

from __future__ import annotations

import asyncio
import json

import pytest

from routers.ai import resolve_pending_tool_approval
from schemas.ai import ResolveToolApprovalRequest
from services.tool_approval_service import (
    ToolApprovalBroker,
    ToolApprovalStatus,
    request_tool_approval,
)
from services.tool_policy import TOOL_POLICIES


@pytest.mark.asyncio
async def test_approval_timeout_fails_closed_and_ignores_late_resolution():
    broker = ToolApprovalBroker()
    events: list[dict] = []

    result = await broker.request(
        tool_name="deleteCharacter",
        args={"characterId": 7},
        policy=TOOL_POLICIES["deleteCharacter"],
        send_chunk=events.append,
        signal=None,
        timeout_seconds=0.01,
    )

    approval_id = events[0]["toolApprovalRequired"]["approvalId"]
    assert result.status is ToolApprovalStatus.TIMED_OUT
    assert broker.resolve(approval_id, True) is None


@pytest.mark.asyncio
async def test_abort_signal_cancels_a_waiting_approval():
    broker = ToolApprovalBroker()
    signal = asyncio.Event()
    events: list[dict] = []
    task = asyncio.create_task(broker.request(
        tool_name="deleteCharacter",
        args={"characterId": 7},
        policy=TOOL_POLICIES["deleteCharacter"],
        send_chunk=events.append,
        signal=signal,
        timeout_seconds=1,
    ))
    await asyncio.sleep(0)

    approval_id = events[0]["toolApprovalRequired"]["approvalId"]
    signal.set()
    result = await task

    assert result.status is ToolApprovalStatus.CANCELED
    assert broker.resolve(approval_id, True) is None


@pytest.mark.asyncio
async def test_missing_event_sink_makes_approval_unavailable_without_waiting():
    broker = ToolApprovalBroker()

    result = await broker.request(
        tool_name="deleteCharacter",
        args={"characterId": 7},
        policy=TOOL_POLICIES["deleteCharacter"],
        send_chunk=None,
        signal=None,
    )

    assert result.status is ToolApprovalStatus.UNAVAILABLE
    assert result.approval_id is None


@pytest.mark.asyncio
async def test_concurrent_approval_ids_are_isolated():
    broker = ToolApprovalBroker()
    events: list[dict] = []
    both_requested = asyncio.Event()

    def _capture(event: dict) -> None:
        events.append(event)
        if len(events) == 2:
            both_requested.set()

    first = asyncio.create_task(broker.request(
        tool_name="deleteCharacter",
        args={"characterId": 1},
        policy=TOOL_POLICIES["deleteCharacter"],
        send_chunk=_capture,
        signal=None,
        timeout_seconds=1,
    ))
    second = asyncio.create_task(broker.request(
        tool_name="deleteCharacter",
        args={"characterId": 2},
        policy=TOOL_POLICIES["deleteCharacter"],
        send_chunk=_capture,
        signal=None,
        timeout_seconds=1,
    ))
    await asyncio.wait_for(both_requested.wait(), timeout=1)

    by_summary = {
        item["toolApprovalRequired"]["summary"]: item["toolApprovalRequired"]["approvalId"]
        for item in events
    }
    first_id = next(value for key, value in by_summary.items() if '"characterId": 1' in key)
    second_id = next(value for key, value in by_summary.items() if '"characterId": 2' in key)
    assert first_id != second_id
    assert broker.resolve(first_id, True) is ToolApprovalStatus.APPROVED
    assert not second.done()
    assert broker.resolve(second_id, False) is ToolApprovalStatus.REJECTED

    first_result, second_result = await asyncio.gather(first, second)
    assert first_result.status is ToolApprovalStatus.APPROVED
    assert second_result.status is ToolApprovalStatus.REJECTED


@pytest.mark.asyncio
async def test_approval_endpoint_handler_consumes_the_decision_once():
    events: list[dict] = []
    task = asyncio.create_task(request_tool_approval(
        tool_name="deleteCharacter",
        args={"characterId": 7},
        policy=TOOL_POLICIES["deleteCharacter"],
        send_chunk=events.append,
        signal=None,
        timeout_seconds=1,
    ))
    await asyncio.sleep(0)
    approval_id = events[0]["toolApprovalRequired"]["approvalId"]

    first = await resolve_pending_tool_approval(
        approval_id,
        ResolveToolApprovalRequest(approved=True),
    )
    second = await resolve_pending_tool_approval(
        approval_id,
        ResolveToolApprovalRequest(approved=True),
    )
    result = await task

    assert first["success"] is True
    assert first["data"]["status"] == "approved"
    assert second["success"] is False
    assert result.status is ToolApprovalStatus.APPROVED


@pytest.mark.asyncio
async def test_chapter_edit_remains_a_proposal_and_emits_the_full_diff(monkeypatch):
    from services.tool_handlers import chapter_tools

    async def _existing_content(_chapter_id: str):
        return {"plainTextFull": "旧正文"}

    def _unexpected_database_access():
        raise AssertionError("edit proposal must not open the database for a write")

    monkeypatch.setattr(chapter_tools, "_read_chapter_plain_full", _existing_content)
    monkeypatch.setattr(chapter_tools, "get_db", _unexpected_database_access)
    events: list[dict] = []
    result = await chapter_tools._tool_edit_chapter_content(
        {
            "chapterId": "ch1",
            "writingChapters": [{"id": "ch1", "title": "第一章"}],
        },
        {"chapterId": "ch1", "content": "新正文"},
        events.append,
    )

    payload = json.loads(result.content)
    assert events == [{
        "proposedChapterDiff": {
            "chapterId": "ch1",
            "beforeText": "旧正文",
            "proposedText": "新正文",
            "source": "ai_tool_edit",
        }
    }]
    proposed = events[0]["proposedChapterDiff"]
    assert payload["success"] is True
    assert payload["pendingUserApproval"] is True
    assert proposed == {
        "chapterId": "ch1",
        "beforeText": "旧正文",
        "proposedText": "新正文",
        "source": "ai_tool_edit",
    }
    assert "toolApprovalRequired" not in events[0]
