"""Host-enforced validation and output bounds for model-generated tool calls."""

from __future__ import annotations

import json
import re
from typing import Any


MAX_TOOL_CALLS_PER_ROUND = 8
MAX_TOOL_ARGUMENT_CHARS = 32_000
MAX_TOOL_RESULT_CHARS = 64_000


def validate_tool_call_batch(tool_calls: list[dict[str, Any]]) -> str | None:
    if len(tool_calls) > MAX_TOOL_CALLS_PER_ROUND:
        return (
            f"Too many tool calls in one round: {len(tool_calls)}; "
            f"maximum is {MAX_TOOL_CALLS_PER_ROUND}."
        )
    ids: list[str] = []
    for call in tool_calls:
        call_id = str(call.get("id") or "").strip()
        if not call_id:
            return "Every tool call must have a non-empty call id."
        ids.append(call_id)
        function = call.get("function")
        if not isinstance(function, dict) or not str(function.get("name") or "").strip():
            return f"Tool call {call_id} must have a function name."
    if len(ids) != len(set(ids)):
        return "Tool call ids must be unique within a round."
    return None


def parse_tool_arguments(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(raw, str):
        return None, "Tool arguments must be a JSON object string."
    if len(raw) > MAX_TOOL_ARGUMENT_CHARS:
        return None, f"Tool arguments exceed the {MAX_TOOL_ARGUMENT_CHARS}-character limit."
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return None, "Tool arguments are not valid JSON."
    if not isinstance(value, dict):
        return None, "Tool arguments must decode to a JSON object."
    return value, None


def validate_book_scope(ctx: dict[str, Any], args: dict[str, Any]) -> str | None:
    """Reject an explicit model-supplied book id that conflicts with host context."""
    host_book = str(ctx.get("bookId") or "").strip()
    supplied_book = str(args.get("bookId") or "").strip()
    if host_book and supplied_book and host_book != supplied_book:
        return "The requested bookId is outside the current Agent Run scope."
    return None


def sanitize_tool_result(content: Any) -> str:
    text = content if isinstance(content, str) else str(content or "")
    if len(text) > MAX_TOOL_RESULT_CHARS:
        return json.dumps({
            "success": False,
            "error": "Tool result exceeded the safe result-size limit.",
            "limit": MAX_TOOL_RESULT_CHARS,
        })
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return text
    if isinstance(payload, dict) and isinstance(payload.get("error"), str):
        payload["error"] = sanitize_error_message(payload["error"])
        return json.dumps(payload, ensure_ascii=False)
    return text


def sanitize_error_message(message: str) -> str:
    text = str(message or "")[:2_000]
    text = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)\b(?:sk|key)-[a-z0-9_-]{8,}\b", "[REDACTED_KEY]", text)
    text = re.sub(r"[A-Za-z]:\\[^\r\n\"']+", "[REDACTED_PATH]", text)
    return text or "Tool execution failed."
