"""Durable, run-bound approval coordination for Agent tools."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from uuid import uuid4

from agent_core.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    ApprovalStatus,
    RunId,
)
from agent_core.events import AgentEvent, CoreEventType
from agent_core.ports import CancellationSignal, EventSink
from infrastructure.persistence import approval_store


@dataclass(slots=True)
class _PendingApproval:
    run_id: RunId
    approval_id: str
    future: asyncio.Future[ApprovalStatus]


class SqliteApprovalGateway:
    """Persist the winning decision before unblocking tool execution."""

    def __init__(self, db) -> None:
        self._db = db
        self._pending: dict[tuple[RunId, str], _PendingApproval] = {}
        self._settlement_lock = asyncio.Lock()
        self._closed = False

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
        if self._closed or (signal is not None and signal.is_set()):
            return ApprovalResult(None, ApprovalStatus.CANCELED)

        approval_id = str(uuid4())
        key = (normalized_run_id, approval_id)
        future = asyncio.get_running_loop().create_future()
        expires_at_ms = approval_store.now_ms() + max(
            0,
            int(float(approval.timeout_seconds) * 1_000),
        )
        try:
            await approval_store.create_approval(
                self._db,
                approval_id=approval_id,
                run_id=normalized_run_id,
                tool_call_id=approval.tool_call.id,
                tool_name=approval.tool_call.name,
                title=approval.title,
                risk_level=approval.risk_level.value,
                summary=approval.summary,
                expires_at_ms=expires_at_ms,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            return ApprovalResult(None, ApprovalStatus.UNAVAILABLE)

        self._pending[key] = _PendingApproval(
            run_id=normalized_run_id,
            approval_id=approval_id,
            future=future,
        )
        signal_task: asyncio.Task[None] | None = None
        timeout_task: asyncio.Task[None] | None = None
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
                    "expiresAtMs": expires_at_ms,
                },
            ))
            if signal is not None:
                signal_task = asyncio.create_task(
                    self._settle_when_signaled(key, signal)
                )
            timeout_task = asyncio.create_task(
                self._settle_after_timeout(key, approval.timeout_seconds)
            )
            status = await asyncio.shield(future)
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
        except asyncio.CancelledError:
            await asyncio.shield(self._settle(key, ApprovalStatus.CANCELED))
            raise
        except Exception:
            await self._settle(key, ApprovalStatus.UNAVAILABLE)
            raise
        finally:
            for task in (signal_task, timeout_task):
                if task is not None:
                    task.cancel()
            for task in (signal_task, timeout_task):
                if task is not None:
                    with suppress(asyncio.CancelledError):
                        await task
            self._pending.pop(key, None)
            if not future.done():
                future.cancel()

    async def resolve(
        self,
        run_id: RunId,
        approval_id: str,
        decision: ApprovalDecision,
    ) -> ApprovalStatus | None:
        normalized = ApprovalDecision(decision)
        status = (
            ApprovalStatus.APPROVED
            if normalized is ApprovalDecision.APPROVE
            else ApprovalStatus.REJECTED
        )
        key = (str(run_id or "").strip(), str(approval_id or "").strip())
        result = await self._settle(
            key,
            status,
            require_unexpired=True,
        )
        if result is not None:
            return result
        row = await approval_store.get_approval(self._db, key[1])
        if (
            row is not None
            and row.get("run_id") == key[0]
            and row.get("status") == approval_store.PENDING_STATUS
            and int(row.get("expires_at_ms") or 0) <= approval_store.now_ms()
        ):
            await self._settle(key, ApprovalStatus.TIMED_OUT)
        return None

    async def cancel_pending(self, run_id: RunId) -> int:
        normalized = str(run_id or "").strip()
        count = 0
        for key in tuple(self._pending):
            if key[0] != normalized:
                continue
            if await self._settle(key, ApprovalStatus.CANCELED) is not None:
                count += 1
        return count

    async def cancel_all(self) -> int:
        count = 0
        for key in tuple(self._pending):
            if await self._settle(key, ApprovalStatus.CANCELED) is not None:
                count += 1
        return count

    async def close(self) -> int:
        self._closed = True
        return await self.cancel_all()

    def pending_count(self, run_id: RunId | None = None) -> int:
        normalized = str(run_id or "").strip() if run_id is not None else None
        return sum(
            1
            for (pending_run_id, _), pending in self._pending.items()
            if not pending.future.done()
            and (normalized is None or pending_run_id == normalized)
        )

    async def _settle_when_signaled(
        self,
        key: tuple[RunId, str],
        signal: CancellationSignal,
    ) -> None:
        await signal.wait()
        await self._settle(key, ApprovalStatus.CANCELED)

    async def _settle_after_timeout(
        self,
        key: tuple[RunId, str],
        timeout_seconds: float,
    ) -> None:
        await asyncio.sleep(max(0.0, float(timeout_seconds)))
        await self._settle(key, ApprovalStatus.TIMED_OUT)

    async def _settle(
        self,
        key: tuple[RunId, str],
        status: ApprovalStatus,
        *,
        require_unexpired: bool = False,
    ) -> ApprovalStatus | None:
        async with self._settlement_lock:
            pending = self._pending.get(key)
            if pending is None or pending.future.done():
                return None
            changed = await approval_store.transition_pending(
                self._db,
                approval_id=key[1],
                run_id=key[0],
                status=status,
                require_unexpired=require_unexpired,
            )
            if not changed:
                return None
            normalized = ApprovalStatus(status)
            pending.future.set_result(normalized)
            return normalized
