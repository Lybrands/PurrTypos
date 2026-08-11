from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from purra.contracts import (
    DomainEffect,
    ModelFinishReason,
    ModelRequest,
    ModelStreamChunk,
)
from purra.model_invocation import ModelInvocationReceipt
from purra.model_protocol import generic_capability_snapshot
from purra.operations import OperationKind, OperationStarted
from purra.output import (
    AgentOutputEvent,
    AgentOutputIntent,
    DomainEffectOutput,
    OutputChannel,
    OutputCommitMode,
    OutputEventKind,
    OutputSource,
    OutputStreamSpec,
    OutputVisibility,
    ToolOutputEvent,
)


def _processor_type():
    try:
        from purra.output.processor import AgentOutputProcessor
    except ModuleNotFoundError as error:
        pytest.fail(f"canonical output processor is missing: {error}")
    return AgentOutputProcessor


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _limit():
    request = ModelRequest(
        provider="test",
        model="model",
        capability_snapshot=replace(
            generic_capability_snapshot(),
            profile_id="test:model",
            max_output_tokens=200,
        ),
    )
    from purra.model_invocation import AgentModelCall

    return AgentModelCall(
        request=request,
        output_intent=AgentOutputIntent.FINAL_PUBLIC,
        commit_mode=OutputCommitMode.LIVE,
    ).output_limit


def _receipt(spec: OutputStreamSpec) -> ModelInvocationReceipt:
    return ModelInvocationReceipt(
        invocation_id=spec.invocation_id,
        output_stream_id=spec.output_stream_id,
        run_id=spec.run_id,
        turn_id=spec.turn_id,
        model="model",
        output_intent=spec.intent,
        commit_mode=spec.commit_mode,
        output_limit=_limit(),
    )


def _spec(
    *,
    intent: AgentOutputIntent = AgentOutputIntent.FINAL_PUBLIC,
    commit_mode: OutputCommitMode = OutputCommitMode.LIVE,
) -> OutputStreamSpec:
    return OutputStreamSpec(
        output_stream_id="output-1",
        run_id="run-1",
        turn_id="turn-1",
        invocation_id="invocation-1",
        intent=intent,
        commit_mode=commit_mode,
    )


class _Repository:
    def __init__(self):
        self.specs = {}
        self.events = []
        self.fail_next = False

    async def open_stream(self, spec):
        self.specs[spec.output_stream_id] = spec
        return spec

    async def append_event(self, draft):
        if self.fail_next:
            self.fail_next = False
            raise OSError("disk full")
        event = AgentOutputEvent(
            event_id=f"event-{len(self.events) + 1}",
            output_stream_id=draft.output_stream_id,
            run_id=draft.run_id,
            turn_id=draft.turn_id,
            invocation_id=draft.invocation_id,
            sequence=len(self.events) + 1,
            source=draft.source,
            kind=draft.kind,
            channel=draft.channel,
            visibility=draft.visibility,
            payload=draft.payload,
            occurred_at=draft.occurred_at,
            emitted_at=_now(),
        )
        self.events.append(event)
        return event

    async def commit_stream(self, output_stream_id, finish_reason):
        del output_stream_id, finish_reason
        return self.events[-1]

    async def abort_stream(self, output_stream_id, error_code):
        del output_stream_id, error_code
        return self.events[-1]


class _Publisher:
    def __init__(self):
        self.published = []

    async def publish_committed(self, event):
        self.published.append(event)


class _Recovery:
    def __init__(self):
        self.codes = []

    async def notify_output_failure(self, run_id, code):
        self.codes.append((run_id, code))


async def _opened_processor(spec: OutputStreamSpec):
    repository = _Repository()
    publisher = _Publisher()
    recovery = _Recovery()
    processor = _processor_type()(
        repository,
        publisher,
        recovery_observer=recovery,
    )
    await processor.open_model_stream(_receipt(spec), spec)
    return processor, repository, publisher, recovery


@pytest.mark.asyncio
async def test_live_chunk_is_persisted_then_published_without_rechunking():
    processor, repository, publisher, _recovery = await _opened_processor(_spec())

    events = await processor.accept_provider_chunk(
        "output-1",
        ModelStreamChunk(content_delta="甲乙"),
    )

    assert [event.payload["delta"] for event in events] == ["甲乙"]
    assert repository.events == publisher.published
    assert publisher.published[0].source is OutputSource.PROVIDER
    assert publisher.published[0].channel is OutputChannel.FINAL


@pytest.mark.asyncio
async def test_execution_public_provider_chunk_uses_commentary_channel():
    processor, _repository, publisher, _recovery = await _opened_processor(
        _spec(intent=AgentOutputIntent.EXECUTION_PUBLIC)
    )

    await processor.accept_provider_chunk(
        "output-1",
        ModelStreamChunk(content_delta="我会先核对当前正文"),
    )

    assert publisher.published[-1].channel is OutputChannel.COMMENTARY
    assert publisher.published[-1].payload == {"delta": "我会先核对当前正文"}


@pytest.mark.asyncio
async def test_gated_and_private_chunks_never_publish():
    processor, repository, publisher, _recovery = await _opened_processor(
        _spec(
            intent=AgentOutputIntent.STRUCTURED_PRIVATE,
            commit_mode=OutputCommitMode.GATED,
        )
    )

    await processor.accept_provider_chunk(
        "output-1",
        ModelStreamChunk(content_delta="候选"),
    )

    assert repository.events[-1].visibility is OutputVisibility.PRIVATE
    assert publisher.published == []


@pytest.mark.asyncio
async def test_non_provider_events_cannot_create_public_text():
    repository = _Repository()
    publisher = _Publisher()
    processor = _processor_type()(repository, publisher)
    await processor.accept_operation_event(
        OperationStarted(
            operation_id="operation-1",
            run_id="run-1",
            invocation_id=None,
            kind=OperationKind.TOOL,
            started_at=_now(),
        )
    )
    await processor.accept_tool_event(
        ToolOutputEvent(
            operation_id="operation-1",
            run_id="run-1",
            invocation_id=None,
            tool_call_id="call-1",
            tool_name="read",
            status="succeeded",
            occurred_at=_now(),
        )
    )
    await processor.accept_domain_effect_event(
        DomainEffectOutput(
            effect_id="effect-1",
            run_id="run-1",
            effect=DomainEffect(type="artifact.finalized"),
            occurred_at=_now(),
        )
    )

    public_text = [
        event
        for event in repository.events
        if event.visibility is OutputVisibility.PUBLIC
        and event.channel in {OutputChannel.COMMENTARY, OutputChannel.FINAL}
    ]
    assert public_text == []


@pytest.mark.asyncio
async def test_persistence_failure_never_creates_ghost_public_event():
    processor, repository, publisher, recovery = await _opened_processor(_spec())
    repository.fail_next = True

    with pytest.raises(Exception) as captured:
        await processor.accept_provider_chunk(
            "output-1",
            ModelStreamChunk(content_delta="不可见"),
        )

    assert type(captured.value).__name__ == "OutputPersistenceError"
    assert captured.value.code == "output_persistence_failed"
    assert publisher.published == []
    assert recovery.codes == [("run-1", "output_persistence_failed")]


@pytest.mark.asyncio
async def test_reasoning_and_usage_are_diagnostic_not_public_text():
    processor, repository, publisher, _recovery = await _opened_processor(_spec())

    await processor.accept_provider_chunk(
        "output-1",
        ModelStreamChunk(
            reasoning_delta="private reasoning",
            finish_reason=ModelFinishReason.STOP,
        ),
    )

    assert repository.events[-1].kind is OutputEventKind.PROVIDER_REASONING_DELTA
    assert repository.events[-1].visibility is OutputVisibility.DIAGNOSTIC
    assert publisher.published == []
