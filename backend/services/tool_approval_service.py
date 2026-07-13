"""In-process Human-in-the-Loop approval broker for protected tool calls."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable
from uuid import uuid4

from services.tool_policy import ToolPolicy


class ToolApprovalStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"
    CANCELED = "canceled"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ToolApprovalResult:
    approval_id: str | None
    status: ToolApprovalStatus

    @property
    def approved(self) -> bool:
        return self.status is ToolApprovalStatus.APPROVED


@dataclass
class _PendingToolApproval:
    approval_id: str
    future: asyncio.Future[ToolApprovalStatus]


class ToolApprovalBroker:
    """Suspend one tool call until the desktop UI explicitly decides it.

    The app is a local single-user desktop process, so pending approvals are
    deliberately in-memory and tied to the live SSE request.  A backend restart
    or disconnected request fails closed rather than replaying an old write.
    """

    def __init__(self) -> None:
        self._pending: dict[str, _PendingToolApproval] = {}

    async def request(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        policy: ToolPolicy,
        send_chunk: Callable[[dict], None] | None,
        signal: asyncio.Event | None,
        timeout_seconds: float = 300,
    ) -> ToolApprovalResult:
        if send_chunk is None:
            return ToolApprovalResult(None, ToolApprovalStatus.UNAVAILABLE)

        approval_id = str(uuid4())
        future: asyncio.Future[ToolApprovalStatus] = asyncio.get_running_loop().create_future()
        pending = _PendingToolApproval(approval_id=approval_id, future=future)
        self._pending[approval_id] = pending
        send_chunk({
            "toolApprovalRequired": {
                "approvalId": approval_id,
                "toolName": tool_name,
                "title": policy.title,
                "riskLevel": policy.risk_level,
                "summary": summarize_tool_arguments(args),
            },
        })

        abort_waiter: asyncio.Task[bool] | None = None
        try:
            waiters: set[asyncio.Future[Any] | asyncio.Task[Any]] = {future}
            if signal is not None:
                abort_waiter = asyncio.create_task(signal.wait())
                waiters.add(abort_waiter)
            done, _ = await asyncio.wait(
                waiters,
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                return ToolApprovalResult(approval_id, ToolApprovalStatus.TIMED_OUT)
            if abort_waiter is not None and abort_waiter in done and signal and signal.is_set():
                return ToolApprovalResult(approval_id, ToolApprovalStatus.CANCELED)
            return ToolApprovalResult(approval_id, future.result())
        finally:
            if abort_waiter is not None:
                abort_waiter.cancel()
            self._pending.pop(approval_id, None)

    def resolve(self, approval_id: str, approved: bool) -> ToolApprovalStatus | None:
        pending = self._pending.get(str(approval_id or ""))
        if pending is None or pending.future.done():
            return None
        status = ToolApprovalStatus.APPROVED if approved else ToolApprovalStatus.REJECTED
        pending.future.set_result(status)
        return status


def summarize_tool_arguments(args: dict[str, Any], *, max_length: int = 420) -> str:
    """Render an inspectable but bounded preview for the human approval card."""

    try:
        text = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, indent=2)
    except (TypeError, ValueError):
        text = str(args or "")
    return text if len(text) <= max_length else text[:max_length] + "\n…（参数已截断）"


_broker = ToolApprovalBroker()


async def request_tool_approval(**kwargs: Any) -> ToolApprovalResult:
    return await _broker.request(**kwargs)


def resolve_tool_approval(approval_id: str, approved: bool) -> ToolApprovalStatus | None:
    return _broker.resolve(approval_id, approved)
