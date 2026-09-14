from __future__ import annotations

import asyncio
import json

import pytest

from purra.contracts import (
    ApprovalResult,
    ApprovalStatus,
    DomainEffect,
    ExecutionState,
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolCall,
    ToolDataContract,
    ToolExecutionLimits,
    ToolEffectState,
    ToolHandlerResult,
    ToolPlanningDisposition,
    ToolStepDisposition,
    ToolPolicy,
    ToolSchema,
)
from purra.events import CoreEventType
from purra.errors import ContractViolationError
from purra.ports import ToolRegistration
from purra.testing import assert_tool_execution_gateway_conforms
from purra.operations import AgentOperationController, OperationStatus
from purra.tools.approval import InMemoryApprovalGateway
from purra.tools.executor import CoreToolExecutor
from purra.tools.registry import InMemoryToolCatalog


class RecordingSink:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


class RecordingOperationOutput:
    def __init__(self):
        self.events = []

    async def accept_operation_event(self, event):
        self.events.append(event)
        return event


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
    parameters=None,
    data_contract=ToolDataContract(),
    cancellation_linearizable=False,
    host_managed_durability=False,
    max_argument_chars=None,
):
    return ToolRegistration(
        schema=ToolSchema(
            name=name,
            description=f"Tool {name}",
            parameters=(
                parameters
                if parameters is not None
                else {"type": "object", "properties": {}}
            ),
        ),
        handler=handler,
        policy=ToolPolicy(
            mode=mode,
            title=f"Use {name}",
            risk_level=("write" if mode != "read" else "read"),
        ),
        scope_validator=scope,
        cache_probe=probe,
        data_contract=data_contract,
        cancellation_linearizable=cancellation_linearizable,
        host_managed_durability=host_managed_durability,
        max_argument_chars=max_argument_chars,
    )


def _request(
    *calls,
    allowed=None,
    state=None,
    run_id="run-1",
    invocation_id=None,
):
    return ToolBatchRequest(
        run_id=run_id,
        invocation_id=invocation_id,
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
    state = ExecutionState()

    result, events = await assert_tool_execution_gateway_conforms(
        gateway=executor,
        request=_request(
            _call("call-a", "readA", '{"value":"first"}'),
            _call("call-b", "readA", '{"value":"second"}'),
            state=state,
        ),
    )

    assert result.outcome is ToolBatchOutcome.COMPLETED
    assert observations == ["first", "second"]
    assert state.domain["last"] == "second"
    assert [item.tool_call_id for item in result.results] == ["call-a", "call-b"]
    assert result.cache_hits == (True, True)
    assert [event.type for event in events] == [
        CoreEventType.TOOL_CALL_COMPLETED,
        CoreEventType.TOOL_CALL_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_each_executed_tool_call_has_one_authoritative_operation():
    async def _read(state, arguments, signal=None):
        del state, arguments, signal
        return ToolHandlerResult('{"success":true}')

    operation_output = RecordingOperationOutput()
    executor = CoreToolExecutor(
        InMemoryToolCatalog((_registration("readA", _read),)),
        operation_controller=AgentOperationController(operation_output),
    )

    result = await executor.execute_batch(
        _request(
            _call("call-a", "readA"),
            _call("call-b", "readA"),
            invocation_id="invocation-1",
        ),
        RecordingSink(),
    )

    assert result.outcome is ToolBatchOutcome.COMPLETED
    assert len(operation_output.events) == 4
    first_started, first_finished, second_started, second_finished = (
        operation_output.events
    )
    assert first_started.display["labelParams"] == {
        "toolCallId": "call-a",
        "toolName": "readA",
    }
    assert first_finished.operation_id == first_started.operation_id
    assert first_started.invocation_id == "invocation-1"
    assert first_finished.status is OperationStatus.SUCCEEDED
    assert second_finished.operation_id == second_started.operation_id
    assert second_finished.status is OperationStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_successful_partial_batch_is_not_reported_as_step_completion():
    calls = 0

    async def _append(state, arguments, signal=None):
        del state, arguments, signal
        nonlocal calls
        calls += 1
        return ToolHandlerResult(
            '{"success":true}',
            step_disposition=(
                ToolStepDisposition.CONTINUE
                if calls == 1
                else ToolStepDisposition.COMPLETE
            ),
        )

    executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration("appendBatch", _append),
    )))

    partial = await executor.execute_batch(
        _request(_call("call-partial", "appendBatch")),
        RecordingSink(),
    )
    completed = await executor.execute_batch(
        _request(_call("call-complete", "appendBatch")),
        RecordingSink(),
    )

    assert partial.outcome is ToolBatchOutcome.PROGRESSED
    assert partial.results[0].step_disposition is ToolStepDisposition.CONTINUE
    assert completed.outcome is ToolBatchOutcome.COMPLETED


@pytest.mark.asyncio
async def test_host_replan_signal_survives_tool_execution_boundary():
    async def _branch(state, arguments, signal=None):
        del state, arguments, signal
        return ToolHandlerResult(
            '{"branch":"selected"}',
            planning_disposition=ToolPlanningDisposition.REPLAN,
        )

    executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration("selectBranch", _branch),
    )))
    result = await executor.execute_batch(
        _request(_call("call-branch", "selectBranch")),
        RecordingSink(),
    )

    assert result.outcome is ToolBatchOutcome.COMPLETED
    assert result.replan_requested is True
    assert result.results[0].planning_disposition is (
        ToolPlanningDisposition.REPLAN
    )


@pytest.mark.asyncio
async def test_handler_receives_detached_standard_json_containers():
    observed = []

    async def _read(state, arguments, signal=None):
        observed.append(arguments)
        arguments["coverage"]["readChapterIds"].append("chapter-2")
        return ToolHandlerResult('{"success":true}')

    executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration("readA", _read),
    )))
    result = await executor.execute_batch(
        _request(_call(
            "call-a",
            "readA",
            '{"coverage":{"readChapterIds":["chapter-1"]}}',
        )),
        RecordingSink(),
    )

    assert result.outcome is ToolBatchOutcome.COMPLETED
    assert isinstance(observed[0], dict)
    assert isinstance(observed[0]["coverage"], dict)
    assert isinstance(observed[0]["coverage"]["readChapterIds"], list)
    assert observed[0]["coverage"]["readChapterIds"] == [
        "chapter-1",
        "chapter-2",
    ]


@pytest.mark.asyncio
async def test_schema_guided_normalization_recovers_stringified_array_arguments():
    observed = []

    async def _read(state, arguments, signal=None):
        observed.append(arguments)
        return ToolHandlerResult('{"success":true}')

    parameters = {
        "type": "object",
        "properties": {
            "episodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "summary": {"type": "string"},
                    },
                },
            },
        },
    }
    # This mirrors the provider output from run_45eb136a8c074c9c: the array
    # was encoded as a string and quotes inside prose were not escaped for the
    # nested JSON layer, while the outer function arguments remained valid.
    stringified_episodes = (
        '[{"id":"ep-01","summary":"看到"犬域"门匾"}]'
    )
    executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration("readA", _read, parameters=parameters),
    )))
    sink = RecordingSink()

    result = await executor.execute_batch(
        _request(_call(
            "call-a",
            "readA",
            json.dumps({"episodes": stringified_episodes}, ensure_ascii=False),
        )),
        sink,
    )

    assert result.outcome is ToolBatchOutcome.COMPLETED
    assert observed == [{
        "episodes": [{"id": "ep-01", "summary": '看到"犬域"门匾'}],
    }]
    assert sink.events[-1].payload["normalizedArgumentPaths"] == ["$.episodes"]


@pytest.mark.asyncio
async def test_schema_guided_normalization_fails_closed_for_undecodable_structure():
    observed = []

    async def _read(state, arguments, signal=None):
        observed.append(arguments)
        return ToolHandlerResult('{"success":true}')

    parameters = {
        "type": "object",
        "properties": {"episodes": {"type": "array", "items": {}}},
    }
    executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration("readA", _read, parameters=parameters),
    )))

    result = await executor.execute_batch(
        _request(_call(
            "call-a",
            "readA",
            json.dumps({"episodes": "[not-json]"}),
        )),
        RecordingSink(),
    )

    assert result.outcome is ToolBatchOutcome.FAILED
    assert result.error == "invalid_tool_arguments_schema"
    assert result.effect_state is ToolEffectState.NOT_STARTED
    assert json.loads(result.results[0].content)["error"].startswith(
        "Tool argument $.episodes must be a JSON array"
    )
    assert observed == []


@pytest.mark.asyncio
async def test_core_enforces_schema_limits_after_json_decode_with_diagnostics():
    observed = []

    async def _read(state, arguments, signal=None):
        observed.append(arguments)
        return ToolHandlerResult('{"success":true}')

    executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration(
            "boundedText",
            _read,
            parameters={
                "type": "object",
                "properties": {
                    "value": {"type": "string", "maxLength": 4},
                },
                "required": ["value"],
                "additionalProperties": False,
            },
        ),
    )))

    result = await executor.execute_batch(
        _request(_call("too-long", "boundedText", '{"value":"12345"}')),
        RecordingSink(),
    )

    assert result.outcome is ToolBatchOutcome.FAILED
    assert result.error == "invalid_tool_arguments_schema"
    payload = json.loads(result.results[0].content)
    assert payload["diagnostics"] == {
        "stage": "schema_validation",
        "toolName": "boundedText",
        "path": "$.value",
        "keyword": "maxLength",
        "actualChars": 5,
        "maxChars": 4,
        "measurement": "decoded_string_chars",
    }
    assert observed == []


@pytest.mark.asyncio
async def test_core_measures_escaped_unicode_by_decoded_schema_value():
    observed = []

    async def _read(state, arguments, signal=None):
        observed.append(arguments["value"])
        return ToolHandlerResult('{"success":true}')

    raw = json.dumps({"value": "雾" * 6_000}, ensure_ascii=True)
    assert len(raw) > 32_000
    executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration(
            "unicodeText",
            _read,
            parameters={
                "type": "object",
                "properties": {
                    "value": {"type": "string", "maxLength": 6_000},
                },
                "required": ["value"],
                "additionalProperties": False,
            },
        ),
    )))

    result = await executor.execute_batch(
        _request(_call("unicode", "unicodeText", raw)),
        RecordingSink(),
    )

    assert result.outcome is ToolBatchOutcome.COMPLETED
    assert observed == ["雾" * 6_000]


@pytest.mark.asyncio
async def test_core_rejects_host_owned_fields_omitted_from_strict_schema():
    observed = []

    async def _read(state, arguments, signal=None):
        observed.append(arguments)
        return ToolHandlerResult('{"success":true}')

    executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration(
            "hostBound",
            _read,
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "additionalProperties": False,
            },
        ),
    )))

    result = await executor.execute_batch(
        _request(_call(
            "host-field",
            "hostBound",
            '{"value":"ok","projectId":"forged"}',
        )),
        RecordingSink(),
    )

    assert result.outcome is ToolBatchOutcome.FAILED
    payload = json.loads(result.results[0].content)
    assert payload["diagnostics"]["keyword"] == "additionalProperties"
    assert payload["diagnostics"]["unknownProperties"] == ["projectId"]
    assert observed == []


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

        async def resolve(self, run_id, approval_id, decision):
            return None

        async def cancel_pending(self, run_id):
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
async def test_recoverable_host_durable_artifact_batches_allow_same_tool_calls():
    observed = []

    async def _append(state, arguments, signal=None):
        observed.append(arguments["value"])
        return ToolHandlerResult('{"success":true}')

    registration = _registration(
        "appendArtifactBatch",
        _append,
        mode="propose",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
        },
        data_contract=ToolDataContract(
            model_owned_paths=("value",),
            host_derived_paths=("revision",),
            payload_mode="batch",
        ),
        cancellation_linearizable=True,
        host_managed_durability=True,
    )
    executor = CoreToolExecutor(InMemoryToolCatalog((registration,)))

    result = await executor.execute_batch(
        _request(
            _call("call-a", "appendArtifactBatch", '{"value":"first"}'),
            _call("call-b", "appendArtifactBatch", '{"value":"second"}'),
        ),
        RecordingSink(),
    )

    assert result.outcome is ToolBatchOutcome.COMPLETED
    assert observed == ["first", "second"]


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

        async def resolve(self, run_id, approval_id, decision):
            return None

        async def cancel_pending(self, run_id):
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
    assert order == ["scope", "cache", "approval", "scope", "handler"]


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
    await broker.resolve("run-1", approval_id, "approve" if approved else "reject")
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
    failing_sink = RecordingSink()
    failed = await failing_executor.execute_batch(
        _request(_call("f", "failing")),
        failing_sink,
    )
    assert failed.outcome is ToolBatchOutcome.FAILED
    assert failed.cache_hits == (False,)
    assert failed.effect_state is ToolEffectState.NOT_STARTED
    assert "secret" not in failed.results[0].content
    assert "private" not in failed.results[0].content
    assert failing_sink.events[-1].payload["exceptionType"] == "RuntimeError"
    assert "secret" not in str(failing_sink.events[-1].payload)

    failing_write_executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration("failingWrite", _handler, mode="propose"),
    )))
    failed_write = await failing_write_executor.execute_batch(
        _request(_call("fw", "failingWrite")),
        RecordingSink(),
    )
    assert failed_write.outcome is ToolBatchOutcome.FAILED
    assert failed_write.effect_state is ToolEffectState.UNKNOWN


@pytest.mark.asyncio
async def test_write_handler_can_prove_input_failure_started_no_effect():
    async def _handler(state, arguments, signal=None):
        return ToolHandlerResult(
            '{"success":false,"error":"invalid item"}',
            error_code="tool_input_invalid",
            effect_state=ToolEffectState.NOT_STARTED,
        )

    executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration("validateWrite", _handler, mode="propose"),
    )))

    result = await executor.execute_batch(
        _request(_call("invalid", "validateWrite")),
        RecordingSink(),
    )

    assert result.outcome is ToolBatchOutcome.FAILED
    assert result.error == "tool_input_invalid"
    assert result.effect_state is ToolEffectState.NOT_STARTED


@pytest.mark.asyncio
async def test_mid_batch_read_failure_closes_unexecuted_tool_calls():
    executed = []

    async def _handler(state, arguments, signal=None):
        del state, signal
        name = str(arguments["name"])
        executed.append(name)
        if name == "invalid":
            return ToolHandlerResult(
                '{"success":false,"error":"outside scoped catalog"}',
                error_code="tool_input_invalid",
                effect_state=ToolEffectState.NOT_STARTED,
            )
        return ToolHandlerResult('{"success":true}')

    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    executor = CoreToolExecutor(InMemoryToolCatalog((
        _registration("readA", _handler, parameters=schema),
    )))
    sink = RecordingSink()
    result = await executor.execute_batch(
        _request(
            _call("first", "readA", '{"name":"valid"}'),
            _call("second", "readA", '{"name":"invalid"}'),
            _call("third", "readA", '{"name":"must-not-run"}'),
        ),
        sink,
    )

    assert result.outcome is ToolBatchOutcome.FAILED
    assert result.error == "tool_input_invalid"
    assert result.effect_state is ToolEffectState.NOT_STARTED
    assert executed == ["valid", "invalid"]
    assert [item.tool_call_id for item in result.results] == [
        "first",
        "second",
        "third",
    ]
    assert [item.error for item in result.results] == [
        None,
        "tool_input_invalid",
        "tool_batch_aborted",
    ]
    assert result.cache_hits == (False, False, False)
    completed = [
        event for event in sink.events
        if event.type == CoreEventType.TOOL_CALL_COMPLETED
    ]
    assert [event.payload["toolCallId"] for event in completed] == [
        "first",
        "second",
        "third",
    ]


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
async def test_registration_can_override_core_argument_size_default():
    observed = []

    async def _handler(state, arguments, signal=None):
        observed.append(arguments["value"])
        return ToolHandlerResult('{"success":true}')

    registration = _registration(
        "boundedLargeInput",
        _handler,
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string", "maxLength": 40}},
            "required": ["value"],
        },
        max_argument_chars=64,
    )
    executor = CoreToolExecutor(
        InMemoryToolCatalog((registration,)),
        limits=ToolExecutionLimits(max_argument_chars=8),
    )
    result = await executor.execute_batch(
        _request(_call("large", "boundedLargeInput", '{"value":"123456789"}')),
        RecordingSink(),
    )

    assert result.outcome is ToolBatchOutcome.COMPLETED
    assert observed == ["123456789"]


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
