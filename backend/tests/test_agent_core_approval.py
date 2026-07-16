from __future__ import annotations

import asyncio

import pytest

from agent_core.contracts import (
    ApprovalRequest,
    ApprovalResult,
    ApprovalStatus,
    ToolCall,
)
from agent_core.events import CoreEventType
from agent_core.ports import ApprovalGateway
from agent_core.tools.approval import InMemoryApprovalGateway


class RecordingSink:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


def _approval(*, timeout: float = 1.0) -> ApprovalRequest:
    return ApprovalRequest(
        tool_call=ToolCall(
            id="call-delete",
            name="dangerous",
            arguments_json='{"id":7}',
        ),
        title="Delete item",
        risk_level="destructive",  # type: ignore[arg-type]
        summary='{"id":7}',
        timeout_seconds=timeout,
    )


async def _wait_for_request(sink: RecordingSink) -> str:
    for _ in range(100):
        if sink.events:
            return str(sink.events[0].payload["approvalId"])
        await asyncio.sleep(0)
    raise AssertionError("approval request was not emitted")


@pytest.mark.asyncio
async def test_approval_is_run_bound_one_shot_and_emits_typed_lifecycle():
    gateway = InMemoryApprovalGateway()
    sink = RecordingSink()
    task = asyncio.create_task(gateway.request("run-a", _approval(), sink))
    approval_id = await _wait_for_request(sink)

    assert isinstance(gateway, ApprovalGateway)
    assert gateway.resolve("run-b", approval_id, "approve") is None
    assert gateway.resolve("run-a", approval_id, "approve") is ApprovalStatus.APPROVED
    assert gateway.resolve("run-a", approval_id, "approve") is None

    result = await task
    assert result.status is ApprovalStatus.APPROVED
    assert gateway.pending_count() == 0
    assert [event.type for event in sink.events] == [
        CoreEventType.APPROVAL_REQUESTED,
        CoreEventType.APPROVAL_RESOLVED,
    ]
    assert sink.events[-1].payload["status"] == "approved"


@pytest.mark.asyncio
async def test_cancel_pending_only_affects_the_selected_run():
    gateway = InMemoryApprovalGateway()
    sink_a = RecordingSink()
    sink_b = RecordingSink()
    task_a = asyncio.create_task(gateway.request("run-a", _approval(), sink_a))
    task_b = asyncio.create_task(gateway.request("run-b", _approval(), sink_b))
    id_a, id_b = await asyncio.gather(
        _wait_for_request(sink_a),
        _wait_for_request(sink_b),
    )

    assert id_a != id_b
    assert gateway.cancel_pending("run-a") == 1
    assert gateway.resolve("run-b", id_b, "approve") is ApprovalStatus.APPROVED
    result_a, result_b = await asyncio.gather(task_a, task_b)

    assert result_a.status is ApprovalStatus.CANCELED
    assert result_b.status is ApprovalStatus.APPROVED
    assert gateway.pending_count() == 0


@pytest.mark.asyncio
async def test_timeout_and_signal_cancellation_always_clean_pending_state():
    gateway = InMemoryApprovalGateway()
    timeout_sink = RecordingSink()
    timed_out = await gateway.request(
        "run-timeout",
        _approval(timeout=0.01),
        timeout_sink,
    )

    assert timed_out.status is ApprovalStatus.TIMED_OUT
    assert timeout_sink.events[-1].payload["status"] == "timed_out"
    timeout_id = str(timeout_sink.events[0].payload["approvalId"])
    assert gateway.resolve("run-timeout", timeout_id, "approve") is None
    assert gateway.pending_count() == 0

    signal = asyncio.Event()
    signal.set()
    canceled_sink = RecordingSink()
    canceled = await gateway.request(
        "run-canceled",
        _approval(),
        canceled_sink,
        signal,
    )
    assert canceled.status is ApprovalStatus.CANCELED
    assert canceled.approval_id is None
    assert canceled_sink.events == []
    assert gateway.pending_count() == 0


@pytest.mark.asyncio
async def test_approval_gateway_instances_do_not_share_pending_requests():
    first = InMemoryApprovalGateway()
    second = InMemoryApprovalGateway()
    sink = RecordingSink()
    task = asyncio.create_task(first.request("run-a", _approval(), sink))
    approval_id = await _wait_for_request(sink)

    assert second.resolve("run-a", approval_id, "approve") is None
    assert first.cancel_pending("run-a") == 1
    assert (await task).status is ApprovalStatus.CANCELED
    assert second.pending_count() == 0


@pytest.mark.asyncio
async def test_cancel_all_fails_closed_for_every_live_run():
    gateway = InMemoryApprovalGateway()
    sink_a = RecordingSink()
    sink_b = RecordingSink()
    task_a = asyncio.create_task(gateway.request("run-a", _approval(), sink_a))
    task_b = asyncio.create_task(gateway.request("run-b", _approval(), sink_b))
    id_a, id_b = await asyncio.gather(
        _wait_for_request(sink_a),
        _wait_for_request(sink_b),
    )

    assert gateway.cancel_all() == 2
    assert gateway.resolve("run-a", id_a, "approve") is None
    assert gateway.resolve("run-b", id_b, "approve") is None
    result_a, result_b = await asyncio.gather(task_a, task_b)

    assert result_a.status is ApprovalStatus.CANCELED
    assert result_b.status is ApprovalStatus.CANCELED
    assert gateway.pending_count() == 0


@pytest.mark.asyncio
async def test_resolve_winner_cannot_be_rewritten_by_same_tick_signal():
    gateway = InMemoryApprovalGateway()
    sink = RecordingSink()
    signal = asyncio.Event()
    task = asyncio.create_task(gateway.request(
        "run-a",
        _approval(),
        sink,
        signal,
    ))
    approval_id = await _wait_for_request(sink)

    assert gateway.resolve(
        "run-a",
        approval_id,
        "approve",
    ) is ApprovalStatus.APPROVED
    signal.set()
    result = await task

    assert result.status is ApprovalStatus.APPROVED
    assert sink.events[-1].payload["status"] == "approved"
    assert gateway.pending_count() == 0


@pytest.mark.asyncio
async def test_signal_winner_rejects_late_resolution():
    gateway = InMemoryApprovalGateway()
    sink = RecordingSink()
    signal = asyncio.Event()
    task = asyncio.create_task(gateway.request(
        "run-a",
        _approval(),
        sink,
        signal,
    ))
    approval_id = await _wait_for_request(sink)

    signal.set()
    while gateway.pending_count():
        await asyncio.sleep(0)

    assert gateway.resolve("run-a", approval_id, "approve") is None
    result = await task
    assert result.status is ApprovalStatus.CANCELED
    assert sink.events[-1].payload["status"] == "canceled"


@pytest.mark.asyncio
async def test_close_is_permanent_and_late_requests_emit_no_card():
    gateway = InMemoryApprovalGateway()
    assert gateway.close() == 0
    assert gateway.close() == 0
    sink = RecordingSink()

    result = await gateway.request("run-late", _approval(), sink)

    assert result == ApprovalResult(None, ApprovalStatus.CANCELED)
    assert sink.events == []
    assert gateway.pending_count() == 0
