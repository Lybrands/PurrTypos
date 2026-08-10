from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from purra.contracts import (
    ApprovalRequest,
    ApprovalStatus,
    ToolCall,
    ToolRiskLevel,
)
from purra.events import CoreEventType
from database.connection import DatabaseConnection
from infrastructure.persistence import approval_store, run_store
from infrastructure.persistence.sqlite_approval_gateway import (
    SqliteApprovalGateway,
)


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        yield connection
    finally:
        await connection.close()


def _approval(*, timeout: float = 1.0) -> ApprovalRequest:
    return ApprovalRequest(
        tool_call=ToolCall(
            id="call-delete",
            name="delete_resource",
            arguments_json='{"id":7}',
        ),
        title="Delete resource",
        risk_level=ToolRiskLevel.DESTRUCTIVE,
        summary='{"id":7}',
        timeout_seconds=timeout,
    )


class _PersistentSink:
    def __init__(self, db: DatabaseConnection) -> None:
        self.db = db
        self.events = []
        self.requested_row = None
        self.requested = asyncio.Event()

    async def emit(self, event) -> None:
        if event.type == CoreEventType.APPROVAL_REQUESTED:
            self.requested_row = await approval_store.get_approval(
                self.db,
                str(event.payload["approvalId"]),
            )
        self.events.append(event)
        if event.type == CoreEventType.APPROVAL_REQUESTED:
            self.requested.set()


async def _wait_for_request(sink: _PersistentSink) -> str:
    await asyncio.wait_for(sink.requested.wait(), timeout=5.0)
    event = next(
        event
        for event in sink.events
        if event.type == CoreEventType.APPROVAL_REQUESTED
    )
    return str(event.payload["approvalId"])


@pytest.mark.asyncio
async def test_request_is_durable_before_emission_and_decision_before_release(db):
    gateway = SqliteApprovalGateway(db)
    sink = _PersistentSink(db)
    task = asyncio.create_task(gateway.request("run-a", _approval(), sink))
    approval_id = await _wait_for_request(sink)

    assert sink.requested_row is not None
    assert sink.requested_row["status"] == "pending"
    assert sink.requested_row["run_id"] == "run-a"
    assert await gateway.resolve("run-a", approval_id, "approve") is (
        ApprovalStatus.APPROVED
    )

    row_before_release = await approval_store.get_approval(db, approval_id)
    assert row_before_release is not None
    assert row_before_release["status"] == "approved"
    assert row_before_release["resolved_at_ms"] is not None
    result = await task
    assert result.status is ApprovalStatus.APPROVED
    assert sink.events[-1].payload["status"] == "approved"
    assert await gateway.resolve("run-a", approval_id, "reject") is None


@pytest.mark.asyncio
async def test_concurrent_decisions_have_exactly_one_durable_winner(db):
    gateway = SqliteApprovalGateway(db)
    sink = _PersistentSink(db)
    task = asyncio.create_task(gateway.request("run-race", _approval(), sink))
    approval_id = await _wait_for_request(sink)

    outcomes = await asyncio.gather(
        gateway.resolve("run-race", approval_id, "approve"),
        gateway.resolve("run-race", approval_id, "reject"),
    )
    winner = [outcome for outcome in outcomes if outcome is not None]

    assert len(winner) == 1
    assert (await task).status is winner[0]
    row = await approval_store.get_approval(db, approval_id)
    assert row is not None
    assert row["status"] == winner[0].value


@pytest.mark.asyncio
async def test_timeout_and_run_cancellation_are_persisted(db):
    gateway = SqliteApprovalGateway(db)
    timeout_sink = _PersistentSink(db)
    timed_out = await gateway.request(
        "run-timeout",
        _approval(timeout=0.01),
        timeout_sink,
    )
    timeout_row = await approval_store.get_approval(
        db,
        str(timed_out.approval_id),
    )
    assert timed_out.status is ApprovalStatus.TIMED_OUT
    assert timeout_row is not None and timeout_row["status"] == "timed_out"

    cancel_sink = _PersistentSink(db)
    task = asyncio.create_task(gateway.request("run-cancel", _approval(), cancel_sink))
    approval_id = await _wait_for_request(cancel_sink)
    assert await gateway.cancel_pending("run-cancel") == 1
    assert (await task).status is ApprovalStatus.CANCELED
    cancel_row = await approval_store.get_approval(db, approval_id)
    assert cancel_row is not None and cancel_row["status"] == "canceled"


@pytest.mark.asyncio
async def test_restart_recovery_fails_closed_and_terminalizes_running_run(db):
    run_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="delete it",
        mode="agent",
    )
    await approval_store.create_approval(
        db,
        approval_id="approval-abandoned",
        run_id=run_id,
        tool_call_id="call-delete",
        tool_name="delete_resource",
        title="Delete resource",
        risk_level="destructive",
        summary='{"id":7}',
        expires_at_ms=approval_store.now_ms() + 60_000,
    )

    assert await approval_store.recover_pending_approvals(db) == 1
    approval = await approval_store.get_approval(db, "approval-abandoned")
    run = await run_store.get_run(db, run_id)
    events = await run_store.get_run_events(db, run_id)

    assert approval is not None and approval["status"] == "unavailable"
    assert run is not None and run["status"] == "canceled"
    assert events[-1]["eventType"] == CoreEventType.RUN_CANCELED.value
    assert events[-1]["payload"]["reason"] == (
        "approval_recovery_after_restart"
    )
    assert await approval_store.recover_pending_approvals(db) == 0
