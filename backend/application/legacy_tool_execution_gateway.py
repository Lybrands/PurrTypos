"""Stage-3 compatibility gateway over the existing policy-enforcing executor."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agent_core.contracts import (
    ApprovalStatus,
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolBatchResult,
    ToolCall,
    ToolCallResult,
)
from agent_core.events import AgentEvent, CoreEventType
from agent_core.ports import CancellationSignal, EventSink
from services import tool_executor


_MISSING = object()


class LegacyToolExecutionGateway:
    """Translate typed batches to the current ``run_tools`` facade.

    The immutable authorization snapshot remains in ``ToolBatchRequest``.  It
    is copied into the legacy mutable context only for the duration of the
    compatibility call and is restored even when execution fails.
    """

    async def execute_batch(
        self,
        request: ToolBatchRequest,
        event_sink: EventSink,
        signal: CancellationSignal | None = None,
    ) -> ToolBatchResult:
        requested_names = frozenset(call.name for call in request.calls)
        if not requested_names.issubset(request.allowed_tool_names):
            return _rejected_batch(request.calls)

        context = request.state.domain
        previous_authorization = context.get("allowedToolNames", _MISSING)
        context["allowedToolNames"] = set(request.allowed_tool_names)
        progress_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        cache_hits: tuple[bool, ...] = ()

        async def _forward_progress() -> None:
            while True:
                raw = await progress_queue.get()
                if raw is None:
                    return
                await event_sink.emit(_progress_event(raw, request.run_id))

        worker = asyncio.create_task(_forward_progress())

        def _capture_progress(raw: dict[str, Any]) -> None:
            nonlocal cache_hits
            snapshot = dict(raw)
            if isinstance(snapshot.get("toolReadCacheMask"), list):
                cache_hits = tuple(bool(item) for item in snapshot["toolReadCacheMask"])
            progress_queue.put_nowait(snapshot)

        try:
            raw_results = await tool_executor.run_tools(
                [_legacy_call(call) for call in request.calls],
                context,
                send_chunk=_capture_progress,
                signal=signal,
            )
        finally:
            try:
                progress_queue.put_nowait(None)
                await worker
            finally:
                if previous_authorization is _MISSING:
                    context.pop("allowedToolNames", None)
                else:
                    context["allowedToolNames"] = previous_authorization

        return _classify_results(
            request.calls,
            raw_results,
            cache_hits=cache_hits,
        )


def _legacy_call(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": call.arguments_json,
        },
    }


def _progress_event(raw: dict[str, Any], run_id: str | None) -> AgentEvent:
    if "toolReadCacheMask" in raw:
        return AgentEvent(
            type="tool.read_cache_mask",
            run_id=run_id,
            payload={"mask": list(raw.get("toolReadCacheMask") or [])},
        )
    if "toolIndexCompleted" in raw:
        return AgentEvent(
            type=CoreEventType.TOOL_CALL_COMPLETED,
            run_id=run_id,
            payload={
                "index": int(raw.get("toolIndexCompleted") or 0),
                "from_cache": bool(raw.get("toolFromCache")),
            },
        )
    if "toolApprovalRequired" in raw:
        payload = raw.get("toolApprovalRequired")
        return AgentEvent(
            type=CoreEventType.APPROVAL_REQUESTED,
            run_id=run_id,
            payload=dict(payload) if isinstance(payload, dict) else {},
        )
    if "proposedChapterDiff" in raw:
        payload = raw.get("proposedChapterDiff")
        return AgentEvent(
            type="writing.proposed_chapter_diff",
            run_id=run_id,
            payload=dict(payload) if isinstance(payload, dict) else {},
        )
    return AgentEvent(type="tool.progress", run_id=run_id, payload=raw)


def _classify_results(
    calls: tuple[ToolCall, ...],
    raw_results: list[dict],
    *,
    cache_hits: tuple[bool, ...],
) -> ToolBatchResult:
    calls_by_id = {call.id: call for call in calls}
    results: list[ToolCallResult] = []
    statuses: set[ApprovalStatus] = set()
    errors: list[str] = []

    for raw in raw_results:
        call_id = str(raw.get("tool_call_id") or "").strip()
        call = calls_by_id.get(call_id)
        tool_name = call.name if call is not None else "unknown"
        content = str(raw.get("content") or "")
        payload = _json_object(content)
        approval_status = _approval_status(payload.get("approvalStatus"))
        error = _optional_text(payload.get("error"))
        if approval_status is not None:
            statuses.add(approval_status)
        if error and approval_status is not ApprovalStatus.REJECTED:
            errors.append(error)
        results.append(ToolCallResult(
            tool_call_id=call_id,
            tool_name=tool_name,
            content=content,
            approval_status=approval_status,
            error=error,
        ))

    if ApprovalStatus.CANCELED in statuses:
        outcome = ToolBatchOutcome.CANCELED
        error_code = "approval_canceled"
    elif statuses & {ApprovalStatus.TIMED_OUT, ApprovalStatus.UNAVAILABLE}:
        outcome = ToolBatchOutcome.FAILED
        error_code = "approval_unavailable"
    elif errors:
        outcome = ToolBatchOutcome.FAILED
        error_code = "tool_execution_failed"
    elif ApprovalStatus.REJECTED in statuses:
        outcome = ToolBatchOutcome.DECLINED
        error_code = None
    else:
        outcome = ToolBatchOutcome.COMPLETED
        error_code = None

    return ToolBatchResult(
        results=tuple(results),
        outcome=outcome,
        error=error_code,
        cache_hits=cache_hits,
    )


def _rejected_batch(calls: tuple[ToolCall, ...]) -> ToolBatchResult:
    content = json.dumps(
        {"success": False, "errorCode": "tool_not_authorized"},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return ToolBatchResult(
        results=tuple(
            ToolCallResult(
                tool_call_id=call.id,
                tool_name=call.name,
                content=content,
                error="tool_not_authorized",
            )
            for call in calls
        ),
        outcome=ToolBatchOutcome.REJECTED,
        error="tool_not_authorized",
    )


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _approval_status(value: Any) -> ApprovalStatus | None:
    try:
        return ApprovalStatus(str(value)) if value else None
    except ValueError:
        return None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
