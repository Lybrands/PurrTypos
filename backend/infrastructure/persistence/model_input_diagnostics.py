"""Developer-only projection of the exact messages sent to model Providers."""

from __future__ import annotations

import json
import re
from typing import Any


_SECRET_FIELD_NAMES = frozenset({
    "apikey",
    "authorization",
    "proxyauthorization",
    "password",
    "secret",
    "accesstoken",
    "refreshtoken",
    "token",
    "reasoning", "reasoningcontent", "reasoningdetails", "chainofthought",
    "clientsecret", "credential", "credentials", "cookie", "setcookie", "privatekey",
})


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalized_field_name(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def _redact(value: Any, *, field_name: str = "", depth: int = 0) -> Any:
    if depth > 20:
        return "<omitted: nesting limit>"
    if _normalized_field_name(field_name) in _SECRET_FIELD_NAMES:
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(key): _redact(item, field_name=str(key), depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(_redact(parsed, depth=depth + 1), ensure_ascii=False)
        value = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[^\s\"',;]+", "<redacted authorization>", value)
        value = re.sub(
            r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
            r"authorization|password|secret|token)\b[\"']?\s*[:=]\s*[\"']?)[^\s\"',&}\]]+",
            r"\1<redacted>", value,
        )
        return re.sub(r"(https?://)[^\s/@:]+:[^\s/@]+@", r"\1<redacted>@", value, flags=re.IGNORECASE)
    return value


async def read_model_input_diagnostics(db, run_id: str) -> list[dict[str, Any]]:
    """Read one authoritative invocation receipt, including private model tasks."""

    rows = await db.fetch_all(
        "SELECT id, payload_json, create_time FROM ai_agent_run_events "
        "WHERE run_id = ? AND event_type = 'stream.opened' ORDER BY id",
        [str(run_id or "").strip()],
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = _mapping(row.get("payload_json"))
        calls = payload.get("callParameters") or []
        parameters = _mapping(calls[0]) if isinstance(calls, list) and calls else {}
        scope = _mapping(payload.get("planningScope"))
        messages = parameters.get("inputMessages")
        if not isinstance(messages, list):
            messages = []
        sdk_attempt = parameters.get("sdkAttemptId")
        sent = await db.fetch_one(
            "SELECT record_json FROM ai_model_sdk_requests WHERE attempt_id = ?", [sdk_attempt],
        ) if sdk_attempt else None
        result.append({
            "eventRowId": int(row.get("id") or 0),
            "phase": "planning" if scope else str(payload.get("outputIntent") or "model"),
            "count": 1,
            "round": None,
            "logicalRound": None,
            "attempt": payload.get("planningAttempt") if scope else None,
            "revision": scope.get("revision"),
            "provider": parameters.get("provider"),
            "model": parameters.get("model"),
            "captured": bool(messages),
            "messages": _redact(messages),
            "recordedAt": row.get("create_time"),
            "sdkRequest": _redact(_mapping(sent["record_json"])) if sent else None,
        })
    return result


__all__ = ["read_model_input_diagnostics"]
