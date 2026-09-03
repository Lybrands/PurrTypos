"""Developer-only projection of the exact messages sent to model Providers."""

from __future__ import annotations

import json
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


def _redact(value: Any, *, field_name: str = "") -> Any:
    if _normalized_field_name(field_name) in _SECRET_FIELD_NAMES:
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(key): _redact(item, field_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


async def read_model_input_diagnostics(db, run_id: str) -> list[dict[str, Any]]:
    """Read development captures from durable model.call_recorded events."""

    rows = await db.fetch_all(
        "SELECT id, payload_json, create_time FROM ai_agent_run_events "
        "WHERE run_id = ? AND event_type = 'model.call_recorded' ORDER BY id",
        [str(run_id or "").strip()],
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = _mapping(row.get("payload_json"))
        parameters = _mapping(payload.get("parameters"))
        messages = parameters.get("inputMessages")
        if not isinstance(messages, list):
            messages = []
        result.append({
            "eventRowId": int(row.get("id") or 0),
            "phase": str(payload.get("phase") or "model"),
            "count": int(payload.get("count") or 0),
            "round": payload.get("round"),
            "logicalRound": payload.get("logicalRound"),
            "attempt": payload.get("attempt"),
            "revision": payload.get("revision"),
            "provider": parameters.get("provider"),
            "model": parameters.get("model"),
            "captured": bool(messages),
            "messages": _redact(messages),
            "recordedAt": row.get("create_time"),
        })
    return result


__all__ = ["read_model_input_diagnostics"]
