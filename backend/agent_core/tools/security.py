"""Strict, provider-neutral tool input/output security helpers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent_core.contracts import ToolCall, ToolExecutionLimits
from agent_core.json_values import freeze_json_mapping, thaw_json_mapping
_ERROR_CODE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_BEARER = re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+")
_API_KEY = re.compile(r"(?i)\b(?:sk|key)-[a-z0-9_-]{8,}\b")
_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\[^\r\n\"']+")
_POSIX_PATH = re.compile(
    r"(?<![:/A-Za-z0-9])/(?:[^/\s\"'\\]+/)+[^/\s\"'\\]*"
)


@dataclass(frozen=True, slots=True)
class ToolSecurityFailure:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ParsedToolCall:
    call: ToolCall
    arguments: Mapping[str, Any]


def preflight_tool_calls(
    calls: Sequence[ToolCall],
    limits: ToolExecutionLimits,
) -> tuple[tuple[ParsedToolCall, ...], ToolSecurityFailure | None]:
    """Validate the complete model-generated batch before any side effect."""

    if len(calls) > limits.max_calls_per_batch:
        return (), ToolSecurityFailure(
            "too_many_tool_calls",
            f"Too many tool calls; maximum is {limits.max_calls_per_batch}.",
        )
    ids = [str(call.id or "").strip() for call in calls]
    if any(not call_id for call_id in ids):
        return (), ToolSecurityFailure(
            "invalid_tool_call_id",
            "Every tool call must have a non-empty id.",
        )
    if len(ids) != len(set(ids)):
        return (), ToolSecurityFailure(
            "duplicate_tool_call_id",
            "Tool call ids must be unique within a batch.",
        )
    if any(not str(call.name or "").strip() for call in calls):
        return (), ToolSecurityFailure(
            "invalid_tool_name",
            "Every tool call must have a function name.",
        )

    parsed: list[ParsedToolCall] = []
    for call in calls:
        arguments, failure = parse_tool_arguments(
            call.arguments_json,
            max_chars=limits.max_argument_chars,
        )
        if failure is not None or arguments is None:
            return (), failure
        parsed.append(ParsedToolCall(call=call, arguments=arguments))
    return tuple(parsed), None


def parse_tool_arguments(
    raw: Any,
    *,
    max_chars: int = 32_000,
) -> tuple[Mapping[str, Any] | None, ToolSecurityFailure | None]:
    if not isinstance(raw, str):
        return None, ToolSecurityFailure(
            "invalid_tool_arguments_type",
            "Tool arguments must be a JSON object string.",
        )
    if len(raw) > int(max_chars):
        return None, ToolSecurityFailure(
            "tool_arguments_too_large",
            f"Tool arguments exceed the {int(max_chars)}-character limit.",
        )
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return None, ToolSecurityFailure(
            "invalid_tool_arguments_json",
            "Tool arguments are not valid JSON.",
        )
    if not isinstance(value, dict):
        return None, ToolSecurityFailure(
            "invalid_tool_arguments_shape",
            "Tool arguments must decode to a JSON object.",
        )
    try:
        return freeze_json_mapping(value), None
    except (TypeError, ValueError):
        return None, ToolSecurityFailure(
            "invalid_tool_arguments_value",
            "Tool arguments contain an unsupported JSON value.",
        )


def normalize_error_code(value: Any, *, fallback: str = "tool_execution_failed") -> str:
    text = str(value or "").strip().lower()
    return text if _ERROR_CODE.fullmatch(text) else fallback


def safe_error_content(code: str, message: str | None = None) -> str:
    payload: dict[str, Any] = {
        "success": False,
        "errorCode": normalize_error_code(code),
    }
    if message:
        payload["error"] = sanitize_error_message(message)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def sanitize_tool_result(content: Any, *, max_chars: int = 64_000) -> str:
    text = content if isinstance(content, str) else str(content or "")
    if len(text) > int(max_chars):
        return safe_error_content(
            "tool_result_too_large",
            f"Tool result exceeded the {int(max_chars)}-character limit.",
        )
    return _redact_sensitive(text)


def sanitize_error_message(message: Any, *, max_chars: int = 2_000) -> str:
    text = _redact_sensitive(str(message or ""))[: int(max_chars)]
    return text or "Tool execution failed."


def summarize_tool_arguments(
    arguments: Mapping[str, Any],
    *,
    max_chars: int = 420,
) -> str:
    text = json.dumps(
        thaw_json_mapping(arguments),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    redacted = _redact_sensitive(text)
    limit = int(max_chars)
    if len(redacted) <= limit:
        return redacted
    marker = "\n…"
    return redacted[: max(0, limit - len(marker))] + marker[:limit]


def _redact_sensitive(text: str) -> str:
    value = _BEARER.sub("Bearer [REDACTED]", str(text or ""))
    value = _API_KEY.sub("[REDACTED_KEY]", value)
    value = _WINDOWS_PATH.sub("[REDACTED_PATH]", value)
    return _POSIX_PATH.sub("[REDACTED_PATH]", value)
