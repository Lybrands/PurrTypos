from __future__ import annotations

import pytest

from agent_core.events import AgentEvent
from agent_core.long_tasks import (
    LongTaskRecord,
    LongTaskStatus,
    LongTaskUnitRecord,
    LongTaskUnitStatus,
)
from application.screenplay_long_task_conversation import (
    ScreenplayLongTaskConversationHub,
    ScreenplayLongTaskConversationStream,
    _RunDescriptor,
    _map_run_event,
)
from application.sse_mapping import core_event_to_sse_chunk


def _task(
    *,
    session_id: int = 7,
    status: LongTaskStatus = LongTaskStatus.COMPLETED,
):
    return LongTaskRecord(
        id="task-1",
        namespace="purrtypos.screenplay",
        kind="screenplay_draft_generation",
        owner_id="project-1",
        work_item_id="work-1",
        created_by_run_id="run-parent",
        status=status,
        revision=3,
        total_units=1,
        completed_units=1 if status is LongTaskStatus.COMPLETED else 0,
        failed_units=0,
        max_parallelism=1,
        metadata={"sessionId": session_id},
    )


def _unit(
    status: LongTaskUnitStatus = LongTaskUnitStatus.COMPLETED,
    *,
    proposal=None,
):
    metadata = {
        "sceneHeadings": ["黄阿福"],
        "runHistory": [{"attempt": 1, "runId": "run-child"}],
    }
    if proposal is not None:
        metadata["proposal"] = proposal
    return LongTaskUnitRecord(
        task_id="task-1",
        id="batch-0001",
        position=0,
        status=status,
        attempt=1,
        run_id="run-child",
        metadata=metadata,
    )


class _LongTasks:
    def __init__(self, task, unit=None):
        self.task = task
        self.unit = unit or _unit()

    async def load(self, task_id):
        assert task_id == "task-1"
        return self.task

    async def list_units(self, task_id):
        assert task_id == "task-1"
        return (self.unit,)


class _RunQueries:
    async def get_snapshot(self, run_id, *, after_event_id, limit):
        assert run_id == "run-child"
        assert after_event_id == 0
        assert limit == 500
        return {
            "events": [
                {
                    "cursor": 1,
                    "type": "screenplay.long_task.thinking_snapshot",
                    "payload": {"content": "先确认上一场连续性。"},
                    "createdAt": "2026-08-04T12:00:00",
                },
                {
                    "cursor": 2,
                    "type": "context.budgeted",
                    "payload": {
                        "windowTokens": 256000,
                        "estimatedInputTokens": 12000,
                        "toolSchemaTokens": 0,
                        "outputReserveTokens": 8192,
                        "safetyReserveTokens": 4096,
                        "runtimeReserveTokens": 2048,
                        "droppedMessages": 0,
                        "projectedTotalTokens": 26240,
                        "overflowTokens": 0,
                    },
                    "createdAt": "2026-08-04T12:00:01",
                },
                {
                    "cursor": 3,
                    "type": "model.call_recorded",
                    "payload": {
                        "phase": "generation",
                        "count": 1,
                        "round": 1,
                        "toolNames": [],
                    },
                    "createdAt": "2026-08-04T12:00:02",
                },
                {
                    "cursor": 4,
                    "type": "model.delta",
                    "payload": {"delta": '{"scenes":['},
                    "createdAt": "2026-08-04T12:00:03",
                },
                {
                    "cursor": 5,
                    "type": "tool.calls_started",
                    "payload": {
                        "calls": [{
                            "id": "call-read",
                            "name": "getScreenplayDocument",
                            "arguments_json": '{"documentId":"doc-1"}',
                            "display_names": {"zh-CN": "读取剧本文档"},
                        }],
                        "in_progress": True,
                        "partial_content": "先读取资料。",
                        "partial_thinking": "读取前序场景。",
                        "model": "mimo-v2.5-pro",
                    },
                    "createdAt": "2026-08-04T12:00:04",
                },
                {
                    "cursor": 6,
                    "type": "tool.call_completed",
                    "payload": {
                        "index": 0,
                        "toolCallId": "call-read",
                        "toolName": "getScreenplayDocument",
                        "outcome": "completed",
                    },
                    "createdAt": "2026-08-04T12:00:05",
                },
                {
                    "cursor": 7,
                    "type": "tool.results",
                    "payload": {"results": [{
                        "tool_call_id": "call-read",
                        "tool_name": "getScreenplayDocument",
                        "content": '{"documentId":"doc-1"}',
                    }]},
                    "createdAt": "2026-08-04T12:00:06",
                },
                {
                    "cursor": 8,
                    "type": "screenplay.long_task.response",
                    "payload": {"content": "已完成 s05 正文。"},
                    "createdAt": "2026-08-04T12:00:07",
                },
                {
                    "cursor": 9,
                    "type": "run.completed",
                    "payload": {"status": "done"},
                    "createdAt": "2026-08-04T12:00:08",
                },
            ],
            "hasMore": False,
        }


@pytest.mark.asyncio
async def test_stream_replays_standard_run_process_and_only_validated_answer():
    stream = ScreenplayLongTaskConversationStream(
        long_tasks=_LongTasks(_task()),
        checkpoint_store=object(),
    )
    stream._run_queries = _RunQueries()

    events = [event async for event in stream.stream(
        "task-1",
        session_id=7,
    )]

    assert [event["type"] for event in events] == [
        "task.progress",
        "turn.started",
        "turn.thinking.snapshot",
        "turn.chunk",
        "turn.chunk",
        "turn.chunk",
        "turn.chunk",
        "turn.chunk",
        "turn.response",
        "turn.chunk",
        "task.terminal",
    ]
    assert events[0]["completedUnits"] == 1
    assert events[0]["units"][0]["status"] == "completed"
    assert "metadata" not in events[0]
    assert events[1]["title"] == "创作 黄阿福"
    assert events[2]["content"] == "先确认上一场连续性。"
    assert events[3]["chunk"]["contextBudget"]["windowTokens"] == 256000
    assert events[4]["chunk"]["modelInvocation"]["round"] == 1
    assert events[5]["chunk"]["toolCalls"][0]["function"]["arguments"] == '{"documentId":"doc-1"}'
    assert events[5]["chunk"]["toolCalls"][0]["displayNames"]["zh-CN"] == "读取剧本文档"
    assert events[6]["chunk"]["toolIndexCompleted"] == 0
    assert events[7]["chunk"]["toolResults"][0]["tool_call_id"] == "call-read"
    assert events[8]["content"] == "已完成 s05 正文。"
    assert all('{"scenes":[' not in str(event) for event in events)


@pytest.mark.asyncio
async def test_completed_task_routes_final_proposal_through_terminal_event():
    proposal = {
        "kind": "scene_draft",
        "title": "后续场景正文",
        "contentJson": {"newSceneIds": ["s05"]},
        "contentText": "INT. 门卫亭 - 日",
        "derivedFromIds": ["scene-list-1"],
    }
    stream = ScreenplayLongTaskConversationStream(
        long_tasks=_LongTasks(_task(), _unit(proposal=proposal)),
        checkpoint_store=object(),
    )
    stream._run_queries = _RunQueries()

    events = [event async for event in stream.stream(
        "task-1",
        session_id=7,
    )]

    assert events[-1]["type"] == "task.terminal"
    assert events[-1]["proposal"] == proposal


@pytest.mark.asyncio
async def test_stream_rejects_a_task_from_another_session():
    stream = ScreenplayLongTaskConversationStream(
        long_tasks=_LongTasks(_task(session_id=7)),
        checkpoint_store=object(),
    )

    with pytest.raises(PermissionError):
        await anext(stream.stream("task-1", session_id=8))


@pytest.mark.asyncio
async def test_live_hub_transports_deltas_without_persisting_task_metadata():
    hub = ScreenplayLongTaskConversationHub()
    queue, unsubscribe = hub.subscribe("task-1")
    event = {
        "type": "turn.thinking.delta",
        "taskId": "task-1",
        "unitId": "batch-0001",
        "attempt": 1,
        "runId": "run-child",
        "delta": "实时增量",
    }

    hub.publish("task-1", event)

    assert await queue.get() == event
    unsubscribe()


@pytest.mark.asyncio
async def test_stream_forwards_live_thinking_through_the_same_subscription():
    class _EmptyRunQueries:
        async def get_snapshot(self, run_id, *, after_event_id, limit):
            del run_id, after_event_id, limit
            return {"events": [], "hasMore": False}

    hub = ScreenplayLongTaskConversationHub()
    stream = ScreenplayLongTaskConversationStream(
        long_tasks=_LongTasks(
            _task(status=LongTaskStatus.RUNNING),
            _unit(status=LongTaskUnitStatus.RUNNING),
        ),
        checkpoint_store=object(),
        live_events=hub,
    )
    stream._run_queries = _EmptyRunQueries()
    iterator = stream.stream("task-1", session_id=7)

    progress = await anext(iterator)
    started = await anext(iterator)
    hub.publish("task-1", {
        "type": "turn.thinking.delta",
        "taskId": "task-1",
        "unitId": "batch-0001",
        "attempt": 1,
        "runId": "run-child",
        "delta": "实时过程",
    })
    live = await anext(iterator)
    await iterator.aclose()

    assert progress["type"] == "task.progress"
    assert started["type"] == "turn.started"
    assert live["type"] == "turn.thinking.delta"
    assert live["delta"] == "实时过程"


@pytest.mark.parametrize(("event_type", "payload"), [
    (
        "run.todos_updated",
        {
            "title": "执行计划",
            "status": "running",
            "steps": [{
                "id": "read",
                "title": "读取资料",
                "type": "read",
                "status": "running",
            }],
        },
    ),
    (
        "tool.calls_started",
        {
            "calls": [{
                "id": "call-read",
                "name": "getScreenplayDocument",
                "arguments_json": '{"documentId":"doc-1"}',
                "display_names": {"zh-CN": "读取剧本文档"},
            }],
            "in_progress": True,
            "partial_content": "先读取资料。",
            "partial_thinking": "确认连续性。",
            "model": "mimo-v2.5-pro",
        },
    ),
    (
        "tool.results",
        {"results": [{
            "tool_call_id": "call-read",
            "tool_name": "getScreenplayDocument",
            "content": '{"documentId":"doc-1"}',
        }]},
    ),
    (
        "approval.requested",
        {
            "approvalId": "approval-1",
            "toolName": "writeDocument",
            "status": "pending",
        },
    ),
    (
        "delegation.created",
        {
            "delegationId": "delegate-1",
            "parentRunId": "run-child",
            "agentRole": "researcher",
            "objective": "核对原作",
            "status": "queued",
        },
    ),
    (
        "conversation.compaction.completed",
        {"status": "completed", "compactedTurns": 4},
    ),
    (
        "screenplay.document_proposal",
        {"documentId": "proposal-1", "title": "场景正文"},
    ),
])
def test_child_core_events_reuse_the_exact_ordinary_sse_mapping(
    event_type,
    payload,
):
    descriptor = _RunDescriptor(
        unit_id="batch-0001",
        position=0,
        attempt=1,
        run_id="run-child",
        title="创作 黄阿福",
        unit_status="running",
        legacy_response="",
    )
    mapped = _map_run_event(
        "task-1",
        descriptor,
        {
            "cursor": 7,
            "type": event_type,
            "payload": payload,
            "createdAt": "2026-08-04T12:00:00",
        },
    )
    ordinary = core_event_to_sse_chunk(AgentEvent(
        type=event_type,
        payload=payload,
        run_id="run-child",
    ))

    assert len(mapped) == 1
    assert mapped[0]["type"] == "turn.chunk"
    assert mapped[0]["chunk"] == ordinary


def test_unvalidated_long_task_model_candidate_is_not_streamed_as_chat_text():
    descriptor = _RunDescriptor(
        unit_id="batch-0001",
        position=0,
        attempt=1,
        run_id="run-child",
        title="创作 黄阿福",
        unit_status="running",
        legacy_response="",
    )

    assert _map_run_event(
        "task-1",
        descriptor,
        {
            "cursor": 8,
            "type": "model.delta",
            "payload": {"delta": '{"scenes":['},
        },
    ) == ()
