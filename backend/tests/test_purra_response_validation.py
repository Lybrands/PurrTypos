from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace

import pytest

from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRuntimeResult,
    DomainContext,
    ExecutionTransition,
    MessageRole,
    ModelCompletion,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelStream,
    ModelStreamChunk,
    ResponseConstraints,
    ResponseValidationResult,
    RuntimeOutcome,
    StepExecutor,
    ToolBatchOutcome,
    ToolBatchResult,
    ToolCallDelta,
    ToolCallResult,
    ToolSchema,
)
from purra.api import AgentCoreRunOptions
from purra.errors import ResponseJudgeContractError
from purra.events import AgentEvent, CoreEventType
from purra.model_protocol import generic_capability_snapshot
from purra.operations import (
    AgentOperationController,
    OperationKind,
    OperationStatus,
)
from purra.ports import ResponseJudge
from purra.runtime import AgentRuntime


class _ScriptedModelGateway:
    def __init__(self, responses: Sequence[str | ModelStreamChunk]):
        self.responses = list(responses)
        self.message_rounds: list[tuple[AgentMessage, ...]] = []
        self.invocations: list[ModelInvocation] = []

    async def stream(self, messages, invocation, signal=None):
        self.message_rounds.append(tuple(messages))
        self.invocations.append(invocation)
        response = self.responses.pop(0)

        async def _chunks():
            if isinstance(response, ModelStreamChunk):
                yield response
                return
            yield ModelStreamChunk(
                content_delta=response,
                finish_reason=ModelFinishReason.STOP,
            )

        return ModelStream(chunks=_chunks(), model="validator-model")

    async def complete(self, messages, invocation, signal=None):
        return ModelCompletion(
            message=AgentMessage(role="assistant", content="unused"),
            model="validator-model",
        )


class _NoBundledAnswer:
    def __init__(self):
        self.calls: list[tuple[str, tuple[AgentMessage, ...]]] = []

    def validate(self, *, content, messages):
        self.calls.append((content, tuple(messages)))
        if "bundled" not in content:
            return ResponseValidationResult()
        return ResponseValidationResult(
            violation_code="fixture.bundled_answer",
            repair_guidance="Do not bundle independent fixture dimensions.",
            details={"fixture": True},
        )


class _SemanticJudge:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def judge(self, *, content, messages, signal=None):
        self.calls.append((content, tuple(messages), signal))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _ReadToolGateway:
    def __init__(self):
        self.requests = []

    async def execute_batch(self, request, event_sink, signal=None):
        del event_sink, signal
        self.requests.append(request)
        call = request.calls[0]
        return ToolBatchResult(
            results=(ToolCallResult(
                tool_call_id=call.id,
                tool_name=call.name,
                content='{"success":true,"content":"fixture source"}',
            ),),
            outcome=ToolBatchOutcome.COMPLETED,
        )


class _TraceObserver:
    def __init__(self, allowed_tool_names: Sequence[str] = ()):
        self.traces = []
        self.allowed_tool_names = frozenset(allowed_tool_names)

    def current_allowed_tool_names(self):
        return self.allowed_tool_names

    def future_allowed_tool_names(self):
        return frozenset()

    def current_execution_transition(self):
        return ExecutionTransition(
            step_id="response-validation-test",
            executor=(
                StepExecutor.TOOL
                if self.allowed_tool_names
                else StepExecutor.MODEL
            ),
            allowed_tool_names=self.allowed_tool_names,
        )

    async def record_trace(self, trace):
        self.traces.append(trace)

    async def on_model_delta(self):
        return None

    async def on_tool_calls_started(self, tool_names):
        del tool_names

    async def on_tool_round_completed(self, outcome=None):
        del outcome


class _OperationOutput:
    def __init__(self):
        self.events = []

    async def accept_operation_event(self, event):
        self.events.append(event)
        return event


def _request(*, tools_enabled: bool = False) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content="review"),),
        model=ModelRequest(
            provider="fixture",
            model="model",
            capability_snapshot=replace(
                generic_capability_snapshot(),
                profile_id="test:model",
                max_output_tokens=32_000,
            ),
        ),
        domain_context=DomainContext(namespace="fixture"),
        tools_enabled=tools_enabled,
    )


async def _collect(runtime: AgentRuntime, *, request=None, **kwargs):
    return [
        update
        async for update in runtime.run(request or _request(), **kwargs)
    ]


def _result(updates) -> AgentRuntimeResult:
    return next(
        update for update in updates
        if isinstance(update, AgentRuntimeResult)
    )


def _model_deltas(updates) -> list[str]:
    return [
        update.payload["delta"]
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
    ]


def _constraint_traces(observer: _TraceObserver):
    return [
        trace
        for trace in observer.traces
        if trace.outcome.startswith("response_constraint_")
    ]


def _assert_traces_omit_candidates(
    observer: _TraceObserver,
    candidates: Sequence[str],
) -> None:
    for trace in observer.traces:
        serialized_details = repr(dict(trace.details))
        assert all(candidate not in serialized_details for candidate in candidates)


def test_response_validation_result_requires_a_complete_rejection_contract():
    assert ResponseValidationResult().accepted is True
    rejected = ResponseValidationResult(
        violation_code="fixture.invalid",
        repair_guidance="Repair it.",
        details={"items": [1]},
    )
    assert rejected.accepted is False
    assert rejected.details["items"] == (1,)

    with pytest.raises(ValueError, match="requires both"):
        ResponseValidationResult(violation_code="fixture.invalid")


def test_run_options_accept_only_request_scoped_response_ports():
    validator = _NoBundledAnswer()
    judge = _SemanticJudge((ResponseValidationResult(),))

    options = AgentCoreRunOptions(
        response_validators=(validator,),
        response_judges=(judge,),
    )

    assert options.response_validators == (validator,)
    assert options.response_judges == (judge,)
    with pytest.raises(TypeError, match="ResponseValidator"):
        AgentCoreRunOptions(response_validators=(object(),))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ResponseJudge"):
        AgentCoreRunOptions(response_judges=(object(),))  # type: ignore[arg-type]


def test_async_semantic_judge_is_a_generic_runtime_checkable_core_port():
    assert isinstance(
        _SemanticJudge((ResponseValidationResult(),)),
        ResponseJudge,
    )


@pytest.mark.asyncio
async def test_injected_validator_withholds_and_repairs_once_without_domain_logic_in_core():
    invalid = "bundled fixture dimensions"
    repaired = "1. one fixture dimension"
    model = _ScriptedModelGateway((invalid, repaired))
    validator = _NoBundledAnswer()

    updates = await _collect(
        AgentRuntime(model_gateway=model),
        response_constraints=ResponseConstraints(
            exact_top_level_item_count=1,
        ),
        response_validators=(validator,),
    )

    assert len(model.invocations) == 2
    assert [call[0] for call in validator.calls] == [invalid, repaired]
    assert model.message_rounds[1][-2] == AgentMessage(
        role=MessageRole.ASSISTANT,
        content=invalid,
    )
    assert model.message_rounds[1][-1].role is MessageRole.DEVELOPER
    assert "Do not bundle independent fixture dimensions" in (
        model.message_rounds[1][-1].content
    )
    assert "exactly 1 top-level items" in model.message_rounds[1][-1].content
    deltas = [
        update.payload["delta"]
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
    ]
    assert deltas == []
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == repaired


@pytest.mark.asyncio
async def test_each_validator_call_has_one_validation_operation():
    output = _OperationOutput()
    controller = AgentOperationController(output)
    validator = _NoBundledAnswer()

    updates = await _collect(
        AgentRuntime(
            model_gateway=_ScriptedModelGateway(("valid response",)),
            operation_controller=controller,
        ),
        response_validators=(validator,),
    )

    validation_starts = [
        event
        for event in output.events
        if getattr(event, "kind", None) is OperationKind.VALIDATION
    ]
    assert len(validation_starts) == 1
    validation_terminals = [
        event
        for event in output.events
        if getattr(event, "operation_id", None)
        == validation_starts[0].operation_id
        and getattr(event, "status", None) is not None
    ]
    assert len(validation_terminals) == 1
    assert validation_terminals[0].status is OperationStatus.SUCCEEDED
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED


@pytest.mark.asyncio
async def test_response_repairs_have_independent_phase_budgets_after_tool_round():
    deterministic_invalid = "private bundled deterministic candidate"
    semantic_invalid = "private semantic candidate"
    compliant = "private compliant candidate"
    tool_name = "read_fixture_source"
    model = _ScriptedModelGateway((
        ModelStreamChunk(
            tool_call_deltas=(ToolCallDelta(
                index=0,
                id="call-read",
                type="function",
                name=tool_name,
                arguments_fragment="{}",
            ),),
            finish_reason=ModelFinishReason.TOOL_CALLS,
        ),
        deterministic_invalid,
        semantic_invalid,
        compliant,
    ))
    validator = _NoBundledAnswer()
    judge = _SemanticJudge((
        ResponseValidationResult(
            violation_code="fixture.semantic_bundle",
            repair_guidance="Keep one semantic dimension only.",
        ),
        ResponseValidationResult(),
    ))
    observer = _TraceObserver((tool_name,))
    tool_gateway = _ReadToolGateway()
    schema = ToolSchema(
        name=tool_name,
        description="Read one fixture source.",
        parameters={"type": "object", "properties": {}},
    )

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tool_gateway,
            observer=observer,
        ),
        request=_request(tools_enabled=True),
        tools=(schema,),
        response_validators=(validator,),
        response_judges=(judge,),
    )

    assert len(model.invocations) == 4
    assert len(tool_gateway.requests) == 1
    assert model.invocations[0].tools == (schema,)
    assert model.invocations[1].tools == (schema,)
    assert model.invocations[2].tools == ()
    assert model.invocations[3].tools == ()
    assert [call[0] for call in validator.calls] == [
        deterministic_invalid,
        semantic_invalid,
        compliant,
    ]
    assert [call[0] for call in judge.calls] == [semantic_invalid, compliant]
    assert model.message_rounds[2][-2] == AgentMessage(
        role=MessageRole.ASSISTANT,
        content=deterministic_invalid,
    )
    assert model.message_rounds[3][-2] == AgentMessage(
        role=MessageRole.ASSISTANT,
        content=semantic_invalid,
    )
    assert all(
        round_messages[-1].role is MessageRole.DEVELOPER
        for round_messages in model.message_rounds[2:]
    )
    assert _model_deltas(updates) == []
    assert _result(updates).final_response == compliant
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == compliant

    repair_traces = _constraint_traces(observer)
    assert [trace.outcome for trace in repair_traces] == [
        "response_constraint_retry",
        "response_constraint_retry",
    ]
    assert [trace.details["repairPhase"] for trace in repair_traces] == [
        "deterministic",
        "semantic",
    ]
    assert [trace.details["repairAttempts"] for trace in repair_traces] == [0, 1]
    assert [trace.details["repairPhasesUsed"] for trace in repair_traces] == [
        (),
        ("deterministic",),
    ]
    _assert_traces_omit_candidates(
        observer,
        (deterministic_invalid, semantic_invalid, compliant),
    )


@pytest.mark.asyncio
async def test_injected_validator_fails_closed_after_its_single_repair():
    candidates = ("bundled once", "bundled twice")
    model = _ScriptedModelGateway(candidates)
    observer = _TraceObserver()
    judge = _SemanticJudge((ResponseValidationResult(),))

    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
        response_validators=(_NoBundledAnswer(),),
        response_judges=(judge,),
    )

    assert len(model.invocations) == 2
    assert model.invocations[1].tools == ()
    assert judge.calls == []
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in updates
    )
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "response_constraint_violation"
    repair_traces = _constraint_traces(observer)
    assert [trace.outcome for trace in repair_traces] == [
        "response_constraint_retry",
        "response_constraint_rejected",
    ]
    assert [trace.details["repairPhase"] for trace in repair_traces] == [
        "deterministic",
        "deterministic",
    ]
    assert [trace.details["repairAttempts"] for trace in repair_traces] == [0, 1]
    _assert_traces_omit_candidates(observer, candidates)


@pytest.mark.asyncio
async def test_async_judge_withholds_repairs_and_rejudges_once_without_tools():
    invalid = "semantic bundle"
    repaired = "one semantic dimension"
    model = _ScriptedModelGateway((invalid, repaired))
    observer = _TraceObserver()
    judge = _SemanticJudge((
        ResponseValidationResult(
            violation_code="fixture.semantic_bundle",
            repair_guidance="Keep one semantic dimension only.",
        ),
        ResponseValidationResult(),
    ))

    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
        response_judges=(judge,),
    )

    assert [call[0] for call in judge.calls] == [invalid, repaired]
    assert len(model.invocations) == 2
    assert model.invocations[1].tools == ()
    assert model.message_rounds[1][-2] == AgentMessage(
        role=MessageRole.ASSISTANT,
        content=invalid,
    )
    assert "Keep one semantic dimension only" in (
        model.message_rounds[1][-1].content
    )
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in updates
    )
    assert _result(updates).final_response == repaired
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    judge_traces = [
        trace for trace in observer.traces
        if trace.outcome.startswith("response_judge_")
    ]
    assert [trace.outcome for trace in judge_traces] == [
        "response_judge_rejected",
        "response_judge_passed",
    ]
    assert [trace.details["judgeIndex"] for trace in judge_traces] == [0, 0]
    assert all(trace.duration_ms is not None for trace in judge_traces)
    assert all("candidate" not in trace.details for trace in judge_traces)


@pytest.mark.asyncio
async def test_deterministic_phase_can_use_remaining_budget_after_semantic_repair():
    semantic_invalid = "private semantic first candidate"
    deterministic_invalid = "private bundled candidate after semantic repair"
    compliant = "private compliant candidate after both repairs"
    tool_name = "available_before_response_repair"
    model = _ScriptedModelGateway((
        semantic_invalid,
        deterministic_invalid,
        compliant,
    ))
    validator = _NoBundledAnswer()
    judge = _SemanticJudge((
        ResponseValidationResult(
            violation_code="fixture.semantic_bundle",
            repair_guidance="Keep one semantic dimension only.",
        ),
        ResponseValidationResult(),
    ))
    observer = _TraceObserver((tool_name,))
    schema = ToolSchema(
        name=tool_name,
        description="A tool unavailable during response repair.",
        parameters={"type": "object", "properties": {}},
    )

    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
        request=_request(tools_enabled=True),
        tools=(schema,),
        response_validators=(validator,),
        response_judges=(judge,),
    )

    assert len(model.invocations) == 3
    assert model.invocations[0].tools == (schema,)
    assert model.invocations[1].tools == ()
    assert model.invocations[2].tools == ()
    assert [call[0] for call in validator.calls] == [
        semantic_invalid,
        deterministic_invalid,
        compliant,
    ]
    assert [call[0] for call in judge.calls] == [semantic_invalid, compliant]
    assert _model_deltas(updates) == []
    assert _result(updates).final_response == compliant
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == compliant

    repair_traces = _constraint_traces(observer)
    assert [trace.outcome for trace in repair_traces] == [
        "response_constraint_retry",
        "response_constraint_retry",
    ]
    assert [trace.details["repairPhase"] for trace in repair_traces] == [
        "semantic",
        "deterministic",
    ]
    assert [trace.details["repairAttempts"] for trace in repair_traces] == [0, 1]
    assert [trace.details["repairPhasesUsed"] for trace in repair_traces] == [
        (),
        ("semantic",),
    ]
    _assert_traces_omit_candidates(
        observer,
        (semantic_invalid, deterministic_invalid, compliant),
    )


@pytest.mark.asyncio
async def test_async_judge_exception_and_contract_violation_fail_closed():
    exception_updates = await _collect(
        AgentRuntime(model_gateway=_ScriptedModelGateway(("candidate",))),
        response_judges=(_SemanticJudge((RuntimeError("private detail"),)),),
    )
    contract_updates = await _collect(
        AgentRuntime(model_gateway=_ScriptedModelGateway(("candidate",))),
        response_judges=(_SemanticJudge((object(),)),),
    )

    assert _result(exception_updates).error_code == "response_judge_error"
    assert _result(contract_updates).error_code == (
        "response_judge_contract_violation"
    )
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in (*exception_updates, *contract_updates)
    )


@pytest.mark.asyncio
async def test_async_judge_declared_contract_error_has_precise_failure_code():
    updates = await _collect(
        AgentRuntime(model_gateway=_ScriptedModelGateway(("candidate",))),
        response_judges=(_SemanticJudge((
            ResponseJudgeContractError("invalid verdict JSON"),
        )),),
    )

    assert _result(updates).error_code == "response_judge_contract_violation"


@pytest.mark.asyncio
async def test_async_judge_fails_closed_after_the_single_semantic_repair():
    rejection = ResponseValidationResult(
        violation_code="fixture.semantic_bundle",
        repair_guidance="Keep one semantic dimension only.",
    )
    judge = _SemanticJudge((rejection, rejection))

    updates = await _collect(
        AgentRuntime(
            model_gateway=_ScriptedModelGateway(("first", "still invalid")),
        ),
        response_judges=(judge,),
    )

    assert [call[0] for call in judge.calls] == ["first", "still invalid"]
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "response_constraint_violation"
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in updates
    )


@pytest.mark.asyncio
async def test_async_judge_obeys_run_cancellation_and_fails_as_canceled():
    started = asyncio.Event()

    class _BlockingJudge:
        async def judge(self, *, content, messages, signal=None):
            del content, messages
            assert signal is cancel_signal
            started.set()
            await asyncio.Event().wait()
            return ResponseValidationResult()

    cancel_signal = asyncio.Event()
    task = asyncio.create_task(_collect(
        AgentRuntime(model_gateway=_ScriptedModelGateway(("candidate",))),
        response_judges=(_BlockingJudge(),),
        signal=cancel_signal,
    ))
    await started.wait()
    cancel_signal.set()
    updates = await task

    assert _result(updates).outcome is RuntimeOutcome.CANCELED
    assert _result(updates).error_code == "request_canceled"


@pytest.mark.asyncio
async def test_hard_structure_rejection_does_not_call_semantic_judge_until_repair():
    model = _ScriptedModelGateway(("bundled", "single"))
    judge = _SemanticJudge((ResponseValidationResult(),))

    updates = await _collect(
        AgentRuntime(model_gateway=model),
        response_validators=(_NoBundledAnswer(),),
        response_judges=(judge,),
    )

    assert [call[0] for call in judge.calls] == ["single"]
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
