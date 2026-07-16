from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

import pytest

from agent_core.cancellation import OperationCanceled
from agent_core.context_budget import (
    allocate_context_budget,
    estimate_tool_schema_tokens,
)
from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRuntimeResult,
    ApprovalStatus,
    ContextBudget,
    ContextBudgetClaim,
    DomainContext,
    ExecutionState,
    MessageRole,
    ModelCompletion,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelStream,
    ModelStreamChunk,
    ResponseConstraints,
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
from agent_core.errors import ModelGatewayError, UnsupportedModelFeatureError
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
                if isinstance(chunk, Exception):
                    raise chunk
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
        self.completed_tool_outcomes: list[ToolBatchOutcome] = []

    def current_allowed_tool_names(self) -> frozenset[str]:
        if not self.scopes:
            return frozenset()
        return self.scopes[min(self.scope_index, len(self.scopes) - 1)]

    def future_allowed_tool_names(self) -> frozenset[str]:
        if not self.scopes:
            return frozenset()
        return frozenset().union(*self.scopes[self.scope_index + 1:])

    async def record_trace(self, trace: TraceRecord) -> None:
        self.traces.append(trace)

    async def on_model_delta(self) -> None:
        self.model_delta_count += 1

    async def on_tool_calls_started(self, tool_names: tuple[str, ...]) -> None:
        self.started_tools.append(tool_names)

    async def on_tool_round_completed(
        self,
        outcome: ToolBatchOutcome = ToolBatchOutcome.COMPLETED,
    ) -> None:
        self.completed_tool_rounds += 1
        self.completed_tool_outcomes.append(ToolBatchOutcome(outcome))
        self.scope_index += 1


def _request(
    *,
    user_text: str = "work",
    tools_enabled: bool = True,
    context_window: int | None = None,
) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content=user_text),),
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
    approval_status: ApprovalStatus | None = None,
) -> ToolBatchResult:
    return ToolBatchResult(
        results=(ToolCallResult(
            tool_call_id=call_id,
            tool_name=name,
            content=content,
            error=error,
            approval_status=approval_status,
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
async def test_runtime_without_response_constraints_keeps_streaming_each_delta():
    model = ScriptedModelGateway([[
        ModelStreamChunk(content_delta="first "),
        ModelStreamChunk(
            content_delta="second",
            finish_reason=ModelFinishReason.STOP,
        ),
    ]])
    observer = RecordingObserver()

    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
    )

    assert [
        update.payload["delta"]
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_DELTA
    ] == ["first ", "second"]
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == "first second"
    assert observer.model_delta_count == 2


@pytest.mark.asyncio
async def test_valid_exact_item_constraint_buffers_and_emits_one_complete_delta():
    model = ScriptedModelGateway([[
        ModelStreamChunk(content_delta="1. first item"),
        ModelStreamChunk(
            content_delta="\n2. second item",
            finish_reason=ModelFinishReason.STOP,
        ),
    ]])
    observer = RecordingObserver()

    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
        response_constraints=ResponseConstraints(
            exact_top_level_item_count=2,
        ),
    )

    expected = "1. first item\n2. second item"
    assert [
        update.payload["delta"]
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_DELTA
    ] == [expected]
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == expected
    assert observer.model_delta_count == 1


@pytest.mark.asyncio
async def test_exact_item_constraint_withholds_invalid_answer_and_repairs_once_without_tools():
    invalid = "1. first\n2. second\n3. extra"
    repaired = "1. first\n2. second"
    model = ScriptedModelGateway([
        _answer(invalid),
        _answer(repaired),
    ])
    observer = RecordingObserver()

    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
        tools=(_schema("readA"),),
        scope_tools_to_observer=False,
        response_constraints=ResponseConstraints(
            exact_top_level_item_count=2,
        ),
    )

    assert len(model.invocations) == 2
    assert model.invocations[0].tool_choice is ToolChoiceMode.AUTO
    assert [schema.name for schema in model.invocations[0].tools] == ["readA"]
    assert model.invocations[1].tool_choice is ToolChoiceMode.NONE
    assert model.invocations[1].tools == ()
    repair_messages = model.message_rounds[1]
    assert repair_messages[-2] == AgentMessage(
        role=MessageRole.ASSISTANT,
        content=invalid,
    )
    assert repair_messages[-1].role is MessageRole.DEVELOPER
    assert "exactly 2 top-level items" in repair_messages[-1].content
    assert [
        update.payload["delta"]
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_DELTA
    ] == [repaired]
    assert invalid not in "".join(
        str(update.payload.get("delta") or "")
        for update in updates
        if isinstance(update, AgentEvent)
    )
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == repaired
    assert any(
        trace.stage == "model_output"
        and trace.outcome == "response_constraint_retry"
        for trace in observer.traces
    )


@pytest.mark.asyncio
async def test_exact_item_constraint_fails_closed_after_one_failed_repair():
    model = ScriptedModelGateway([
        _answer("1. first\n2. second\n3. extra"),
        _answer("1. still only one"),
    ])
    observer = RecordingObserver()

    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
        response_constraints=ResponseConstraints(
            exact_top_level_item_count=2,
        ),
    )

    assert len(model.invocations) == 2
    assert not [
        update
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_DELTA
    ]
    assert observer.model_delta_count == 0
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "response_constraint_violation"
    assert any(
        trace.stage == "model_output"
        and trace.outcome == "response_constraint_rejected"
        for trace in observer.traces
    )


@pytest.mark.asyncio
async def test_textual_tool_call_during_response_repair_is_rejected_without_another_retry():
    model = ScriptedModelGateway([
        _answer("1. first\n2. second\n3. extra"),
        _answer(
            "<tool_call>\n"
            "<function=readA>\n"
            "<parameter=value>ignored</parameter>\n"
            "</function>\n"
            "</tool_call>"
        ),
    ])
    observer = RecordingObserver()

    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
        tools=(_schema("readA"),),
        scope_tools_to_observer=False,
        response_constraints=ResponseConstraints(
            exact_top_level_item_count=2,
        ),
    )

    assert len(model.invocations) == 2
    assert model.invocations[1].tool_choice is ToolChoiceMode.NONE
    assert model.invocations[1].tools == ()
    assert not [
        update
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_DELTA
    ]
    assert observer.model_delta_count == 0
    assert _result(updates).outcome is RuntimeOutcome.FAILED


@pytest.mark.asyncio
async def test_structured_tool_call_during_response_repair_is_rejected():
    model = ScriptedModelGateway([
        _answer("1. first\n2. second\n3. extra"),
        _tool_call("call-a", "readA"),
    ])

    updates = await _collect(
        AgentRuntime(model_gateway=model),
        tools=(_schema("readA"),),
        scope_tools_to_observer=False,
        response_constraints=ResponseConstraints(
            exact_top_level_item_count=2,
        ),
    )

    assert len(model.invocations) == 2
    assert model.invocations[1].tool_choice is ToolChoiceMode.NONE
    assert model.invocations[1].tools == ()
    assert not any(
        isinstance(update, AgentEvent)
        and update.type in {
            CoreEventType.MODEL_DELTA,
            CoreEventType.TOOL_CALLS_STARTED,
        }
        for update in updates
    )
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "tool_call_during_response_repair"


@pytest.mark.asyncio
async def test_exact_item_constraint_ignores_indented_numbered_subitems():
    answer = (
        "1. first item\n"
        "   1. nested evidence\n"
        "   2. nested suggestion\n"
        "2. second item\n"
        "   1. another nested detail"
    )
    model = ScriptedModelGateway([_answer(answer)])

    updates = await _collect(
        AgentRuntime(model_gateway=model),
        response_constraints=ResponseConstraints(
            exact_top_level_item_count=2,
        ),
    )

    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == answer


@pytest.mark.asyncio
async def test_exact_item_constraint_rejects_an_extra_column_zero_item():
    answer = (
        "1. first item\n"
        "   1. nested detail\n"
        "2. second item\n"
        "3. extra top-level item"
    )
    model = ScriptedModelGateway([_answer(answer)])

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            limits=RuntimeLimits(max_model_rounds=1),
        ),
        response_constraints=ResponseConstraints(
            exact_top_level_item_count=2,
        ),
    )

    assert not [
        update
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_DELTA
    ]
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "response_constraint_violation"


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
async def test_runtime_retries_buffered_partial_stream_with_frozen_inputs():
    interruption = ModelGatewayError(
        "interrupted",
        code="upstream_stream_interrupted",
        retryable=True,
    )
    model = ScriptedModelGateway([
        [ModelStreamChunk(content_delta="1. discarded"), interruption],
        _answer("1. final"),
    ])
    observer = RecordingObserver()

    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
        response_constraints=ResponseConstraints(
            exact_top_level_item_count=1,
        ),
    )

    assert [
        update.payload["delta"]
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_DELTA
    ] == ["1. final"]
    assert model.message_rounds[0] == model.message_rounds[1]
    assert model.invocations[0] is model.invocations[1]
    terminal_traces = [
        trace
        for trace in observer.traces
        if trace.stage in {"stream", "model_round"}
    ]
    assert [trace.outcome for trace in terminal_traces] == [
        "interrupted_retry",
        "stop",
    ]
    assert [trace.details["attempt"] for trace in terminal_traces] == [1, 2]
    assert [trace.details["logicalRound"] for trace in terminal_traces] == [1, 1]
    assert terminal_traces[0].details["receivedChunkCount"] == 1
    assert terminal_traces[0].details["emittedDeltaCount"] == 0
    assert terminal_traces[0].details["batchExecuted"] is False
    assert _result(updates).final_response == "1. final"


@pytest.mark.asyncio
async def test_runtime_does_not_retry_after_unbuffered_partial_was_emitted():
    model = ScriptedModelGateway([[
        ModelStreamChunk(content_delta="visible partial"),
        ModelGatewayError(
            "interrupted",
            code="upstream_stream_interrupted",
            retryable=True,
        ),
    ]])
    observer = RecordingObserver()

    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
    )

    assert [
        update.payload["delta"]
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_DELTA
    ] == ["visible partial"]
    assert len(model.invocations) == 1
    trace = next(item for item in observer.traces if item.stage == "stream")
    assert trace.outcome == "interrupted"
    assert trace.details["emittedDeltaCount"] == 1
    assert trace.details["retryScheduled"] is False
    assert _result(updates).error_code == "upstream_stream_interrupted"


@pytest.mark.asyncio
async def test_runtime_discards_half_tool_delta_before_retry_and_executes_once():
    model = ScriptedModelGateway([
        [
            ModelStreamChunk(tool_call_deltas=(ToolCallDelta(
                index=0,
                id="call-a",
                type="function",
                name="readA",
                arguments_fragment="{",
            ),)),
            ModelGatewayError(
                "interrupted",
                code="upstream_stream_interrupted",
                retryable=True,
            ),
        ],
        _tool_call("call-a", "readA"),
        _answer("done"),
    ])
    tools = ScriptedToolGateway([_batch("call-a", "readA")])
    observer = RecordingObserver([{"readA"}, set()])

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
            observer=observer,
        ),
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    assert len(tools.requests) == 1
    assert len(tools.requests[0].calls) == 1
    assert observer.started_tools == [("readA",)]
    assert model.invocations[0] is model.invocations[1]
    retry_trace = next(
        trace
        for trace in observer.traces
        if trace.stage == "stream" and trace.outcome == "interrupted_retry"
    )
    assert retry_trace.details["receivedChunkCount"] == 1
    assert retry_trace.details["emittedDeltaCount"] == 0
    assert retry_trace.details["batchExecuted"] is False
    assert _result(updates).final_response == "done"


@pytest.mark.asyncio
async def test_runtime_allows_only_one_interruption_retry_per_run():
    model = ScriptedModelGateway([
        ModelGatewayError(
            "interrupted",
            code="upstream_stream_interrupted",
            retryable=True,
        ),
        ModelGatewayError(
            "interrupted",
            code="upstream_stream_interrupted",
            retryable=True,
        ),
        _answer("late"),
    ])
    observer = RecordingObserver()

    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
    )

    assert len(model.invocations) == 2
    assert [
        trace.outcome for trace in observer.traces if trace.stage == "stream"
    ] == ["interrupted_retry", "interrupted"]
    assert _result(updates).error_code == "upstream_stream_interrupted"


@pytest.mark.asyncio
async def test_runtime_never_reexecutes_completed_tool_during_repair_retry():
    interruption = ModelGatewayError(
        "interrupted",
        code="upstream_stream_interrupted",
        retryable=True,
    )
    model = ScriptedModelGateway([
        _tool_call("call-a", "writeA"),
        _answer("invalid unnumbered response"),
        [ModelStreamChunk(content_delta="1. discarded"), interruption],
        _answer("1. final"),
    ])
    tools = ScriptedToolGateway([_batch("call-a", "writeA")])
    observer = RecordingObserver([{"writeA"}, set()])

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
            observer=observer,
            limits=RuntimeLimits(max_model_rounds=4),
        ),
        tools=(_schema("writeA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        response_constraints=ResponseConstraints(
            exact_top_level_item_count=1,
        ),
    )

    assert len(tools.requests) == 1
    assert observer.started_tools == [("writeA",)]
    assert observer.completed_tool_rounds == 1
    assert model.invocations[2] is model.invocations[3]
    assert model.invocations[2].tools == ()
    assert model.message_rounds[2] == model.message_rounds[3]
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == "1. final"


@pytest.mark.asyncio
async def test_runtime_never_retries_non_retryable_stream_error():
    model = ScriptedModelGateway([
        ModelGatewayError(
            "not retryable",
            code="upstream_stream_interrupted",
            retryable=False,
        ),
        _answer("must not run"),
    ])

    updates = await _collect(AgentRuntime(model_gateway=model))

    assert len(model.invocations) == 1
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "upstream_stream_interrupted"


@pytest.mark.asyncio
async def test_runtime_does_not_retry_interruption_without_an_outer_round_slot():
    model = ScriptedModelGateway([ModelGatewayError(
        "interrupted",
        code="upstream_stream_interrupted",
        retryable=True,
    )])
    observer = RecordingObserver()

    updates = await _collect(AgentRuntime(
        model_gateway=model,
        observer=observer,
        limits=RuntimeLimits(max_model_rounds=1),
    ))

    assert len(model.invocations) == 1
    trace = next(item for item in observer.traces if item.stage == "stream")
    assert trace.outcome == "interrupted"
    assert trace.details["retryScheduled"] is False
    assert _result(updates).error_code == "upstream_stream_interrupted"


@pytest.mark.asyncio
async def test_runtime_required_fallback_consumes_outer_slot_with_same_input():
    model = ScriptedModelGateway([
        UnsupportedModelFeatureError("unsupported"),
        _answer("not a structured call"),
    ])
    observer = RecordingObserver([{"readA"}])

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            observer=observer,
            limits=RuntimeLimits(max_model_rounds=2),
        ),
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        require_tool_call=True,
    )

    assert len(model.invocations) == 2
    assert model.message_rounds[0] == model.message_rounds[1]
    first, second = model.invocations
    assert first.tool_choice is ToolChoiceMode.REQUIRED
    assert second.tool_choice is ToolChoiceMode.AUTO
    assert first.request == second.request
    assert first.tools == second.tools
    assert first.max_output_tokens == second.max_output_tokens
    terminal_traces = [
        trace for trace in observer.traces if trace.stage == "model_round"
    ]
    assert [trace.details["logicalRound"] for trace in terminal_traces] == [1, 1]
    assert [trace.details["attempt"] for trace in terminal_traces] == [1, 2]
    assert terminal_traces[0].details["retryScheduled"] is True
    assert _result(updates).error_code == "missing_required_tool_call"
    assert _result(updates).round_count == 2


@pytest.mark.asyncio
async def test_runtime_treats_eof_without_finish_as_interruption_not_completion():
    model = ScriptedModelGateway([
        [ModelStreamChunk(content_delta="1. incomplete")],
        [ModelStreamChunk(content_delta="1. still incomplete")],
    ])
    observer = RecordingObserver()

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            observer=observer,
            limits=RuntimeLimits(max_model_rounds=2),
        ),
        response_constraints=ResponseConstraints(
            exact_top_level_item_count=1,
        ),
    )

    assert not any(
        isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_DELTA
        for update in updates
    )
    assert [
        trace.outcome for trace in observer.traces if trace.stage == "stream"
    ] == ["interrupted_retry", "interrupted"]
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "upstream_stream_interrupted"


@pytest.mark.asyncio
async def test_runtime_prefers_cancellation_when_signal_is_set_at_stream_eof():
    signal = asyncio.Event()
    observer = RecordingObserver()

    class CancelAtEofGateway(ScriptedModelGateway):
        def __init__(self):
            super().__init__([])

        async def stream(self, messages, invocation, signal_arg=None):
            self.invocations.append(invocation)
            self.message_rounds.append(tuple(messages))

            async def _chunks():
                signal.set()
                if False:  # pragma: no cover - makes this an async generator
                    yield ModelStreamChunk()

            return ModelStream(chunks=_chunks(), model="resolved-model")

    updates = await _collect(
        AgentRuntime(
            model_gateway=CancelAtEofGateway(),
            observer=observer,
        ),
        signal=signal,
    )

    assert _result(updates).outcome is RuntimeOutcome.CANCELED
    assert _result(updates).error_code == "request_canceled"
    terminal_traces = [
        trace
        for trace in observer.traces
        if trace.details.get("providerAttemptTerminal") is True
    ]
    assert [
        (trace.stage, trace.outcome) for trace in terminal_traces
    ] == [("stream", "canceled")]
    assert terminal_traces[0].details["receivedChunkCount"] == 0
    assert terminal_traces[0].details["retryScheduled"] is False


@pytest.mark.asyncio
async def test_runtime_fails_when_required_model_does_not_call_a_tool():
    model = ScriptedModelGateway([
        _answer("fake textual call"),
        _answer("still not a structured call"),
    ])
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
    assert len(model.invocations) == 2
    assert model.invocations[0].tool_choice is ToolChoiceMode.REQUIRED
    assert model.invocations[1].tool_choice is ToolChoiceMode.REQUIRED
    assert any(
        message.role is MessageRole.DEVELOPER
        and "exactly one valid structured call" in message.content
        for message in model.message_rounds[1]
    )
    assert [
        trace.outcome
        for trace in observer.traces
        if trace.stage == "tool_round"
    ] == ["missing_required_call_retry", "missing_required_call"]
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "missing_required_tool_call"
    assert not any(
        isinstance(update, AgentEvent)
        and update.type in {
            CoreEventType.MODEL_DELTA,
            CoreEventType.MODEL_THINKING_DELTA,
        }
        for update in updates
    )


@pytest.mark.asyncio
async def test_runtime_keeps_logical_required_guard_after_provider_fallback():
    missing_round = [ModelStreamChunk(
        thinking_delta="private reasoning",
        content_delta="fake textual call",
        finish_reason=ModelFinishReason.STOP,
    )]
    model = ScriptedModelGateway([
        UnsupportedModelFeatureError("unsupported"),
        missing_round,
        missing_round,
    ])
    observer = RecordingObserver([{"readA"}])

    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        require_tool_call=True,
    )

    assert [item.tool_choice for item in model.invocations] == [
        ToolChoiceMode.REQUIRED,
        ToolChoiceMode.AUTO,
        ToolChoiceMode.AUTO,
    ]
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "missing_required_tool_call"
    assert not any(
        isinstance(update, AgentEvent)
        and update.type in {
            CoreEventType.MODEL_DELTA,
            CoreEventType.MODEL_THINKING_DELTA,
        }
        for update in updates
    )


@pytest.mark.asyncio
async def test_runtime_does_not_schedule_missing_call_retry_without_round_budget():
    observer = RecordingObserver([{"readA"}])
    updates = await _collect(
        AgentRuntime(
            model_gateway=ScriptedModelGateway([_answer("fake")]),
            observer=observer,
            limits=RuntimeLimits(max_model_rounds=1),
        ),
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        require_tool_call=True,
    )

    assert _result(updates).error_code == "missing_required_tool_call"
    tool_trace = next(
        trace for trace in observer.traces if trace.stage == "tool_round"
    )
    assert tool_trace.outcome == "missing_required_call"
    assert tool_trace.details["retryAttempt"] == 0
    assert tool_trace.details["roundsRemaining"] == 0


@pytest.mark.asyncio
async def test_runtime_hides_required_tool_round_thinking_from_events():
    tool_round = [ModelStreamChunk(
        thinking_delta="private reasoning",
        tool_call_deltas=(ToolCallDelta(
            index=0,
            id="call-a",
            type="function",
            name="readA",
            arguments_fragment="{}",
        ),),
        finish_reason=ModelFinishReason.TOOL_CALLS,
    )]
    model = ScriptedModelGateway([tool_round, _answer("done")])
    tools = ScriptedToolGateway([_batch("call-a", "readA")])
    observer = RecordingObserver([{"readA"}, set()])

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
            observer=observer,
        ),
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        require_tool_call=True,
    )

    assert not any(
        isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_THINKING_DELTA
        for update in updates
    )
    started = next(
        update
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.TOOL_CALLS_STARTED
    )
    assert started.payload["partial_thinking"] == ""
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED


@pytest.mark.asyncio
async def test_runtime_repairs_one_missing_required_call_before_execution():
    model = ScriptedModelGateway([
        _answer("fake textual call"),
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

    assert len(model.invocations) == 3
    assert len(tools.requests) == 1
    assert tools.requests[0].calls[0].name == "readA"
    assert [
        trace.outcome
        for trace in observer.traces
        if trace.stage == "tool_round"
    ] == ["missing_required_call_retry", "completed"]
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == "done"


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
    assert CoreEventType.TOOL_RESULTS not in [
        update.type for update in updates if isinstance(update, AgentEvent)
    ]
    assert _result(updates).error_code == "tool_not_authorized"


@pytest.mark.asyncio
async def test_runtime_retries_one_current_and_future_batch_with_zero_execution():
    mixed = [ModelStreamChunk(
        content_delta="complete assistant content",
        thinking_delta="private reasoning",
        tool_call_deltas=(
            ToolCallDelta(
                index=0,
                id="early-current",
                type="function",
                name="readA",
                arguments_fragment="{}",
            ),
            ToolCallDelta(
                index=1,
                id="early-future",
                type="function",
                name="readB",
                arguments_fragment="{}",
            ),
        ),
        finish_reason=ModelFinishReason.TOOL_CALLS,
    )]
    model = ScriptedModelGateway([
        mixed,
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

    assert [tuple(call.name for call in request.calls) for request in tools.requests] == [
        ("readA",),
        ("readB",),
    ]
    assert observer.started_tools == [("readA",), ("readB",)]
    assert observer.completed_tool_rounds == 2
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == "done"
    assert any(
        trace.stage == "tool_authorization"
        and trace.outcome == "future_step_retry"
        and trace.details["batchExecuted"] is False
        for trace in observer.traces
    )

    retry_tail = model.message_rounds[1][-4:]
    assert retry_tail[0].role.value == "assistant"
    assert retry_tail[0].content == "complete assistant content"
    assert retry_tail[0].thinking == "private reasoning"
    assert [call.id for call in retry_tail[0].tool_calls] == [
        "early-current",
        "early-future",
    ]
    assert [message.tool_call_id for message in retry_tail[1:3]] == [
        "early-current",
        "early-future",
    ]
    for message in retry_tail[1:3]:
        error = json.loads(message.content)
        assert error["batchExecuted"] is False
        assert error["retryable"] is True
        assert error["currentAllowed"] == ["readA"]
    assert retry_tail[3].role.value == "developer"
    assert "only authorized tool" in retry_tail[3].content


@pytest.mark.asyncio
async def test_runtime_rejects_a_second_future_step_batch_without_advancing():
    model = ScriptedModelGateway([
        _tool_call("early-1", "readB"),
        _tool_call("early-2", "readB"),
    ])
    tools = ScriptedToolGateway([])
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

    assert tools.requests == []
    assert observer.started_tools == []
    assert observer.completed_tool_rounds == 0
    assert _result(updates).error_code == "tool_step_out_of_order"
    assert [trace.outcome for trace in observer.traces].count("future_step_retry") == 1
    assert [trace.outcome for trace in observer.traces].count("future_step_rejected") == 1
    assert not any(
        isinstance(update, AgentEvent)
        and update.type in {
            CoreEventType.TOOL_CALLS_STARTED,
            CoreEventType.TOOL_RESULTS,
            CoreEventType.TOOL_ROUND_COMPLETED,
        }
        for update in updates
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", ["unknown", "readA"])
async def test_runtime_rejects_unknown_or_past_tools_without_retry(requested):
    model = ScriptedModelGateway([_tool_call("bad", requested)])
    tools = ScriptedToolGateway([])
    observer = RecordingObserver([{"readA"}, {"readB"}, set()])
    if requested == "readA":
        observer.scope_index = 1
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

    assert len(model.invocations) == 1
    assert tools.requests == []
    assert observer.started_tools == []
    assert observer.completed_tool_rounds == 0
    assert _result(updates).error_code == "tool_not_authorized"
    assert not any(trace.outcome == "future_step_retry" for trace in observer.traces)


@pytest.mark.asyncio
async def test_runtime_rejects_partial_raw_tool_batch_before_executing_valid_calls():
    malformed = [ModelStreamChunk(
        tool_call_deltas=(
            ToolCallDelta(
                index=0,
                id="valid",
                type="function",
                name="readA",
                arguments_fragment="{}",
            ),
            ToolCallDelta(
                index=1,
                type="function",
                name="readA",
                arguments_fragment="{}",
            ),
        ),
        finish_reason=ModelFinishReason.TOOL_CALLS,
    )]
    model = ScriptedModelGateway([malformed])
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
    assert observer.started_tools == []
    assert observer.completed_tool_rounds == 0
    assert _result(updates).error_code == "malformed_tool_call_batch"
    assert any(
        trace.stage == "tool_authorization"
        and trace.outcome == "malformed_batch"
        and trace.details["callCount"] == 2
        for trace in observer.traces
    )
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == CoreEventType.TOOL_CALLS_STARTED
        for update in updates
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_round",
    [
        [
            ModelStreamChunk(tool_call_deltas=(ToolCallDelta(
                index=0,
                id="first-id",
                type="function",
                name="readA",
                arguments_fragment="{",
            ),)),
            ModelStreamChunk(
                tool_call_deltas=(ToolCallDelta(
                    index=0,
                    id="conflicting-id",
                    type="function",
                    name="readA",
                    arguments_fragment="}",
                ),),
                finish_reason=ModelFinishReason.TOOL_CALLS,
            ),
        ],
        [ModelStreamChunk(
            tool_call_deltas=(
                ToolCallDelta(
                    index=0,
                    id="duplicate-id",
                    type="function",
                    name="readA",
                    arguments_fragment="{}",
                ),
                ToolCallDelta(
                    index=1,
                    id="duplicate-id",
                    type="function",
                    name="readA",
                    arguments_fragment="{}",
                ),
            ),
            finish_reason=ModelFinishReason.TOOL_CALLS,
        )],
    ],
    ids=["conflicting-id", "duplicate-id"],
)
async def test_runtime_rejects_conflicting_or_duplicate_call_ids(malformed_round):
    model = ScriptedModelGateway([malformed_round])
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
    assert observer.started_tools == []
    assert observer.completed_tool_rounds == 0
    assert _result(updates).error_code == "malformed_tool_call_batch"


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
        approval_status=(
            ApprovalStatus.REJECTED
            if outcome is ToolBatchOutcome.DECLINED
            else None
        ),
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
    assert observer.completed_tool_outcomes == ([outcome] if second_round else [])
    if outcome is ToolBatchOutcome.DECLINED:
        assert any(
            trace.stage == "tool_round"
            and trace.outcome == "declined"
            and trace.details["approvalStatuses"] == ["rejected"]
            for trace in observer.traces
        )
        assert _result(updates).final_response == (
            "You rejected the approval. The operation was not executed, and "
            "the related data remains unchanged."
        )
        assert [
            str(update.payload["delta"])
            for update in updates
            if isinstance(update, AgentEvent)
            and update.type == CoreEventType.MODEL_DELTA
        ] == [
            "You rejected the approval. The operation was not executed, and "
            "the related data remains unchanged."
        ]


@pytest.mark.asyncio
async def test_runtime_suppresses_textual_tool_call_after_decline_and_retries_plain_text():
    textual_call = [
        ModelStreamChunk(content_delta="<tool_"),
        ModelStreamChunk(
            content_delta=(
                "call>\n<function=deleteCharacter>\n"
                "<parameter=characterId>1</parameter>\n</function>\n"
            ),
        ),
        ModelStreamChunk(
            content_delta="</tool_call>",
            finish_reason=ModelFinishReason.STOP,
        ),
    ]
    model = ScriptedModelGateway([
        _tool_call("call-delete", "deleteCharacter"),
        textual_call,
        [
            ModelStreamChunk(
                thinking_delta=(
                    "正在删除并重试 <tool_call><function=deleteCharacter>"
                ),
            ),
            ModelStreamChunk(
                content_delta=(
                    "已找到人物，正在删除。删除失败，请检查权限或联系管理员。"
                ),
                finish_reason=ModelFinishReason.STOP,
            ),
        ],
    ])
    tools = ScriptedToolGateway([_batch(
        "call-delete",
        "deleteCharacter",
        outcome=ToolBatchOutcome.DECLINED,
        content=(
            '{"success":false,"errorCode":"approval_rejected",'
            '"error":"The operation was not approved."}'
        ),
        error="approval_rejected",
    )])
    observer = RecordingObserver([{"deleteCharacter"}, set()])
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    )

    updates = await _collect(
        runtime,
        request=_request(user_text="删除人物"),
        tools=(_schema("deleteCharacter"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    deltas = [
        str(update.payload["delta"])
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_DELTA
    ]
    assert deltas == ["您已拒绝审批；操作未执行，相关数据仍保留。"]
    assert "正在删除" not in "".join(deltas)
    assert "删除失败" not in "".join(deltas)
    assert "权限" not in "".join(deltas)
    assert "<tool_call>" not in "".join(deltas)
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_THINKING_DELTA
        for update in updates
    )
    assert len(tools.requests) == 1
    assert len(model.message_rounds) == 3
    assert model.invocations[1].tools == model.invocations[2].tools == ()
    assert model.invocations[1].tool_choice is ToolChoiceMode.NONE
    assert model.invocations[2].tool_choice is ToolChoiceMode.NONE
    assert model.message_rounds[1][-1].role is MessageRole.DEVELOPER
    assert "user rejected" in str(model.message_rounds[1][-1].content)
    assert model.message_rounds[2][-1].role is MessageRole.DEVELOPER
    assert "not shown to the user" in str(model.message_rounds[2][-1].content)
    assert observer.model_delta_count == 1
    assert any(
        trace.stage == "model_output"
        and trace.outcome == "textual_tool_call_retry"
        for trace in observer.traces
    )
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == (
        "您已拒绝审批；操作未执行，相关数据仍保留。"
    )


@pytest.mark.asyncio
async def test_runtime_uses_host_decline_response_when_model_content_is_empty():
    model = ScriptedModelGateway([
        _tool_call("call-delete", "deleteCharacter"),
        _answer(""),
    ])
    tools = ScriptedToolGateway([_batch(
        "call-delete",
        "deleteCharacter",
        outcome=ToolBatchOutcome.DECLINED,
        content=(
            '{"success":false,"errorCode":"approval_rejected",'
            '"error":"The operation was not approved."}'
        ),
        error="approval_rejected",
    )])
    observer = RecordingObserver([{"deleteCharacter"}, set()])
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    )

    updates = await _collect(
        runtime,
        request=_request(user_text="删除人物"),
        tools=(_schema("deleteCharacter"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    deltas = [
        str(update.payload["delta"])
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_DELTA
    ]
    assert deltas == ["您已拒绝审批；操作未执行，相关数据仍保留。"]
    assert _result(updates).final_response == deltas[0]
    assert len(tools.requests) == 1
    assert model.invocations[1].tools == ()
    assert model.invocations[1].tool_choice is ToolChoiceMode.NONE


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe_text", [
    "<tool_call><function=deleteCharacter></function></tool_call>",
    "即将执行：\n<tool_call><function=deleteCharacter></function></tool_call>",
    "```xml\n<tool_call><function=deleteCharacter></function></tool_call>\n```",
    "<tool_call><function=deleteCharacter>",
    "<tool_",
    "<function=deleteCharacter><parameter=characterId>1",
])
async def test_runtime_fails_closed_when_textual_tool_call_repeats_after_decline(
    unsafe_text,
):
    textual_call = _answer(unsafe_text)
    model = ScriptedModelGateway([
        _tool_call("call-delete", "deleteCharacter"),
        textual_call,
        textual_call,
    ])
    tools = ScriptedToolGateway([_batch(
        "call-delete",
        "deleteCharacter",
        outcome=ToolBatchOutcome.DECLINED,
        content=(
            '{"success":false,"errorCode":"approval_rejected",'
            '"error":"The operation was not approved."}'
        ),
        error="approval_rejected",
    )])
    observer = RecordingObserver([{"deleteCharacter"}, set()])
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    )

    updates = await _collect(
        runtime,
        tools=(_schema("deleteCharacter"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    assert not any(
        isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_DELTA
        for update in updates
    )
    assert len(tools.requests) == 1
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert (
        _result(updates).error_code
        == "unstructured_tool_call_after_rejection"
    )


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
    observer = RecordingObserver()
    consumer = asyncio.create_task(_collect(
        AgentRuntime(model_gateway=model, observer=observer),
        signal=signal,
    ))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    signal.set()
    updates = await asyncio.wait_for(consumer, timeout=1.0)

    assert finalized.is_set()
    assert len(model.invocations) == 1
    assert _result(updates).outcome is RuntimeOutcome.CANCELED
    assert _result(updates).error_code == "request_canceled"
    terminal_traces = [
        trace
        for trace in observer.traces
        if trace.details.get("providerAttemptTerminal") is True
    ]
    assert [
        (trace.stage, trace.outcome) for trace in terminal_traces
    ] == [("model_round", "canceled")]
    assert terminal_traces[0].details["receivedChunkCount"] == 0
    assert terminal_traces[0].details["retryScheduled"] is False


@pytest.mark.asyncio
async def test_runtime_records_terminal_trace_for_opening_operation_canceled():
    model = ScriptedModelGateway([
        OperationCanceled("provider opening was canceled"),
    ])
    observer = RecordingObserver()

    updates = await _collect(AgentRuntime(
        model_gateway=model,
        observer=observer,
    ))

    assert len(model.invocations) == 1
    assert _result(updates).outcome is RuntimeOutcome.CANCELED
    assert _result(updates).error_code == "request_canceled"
    terminal_traces = [
        trace
        for trace in observer.traces
        if trace.details.get("providerAttemptTerminal") is True
    ]
    assert [
        (trace.stage, trace.outcome) for trace in terminal_traces
    ] == [("model_round", "canceled")]
    assert terminal_traces[0].details["attempt"] == 1
    assert terminal_traces[0].details["logicalRound"] == 1


@pytest.mark.asyncio
async def test_runtime_prefers_same_tick_opening_cancel_over_retryable_error():
    signal = asyncio.Event()

    class CancelThenFailOpenGateway(ScriptedModelGateway):
        def __init__(self):
            super().__init__([])

        async def stream(self, messages, invocation, signal_arg=None):
            self.invocations.append(invocation)
            self.message_rounds.append(tuple(messages))
            signal.set()
            raise ModelGatewayError(
                "opening connection failed",
                code="upstream_stream_interrupted",
                retryable=True,
            )

    model = CancelThenFailOpenGateway()
    observer = RecordingObserver()

    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
        signal=signal,
    )

    assert len(model.invocations) == 1
    assert _result(updates).outcome is RuntimeOutcome.CANCELED
    assert _result(updates).error_code == "request_canceled"
    terminal_traces = [
        trace
        for trace in observer.traces
        if trace.details.get("providerAttemptTerminal") is True
    ]
    assert [
        (trace.stage, trace.outcome) for trace in terminal_traces
    ] == [("model_round", "canceled")]
    assert terminal_traces[0].details["retryScheduled"] is False


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
    observer = RecordingObserver()
    consumer = asyncio.create_task(_collect(
        AgentRuntime(model_gateway=model, observer=observer),
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
    terminal_traces = [
        trace
        for trace in observer.traces
        if trace.details.get("providerAttemptTerminal") is True
    ]
    assert [
        (trace.stage, trace.outcome) for trace in terminal_traces
    ] == [("stream", "canceled")]
    assert terminal_traces[0].details["receivedChunkCount"] == 1
    assert terminal_traces[0].details["emittedDeltaCount"] == 1
    assert terminal_traces[0].details["retryScheduled"] is False


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
