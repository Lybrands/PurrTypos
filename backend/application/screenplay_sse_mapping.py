"""Temporary SSE projection for screenplay-owned Agent events."""

from __future__ import annotations

from typing import Any

from agent_core.contracts import AgentRunResult
from agent_core.events import AgentEvent
from agent_core.json_values import thaw_json_mapping
from application.sse_mapping import core_update_to_sse_chunk


def screenplay_update_to_sse_chunk(
    update: AgentEvent | AgentRunResult,
    *,
    model: str,
) -> dict[str, Any] | None:
    return core_update_to_sse_chunk(
        update,
        model=model,
        domain_event_mapper=screenplay_event_to_sse_chunk,
    )


def screenplay_event_to_sse_chunk(
    event: AgentEvent,
) -> dict[str, Any] | None:
    payload = thaw_json_mapping(event.payload)
    run_id = str(event.run_id or "")
    if event.type == "screenplay.long_task.response":
        content = str(payload.get("content") or "").strip()
        return {"delta": content} if content else None
    names = {
        "screenplay.document_proposal": "proposedScreenplayDocument",
        "screenplay.revision_ready": "screenplayRevisionReady",
    }
    public_name = names.get(event.type)
    if public_name is None:
        return None
    if run_id:
        payload["sourceRunId"] = run_id
    return {public_name: payload}


__all__ = ["screenplay_event_to_sse_chunk", "screenplay_update_to_sse_chunk"]
