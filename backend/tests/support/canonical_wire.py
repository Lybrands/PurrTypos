from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_CANONICAL_FIELDS = frozenset({
    "eventId",
    "outputStreamId",
    "runId",
    "turnId",
    "invocationId",
    "sequence",
    "source",
    "kind",
    "channel",
    "visibility",
    "payload",
    "occurredAt",
    "emittedAt",
})


def is_canonical_wire_event(value: Mapping[str, Any]) -> bool:
    return _CANONICAL_FIELDS.issubset(value)


def assert_raw_canonical_wire(events: Sequence[Mapping[str, Any]]) -> None:
    for event in events:
        if "done" in event or "error" in event:
            continue
        assert is_canonical_wire_event(event), event
        assert not {
            "delta",
            "commentaryDelta",
            "agentRunStarted",
            "agentRunTodosUpdated",
            "toolCalls",
            "toolIndexCompleted",
        }.intersection(event), event


def project_wire_events_for_legacy_assertions(
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Test-only compatibility view; production never performs this mapping."""

    projected: list[dict[str, Any]] = []
    for event in events:
        view = _project_event(event)
        if view is not None:
            projected.append(view)
    return projected


def project_wire_event_for_legacy_assertion(
    event: Mapping[str, Any],
) -> dict[str, Any] | None:
    return _project_event(event)


def _project_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    if not is_canonical_wire_event(event):
        result = event.get("runResult")
        if event.get("done") is True and isinstance(result, Mapping):
            status = str(result.get("status") or "")
            code = str(result.get("errorCode") or "")
            if status == "failed":
                return {"error": _legacy_error_message(code)}
            if status == "blocked":
                return {"error": "Agent 未完成全部计划步骤，已安全停止。"}
            if status == "done":
                return {
                    key: value for key, value in event.items()
                    if key != "runResult"
                }
        return dict(event)
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    kind = str(event.get("kind") or "")
    channel = str(event.get("channel") or "")
    source = str(event.get("source") or "")

    if source == "provider" and kind == "provider.content_delta":
        delta = str(payload.get("delta") or "")
        if not delta:
            return None
        if channel == "final":
            return {"delta": delta}
        if channel == "commentary":
            return {"commentaryDelta": delta}
        return None

    if source == "provider" and kind == "provider.delta_batch":
        entries = payload.get("entries")
        delta = "".join(
            str((entry.get("payload") or {}).get("delta") or "")
            for entry in (entries if isinstance(entries, list) else [])
            if isinstance(entry, Mapping)
            and entry.get("kind") == "provider.content_delta"
            and isinstance(entry.get("payload"), Mapping)
        )
        if not delta:
            return None
        if channel == "final":
            return {"delta": delta}
        if channel == "commentary":
            return {"commentaryDelta": delta}
        return None

    if kind == "runtime.event":
        event_type = str(payload.get("eventType") or "")
        data = payload.get("data")
        if not event_type or not isinstance(data, Mapping):
            return None
        return _legacy_runtime_view(
            event_type,
            str(event.get("runId") or ""),
            data,
        )

    if kind == "run.lifecycle":
        status = str(payload.get("status") or "")
        names = {
            "done": "agentRunCompleted",
            "blocked": "agentRunBlocked",
            "failed": "agentRunFailed",
            "canceled": "agentRunCanceled",
            "running": "agentRunStarted",
        }
        name = names.get(status)
        return (
            {name: {"runId": event.get("runId"), **dict(payload)}}
            if name else None
        )

    if kind == "delegation.event":
        event_type = str(payload.get("eventType") or "")
        if event_type == "status":
            name = (
                "agentDelegationCreated"
                if payload.get("status") == "queued"
                else "agentDelegationUpdated"
            )
            return {name: {"runId": event.get("runId"), **dict(payload)}}
    return None


def _legacy_error_message(code: str) -> str:
    return {
        "missing_required_tool_call": (
            "当前计划步骤必须调用工具，但模型未返回结构化调用。"
        ),
        "planning_invalid": "Agent 计划格式无效，已安全停止。",
    }.get(code, "Agent 运行过程中发生异常，已安全停止；请稍后重试。")


def _legacy_runtime_view(
    event_type: str,
    run_id: str,
    payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Old UI view retained only so pre-refactor business assertions stay useful."""

    if event_type == "run.started":
        return {"agentRunStarted": {"runId": run_id, **dict(payload)}}
    if event_type in {
        "conversation.compaction.started",
        "conversation.compaction.completed",
    }:
        return {"contextCompaction": dict(payload)}
    if event_type == "run.todos_updated":
        return {"agentRunTodosUpdated": {
            "runId": run_id,
            **dict(payload),
            "steps": [
                _step_view(step)
                for step in payload.get("steps", [])
                if isinstance(step, Mapping)
                and not bool(step.get("protocol_private"))
            ],
        }}
    if event_type == "run.todo_updated":
        step = payload.get("step")
        if isinstance(step, Mapping) and bool(step.get("protocol_private")):
            return None
        return {"agentRunTodoUpdated": {
            "runId": run_id,
            "stepId": payload.get("step_id"),
            "step": _step_view(step) if isinstance(step, Mapping) else step,
            "status": payload.get("status"),
        }}
    if event_type == "model.call_recorded":
        return {"modelInvocation": dict(payload)}
    terminal_names = {
        "run.completed": "agentRunCompleted",
        "run.blocked": "agentRunBlocked",
        "run.failed": "agentRunFailed",
        "run.canceled": "agentRunCanceled",
    }
    if event_type in terminal_names:
        terminal = {"runId": run_id, **dict(payload)}
        final = str(payload.get("final_response") or "")
        if final:
            terminal["finalResponse"] = final
        return {terminal_names[event_type]: terminal}
    if event_type == "tool.calls_started":
        return {
            "toolCalls": [
                {
                    "id": call.get("id"),
                    "type": "function",
                    "displayNames": dict(call.get("display_names") or {}),
                    "function": {
                        "name": call.get("name"),
                        "arguments": call.get("arguments_json", ""),
                    },
                }
                for call in payload.get("calls", [])
                if isinstance(call, Mapping)
            ],
            "toolCallsInProgress": bool(payload.get("in_progress")),
            "model": payload.get("model"),
        }
    if event_type == "tool.results":
        return {"toolResults": [
            {
                "tool_call_id": item.get("tool_call_id"),
                "name": item.get("tool_name"),
                "content": item.get("content", ""),
            }
            for item in payload.get("results", [])
            if isinstance(item, Mapping)
        ]}
    if event_type == "tool.call_completed":
        chunk: dict[str, Any] = {
            "toolIndexCompleted": int(payload.get("index") or 0),
        }
        fields = {
            "toolCallId": payload.get("toolCallId") or payload.get("tool_call_id"),
            "toolName": payload.get("toolName") or payload.get("tool_name"),
            "toolOutcome": payload.get("outcome"),
            "toolErrorCode": payload.get("errorCode") or payload.get("error_code"),
            "toolExceptionType": payload.get("exceptionType") or payload.get("exception_type"),
        }
        chunk.update({key: value for key, value in fields.items() if value})
        if payload.get("fromCache") or payload.get("from_cache"):
            chunk["toolFromCache"] = True
        return chunk
    if event_type == "tool.round_completed":
        return None
    if event_type == "approval.requested":
        return {"toolApprovalRequired": {"runId": run_id, **dict(payload)}}
    if event_type == "approval.resolved":
        return {"toolApprovalResolved": {"runId": run_id, **dict(payload)}}
    if event_type == "delegation.created":
        return {"agentDelegationCreated": {"runId": run_id, **dict(payload)}}
    if event_type in {
        "delegation.claimed",
        "delegation.completed",
        "delegation.failed",
        "delegation.canceled",
    }:
        return {"agentDelegationUpdated": {"runId": run_id, **dict(payload)}}
    if event_type == "context.budgeted":
        return {"contextBudget": dict(payload)}
    if event_type == "context.usage_recorded":
        return {"contextBudget": dict(payload)}
    if event_type == "task.admission_decided":
        return {"taskAdmission": {"runId": run_id, **dict(payload)}}
    if event_type == "long_task.dispatched":
        return {"longTaskDispatched": {"runId": run_id, **dict(payload)}}
    if event_type == "long_task.progress":
        return {"longTaskProgress": {"runId": run_id, **dict(payload)}}
    domain_names = {
        "writing.proposed_chapter_diff": "proposedChapterDiff",
        "writing.proposed_setting_diff": "proposedSettingDiff",
        "writing.setting_updated": "settingUpdated",
        "writing.chapter_created": "chapterCreated",
    }
    if event_type in domain_names:
        return {domain_names[event_type]: dict(payload)}
    if event_type == "writing.progress":
        return dict(payload)
    return None


def _step_view(step: Mapping[str, Any]) -> dict[str, Any]:
    capability = str(step.get("planning_capability") or "").strip()
    result = {
        "id": step.get("id"),
        "title": step.get("title"),
        "type": step.get("type"),
        "executor": step.get("executor"),
        "status": step.get("status"),
        "riskLevel": step.get("risk_level"),
        "suggestedTools": [capability] if capability else list(step.get("suggested_tools") or ()),
        "description": step.get("description"),
        "resultSummary": step.get("result_summary"),
        "error": step.get("error"),
    }
    return result


__all__ = [
    "assert_raw_canonical_wire",
    "is_canonical_wire_event",
    "project_wire_event_for_legacy_assertion",
    "project_wire_events_for_legacy_assertions",
]
