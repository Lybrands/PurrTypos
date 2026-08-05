"""Read-only conversation stream for durable screenplay task child Runs.

The durable task repository owns orchestration state and checkpoints.  Model
thinking, tool activity, and validated assistant replies remain Run events.
This adapter joins those existing records for the UI without persisting a
second copy of the conversation in task-unit metadata.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from agent_core.events import AgentEvent, CoreEventType
from agent_core.json_values import thaw_json_mapping
from application.agent_run_queries import AgentRunQueryService
from application.sse_mapping import core_event_to_sse_chunk


SCREENPLAY_LONG_TASK_RESPONSE_EVENT = "screenplay.long_task.response"
SCREENPLAY_LONG_TASK_THINKING_SNAPSHOT_EVENT = (
    "screenplay.long_task.thinking_snapshot"
)
SCREENPLAY_LONG_TASK_VALIDATION_FAILED_EVENT = (
    "screenplay.long_task.validation_failed"
)


@dataclass(frozen=True, slots=True)
class _RunDescriptor:
    unit_id: str
    position: int
    attempt: int
    run_id: str
    title: str
    unit_status: str
    legacy_response: str


class ScreenplayLongTaskConversationHub:
    """Process-local transport for child-Run token deltas.

    Token deltas follow the same rule as ordinary chat SSE: they are live
    transport, not a stream of database writes.  Completed turns persist one
    final snapshot separately for reload/recovery.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}

    def subscribe(self, task_id: str):
        normalized = str(task_id or "").strip()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.setdefault(normalized, set()).add(queue)

        def unsubscribe() -> None:
            subscribers = self._subscribers.get(normalized)
            if subscribers is None:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(normalized, None)

        return queue, unsubscribe

    def publish(self, task_id: str, event: Mapping[str, Any]) -> None:
        for queue in tuple(self._subscribers.get(str(task_id), ())):
            queue.put_nowait(dict(event))


class ScreenplayLongTaskConversationStream:
    """Tail canonical child-Run events for one durable screenplay task."""

    def __init__(
        self,
        *,
        long_tasks,
        checkpoint_store,
        role_registry=None,
        live_events: ScreenplayLongTaskConversationHub | None = None,
        poll_interval_seconds: float = 0.08,
    ) -> None:
        self._long_tasks = long_tasks
        self._run_queries = AgentRunQueryService(
            checkpoint_store,
            role_registry=role_registry,
        )
        self._poll_interval = max(0.02, float(poll_interval_seconds))
        self._live_events = live_events

    async def stream(
        self,
        task_id: str,
        *,
        session_id: int | None,
        after_event_id: int = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        normalized_task_id = str(task_id or "").strip()
        if not normalized_task_id:
            raise ValueError("long task id is required")
        normalized_after = max(0, int(after_event_id))
        task = await self._require_task(normalized_task_id, session_id=session_id)
        cursors: dict[str, int] = {}
        drained: set[str] = set()
        announced: set[str] = set()
        responded: set[str] = set()
        emitted_legacy_response: set[str] = set()
        live_queue = None
        unsubscribe = lambda: None
        if self._live_events is not None:
            live_queue, unsubscribe = self._live_events.subscribe(task.id)

        try:
            last_progress_fingerprint: tuple[Any, ...] | None = None
            while True:
                task = await self._require_task(
                    normalized_task_id,
                    session_id=session_id,
                )
                units = await self._long_tasks.list_units(task.id)
                descriptors = _run_descriptors(units)
                emitted = False

                progress_fingerprint = _task_progress_fingerprint(task, units)
                if progress_fingerprint != last_progress_fingerprint:
                    last_progress_fingerprint = progress_fingerprint
                    emitted = True
                    yield _task_progress_payload(task, units)

                if live_queue is not None:
                    while not live_queue.empty():
                        emitted = True
                        yield live_queue.get_nowait()

                for descriptor in descriptors:
                    if descriptor.run_id in drained:
                        continue
                    cursor = cursors.get(descriptor.run_id, normalized_after)
                    snapshot = await self._run_queries.get_snapshot(
                        descriptor.run_id,
                        after_event_id=cursor,
                        limit=500,
                    )
                    if snapshot is None:
                        continue
                    if descriptor.run_id not in announced:
                        announced.add(descriptor.run_id)
                        emitted = True
                        yield {
                            "type": "turn.started",
                            **_descriptor_payload(task.id, descriptor),
                        }
                    for envelope in snapshot.get("events", ()):
                        event_cursor = int(envelope.get("cursor") or 0)
                        cursors[descriptor.run_id] = max(
                            cursors.get(descriptor.run_id, normalized_after),
                            event_cursor,
                        )
                        mapped_events = _map_run_event(
                            task.id,
                            descriptor,
                            envelope,
                        )
                        if any(
                            event.get("type") == "turn.response"
                            for event in mapped_events
                        ):
                            responded.add(descriptor.run_id)
                        for event in mapped_events:
                            emitted = True
                            yield event
                    if snapshot.get("hasMore"):
                        emitted = True
                        continue
                    if descriptor.unit_status not in {"claimed", "running"}:
                        if (
                            descriptor.legacy_response
                            and descriptor.run_id not in responded
                            and descriptor.run_id not in emitted_legacy_response
                        ):
                            emitted_legacy_response.add(descriptor.run_id)
                            emitted = True
                            yield {
                                "type": "turn.response",
                                **_descriptor_payload(task.id, descriptor),
                                "content": descriptor.legacy_response,
                                "legacy": True,
                            }
                        drained.add(descriptor.run_id)

                terminal = str(task.status.value) not in {"pending", "running"}
                if terminal and all(
                    descriptor.run_id in drained for descriptor in descriptors
                ) and (live_queue is None or live_queue.empty()):
                    terminal_event = {
                        "type": "task.terminal",
                        "taskId": task.id,
                        "status": task.status.value,
                        "cursor": max(cursors.values(), default=normalized_after),
                    }
                    if str(task.status.value) == "completed":
                        proposal = _task_proposal(units)
                        if proposal is not None:
                            terminal_event["proposal"] = proposal
                        final_response = _task_final_response(units)
                        if final_response:
                            terminal_event["finalResponse"] = final_response
                    yield terminal_event
                    return
                if not emitted:
                    if live_queue is None:
                        await asyncio.sleep(self._poll_interval)
                    else:
                        try:
                            yield await asyncio.wait_for(
                                live_queue.get(),
                                timeout=self._poll_interval,
                            )
                        except asyncio.TimeoutError:
                            pass
        finally:
            unsubscribe()

    async def _require_task(self, task_id: str, *, session_id: int | None):
        task = await self._long_tasks.load(task_id)
        if task is None:
            raise LookupError("long task does not exist")
        origin_session = _session_id(
            thaw_json_mapping(task.metadata).get("sessionId")
        )
        if origin_session is not None and origin_session != session_id:
            raise PermissionError("long task does not belong to this session")
        return task


def _run_descriptors(units) -> tuple[_RunDescriptor, ...]:
    descriptors: list[_RunDescriptor] = []
    for unit in sorted(units, key=lambda item: item.position):
        metadata = thaw_json_mapping(unit.metadata)
        title = _unit_title(metadata, fallback=unit.id)
        legacy_response = _legacy_unit_response(metadata)
        current_run_id = str(unit.run_id or "").strip()
        history = metadata.get("runHistory")
        entries = history if isinstance(history, (list, tuple)) else ()
        seen: set[str] = set()
        for raw in entries:
            if not isinstance(raw, Mapping):
                continue
            run_id = str(raw.get("runId") or "").strip()
            if not run_id or run_id in seen:
                continue
            seen.add(run_id)
            descriptors.append(_RunDescriptor(
                unit_id=unit.id,
                position=unit.position,
                attempt=max(1, int(raw.get("attempt") or 1)),
                run_id=run_id,
                title=title,
                unit_status=(
                    str(unit.status.value)
                    if run_id == current_run_id
                    else "archived"
                ),
                legacy_response=legacy_response,
            ))
        if current_run_id and current_run_id not in seen:
            descriptors.append(_RunDescriptor(
                unit_id=unit.id,
                position=unit.position,
                attempt=max(1, int(unit.attempt or 1)),
                run_id=current_run_id,
                title=title,
                unit_status=str(unit.status.value),
                legacy_response=legacy_response,
            ))
    return tuple(sorted(
        descriptors,
        key=lambda item: (item.position, item.attempt, item.run_id),
    ))


def _map_run_event(
    task_id: str,
    descriptor: _RunDescriptor,
    envelope: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    event_type = str(envelope.get("type") or "")
    payload = thaw_json_mapping(envelope.get("payload") or {})
    base = {
        **_descriptor_payload(task_id, descriptor),
        "cursor": int(envelope.get("cursor") or 0),
        "createdAt": envelope.get("createdAt"),
    }
    if event_type == SCREENPLAY_LONG_TASK_THINKING_SNAPSHOT_EVENT:
        content = str(payload.get("content") or "")
        return ({
            "type": "turn.thinking.snapshot",
            **base,
            "content": content,
        },)
    if event_type == SCREENPLAY_LONG_TASK_RESPONSE_EVENT:
        content = str(payload.get("content") or "").strip()
        return (({"type": "turn.response", **base, "content": content},)
                if content else ())
    if event_type == SCREENPLAY_LONG_TASK_VALIDATION_FAILED_EVENT:
        return ({
            "type": "turn.completed",
            **base,
            "status": "failed",
            "errorCode": str(payload.get("errorCode") or "validation_failed"),
        },)

    # A long-task model candidate is structured host data and must remain
    # buffered until screenplay validation emits SCREENPLAY_LONG_TASK_RESPONSE_EVENT.
    # Every other canonical Run event uses the exact same mapper as ordinary
    # Agent chat, so new tool/approval/delegation/context events cannot drift.
    if event_type in {CoreEventType.MODEL_DELTA, CoreEventType.RUN_STARTED}:
        return ()
    chunk = core_event_to_sse_chunk(AgentEvent(
        type=event_type,
        payload=payload,
        run_id=descriptor.run_id,
    ))
    if chunk is None:
        return ()
    return ({
        "type": "turn.chunk",
        **base,
        "chunk": chunk,
    },)


def _descriptor_payload(task_id: str, descriptor: _RunDescriptor) -> dict[str, Any]:
    return {
        "taskId": task_id,
        "unitId": descriptor.unit_id,
        "attempt": descriptor.attempt,
        "runId": descriptor.run_id,
        "title": descriptor.title,
    }


def _task_progress_fingerprint(task, units) -> tuple[Any, ...]:
    return (
        str(task.status.value),
        int(task.revision),
        int(task.total_units),
        int(task.completed_units),
        int(task.failed_units),
        getattr(task, "update_time", None),
        tuple(
            (
                unit.id,
                int(unit.position),
                str(unit.status.value),
                int(unit.attempt),
                int(unit.max_attempts),
                unit.run_id,
                unit.output_ref,
                unit.error_code,
                getattr(unit, "update_time", None),
            )
            for unit in units
        ),
    )


def _task_progress_payload(task, units) -> dict[str, Any]:
    """Small status-only projection for the already-open conversation SSE."""

    return {
        "type": "task.progress",
        "taskId": task.id,
        "status": task.status.value,
        "revision": task.revision,
        "totalUnits": task.total_units,
        "completedUnits": task.completed_units,
        "failedUnits": task.failed_units,
        "updateTime": getattr(task, "update_time", None),
        "units": [
            {
                "id": unit.id,
                "position": unit.position,
                "status": unit.status.value,
                "attempt": unit.attempt,
                "maxAttempts": unit.max_attempts,
                "runId": unit.run_id,
                "outputRef": unit.output_ref,
                "errorCode": unit.error_code,
                "updateTime": getattr(unit, "update_time", None),
            }
            for unit in units
        ],
    }


def _task_proposal(units) -> dict[str, Any] | None:
    """Return the final validated proposal for the ordinary chat terminal."""

    for unit in sorted(units, key=lambda item: item.position, reverse=True):
        if str(unit.status.value) != "completed":
            continue
        raw = thaw_json_mapping(unit.metadata).get("proposal")
        if not isinstance(raw, Mapping):
            continue
        proposal = thaw_json_mapping(raw)
        if (
            not isinstance(proposal.get("kind"), str)
            or not isinstance(proposal.get("title"), str)
            or not isinstance(proposal.get("contentJson"), Mapping)
            or not isinstance(proposal.get("contentText"), str)
            or not isinstance(proposal.get("derivedFromIds"), (list, tuple))
        ):
            continue
        proposal["contentJson"] = dict(proposal["contentJson"])
        proposal["derivedFromIds"] = list(proposal["derivedFromIds"])
        return proposal
    return None


def _task_final_response(units) -> str:
    for unit in sorted(units, key=lambda item: item.position, reverse=True):
        if str(unit.status.value) != "completed":
            continue
        value = str(
            thaw_json_mapping(unit.metadata).get("finalResponse") or ""
        ).strip()
        if value:
            return value
    return ""


def _unit_title(metadata: Mapping[str, Any], *, fallback: str) -> str:
    label = str(metadata.get("label") or "").strip()
    if label:
        return label
    headings = metadata.get("sceneHeadings")
    names = [
        str(item or "").strip()
        for item in (headings if isinstance(headings, (list, tuple)) else ())
        if str(item or "").strip()
    ]
    return f"创作 {'、'.join(names)}" if names else fallback


def _legacy_unit_response(metadata: Mapping[str, Any]) -> str:
    """Read pre-event-stream task records without keeping the old write path."""

    response = str(metadata.get("assistantResponse") or "").strip()
    if response:
        return response
    live = metadata.get("liveConversation")
    if not isinstance(live, Mapping):
        return ""
    turns = live.get("turns")
    for raw in reversed(turns if isinstance(turns, (list, tuple)) else ()):
        if not isinstance(raw, Mapping):
            continue
        response = str(raw.get("response") or "").strip()
        if response:
            return response
    return ""


def _session_id(value: object) -> int | None:
    try:
        normalized = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return normalized if normalized is not None and normalized > 0 else None


__all__ = [
    "SCREENPLAY_LONG_TASK_RESPONSE_EVENT",
    "SCREENPLAY_LONG_TASK_THINKING_SNAPSHOT_EVENT",
    "SCREENPLAY_LONG_TASK_VALIDATION_FAILED_EVENT",
    "ScreenplayLongTaskConversationHub",
    "ScreenplayLongTaskConversationStream",
]
