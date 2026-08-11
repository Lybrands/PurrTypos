"""Transport-only serialization for canonical PurrA output."""

from __future__ import annotations

from typing import Any

from purra.contracts import AgentRunResult
from purra.json_values import thaw_json_mapping
from purra.output import AgentOutputEvent, OutputVisibility


def core_update_to_sse_chunk(
    update: AgentOutputEvent | AgentRunResult,
    *,
    model: str,
) -> dict[str, Any] | None:
    if isinstance(update, AgentOutputEvent):
        return canonical_output_to_sse_chunk(update)
    if not isinstance(update, AgentRunResult):
        raise TypeError("SSE accepts only canonical output or a transport result")
    return {
        "done": True,
        "model": update.model or model,
        "runResult": {
            "runId": update.run_id,
            "status": update.status.value,
            "errorCode": update.error,
        },
    }


def canonical_output_to_sse_chunk(
    event: AgentOutputEvent,
) -> dict[str, Any] | None:
    if event.visibility is not OutputVisibility.PUBLIC:
        return None
    return {
        "eventId": event.event_id,
        "outputStreamId": event.output_stream_id,
        "runId": event.run_id,
        "turnId": event.turn_id,
        "invocationId": event.invocation_id,
        "sequence": event.sequence,
        "source": event.source.value,
        "kind": event.kind.value,
        "channel": event.channel.value,
        "visibility": event.visibility.value,
        "payload": thaw_json_mapping(event.payload),
        "occurredAt": event.occurred_at.isoformat(),
        "emittedAt": event.emitted_at.isoformat(),
    }


__all__ = ["canonical_output_to_sse_chunk", "core_update_to_sse_chunk"]
