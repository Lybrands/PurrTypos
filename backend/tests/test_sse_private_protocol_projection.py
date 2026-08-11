from __future__ import annotations

from datetime import datetime, timezone

from purra.output import (
    AgentOutputEvent,
    OutputChannel,
    OutputEventKind,
    OutputSource,
    OutputVisibility,
)
from application.sse_mapping import canonical_output_to_sse_chunk


def test_sse_does_not_apply_a_second_plan_projection():
    now = datetime.now(timezone.utc)
    event = AgentOutputEvent(
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
                "title": "生成场景表",
                "steps": [{"id": "generate", "title": "生成完整场景表"}],
            },
        },
        occurred_at=now,
        emitted_at=now,
    )

    wire = canonical_output_to_sse_chunk(event)
    assert wire is not None
    assert wire["kind"] == "runtime.event"
    assert wire["payload"] == event.payload
    assert "agentRunTodosUpdated" not in wire
