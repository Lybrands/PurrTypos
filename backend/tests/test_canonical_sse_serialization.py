from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from purra.output import (
    AgentOutputEvent,
    OutputChannel,
    OutputEventKind,
    OutputSource,
    OutputVisibility,
)
from application.sse_mapping import canonical_output_to_sse_chunk


def test_sse_serializes_canonical_output_without_semantic_projection() -> None:
    occurred_at = datetime(2026, 8, 12, 8, 1, 2, tzinfo=timezone.utc)
    emitted_at = datetime(2026, 8, 12, 8, 1, 3, tzinfo=timezone.utc)
    event = AgentOutputEvent(
        event_id="event-1",
        output_stream_id="stream-1",
        run_id="run-1",
        turn_id="turn-1",
        invocation_id="invocation-1",
        sequence=7,
        source=OutputSource.PROVIDER,
        kind=OutputEventKind.PROVIDER_CONTENT_DELTA,
        channel=OutputChannel.FINAL,
        visibility=OutputVisibility.PUBLIC,
        payload={"delta": "真实片段"},
        occurred_at=occurred_at,
        emitted_at=emitted_at,
    )

    assert canonical_output_to_sse_chunk(event) == {
        "eventId": "event-1",
        "outputStreamId": "stream-1",
        "runId": "run-1",
        "turnId": "turn-1",
        "invocationId": "invocation-1",
        "sequence": 7,
        "source": "provider",
        "kind": "provider.content_delta",
        "channel": "final",
        "visibility": "public",
        "payload": {"delta": "真实片段"},
        "occurredAt": occurred_at.isoformat(),
        "emittedAt": emitted_at.isoformat(),
    }


def test_private_canonical_output_is_not_written_to_public_sse() -> None:
    now = datetime.now(timezone.utc)
    event = AgentOutputEvent(
        event_id="event-private",
        output_stream_id="stream-private",
        run_id="run-1",
        turn_id=None,
        invocation_id="invocation-private",
        sequence=8,
        source=OutputSource.PROVIDER,
        kind=OutputEventKind.PROVIDER_REASONING_DELTA,
        channel=OutputChannel.DIAGNOSTIC,
        visibility=OutputVisibility.PRIVATE,
        payload={"delta": "hidden"},
        occurred_at=now,
        emitted_at=now,
    )

    assert canonical_output_to_sse_chunk(event) is None


def test_sse_keeps_public_plan_and_filters_private_recipe_progress() -> None:
    now = datetime.now(timezone.utc)
    plan = AgentOutputEvent(
        event_id="event-plan",
        output_stream_id=None,
        run_id="run-1",
        turn_id=None,
        invocation_id=None,
        sequence=1,
        source=OutputSource.RUNTIME,
        kind=OutputEventKind.RUNTIME,
        channel=OutputChannel.LIFECYCLE,
        visibility=OutputVisibility.PUBLIC,
        payload={
            "eventType": "run.todos_updated",
            "data": {
                "title": "续写故事",
                "steps": [
                    {"id": "understand-source", "title": "理解原作"},
                    {"id": "draft-continuation", "title": "撰写续篇"},
                ],
            },
        },
        occurred_at=now,
        emitted_at=now,
    )
    progress = replace(
        plan,
        event_id="event-recipe-progress",
        sequence=2,
        visibility=OutputVisibility.PRIVATE,
        payload={
            "eventType": "long_task.progress",
            "data": {"taskId": "recipe-task-1"},
        },
    )

    plan_wire = canonical_output_to_sse_chunk(plan)

    assert plan_wire is not None
    assert canonical_output_to_sse_chunk(progress) is None
    assert plan_wire["kind"] == "runtime.event"
    assert plan_wire["payload"] == plan.payload
    assert "agentRunTodosUpdated" not in plan_wire
