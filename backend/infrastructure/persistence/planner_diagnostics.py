"""Developer-only projection of persisted Planner model content."""

from __future__ import annotations

import json
from typing import Any


_PLANNING_PROTOCOL = "purra.planning-stream/v1"
_MAX_RAW_CONTENT_CHARACTERS = 512_000
_TIMING_FIELDS = (
    "firstActivityMs",
    "firstProgressMs",
    "firstSemanticChunkMs",
    "firstPublicProgressMs",
    "planReceivedMs",
    "invocationDurationMs",
    "validationMs",
    "rejectedPublicProgressRecords",
    "activitySupport",
)


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def read_planner_model_outputs(
    db,
    run_id: str,
) -> list[dict[str, Any]]:
    """Reconstruct exact Planner content deltas without exposing reasoning."""

    rows = await db.fetch_all(
        "SELECT id, event_id, sequence, kind, visibility, invocation_id, "
        "output_stream_id, occurred_at, payload_json "
        "FROM ai_agent_run_events WHERE run_id = ? AND kind IN "
        "('stream.opened', 'provider.delta_batch', 'planning.progress', "
        "'stream.committed', 'stream.aborted', 'model.diagnostics') "
        "ORDER BY COALESCE(sequence, id), id",
        [str(run_id or "").strip()],
    )
    outputs: dict[str, dict[str, Any]] = {}
    streams: dict[str, str] = {}
    content_parts: dict[str, dict[tuple[int, int], str]] = {}
    conflicting_parts: set[str] = set()

    for row in rows:
        if row.get("kind") != "stream.opened":
            continue
        payload = _mapping(row.get("payload_json"))
        planning_scope = payload.get("planningScope")
        if (
            payload.get("outputProtocol") != _PLANNING_PROTOCOL
            or not isinstance(planning_scope, dict)
        ):
            continue
        invocation_id = str(row.get("invocation_id") or "").strip()
        output_stream_id = str(row.get("output_stream_id") or "").strip()
        if not invocation_id or not output_stream_id:
            continue
        outputs[invocation_id] = {
            "runId": str(run_id),
            "invocationId": invocation_id,
            "outputStreamId": output_stream_id,
            "operationId": str(planning_scope.get("operationId") or ""),
            "revision": _integer(planning_scope.get("revision")),
            "attempt": _integer(payload.get("planningAttempt")),
            "model": str(payload.get("model") or "") or None,
            "status": "open",
            "finishReason": None,
            "errorCode": None,
            "progressRecords": [],
            "timing": {},
        }
        streams[output_stream_id] = invocation_id
        content_parts[invocation_id] = {}

    for row in rows:
        invocation_id = str(row.get("invocation_id") or "").strip()
        if invocation_id not in outputs:
            invocation_id = streams.get(
                str(row.get("output_stream_id") or "").strip(),
                "",
            )
        output = outputs.get(invocation_id)
        if output is None:
            continue
        payload = _mapping(row.get("payload_json"))
        kind = str(row.get("kind") or "")
        if kind == "provider.delta_batch":
            entries = payload.get("entries")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                # Planner reasoning uses provider.reasoning_delta and remains
                # private even in this developer projection.
                if entry.get("kind") != "provider.content_delta":
                    continue
                entry_payload = entry.get("payload")
                if not isinstance(entry_payload, dict):
                    continue
                delta = entry_payload.get("delta")
                if not isinstance(delta, str):
                    continue
                key = (
                    _integer(entry.get("sourceChunkIndex")),
                    _integer(entry.get("sourcePartIndex")),
                )
                previous = content_parts[invocation_id].get(key)
                if previous is not None and previous != delta:
                    conflicting_parts.add(invocation_id)
                    continue
                content_parts[invocation_id][key] = delta
        elif kind == "planning.progress":
            output["progressRecords"].append({
                "eventId": row.get("event_id"),
                "sequence": row.get("sequence"),
                "recordIndex": _integer(payload.get("recordIndex")),
                "revision": _integer(payload.get("revision")),
                "attempt": _integer(payload.get("attempt")),
                "text": str(payload.get("text") or ""),
                "sourceStart": _integer(payload.get("sourceStart")),
                "sourceEnd": _integer(payload.get("sourceEnd")),
                "occurredAt": row.get("occurred_at"),
            })
        elif kind == "stream.committed":
            output["status"] = "committed"
            output["finishReason"] = payload.get("finishReason")
        elif kind == "stream.aborted":
            output["status"] = "aborted"
            output["errorCode"] = payload.get("errorCode")
        elif kind == "model.diagnostics":
            output["timing"] = {
                field: payload[field]
                for field in _TIMING_FIELDS
                if field in payload
            }

    result = []
    for invocation_id, output in outputs.items():
        parts = content_parts[invocation_id]
        raw_content = "".join(parts[key] for key in sorted(parts))
        total_characters = len(raw_content)
        output["rawContent"] = raw_content[:_MAX_RAW_CONTENT_CHARACTERS]
        output["rawContentCharacters"] = total_characters
        output["rawContentTruncated"] = (
            total_characters > _MAX_RAW_CONTENT_CHARACTERS
        )
        output["contentDeltaCount"] = len(parts)
        output["contentDeltaConflict"] = invocation_id in conflicting_parts
        result.append(output)
    return result


__all__ = ["read_planner_model_outputs"]
