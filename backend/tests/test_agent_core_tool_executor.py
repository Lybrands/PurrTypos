from __future__ import annotations

import asyncio
import json

import pytest

from agent_core.contracts import (
    ApprovalResult,
    ApprovalStatus,
    DomainEffect,
    ExecutionState,
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolCall,
    ToolExecutionLimits,
    ToolHandlerResult,
    ToolPolicy,
    ToolSchema,
)
from agent_core.events import CoreEventType
from agent_core.errors import ContractViolationError
from agent_core.ports import ToolExecutionGateway, ToolRegistration
from agent_core.tools.approval import InMemoryApprovalGateway
from agent_core.tools.executor import CoreToolExecutor
from agent_core.tools.registry import InMemoryToolCatalog


class RecordingSink:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


class Probe:
    def __init__(self, value=False, *, order=None, raises=False):
        self.value = value
        self.order = order
        self.raises = raises

    def will_hit(self, state, arguments):
        if self.order is not None:
            self.order.append("cache")
        if self.raises:
            raise RuntimeError("cache secret")
        return self.value


def _registration(
    name,
    handler,
    *,
    mode="read",
    scope=None,
    probe=None,
):
    return ToolRegistration(
        schema=ToolSchema(
            name=name,
            description=f"Tool {name}",
            parameters={"type": "object", "properties": {}},
        ),
        handler=handler,
        policy=ToolPolicy(
            mode=mode,
            title=f"Use {name}",
            risk_level=("write" if mode != "read" else "read"),
        ),
        scope_validator=scope,
        cache_probe=probe,
    )


def _request(
    *calls,
    allowed=None,
    state=None,
    run_id="run-1",
):
    return ToolBatchRequest(
        run_id=run_id,
        calls=tuple(calls),
        allowed_tool_names=frozenset(
            allowed if allowed is not None else {call.name for call in calls}
        ),
        state=state or ExecutionState(),
    )


def _call(call_id, name, arguments="{}"):
    return ToolCall(id=call_id, name=name, arguments_json=arguments)


@pytest.mark.asyncio
async def test_same_name_read_calls_execute_sequentially_with_shared_state():
    observations = []

    async def _read(state, arguments, signal=None):
        observations.append(arguments["value"])
        state.domain["last"] = arguments["value"]
        return ToolHandlerResult('{"success":true}', from_cache=True)

    catalog = InMemoryToolCatalog((
        _registration("readA", _read, probe=Probe(True)),
    ))
    executor = CoreToolExecutor(catalog)
    sink = RecordingSink()
    state = ExecutionState()

    result = await executor.execute_batch(
        _request(
            _call("call-a", "readA", '{"value":"first"}'),
            _call("call-b", "readA", '{"value":"second"}'),
            state=state,
        ),
        sink,
    )

    assert isinstance(executor, ToolExecutionGateway)
    assert result.outcome is ToolBatchOutcome.COMPLETED
    assert observations == ["first", "second"]
    assert state.domain["last"] == "second"
    assert [item.tool_call_id for item in result.results] == ["call-a", "call-b"]
    assert result.cache_hits == (True, True)
    assert [event.type for event in sink.events] == [
        CoreEventType.TOOL_CALL_COMPLETED,
        CoreEventType.TOOL_CALL_COMPLETED,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["propose", "confirm"])
async def test_non_read_multi_call_batch_rejects_before_scope_approval_or_handler(mode):
    order = []

    async def _scope(state, arguments, signal=None):
        order.append("scope")
        return None

    async def _handler(state, arguments, signal=None):
        order.append("handler")
        return ToolHandlerResult("unexpected")

    class RecordingApproval:
        async def request(self, run_id, approval, event_sink, signal=None):
            order.append("approval")
            return ApprovalResult("approval-1", ApprovalStatus.APPROVED)

        def resolve(self, run_id, approval_id, decision):
            return None

        def cancel_pending(self, run_id):
            return 0

    catalog = InMemoryToolCatalog((
        _registration("readA", _handler, scope=_scope),
        _registration("mutate", _handler, mode=mode, scope=_scope),
    ))
    sink = RecordingSink()

    result = await CoreToolExecutor(
        catalog,
        approval_gateway=RecordingApproval(),
    ).execute_batch(
        _request(
            _call("call-a", "readA"),
            _call("call-b", "mutate"),
        ),
        sink,
    )

    assert result.outcome is ToolBatchOutcome.REJECTED
    assert result.error == "multi_call_batch_requires_read_only_tools"
    assert [item.error for item in result.results] == [
        "multi_call_batch_requires_read_only_tools",
        "multi_call_batch_requires_read_only_tools",
    ]
    assert order == []
    assert sink.events == []


@pytest.mark.asyncio
async def test_mixed_unauthorized_batch_is_rejected_before_scope_or_any_handler():
    calls = []

    async def _scope(state, arguments, signal=None):
        calls.append("scope")
        return None

    async def _handler(state, arguments, signal=None):
        calls.append("handler")
        return ToolHandlerResult("ok")

    catalog = InMemoryToolCatalog((
        _registration("allowed", _handler, scope=_scope),
        _registration("blocked", _handler, scope=_scope),
    ))
    sink = RecordingSink()
    result = await CoreToolExecutor(catalog).execute_batch(
        _request(
            _call("a", "allowed"),
            _call("b", "blocked"),
            allowed={"allowed"},
        ),
        sink,
    )

    assert result.outcome is ToolBatchOutcome.REJECTED
    assert result.error == "tool_not_authorized"
    assert calls == []
    assert sink.events == []


@pytest.mark.asyncio
async def test_invalid_arguments_and_unknown_tool_fail_preflight_with_zero_execution():
    handler_calls = []

    async def _handler(state, arguments, signal=None):
        handler_calls.append(arguments)
        return ToolHandlerResult("ok")

    catalog = InMemoryToolCatalog((_registration("known", _handler),))
    executor = CoreToolExecutor(catalog)
    sink = RecordingSink()

    invalid = await executor.execute_batch(
        _request(_call("bad", "known", "[]")),
        sink,
    )
    unknown = await executor.execute_batch(
        _request(_call("missing", "unknown")),
        sink,
    )

    assert invalid.outcome is ToolBatchOutcome.FAILED
    assert invalid.error == "invalid_tool_arguments_shape"
    assert unknown.outcome is ToolBatchOutcome.REJECTED
    assert unknown.error == "unknown_tool"
    assert handler_calls == []
    assert sink.events == []


@pytest.mark.asyncio
async def test_scope_cache_approval_and_handler_run_in_fixed_order():
    order = []

    async def _scope(state, arguments, signal=None):
        order.append("scope")
        return None

    async def _handler(state, arguments, signal=None):
        order.append("handler")
        return ToolHandlerResult("ok")

    class ImmediateApproval:
        async def request(self, run_id, approval, event_sink, signal=None):
            order.append("approval")
            return ApprovalResult("approval-1", ApprovalStatus.APPROVED)

        def resolve(self, run_id, approval_id, decision):
            return None

        def cancel_pending(self, run_id):
            return 0

    catalog = InMemoryToolCatalog((
        _registration(
            "confirmA",
            _handler,
            mode="confirm",
            scope=_scope,
            probe=Probe(True, order=order),
        ),
    ))
    result = await CoreToolExecutor(
        catalog,
        approval_gateway=ImmediateApproval(),
    ).execute_batch(
        _request(_call("a", "confirmA")),
        RecordingSink(),
    )

    assert result.outcome is ToolBatchOutcome.COMPLETED
    assert order == ["scope", "cache", "approval", "handler"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approved", "expected_outcome", "expected_calls"),
    [
        (True, ToolBatchOutcome.COMPLETED, 1),
        (False, ToolBatchOutcome.DECLINED, 0),
    ],
)
async def test_confirm_tool_uses_real_run_bound_approval_before_handler(
    approved,
    expected_outcome,
    expected_calls,
):
    handler_calls = []

    async def _handler(state, arguments, signal=None):
        handler_calls.append(arguments)
        return ToolHandlerResult('{"success":true}')

    broker = InMemoryApprovalGateway()
    executor = CoreToolExecutor(
        InMemoryToolCatalog((_registration("confirmA", _handler, mode="confirm"),)),
        approval_gateway=broker,
    )
    sink = RecordingSink()
    task = asyncio.create_task(executor.execute_batch(
        _request(_call("a", "confirmA", '{"id":7}')),
        sink,
    ))

    for _ in range(100):
        if sink.events:
            break
        await asyncio.sleep(0)
    approval_id = str(sink.events[0].payload["approvalId"])
    broker.resolve("run-1", approval_id, "approve" if approved else "reject")
    result = await task

    assert result.outcome is expected_outcome
    assert len(handler_calls) == expected_calls
    assert [event.type for event in sink.events[:2]] == [
        CoreEventType.APPROVAL_REQUESTED,
        CoreEventType.APPROVAL_RESOLVED,
    ]
    assert sink.events[-1].type == CoreEventType.TOOL_CALL_COMPLETED


@pytest.mark.asyncio
async def test_confirm_tool_without_run_or_gateway_fails_closed():
    calls = []

    async def _handler(state, arguments, signal=None):
        calls.append(arguments)
        return ToolHandlerResult("ok")

    executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration("confirmA", _handler, mode="confirm"),
    )))
    result = await executor.execute_batch(
        _request(_call("a", "confirmA"), run_id=None),
        RecordingSink(),
    )

    assert result.outcome is ToolBatchOutcome.FAILED
    assert result.error == "approval_unavailable"
    assert result.results[0].approval_status is ApprovalStatus.UNAVAILABLE
    assert calls == []


@pytest.mark.asyncio
async def test_cancellation_during_approval_cleans_pending_and_never_calls_handler():
    calls = []

    async def _handler(state, arguments, signal=None):
        calls.append(arguments)
        return ToolHandlerResult("unexpected")

    broker = InMemoryApprovalGateway()
    executor = CoreToolExecutor(
        InMemoryToolCatalog((_registration("confirmA", _handler, mode="confirm"),)),
        approval_gateway=broker,
    )
    signal = asyncio.Event()
    sink = RecordingSink()
    task = asyncio.create_task(executor.execute_batch(
        _request(_call("a", "confirmA")),
        sink,
        signal,
    ))
    for _ in range(100):
        if sink.events:
            break
        await asyncio.sleep(0)
    signal.set()
    result = await asyncio.wait_for(task, timeout=1)

    assert result.outcome is ToolBatchOutcome.CANCELED
    assert result.error == "approval_canceled"
    assert calls == []
    assert broker.pending_count() == 0
    assert [event.type for event in sink.events] == [
        CoreEventType.APPROVAL_REQUESTED,
        CoreEventType.APPROVAL_RESOLVED,
        CoreEventType.TOOL_CALL_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_scope_rejection_and_handler_exception_never_leak_or_execute_unsafely():
    calls = []

    async def _reject_scope(state, arguments, signal=None):
        return "outside scope Bearer abcdefgh"

    async def _handler(state, arguments, signal=None):
        calls.append(arguments)
        raise RuntimeError("secret provider path C:\\private\\data.db")

    rejected_executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration("scoped", _handler, scope=_reject_scope),
    )))
    rejected = await rejected_executor.execute_batch(
        _request(_call("s", "scoped")),
        RecordingSink(),
    )
    assert rejected.outcome is ToolBatchOutcome.REJECTED
    assert rejected.error == "tool_scope_violation"
    assert "abcdefgh" not in rejected.results[0].content
    assert calls == []

    failing_executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration("failing", _handler, probe=Probe(raises=True)),
    )))
    failed = await failing_executor.execute_batch(
        _request(_call("f", "failing")),
        RecordingSink(),
    )
    assert failed.outcome is ToolBatchOutcome.FAILED
    assert failed.cache_hits == (False,)
    assert "secret" not in failed.results[0].content
    assert "private" not in failed.results[0].content


@pytest.mark.asyncio
async def test_oversized_handler_result_fails_without_emitting_domain_effects():
    async def _handler(state, arguments, signal=None):
        return ToolHandlerResult(
            "x" * 9,
            effects=(DomainEffect("test.must_not_emit", {"secret": True}),),
        )

    executor = CoreToolExecutor(
        InMemoryToolCatalog((_registration("large", _handler),)),
        limits=ToolExecutionLimits(max_result_chars=8),
    )
    sink = RecordingSink()
    result = await executor.execute_batch(
        _request(_call("large", "large")),
        sink,
    )

    assert result.outcome is ToolBatchOutcome.FAILED
    assert result.error == "tool_result_too_large"
    assert json.loads(result.results[0].content)["errorCode"] == "tool_result_too_large"
    assert "test.must_not_emit" not in [event.type for event in sink.events]


@pytest.mark.asyncio
async def test_domain_effects_cannot_emit_controller_owned_lifecycle_events():
    async def _handler(state, arguments, signal=None):
        return ToolHandlerResult(
            '{"proposal":true}',
            effects=(
                DomainEffect("test.before_reserved", {"visible": False}),
                DomainEffect(CoreEventType.RUN_COMPLETED, {"status": "done"}),
            ),
        )

    executor = CoreToolExecutor(
        InMemoryToolCatalog((_registration("malicious", _handler),)),
    )
    sink = RecordingSink()

    with pytest.raises(ContractViolationError, match="controller-owned"):
        await executor.execute_batch(
            _request(_call("malicious", "malicious")),
            sink,
        )

    assert sink.events == []


@pytest.mark.asyncio
async def test_cancellation_during_handler_is_awaited_and_stops_later_calls():
    started = asyncio.Event()
    cleaned = asyncio.Event()
    second_calls = []

    async def _blocked(state, arguments, signal=None):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    async def _second(state, arguments, signal=None):
        second_calls.append(arguments)
        return ToolHandlerResult("unexpected")

    executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration("blocked", _blocked),
        _registration("second", _second),
    )))
    signal = asyncio.Event()
    sink = RecordingSink()
    task = asyncio.create_task(executor.execute_batch(
        _request(
            _call("a", "blocked"),
            _call("b", "second"),
        ),
        sink,
        signal,
    ))
    await started.wait()
    signal.set()
    result = await asyncio.wait_for(task, timeout=1)

    assert result.outcome is ToolBatchOutcome.CANCELED
    assert result.error == "tool_execution_canceled"
    assert cleaned.is_set()
    assert second_calls == []
    assert sink.events[-1].payload["outcome"] == "canceled"


@pytest.mark.asyncio
async def test_cancellation_during_scope_validation_never_starts_handler():
    scope_started = asyncio.Event()
    scope_cleaned = asyncio.Event()
    handler_calls = []

    async def _scope(state, arguments, signal=None):
        scope_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            scope_cleaned.set()

    async def _handler(state, arguments, signal=None):
        handler_calls.append(arguments)
        return ToolHandlerResult("unexpected")

    executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration("scoped", _handler, scope=_scope),
    )))
    signal = asyncio.Event()
    task = asyncio.create_task(executor.execute_batch(
        _request(_call("a", "scoped")),
        RecordingSink(),
        signal,
    ))
    await scope_started.wait()
    signal.set()
    result = await asyncio.wait_for(task, timeout=1)

    assert result.outcome is ToolBatchOutcome.CANCELED
    assert scope_cleaned.is_set()
    assert handler_calls == []
