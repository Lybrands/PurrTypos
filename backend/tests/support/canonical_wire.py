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


def runtime_events(
    events: Sequence[Mapping[str, Any]], event_type: str,
) -> list[Mapping[str, Any]]:
    return [
        event for event in events
        if event.get("kind") == "runtime.event"
        and event["payload"]["eventType"] == event_type
    ]


def provider_text(
    events: Sequence[Mapping[str, Any]], *, channel: str = "final",
) -> str:
    deltas: list[str] = []
    for event in events:
        if event.get("source") != "provider" or event.get("channel") != channel:
            continue
        entries = (
            event["payload"]["entries"]
            if event["kind"] == "provider.delta_batch" else (event,)
        )
        deltas.extend(
            entry["payload"]["delta"] for entry in entries
            if entry["kind"] == "provider.content_delta"
        )
    return "".join(deltas)


__all__ = [
    "assert_raw_canonical_wire",
    "is_canonical_wire_event",
    "provider_text",
    "runtime_events",
]
