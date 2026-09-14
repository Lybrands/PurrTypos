"""Developer-only, paged projection of durable tool request/response records."""

from __future__ import annotations

import json
import re
from typing import Any

from infrastructure.persistence.model_input_diagnostics import (
    _SECRET_FIELD_NAMES,
    _mapping,
    _normalized_field_name,
)


_EVENT_TYPES = (
    "tool.calls_started",
    "tool.results",
    "tool.call_completed",
    "operation.started",
)
_PAGE_SIZE = 50
_PREVIEW_CHARACTERS = 32_000
_PRIVATE_FIELDS = _SECRET_FIELD_NAMES | {
    "clientsecret", "credential", "credentials", "cookie", "setcookie",
    "reasoning", "chainofthought", "privatekey",
}
_AUTH_CREDENTIAL = re.compile(r"(?i)\b(?:Bearer|Basic)\s+[^\s\"',;]+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"authorization|password|secret|token)\b[\"']?\s*[:=]\s*[\"']?)"
    r"[^\s\"',&}\]]+"
)
_URL_CREDENTIAL = re.compile(r"(https?://)[^\s/@:]+:[^\s/@]+@", re.IGNORECASE)


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 20:
        return "<omitted: nesting limit>"
    if isinstance(value, dict):
        return {
            key: "<redacted>" if _normalized_field_name(str(key)) in _PRIVATE_FIELDS
            else _redact(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, depth + 1) for item in value]
    if isinstance(value, str):
        # Tool arguments and content are JSON strings, including nested JSON.
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(_redact(parsed, depth + 1), ensure_ascii=False)
        value = _AUTH_CREDENTIAL.sub("<redacted authorization>", value)
        value = _SECRET_ASSIGNMENT.sub(r"\1<redacted>", value)
        return _URL_CREDENTIAL.sub(r"\1<redacted>@", value)
    return value


def _preview(value: Any) -> dict[str, Any]:
    # Pretty-print the outer tool payload, but retain nested JSON strings as
    # strings so diagnostics do not misrepresent the model's argument types.
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            value = parsed
    safe = _redact(value)
    text = safe if isinstance(safe, str) else json.dumps(safe, ensure_ascii=False, indent=2)
    return {
        "text": text[:_PREVIEW_CHARACTERS],
        "characters": len(text),
        "truncated": len(text) > _PREVIEW_CHARACTERS,
    }


def _presentation_group(value: Any) -> dict[str, str] | None:
    group = _mapping(value)
    key = str(group.get("key") or "").strip()
    label = str(group.get("label") or "").strip()
    return {"key": key, "label": label} if key and label else None


async def read_tool_diagnostics(db, run_id: str, *, after: int = 0) -> dict[str, Any]:
    """Return partial call records; consumers merge pages by (runId, toolCallId).

    Raw runtime events remain authoritative for tool IO.  The canonical tool
    Operation is the authority for the argument-sensitive public name, and is
    joined by toolCallId. Canonical runtime.event envelopes are also supported
    for persisted journals without raw rows. Neither provider deltas nor
    reasoning records are queried. Missing fields stay missing.
    """
    placeholders = ",".join("?" for _ in _EVENT_TYPES)
    rows = await db.fetch_all(
        "SELECT id, event_type, payload_json, create_time FROM ai_agent_run_events "
        f"WHERE run_id = ? AND id > ? AND (event_type IN ({placeholders}) OR "
        "(event_type = 'runtime.event' AND json_valid(payload_json) AND "
        f"json_extract(payload_json, '$.eventType') IN ({placeholders}))) "
        "ORDER BY id LIMIT ?",
        [run_id, after, *_EVENT_TYPES, *_EVENT_TYPES, _PAGE_SIZE + 1],
    )
    calls: dict[str, dict[str, Any]] = {}
    for row in rows[:_PAGE_SIZE]:
        event_type = row["event_type"]
        payload = _mapping(row["payload_json"])
        if event_type == "runtime.event":
            event_type = payload.get("eventType")
            payload = _mapping(payload.get("data"))
        items = (
            payload.get("calls", []) if event_type == "tool.calls_started"
            else payload.get("results", []) if event_type == "tool.results"
            else [payload]
        )
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if event_type == "operation.started":
                if item.get("kind") != "tool":
                    continue
                label_params = _mapping(
                    _mapping(item.get("display")).get("labelParams")
                )
                call_id = label_params.get("toolCallId")
            else:
                label_params = {}
                call_id = item.get("id") if event_type == "tool.calls_started" else (
                    item.get("tool_call_id") if event_type == "tool.results"
                    else item.get("toolCallId")
                )
            if not isinstance(call_id, str) or not call_id.strip():
                continue
            call = calls.setdefault(call_id, {
                "runId": run_id, "toolCallId": call_id, "eventRowId": row["id"],
            })
            name = (
                label_params.get("toolName")
                or item.get("name")
                or item.get("tool_name")
                or item.get("toolName")
            )
            if name:
                call["name"] = str(name)
            if event_type == "operation.started":
                display_names = _mapping(label_params.get("displayNames"))
                display_name = display_names.get("zh-CN")
                if isinstance(display_name, str) and display_name.strip():
                    call["displayName"] = display_name.strip()
                presentation_group = _presentation_group(
                    label_params.get("presentationGroup")
                )
                if presentation_group is not None:
                    call["presentationGroup"] = presentation_group
                if item.get("operationId"):
                    call["operationId"] = str(item["operationId"])
            elif event_type == "tool.calls_started":
                call["startedAt"] = row["create_time"]
                if "arguments_json" in item:
                    call["arguments"] = _preview(item["arguments_json"])
            elif event_type == "tool.results":
                call["completedAt"] = row["create_time"]
                if "content" in item:
                    call["result"] = _preview(item["content"])
                call["cached"] = bool(item.get("from_cache"))
                call["status"] = "failed" if item.get("error") else "completed"
                if item.get("error"):
                    call["error"] = _preview(item["error"])
                if item.get("approval_status"):
                    call["approvalStatus"] = str(item["approval_status"])
            else:
                call["completedAt"] = row["create_time"]
                call["cached"] = bool(item.get("fromCache"))
                if item.get("outcome"):
                    call["outcome"] = str(item["outcome"])
                    call["status"] = (
                        "completed" if item["outcome"] == "completed" else "failed"
                    )
                if item.get("errorCode"):
                    call["error"] = _preview(item["errorCode"])
    page = rows[:_PAGE_SIZE]
    return {
        "runId": run_id,
        "calls": list(calls.values()),
        "nextCursor": page[-1]["id"] if page else after,
        "hasMore": len(rows) > _PAGE_SIZE,
    }
