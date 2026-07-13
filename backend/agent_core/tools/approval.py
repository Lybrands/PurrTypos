"""Run-bound, one-shot in-memory approval broker for Core tools."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from agent_core.cancellation import OperationCanceled, await_with_cancellation
from agent_core.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    ApprovalStatus,
    RunId,
)
from agent_core.events import AgentEvent, CoreEventType
from agent_core.ports import CancellationSignal, EventSink


@dataclass(slots=True)
class _PendingApproval:
    run_id: RunId
    approval_id: str
    future: asyncio.Future[ApprovalStatus]


class InMemoryApprovalGateway:
    """Keep live approvals isolated by run and consume every decision once."""

    def __init__(self) -> None:
        self._pending: dict[tuple[RunId, str], _PendingApproval] = {}

    async def request(
        self,
        run_id: RunId,
        approval: ApprovalRequest,
        event_sink: EventSink,
        signal: CancellationSignal | None = None,
    ) -> ApprovalResult:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return ApprovalResult(None, ApprovalStatus.UNAVAILABLE)
        if signal is not None and signal.is_set():
            return ApprovalResult(None, ApprovalStatus.CANCELED)

        approval_id = str(uuid4())
        future: asyncio.Future[ApprovalStatus] = (
            asyncio.get_running_loop().create_future()
        )
        key = (normalized_run_id, approval_id)
        self._pending[key] = _PendingApproval(
            run_id=normalized_run_id,
            approval_id=approval_id,
            future=future,
        )

        try:
            await event_sink.emit(AgentEvent(
                type=CoreEventType.APPROVAL_REQUESTED,
                run_id=normalized_run_id,
                payload={
                    "approvalId": approval_id,
                    "toolName": approval.tool_call.name,
                    "title": approval.title,
                    "riskLevel": approval.risk_level.value,
                    "summary": approval.summary,
                },
            ))
            try:
                status = await asyncio.wait_for(
                    await_with_cancellation(asyncio.shield(future), signal),
                    timeout=approval.timeout_seconds,
                )
            except asyncio.TimeoutError:
                status = ApprovalStatus.TIMED_OUT
            except OperationCanceled:
                status = ApprovalStatus.CANCELED

            await event_sink.emit(AgentEvent(
                type=CoreEventType.APPROVAL_RESOLVED,
                run_id=normalized_run_id,
                payload={
                    "approvalId": approval_id,
                    "toolName": approval.tool_call.name,
                    "status": status.value,
                },
            ))
            return ApprovalResult(approval_id, status)
        finally:
            self._pending.pop(key, None)
            if not future.done():
                future.cancel()

    def resolve(
        self,
        run_id: RunId,
        approval_id: str,
        decision: ApprovalDecision,
    ) -> ApprovalStatus | None:
        key = (str(run_id or "").strip(), str(approval_id or "").strip())
        pending = self._pending.get(key)
        if pending is None or pending.future.done():
            return None
        normalized = ApprovalDecision(decision)
        status = (
            ApprovalStatus.APPROVED
            if normalized is ApprovalDecision.APPROVE
            else ApprovalStatus.REJECTED
        )
        pending.future.set_result(status)
        return status

    def cancel_pending(self, run_id: RunId) -> int:
        normalized_run_id = str(run_id or "").strip()
        count = 0
        for (pending_run_id, _), pending in tuple(self._pending.items()):
            if pending_run_id != normalized_run_id or pending.future.done():
                continue
            pending.future.set_result(ApprovalStatus.CANCELED)
            count += 1
        return count

    def pending_count(self, run_id: RunId | None = None) -> int:
        if run_id is None:
            return len(self._pending)
        normalized = str(run_id or "").strip()
        return sum(1 for pending_run_id, _ in self._pending if pending_run_id == normalized)
