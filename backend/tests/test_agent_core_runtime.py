from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from agent_core.context_budget import (
    allocate_context_budget,
    estimate_tool_schema_tokens,
)
from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRuntimeResult,
    ContextBudget,
    ContextBudgetClaim,
    DomainContext,
    ExecutionState,
    ModelCompletion,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelStream,
    ModelStreamChunk,
    RuntimeLimits,
    RuntimeOutcome,
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolBatchResult,
    ToolCallDelta,
    ToolCallResult,
    ToolChoiceMode,
    ToolSchema,
    TraceRecord,
)
from agent_core.errors import UnsupportedModelFeatureError
from agent_core.events import AgentEvent, CoreEventType
from agent_core.ports import ModelGateway, RuntimeObserver, ToolExecutionGateway
from agent_core.runtime import AgentRuntime


class ScriptedModelGateway:
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.invocations: list[ModelInvocation] = []
        self.message_rounds: list[tuple[AgentMessage, ...]] = []

    async def stream(self, messages, invocation, signal=None):
        self.invocations.append(invocation)
        self.message_rounds.append(tuple(messages))
        if not self.rounds:
            raise AssertionError("unexpected model round")
        scripted = self.rounds.pop(0)
        if isinstance(scripted, Exception):
            raise scripted

        async def _chunks():
            for chunk in scripted:
                yield chunk

        return ModelStream(chunks=_chunks(), model="resolved-model")

    async def complete(self, messages, invocation, signal=None):
        return ModelCompletion(
            message=AgentMessage(role="assistant", content="unused"),
            model="resolved-model",
        )


class ScriptedToolGateway:
    def __init__(self, batches, progress: Sequence[AgentEvent] = ()):
        self.batches = list(batches)
        self.progress = tuple(progress)
        self.requests: list[ToolBatchRequest] = []

    async def execute_batch(self, request, event_sink, signal=None):
        self.requests.append(request)
        for event in self.progress:
            await event_sink.emit(event)
            await asyncio.sleep(0)
        if not self.batches:
            raise AssertionError("unexpected tool batch")
        return self.batches.pop(0)


class RecordingObserver:
    def __init__(self, scopes: Sequence[set[str] | frozenset[str]] = ()):
        self.scopes = [frozenset(scope) for scope in scopes]
        self.scope_index = 0
        self.traces: list[TraceRecord] = []
        self.model_delta_count = 0
        self.started_tools: list[tuple[str, ...]] = []
        self.completed_tool_rounds = 0

    def current_allowed_tool_names(self) -> frozenset[str]:
        if not self.scopes:
            return frozenset()
        return self.scopes[min(self.scope_index, len(self.scopes) - 1)]

    async def record_trace(self, trace: TraceRecord) -> None:
        self.traces.append(trace)

    async def on_model_delta(self) -> None:
        self.model_delta_count += 1

    async def on_tool_calls_started(self, tool_names: tuple[str, ...]) -> None:
        self.started_tools.append(tool_names)

    async def on_tool_round_completed(self) -> None:
        self.completed_tool_rounds += 1
        self.scope_index += 1


def _request(
    *,
    tools_enabled: bool = True,
    context_window: int | None = None,
) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content="work"),),
        model=ModelRequest(provider="openai", model="model"),
        domain_context=DomainContext(namespace="test"),
        context_window=context_window,
        tools_enabled=tools_enabled,
    )


def _schema(name: str) -> ToolSchema:
    return ToolSchema(
        name=name,
        description=f"Tool {name}",
        parameters={"type": "object", "properties": {}},
    )


def _matching_budget(
    *,
    window: int,
    tools: Sequence[ToolSchema] = (),
    output: int = 64,
) -> ContextBudget:
    schema_tokens = estimate_tool_schema_tokens(tools)
    safety = 64
    runtime = 128
    return ContextBudget(
        window_tokens=window,
        output_reserve_tokens=output,
        safety_reserve_tokens=safety,
        runtime_reserve_tokens=runtime,
        tool_schema_tokens=schema_tokens,
        provider_input_tokens=(
            window - output - safety - runtime - schema_tokens
        ),
    )


def _answer(text: str) -> list[ModelStreamChunk]:
    return [ModelStreamChunk(
        content_delta=text,
        finish_reason=ModelFinishReason.STOP,
    )]


def _tool_call(
    call_id: str,
    name: str,
    *,
    content_delta: str = "",
) -> list[ModelStreamChunk]:
    return [ModelStreamChunk(
        content_delta=content_delta,
        tool_call_deltas=(ToolCallDelta(
            index=0,
            id=call_id,
            type="function",
            name=name,
            arguments_fragment="{}",
        ),),
        finish_reason=ModelFinishReason.TOOL_CALLS,
    )]


def _batch(
    call_id: str,
    name: str,
    *,
    outcome: ToolBatchOutcome = ToolBatchOutcome.COMPLETED,
    content: str = '{"success":true}',
    error: str | None = None,
) -> ToolBatchResult:
    return ToolBatchResult(
        results=(ToolCallResult(
            tool_call_id=call_id,
            tool_name=name,
            content=content,
            error=error,
        ),),
        outcome=outcome,
        error=error,
    )


async def _collect(runtime: AgentRuntime, *, request=None, **kwargs):
    return [
        update
        async for update in runtime.run(request or _request(), **kwargs)
    ]


def _result(updates) -> AgentRuntimeResult:
    result = updates[-1]
    assert isinstance(result, AgentRuntimeResult)
    return result


@pytest.mark.asyncio
async def test_runtime_streams_provider_neutral_deltas_and_completes_without_tools():
    model = ScriptedModelGateway([[
        ModelStreamChunk(thinking_delta="brief"),
        ModelStreamChunk(content_delta="answer", finish_reason=ModelFinishReason.STOP),
    ]])
    observer = RecordingObserver()
    runtime = AgentRuntime(model_gateway=model, observer=observer)

    updates = await _collect(runtime)

    assert isinstance(model, ModelGateway)
    assert [update.type for update in updates if isinstance(update, AgentEvent)] == [
        CoreEventType.MODEL_THINKING_DELTA,
        CoreEventType.MODEL_DELTA,
    ]
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == "answer"
    assert model.invocations[0].tool_choice is ToolChoiceMode.NONE
    assert observer.model_delta_count == 1
    assert observer.traces[-1].outcome == "stop"


@pytest.mark.asyncio
async def test_runtime_preserves_typed_continuation_scope_and_state_across_tool_rounds():
    model = ScriptedModelGateway([
        _tool_call("call-a", "readA", content_delta="hidden-a"),
        _tool_call("call-b", "readB", content_delta="hidden-b"),
        _answer("final"),
    ])
    progress = AgentEvent(
        type=CoreEventType.TOOL_CALL_COMPLETED,
        payload={"index": 0},
    )
    tools = ScriptedToolGateway(
        [_batch("call-a", "readA"), _batch("call-b", "readB")],
        progress=(progress,),
    )
    observer = RecordingObserver([{"readA"}, {"readB"}, set()])
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    )
    state = ExecutionState(domain={"scope": "same"})

    updates = await _collect(
        runtime,
        tools=(_schema("readA"), _schema("readB")),
        execution_state=state,
        run_id="run-1",
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    assert isinstance(tools, ToolExecutionGateway)
    assert [tuple(schema.name for schema in invocation.tools) for invocation in model.invocations] == [
        ("readA",), ("readB",), (),
    ]
    assert [invocation.tool_choice for invocation in model.invocations] == [
        ToolChoiceMode.REQUIRED,
        ToolChoiceMode.REQUIRED,
        ToolChoiceMode.NONE,
    ]
    assert all(request.state is state for request in tools.requests)
    assert tools.requests[0].allowed_tool_names == frozenset({"readA"})
    assert tools.requests[1].allowed_tool_names == frozenset({"readB"})
    second_round_tail = model.message_rounds[1][-2:]
    assert second_round_tail[0].tool_calls[0].name == "readA"
    assert second_round_tail[0].content == ""
    assert second_round_tail[1].tool_call_id == "call-a"
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_DELTA
        and update.payload["delta"].startswith("hidden")
        for update in updates
    )
    assert observer.started_tools == [("readA",), ("readB",)]
    assert observer.completed_tool_rounds == 2
    assert _result(updates).final_response == "final"


@pytest.mark.asyncio
async def test_runtime_required_tool_choice_falls_back_once_but_stays_fail_closed():
    model = ScriptedModelGateway([
        UnsupportedModelFeatureError("unsupported"),
        _tool_call("call-a", "readA"),
        _answer("done"),
    ])
    tools = ScriptedToolGateway([_batch("call-a", "readA")])
    observer = RecordingObserver([{"readA"}, set()])
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    )

    updates = await _collect(
        runtime,
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    assert [item.tool_choice for item in model.invocations[:2]] == [
        ToolChoiceMode.REQUIRED,
        ToolChoiceMode.AUTO,
    ]
    assert any(
        trace.stage == "tool_choice" and trace.outcome == "provider_fallback_auto"
        for trace in observer.traces
    )
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED


@pytest.mark.asyncio
async def test_runtime_keeps_required_tool_choice_disabled_after_provider_fallback():
    model = ScriptedModelGateway([
        UnsupportedModelFeatureError("unsupported"),
        _tool_call("call-a", "readA"),
        _tool_call("call-b", "readB"),
        _answer("done"),
    ])
    tools = ScriptedToolGateway([
        _batch("call-a", "readA"),
        _batch("call-b", "readB"),
    ])
    observer = RecordingObserver([{"readA"}, {"readB"}, set()])
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    )

    updates = await _collect(
        runtime,
        tools=(_schema("readA"), _schema("readB")),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    assert [item.tool_choice for item in model.invocations] == [
        ToolChoiceMode.REQUIRED,
        ToolChoiceMode.AUTO,
        ToolChoiceMode.AUTO,
        ToolChoiceMode.NONE,
    ]
    assert [tuple(schema.name for schema in item.tools) for item in model.invocations] == [
        ("readA",),
        ("readA",),
        ("readB",),
        (),
    ]
    assert len([
        trace
        for trace in observer.traces
        if trace.stage == "tool_choice"
        and trace.outcome == "provider_fallback_auto"
    ]) == 1
    assert [request.allowed_tool_names for request in tools.requests] == [
        frozenset({"readA"}),
        frozenset({"readB"}),
    ]
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == "done"


@pytest.mark.asyncio
async def test_runtime_stops_consuming_model_stream_after_terminal_chunk():
    resumed_after_finish = asyncio.Event()
    stream_closed = asyncio.Event()
    never_finishes = asyncio.Event()

    class FinishThenHangModelGateway(ScriptedModelGateway):
        def __init__(self):
            super().__init__([])

        async def stream(self, messages, invocation, signal=None):
            self.invocations.append(invocation)
            self.message_rounds.append(tuple(messages))

            async def _chunks():
                try:
                    yield ModelStreamChunk(
                        thinking_delta="brief",
                        content_delta="answer",
                        finish_reason=ModelFinishReason.STOP,
                    )
                    resumed_after_finish.set()
                    await never_finishes.wait()
                finally:
                    stream_closed.set()

            return ModelStream(chunks=_chunks(), model="resolved-model")

    model = FinishThenHangModelGateway()
    updates = await asyncio.wait_for(
        _collect(AgentRuntime(model_gateway=model)),
        timeout=1.0,
    )

    assert not resumed_after_finish.is_set()
    assert stream_closed.is_set()
    assert [update.type for update in updates if isinstance(update, AgentEvent)] == [
        CoreEventType.MODEL_THINKING_DELTA,
        CoreEventType.MODEL_DELTA,
    ]
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == "answer"


@pytest.mark.asyncio
async def test_runtime_fails_when_required_model_does_not_call_a_tool():
    model = ScriptedModelGateway([_answer("fake textual call")])
    tools = ScriptedToolGateway([])
    observer = RecordingObserver([{"readA"}])
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    )

    updates = await _collect(
        runtime,
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    assert tools.requests == []
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "missing_required_tool_call"
    assert not any(
        isinstance(update, AgentEvent) and update.type == CoreEventType.MODEL_DELTA
        for update in updates
    )


@pytest.mark.asyncio
async def test_runtime_rejects_the_whole_unauthorized_batch_before_gateway_execution():
    model = ScriptedModelGateway([_tool_call("call-b", "readB")])
    tools = ScriptedToolGateway([])
    observer = RecordingObserver([{"readA"}])
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    )

    updates = await _collect(
        runtime,
        tools=(_schema("readA"), _schema("readB")),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    assert tools.requests == []
    assert observer.started_tools == []
    assert CoreEventType.TOOL_RESULTS in [
        update.type for update in updates if isinstance(update, AgentEvent)
    ]
    assert _result(updates).error_code == "tool_not_authorized"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected", "second_round"),
    [
        (ToolBatchOutcome.DECLINED, RuntimeOutcome.COMPLETED, True),
        (ToolBatchOutcome.CANCELED, RuntimeOutcome.CANCELED, False),
        (ToolBatchOutcome.FAILED, RuntimeOutcome.FAILED, False),
    ],
)
async def test_runtime_preserves_declined_canceled_and_failed_tool_semantics(
    outcome,
    expected,
    second_round,
):
    rounds = [_tool_call("call-a", "readA")]
    if second_round:
        rounds.append(_answer("continued"))
    model = ScriptedModelGateway(rounds)
    tools = ScriptedToolGateway([_batch(
        "call-a",
        "readA",
        outcome=outcome,
        error=(None if outcome is ToolBatchOutcome.DECLINED else "tool_terminal"),
    )])
    observer = RecordingObserver([{"readA"}, set()])
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    )

    updates = await _collect(
        runtime,
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    assert _result(updates).outcome is expected
    assert len(model.message_rounds) == (2 if second_round else 1)
    assert observer.completed_tool_rounds == (1 if second_round else 0)


@pytest.mark.asyncio
async def test_runtime_does_not_execute_tools_on_the_last_model_round():
    model = ScriptedModelGateway([_tool_call("call-a", "readA")])
    tools = ScriptedToolGateway([])
    observer = RecordingObserver([{"readA"}])
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
        limits=RuntimeLimits(max_model_rounds=1),
    )

    updates = await _collect(
        runtime,
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    assert tools.requests == []
    assert _result(updates).error_code == "max_model_rounds"
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == CoreEventType.TOOL_CALLS_STARTED
        for update in updates
    )


@pytest.mark.asyncio
async def test_runtime_stops_before_second_model_round_when_tool_history_overflows():
    model = ScriptedModelGateway([_tool_call("call-a", "readA")])
    tools = ScriptedToolGateway([_batch(
        "call-a",
        "readA",
        content="x" * 10_000,
    )])
    observer = RecordingObserver([{"readA"}, set()])
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    )

    updates = await _collect(
        runtime,
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        round_input_tokens=100,
    )

    assert len(model.message_rounds) == 1
    assert _result(updates).error_code == "context_overflow_after_tool"


@pytest.mark.asyncio
async def test_runtime_applies_typed_budget_before_the_first_model_round():
    model = ScriptedModelGateway([])
    budget = ContextBudget(
        window_tokens=20,
        output_reserve_tokens=1,
        safety_reserve_tokens=0,
        runtime_reserve_tokens=0,
        provider_input_tokens=1,
    )

    updates = await _collect(
        AgentRuntime(model_gateway=model),
        request=_request(context_window=20),
        context_budget=budget,
    )

    assert model.invocations == []
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "context_overflow_initial"
    assert _result(updates).round_count == 0


@pytest.mark.asyncio
async def test_runtime_uses_typed_round_budget_after_tool_continuation():
    model = ScriptedModelGateway([_tool_call("call-a", "readA")])
    tools = ScriptedToolGateway([_batch(
        "call-a",
        "readA",
        content="x" * 10_000,
    )])
    schema = _schema("readA")
    budget = ContextBudget(
        window_tokens=1_000,
        output_reserve_tokens=1,
        safety_reserve_tokens=0,
        runtime_reserve_tokens=50,
        tool_schema_tokens=estimate_tool_schema_tokens((schema,)),
        provider_input_tokens=100,
    )

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
        ),
        request=_request(context_window=1_000),
        tools=(schema,),
        context_budget=budget,
        scope_tools_to_observer=False,
    )

    assert len(model.message_rounds) == 1
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "context_overflow_after_tool"


@pytest.mark.asyncio
async def test_runtime_rejects_typed_budget_window_mismatch_before_model_call():
    model = ScriptedModelGateway([])
    budget = _matching_budget(window=4_096)

    updates = await _collect(
        AgentRuntime(model_gateway=model),
        request=_request(context_window=8_192),
        context_budget=budget,
    )

    assert model.invocations == []
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "context_budget_window_mismatch"
    assert _result(updates).round_count == 0


@pytest.mark.asyncio
async def test_runtime_rejects_actual_tool_schema_cost_mismatch_before_model_call():
    model = ScriptedModelGateway([])
    schema = _schema("readA")
    budget = _matching_budget(window=4_096, tools=())

    updates = await _collect(
        AgentRuntime(model_gateway=model),
        request=_request(context_window=4_096),
        context_budget=budget,
        tools=(schema,),
        scope_tools_to_observer=False,
    )

    assert model.invocations == []
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "context_budget_tool_schema_mismatch"


@pytest.mark.asyncio
async def test_runtime_forces_typed_output_reserve_on_normal_model_invocation():
    schema = _schema("readA")
    budget = _matching_budget(window=4_096, tools=(schema,), output=321)
    model = ScriptedModelGateway([_answer("done")])

    updates = await _collect(
        AgentRuntime(model_gateway=model),
        request=_request(context_window=4_096),
        context_budget=budget,
        tools=(schema,),
        scope_tools_to_observer=False,
    )

    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert model.invocations[0].max_output_tokens == 321
    assert model.invocations[0].tools == (schema,)


@pytest.mark.asyncio
async def test_runtime_preserves_typed_output_reserve_on_tool_choice_fallback():
    schema = _schema("readA")
    budget = _matching_budget(window=4_096, tools=(schema,), output=257)
    model = ScriptedModelGateway([
        UnsupportedModelFeatureError("unsupported"),
        _tool_call("call-a", "readA"),
        _answer("done"),
    ])
    tools = ScriptedToolGateway([_batch("call-a", "readA")])

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
        ),
        request=_request(context_window=4_096),
        context_budget=budget,
        tools=(schema,),
        scope_tools_to_observer=False,
        force_tool_choice=True,
    )

    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert [item.tool_choice for item in model.invocations] == [
        ToolChoiceMode.REQUIRED,
        ToolChoiceMode.AUTO,
        ToolChoiceMode.AUTO,
    ]
    assert [item.max_output_tokens for item in model.invocations] == [257, 257, 257]


@pytest.mark.asyncio
async def test_runtime_tool_scope_is_fail_closed_by_default_without_observer():
    model = ScriptedModelGateway([_answer("done")])

    updates = await _collect(
        AgentRuntime(model_gateway=model),
        tools=(_schema("readA"),),
    )

    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert model.invocations[0].tools == ()
    assert model.invocations[0].tool_choice is ToolChoiceMode.NONE


def test_context_claim_allocation_uses_exact_integer_remainders_for_huge_values():
    huge = 10**400
    kwargs = {
        "window_tokens": 100,
        "output_reserve_tokens": 1,
        "safety_reserve_tokens": 10,
        "runtime_reserve_tokens": 10,
        "minimum_message_tokens": 72,
        "claims": (
            ContextBudgetClaim(name="first", desired_tokens=2 * huge),
            ContextBudgetClaim(name="second", desired_tokens=huge),
            ContextBudgetClaim(name="third", desired_tokens=huge),
        ),
    }

    first = allocate_context_budget(**kwargs)
    second = allocate_context_budget(**kwargs)

    assert first.context_pool_tokens == 7
    assert first.context_allocations == {"first": 3, "second": 2, "third": 2}
    assert first.context_allocations == second.context_allocations


@pytest.mark.asyncio
async def test_runtime_executes_unplanned_agent_tools_in_auto_mode():
    model = ScriptedModelGateway([
        _tool_call("call-a", "readA"),
        _answer("auto finished"),
    ])
    tools = ScriptedToolGateway([_batch("call-a", "readA")])
    observer = RecordingObserver([set()])
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    )

    updates = await _collect(
        runtime,
        tools=(_schema("readA"),),
        scope_tools_to_observer=False,
    )

    assert [item.tool_choice for item in model.invocations] == [
        ToolChoiceMode.AUTO,
        ToolChoiceMode.AUTO,
    ]
    assert len(tools.requests) == 1
    assert observer.started_tools == []
    assert observer.completed_tool_rounds == 0
    assert _result(updates).final_response == "auto finished"


@pytest.mark.asyncio
async def test_runtime_displays_external_tool_calls_without_executing_them():
    model = ScriptedModelGateway([_tool_call("call-a", "externalTool")])
    gateway = ScriptedToolGateway([])
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=gateway,
    )

    updates = await _collect(
        runtime,
        tools=(_schema("externalTool"),),
        scope_tools_to_observer=False,
        tools_executable=False,
    )

    assert gateway.requests == []
    assert any(
        isinstance(update, AgentEvent)
        and update.type == CoreEventType.TOOL_CALLS_STARTED
        for update in updates
    )
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED


@pytest.mark.asyncio
async def test_runtime_cancellation_and_model_errors_have_safe_structured_outcomes():
    canceled = asyncio.Event()
    canceled.set()
    untouched_model = ScriptedModelGateway([])
    canceled_updates = await _collect(
        AgentRuntime(model_gateway=untouched_model),
        signal=canceled,
    )
    assert _result(canceled_updates).outcome is RuntimeOutcome.CANCELED
    assert untouched_model.invocations == []

    failing_model = ScriptedModelGateway([RuntimeError("secret provider detail")])
    failed_updates = await _collect(AgentRuntime(model_gateway=failing_model))
    assert _result(failed_updates).outcome is RuntimeOutcome.FAILED
    assert _result(failed_updates).error_code == "model_gateway_error"
    assert "secret" not in str(_result(failed_updates))


@pytest.mark.asyncio
async def test_runtime_cancels_while_model_stream_is_opening():
    started = asyncio.Event()
    finalized = asyncio.Event()
    signal = asyncio.Event()
    never_finishes = asyncio.Event()

    class BlockingOpenGateway(ScriptedModelGateway):
        def __init__(self):
            super().__init__([])

        async def stream(self, messages, invocation, signal=None):
            self.invocations.append(invocation)
            self.message_rounds.append(tuple(messages))
            started.set()
            try:
                await never_finishes.wait()
                raise AssertionError("model stream open resumed after cancellation")
            finally:
                finalized.set()

    model = BlockingOpenGateway()
    consumer = asyncio.create_task(_collect(
        AgentRuntime(model_gateway=model),
        signal=signal,
    ))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    signal.set()
    updates = await asyncio.wait_for(consumer, timeout=1.0)

    assert finalized.is_set()
    assert len(model.invocations) == 1
    assert _result(updates).outcome is RuntimeOutcome.CANCELED
    assert _result(updates).error_code == "request_canceled"


@pytest.mark.asyncio
async def test_runtime_cancels_while_waiting_for_the_next_model_chunk():
    waiting_for_next = asyncio.Event()
    finalized = asyncio.Event()
    signal = asyncio.Event()
    never_finishes = asyncio.Event()

    class BlockingChunkGateway(ScriptedModelGateway):
        def __init__(self):
            super().__init__([])

        async def stream(self, messages, invocation, signal=None):
            self.invocations.append(invocation)
            self.message_rounds.append(tuple(messages))

            async def _chunks():
                try:
                    yield ModelStreamChunk(content_delta="partial")
                    waiting_for_next.set()
                    await never_finishes.wait()
                    yield ModelStreamChunk(
                        content_delta="late",
                        finish_reason=ModelFinishReason.STOP,
                    )
                finally:
                    finalized.set()

            return ModelStream(chunks=_chunks(), model="resolved-model")

    model = BlockingChunkGateway()
    consumer = asyncio.create_task(_collect(
        AgentRuntime(model_gateway=model),
        signal=signal,
    ))
    await asyncio.wait_for(waiting_for_next.wait(), timeout=1.0)
    signal.set()
    updates = await asyncio.wait_for(consumer, timeout=1.0)

    assert finalized.is_set()
    assert [
        update.payload["delta"]
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_DELTA
    ] == ["partial"]
    assert _result(updates).outcome is RuntimeOutcome.CANCELED
    assert _result(updates).error_code == "request_canceled"


@pytest.mark.asyncio
async def test_runtime_cancels_active_tool_gateway_without_fake_completion():
    started = asyncio.Event()
    finalized = asyncio.Event()
    signal = asyncio.Event()
    never_finishes = asyncio.Event()

    class BlockingToolGateway(ScriptedToolGateway):
        def __init__(self):
            super().__init__([])

        async def execute_batch(self, request, event_sink, signal=None):
            self.requests.append(request)
            started.set()
            try:
                await never_finishes.wait()
                raise AssertionError("tool execution resumed after cancellation")
            finally:
                finalized.set()

    model = ScriptedModelGateway([
        _tool_call("call-a", "readA"),
        _answer("must not run"),
    ])
    tools = BlockingToolGateway()
    consumer = asyncio.create_task(_collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
        ),
        tools=(_schema("readA"),),
        scope_tools_to_observer=False,
        signal=signal,
    ))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    signal.set()
    updates = await asyncio.wait_for(consumer, timeout=1.0)

    event_types = [
        update.type for update in updates if isinstance(update, AgentEvent)
    ]
    assert finalized.is_set()
    assert len(model.message_rounds) == 1
    assert len(tools.requests) == 1
    assert CoreEventType.TOOL_CALLS_STARTED in event_types
    assert CoreEventType.TOOL_RESULTS not in event_types
    assert CoreEventType.TOOL_ROUND_COMPLETED not in event_types
    assert CoreEventType.TOOL_CALL_COMPLETED not in event_types
    assert _result(updates).outcome is RuntimeOutcome.CANCELED
    assert _result(updates).error_code == "request_canceled"
