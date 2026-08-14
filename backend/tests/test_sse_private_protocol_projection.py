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


def _runtime_event(
    *,
    event_id: str,
    sequence: int,
    event_type: str,
    data: dict,
    visibility: OutputVisibility = OutputVisibility.PUBLIC,
) -> AgentOutputEvent:
    now = datetime.now(timezone.utc)
    return AgentOutputEvent(
        event_id=event_id,
        output_stream_id=None,
        run_id="run-1",
        turn_id=None,
        invocation_id=None,
        sequence=sequence,
        source=OutputSource.RUNTIME,
        kind=OutputEventKind.RUNTIME,
        channel=OutputChannel.LIFECYCLE,
        visibility=visibility,
        payload={
            "eventType": event_type,
            "data": data,
        },
        occurred_at=now,
        emitted_at=now,
    )


def test_sse_keeps_public_plan_and_filters_private_recipe_progress():
    plan = _runtime_event(
        event_id="event-plan",
        sequence=1,
        event_type="run.todos_updated",
        data={
            "title": "续写故事",
            "steps": [
                {"id": "understand-source", "title": "理解原作"},
                {"id": "draft-continuation", "title": "撰写续篇"},
            ],
        },
    )
    progress = _runtime_event(
        event_id="event-recipe-progress",
        sequence=2,
        event_type="long_task.progress",
        visibility=OutputVisibility.PRIVATE,
        data={
            "taskId": "recipe-task-1",
            "taskTitle": "Recipe 内部执行",
            "units": [
                {
                    "id": "validate",
                    "title": "校验候选稿",
                    "plannerStepId": "draft-continuation",
                },
                {
                    "id": "publish",
                    "title": "发布候选稿",
                    "plannerStepId": "draft-continuation",
                },
            ],
        },
    )

    plan_wire = canonical_output_to_sse_chunk(plan)
    progress_wire = canonical_output_to_sse_chunk(progress)

    assert plan_wire is not None
    assert progress_wire is None
    assert plan_wire["kind"] == "runtime.event"
    assert plan_wire["payload"] == plan.payload
    assert "agentRunTodosUpdated" not in plan_wire
