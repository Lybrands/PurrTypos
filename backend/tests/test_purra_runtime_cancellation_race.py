from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

import purra.runtime.orchestrator as runtime_module
from purra.cancellation import OperationCanceled
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    ExecutionState,
    ModelFinishReason,
    ModelRequest,
    ModelStream,
    ModelStreamChunk,
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolBatchResult,
    ToolCall,
    ToolCallDelta,
    ToolCallResult,
    ToolSchema,
)
from purra.events import AgentEvent, CoreEventType
from purra.model_protocol import generic_capability_snapshot
from purra.runtime import AgentRuntime
from purra.runtime.tool_round import stream_tool_batch as _stream_tool_batch


def _request() -> ToolBatchRequest:
    return ToolBatchRequest(
        run_id="run-1",
        calls=(ToolCall(id="call-a", name="readA", arguments_json="{}"),),
        allowed_tool_names=frozenset({"readA"}),
        state=ExecutionState(),
    )


def _result() -> ToolBatchResult:
    return ToolBatchResult(
        results=(ToolCallResult(
            tool_call_id="call-a",
            tool_name="readA",
            content='{"success":true}',
        ),),
        outcome=ToolBatchOutcome.COMPLETED,
    )


async def _collect(gateway, signal):
    return [
        update
        async for update in _stream_tool_batch(gateway, _request(), signal)
    ]


@pytest.mark.asyncio
async def test_gateway_commit_wins_same_tick_progress_and_cancel_barrier():
    signal = asyncio.Event()
    progress = AgentEvent(
        type=CoreEventType.TOOL_CALL_COMPLETED,
        run_id="run-1",
        payload={"index": 0},
    )

    class CommitGateway:
        async def execute_batch(self, request, event_sink, signal=None):
            await event_sink.emit(progress)
            signal.set()
            return _result()

    updates = await asyncio.wait_for(
        _collect(CommitGateway(), signal),
        timeout=1,
    )

    assert updates == [progress, _result()]


@pytest.mark.asyncio
async def test_signal_during_gateway_commit_ack_preserves_durable_result():
    signal = asyncio.Event()
    durable = asyncio.Event()
    release_ack = asyncio.Event()
    progress = AgentEvent(
        type=CoreEventType.TOOL_CALL_COMPLETED,
        run_id="run-1",
        payload={"index": 0},
    )

    class CommitAckGateway:
        async def execute_batch(self, request, event_sink, signal=None):
            durable.set()
            while not release_ack.is_set():
                try:
                    await release_ack.wait()
                except asyncio.CancelledError:
                    continue
            await event_sink.emit(progress)
            return _result()

    consumer = asyncio.create_task(_collect(CommitAckGateway(), signal))
    await asyncio.wait_for(durable.wait(), timeout=1)
    signal.set()
    await asyncio.sleep(0)
    assert not consumer.done()

    release_ack.set()
    updates = await asyncio.wait_for(consumer, timeout=1)
    assert updates == [progress, _result()]


@pytest.mark.asyncio
async def test_consumer_task_cancel_during_commit_ack_preserves_durable_result():
    durable = asyncio.Event()
    release_ack = asyncio.Event()
    progress = AgentEvent(
        type=CoreEventType.TOOL_CALL_COMPLETED,
        run_id="run-1",
        payload={"index": 0},
    )

    class CommitAckGateway:
        async def execute_batch(self, request, event_sink, signal=None):
            durable.set()
            while not release_ack.is_set():
                try:
                    await release_ack.wait()
                except asyncio.CancelledError:
                    continue
            await event_sink.emit(progress)
            return _result()

    consumer = asyncio.create_task(_collect(CommitAckGateway(), None))
    await asyncio.wait_for(durable.wait(), timeout=1)
    consumer.cancel()
    await asyncio.sleep(0)
    consumer.cancel()
    await asyncio.sleep(0)
    assert not consumer.done()

    release_ack.set()
    updates = await asyncio.wait_for(consumer, timeout=1)
    assert updates == [progress, _result()]


@pytest.mark.asyncio
async def test_gateway_exception_wins_same_tick_cancel_barrier():
    signal = asyncio.Event()

    class GatewayCommittedError(RuntimeError):
        pass

    class FailingGateway:
        async def execute_batch(self, request, event_sink, signal=None):
            signal.set()
            raise GatewayCommittedError("committed failure")

    with pytest.raises(GatewayCommittedError, match="committed failure"):
        await asyncio.wait_for(_collect(FailingGateway(), signal), timeout=1)


@pytest.mark.asyncio
async def test_progress_wins_same_tick_cancel_then_gateway_is_awaited():
    signal = asyncio.Event()
    finalized = asyncio.Event()
    progress = AgentEvent(
        type=CoreEventType.TOOL_CALL_COMPLETED,
        run_id="run-1",
        payload={"index": 0},
    )

    class ProgressThenBlockGateway:
        async def execute_batch(self, request, event_sink, signal=None):
            try:
                await event_sink.emit(progress)
                signal.set()
                await asyncio.Event().wait()
                raise AssertionError("gateway resumed after cancellation")
            finally:
                finalized.set()

    stream = _stream_tool_batch(ProgressThenBlockGateway(), _request(), signal)
    first = await asyncio.wait_for(anext(stream), timeout=1)
    assert first is progress

    with pytest.raises(OperationCanceled):
        await asyncio.wait_for(anext(stream), timeout=1)
    assert finalized.is_set()


@pytest.mark.asyncio
async def test_signal_first_cleanup_exception_does_not_replace_canceled_outcome():
    signal = asyncio.Event()
    started = asyncio.Event()
    finalized = asyncio.Event()

    class BadCleanupGateway:
        async def execute_batch(self, request, event_sink, signal=None):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                finalized.set()
                raise RuntimeError("cleanup must stay private")

    consumer = asyncio.create_task(_collect(BadCleanupGateway(), signal))
    await asyncio.wait_for(started.wait(), timeout=1)
    signal.set()

    with pytest.raises(OperationCanceled):
        await asyncio.wait_for(consumer, timeout=1)
    assert finalized.is_set()


@pytest.mark.asyncio
async def test_runtime_aclose_closes_nested_tool_stream_and_gateway_by_next_tick(
    monkeypatch,
):
    progress = AgentEvent(
        type=CoreEventType.TOOL_CALL_COMPLETED,
        run_id="run-1",
        payload={"index": 0},
    )
    gateway_finalized = asyncio.Event()
    tracked_streams = []
    original_stream_tool_batch = _stream_tool_batch

    class ToolCallingModelGateway:
        async def stream(self, messages, invocation, signal=None):
            async def chunks():
                yield ModelStreamChunk(
                    tool_call_deltas=(ToolCallDelta(
                        index=0,
                        id="call-a",
                        type="function",
                        name="readA",
                        arguments_fragment="{}",
                    ),),
                    finish_reason=ModelFinishReason.TOOL_CALLS,
                )

            return ModelStream(applied_output_limit=invocation.max_call_output_tokens, chunks=chunks(), model="model")

        async def complete(self, messages, invocation, signal=None):
            raise AssertionError("runtime must use the streaming boundary")

    class BlockingToolGateway:
        async def execute_batch(self, request, event_sink, signal=None):
            try:
                await event_sink.emit(progress)
                await asyncio.Event().wait()
                raise AssertionError("gateway resumed after outer stream close")
            finally:
                gateway_finalized.set()

    class TrackedToolStream:
        def __init__(self, gateway, request, signal):
            self._inner = original_stream_tool_batch(gateway, request, signal)
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await anext(self._inner)

        async def aclose(self):
            self.closed = True
            await self._inner.aclose()

    def tracked_stream_tool_batch(gateway, request, signal):
        stream = TrackedToolStream(gateway, request, signal)
        tracked_streams.append(stream)
        return stream

    monkeypatch.setattr(runtime_module, "_stream_tool_batch", tracked_stream_tool_batch)
    runtime = AgentRuntime(
        model_gateway=ToolCallingModelGateway(),
        tool_execution_gateway=BlockingToolGateway(),
    )
    request = AgentRunRequest(
        messages=(AgentMessage(role="user", content="work"),),
        model=ModelRequest(
            provider="test",
            model="model",
            capability_snapshot=replace(
                generic_capability_snapshot(),
                profile_id="test:model",
                max_call_output_tokens=1_024,
            ),
        ),
        domain_context=DomainContext(namespace="test"),
        tools_enabled=True,
    )
    stream = runtime.run(
        request,
        tools=(ToolSchema(
            name="readA",
            description="Read A",
            parameters={"type": "object", "properties": {}},
        ),),
        run_id="run-1",
        scope_tools_to_observer=False,
    )

    while True:
        started = await asyncio.wait_for(anext(stream), timeout=1)
        assert isinstance(started, AgentEvent)
        if started.type == CoreEventType.TOOL_CALLS_STARTED:
            break
    assert await asyncio.wait_for(anext(stream), timeout=1) is progress
    assert tracked_streams and not tracked_streams[0].closed

    await asyncio.wait_for(stream.aclose(), timeout=1)
    await asyncio.sleep(0)
    closed_after_close = tracked_streams[0].closed
    finalized_after_tick = gateway_finalized.is_set()
    if not closed_after_close:
        await tracked_streams[0].aclose()

    assert closed_after_close
    assert finalized_after_tick
