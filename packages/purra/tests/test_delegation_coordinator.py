from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from purra.contracts import (
    AgentDelegation,
    AgentMessage,
    AgentRunRequest,
    AgentRunResult,
    DelegationAggregation,
    DelegationClaim,
    DomainContext,
    ExecutionState,
    ModelRequest,
    RunLineage,
    RunStatus,
)
from purra.model_protocol import generic_capability_snapshot
from purra.operations import AgentOperationController
from purra.output import (
    AgentOutputEvent,
    OutputChannel,
    OutputEventKind,
    OutputSource,
    OutputVisibility,
)
from purra.output.processor import AgentOutputProcessor


def _coordinator_type():
    try:
        from purra.delegation import AgentDelegationCoordinator
    except ImportError as error:
        pytest.fail(f"delegation coordinator is missing: {error}")
    return AgentDelegationCoordinator


def _request() -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content="child"),),
        model=ModelRequest(
            provider="test",
            model="model",
            capability_snapshot=replace(
                generic_capability_snapshot(),
                profile_id="test:model",
                max_output_tokens=1_024,
            ),
        ),
        domain_context=DomainContext(namespace="test"),
    )


def _event(run_id: str, sequence: int) -> AgentOutputEvent:
    now = datetime.now(timezone.utc)
    return AgentOutputEvent(
        event_id=f"{run_id}-event-{sequence}",
        output_stream_id=None,
        run_id=run_id,
        turn_id=None,
        invocation_id=None,
        sequence=sequence,
        source=OutputSource.RUNTIME,
        kind=OutputEventKind.RUN_LIFECYCLE,
        channel=OutputChannel.LIFECYCLE,
        visibility=OutputVisibility.PUBLIC,
        payload={"sourceSequence": sequence},
        occurred_at=now,
        emitted_at=now,
    )


class _OutputRepository:
    def __init__(self) -> None:
        self.events = {}

    async def append_event(self, draft):
        rows = self.events.setdefault(draft.run_id, [])
        now = datetime.now(timezone.utc)
        event = AgentOutputEvent(
            event_id=f"parent-event-{len(rows) + 1}",
            output_stream_id=draft.output_stream_id,
            run_id=draft.run_id,
            turn_id=draft.turn_id,
            invocation_id=draft.invocation_id,
            sequence=len(rows) + 1,
            source=draft.source,
            kind=draft.kind,
            channel=draft.channel,
            visibility=draft.visibility,
            payload=draft.payload,
            occurred_at=draft.occurred_at,
            emitted_at=now,
        )
        rows.append(event)
        return event

    async def list_events(self, run_id, *, after_sequence, limit=200):
        return tuple(
            event
            for event in self.events.get(run_id, ())
            if event.sequence > after_sequence
        )[:limit]


class _Publisher:
    def __init__(self) -> None:
        self.events = []

    async def publish_committed(self, event):
        self.events.append(event)


class _Delegations:
    def __init__(self) -> None:
        delegation = AgentDelegation(
            id="delegation-1",
            parent_run_id="parent-1",
            root_run_id="parent-1",
            agent_role="researcher",
            objective="collect facts",
        )
        self.claim_record = DelegationClaim(
            delegation=delegation,
            lineage=RunLineage(
                parent_run_id="parent-1",
                root_run_id="parent-1",
                delegation_id="delegation-1",
                agent_role="researcher",
                depth=1,
            ),
        )
        self.results = []
        self.attached = []
        self.cancel_calls = 0

    async def create(self, **kwargs):
        del kwargs
        return self.claim_record.delegation

    async def claim(self, **kwargs):
        del kwargs
        return self.claim_record

    async def record_result(self, **kwargs):
        self.results.append(kwargs)
        return True

    async def attach_child_run(self, **kwargs):
        self.attached.append(kwargs)
        return True

    async def fail(self, **kwargs):
        raise AssertionError(f"unexpected delegation failure: {kwargs}")

    async def cancel_children(self, parent_run_id):
        del parent_run_id
        self.cancel_calls += 1
        return 1

    async def aggregate(self, parent_run_id):
        del parent_run_id
        return DelegationAggregation(state="ready", counts={"done": 1})


class _Factory:
    async def build(self, claim):
        del claim
        return _request(), object()


class _ChildHandle:
    def __init__(self, *, pending=False) -> None:
        self._events = (_event("child-1", 1), _event("child-1", 2))
        self._result = asyncio.get_running_loop().create_future()
        self.cancel_calls = 0
        if not pending:
            self._result.set_result(AgentRunResult(
                run_id="child-1",
                status=RunStatus.DONE,
                final_response="verified facts",
            ))

    @property
    def run_id(self):
        return "child-1"

    async def _subscribe(self, after_sequence=0):
        for event in self._events:
            if event.sequence > after_sequence:
                yield event

    def subscribe(self, after_sequence=0):
        return self._subscribe(after_sequence)

    async def wait(self):
        return await asyncio.shield(self._result)

    async def cancel(self, reason):
        del reason
        self.cancel_calls += 1
        if not self._result.done():
            self._result.set_result(AgentRunResult(
                run_id="child-1",
                status=RunStatus.CANCELED,
                error="parent_canceled",
            ))


class _Core:
    def __init__(self, handle):
        self.handle = handle

    async def submit(self, request, *, options=None):
        del request, options
        return self.handle


def _fixture(*, pending=False):
    output_repository = _OutputRepository()
    publisher = _Publisher()
    processor = AgentOutputProcessor(output_repository, publisher)
    operations = AgentOperationController(processor)
    delegations = _Delegations()
    child = _ChildHandle(pending=pending)
    coordinator = _coordinator_type()(
        repository=delegations,
        core=_Core(child),
        output_processor=processor,
        operation_controller=operations,
        child_request_factory=_Factory(),
        worker_id="worker-1",
        max_parallel_children=2,
    )
    return coordinator, output_repository, delegations, child


@pytest.mark.asyncio
async def test_child_events_receive_parent_sequence_and_keep_source_ids():
    coordinator, output, delegations, _child = _fixture()

    handle = await coordinator.claim_and_submit(
        "delegation-1",
        parent_run_id="parent-1",
    )
    result = await handle.wait()
    parent_events = await output.list_events(
        "parent-1",
        after_sequence=0,
    )

    assert result.status is RunStatus.DONE
    assert [event.sequence for event in parent_events] == list(
        range(1, len(parent_events) + 1)
    )
    delegation_events = [
        event
        for event in parent_events
        if event.kind is OutputEventKind.DELEGATION
    ]
    statuses = [
        event.payload["status"]
        for event in delegation_events
        if event.payload.get("eventType") == "status"
    ]
    assert statuses == ["claimed", "running", "done"]
    federated = [
        event
        for event in delegation_events
        if event.payload.get("eventType") == "child_output"
    ]
    assert [event.payload["sourceSequence"] for event in federated] == [1, 2]
    assert all(event.payload["sourceRunId"] == "child-1" for event in federated)
    assert all(event.payload["parentRunId"] == "parent-1" for event in federated)
    assert delegations.attached == [{
        "delegation_id": "delegation-1",
        "child_run_id": "child-1",
        "worker_id": "worker-1",
    }]
    assert len(delegations.results) == 1


@pytest.mark.asyncio
async def test_cancel_parent_cancels_active_child_once():
    coordinator, _output, delegations, child = _fixture(pending=True)
    handle = await coordinator.claim_and_submit(
        "delegation-1",
        parent_run_id="parent-1",
    )

    first = await coordinator.cancel_children("parent-1")
    second = await coordinator.cancel_children("parent-1")
    await handle.wait()

    assert first == 1
    assert second == 0
    assert child.cancel_calls == 1
    assert delegations.cancel_calls == 1


@pytest.mark.asyncio
async def test_generic_delegation_tool_uses_coordinator_and_returns_aggregate():
    from purra.delegation import build_delegation_tool_registration

    coordinator, output, _delegations, _child = _fixture()
    registration = build_delegation_tool_registration(
        coordinator,
        role_guidance={
            "researcher": {
                "title": "Researcher",
                "description": "Collect independent evidence",
            },
        },
    )
    state = ExecutionState(run_id="parent-1")

    result = await registration.handler(
        state,
        {
            "delegations": [{
                "agentRole": "researcher",
                "objective": "collect facts",
            }],
        },
    )

    assert json.loads(result.content) == {
        "state": "ready",
        "counts": {"done": 1},
        "requiredFailures": [],
        "results": [],
    }
    status_events = [
        event
        for event in await output.list_events("parent-1", after_sequence=0)
        if event.kind is OutputEventKind.DELEGATION
        and event.payload.get("eventType") == "status"
    ]
    assert [event.payload["status"] for event in status_events] == [
        "queued",
        "claimed",
        "running",
        "done",
    ]
