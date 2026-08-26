from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import replace

import pytest

from purra.cancellation import OperationCanceled
from purra.context_budget import (
    allocate_context_budget,
    estimate_tool_schema_tokens,
)
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRuntimeResult,
    ApprovalStatus,
    ContextBudget,
    ContextBudgetClaim,
    DomainContext,
    ExecutionState,
    ExecutionTransition,
    MessageRole,
    ModelCompletion,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelStream,
    ModelStreamChunk,
    ModelTokenUsage,
    ReasoningMode,
    ResponseConstraints,
    RuntimeLimits,
    RuntimeOutcome,
    StepExecutor,
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolBatchResult,
    ToolCallDelta,
    ToolCallResult,
    ToolChoiceMode,
    ToolEffectState,
    ToolHandlerResult,
    ToolPlanningDisposition,
    ToolPolicy,
    ToolSchema,
    TraceRecord,
)
from purra.errors import ModelGatewayError, UnsupportedModelFeatureError
from purra.events import AgentEvent, CoreEventType
from purra.host_planned_tool_gateway import HostPlannedToolGateway
from purra.model_protocol import (
    InvocationOutputLimit,
    InvocationOutputLimitSource,
    generic_capability_snapshot,
)
from purra.ports import (
    ModelGateway,
    RuntimeObserver,
    ToolExecutionGateway,
    ToolRegistration,
)
from purra.runtime import AgentRuntime
from purra.tools import InMemoryToolCatalog
from purra.tools.executor import CoreToolExecutor


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

    def current_execution_transition(self) -> ExecutionTransition:
        return ExecutionTransition(
            step_id=f"test-step-{self.scope_index}",
            executor=StepExecutor.TOOL if self.scopes else StepExecutor.MODEL,
            allowed_tool_names=self.current_allowed_tool_names(),
            future_tool_names=self.future_allowed_tool_names(),
        )

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


class RecordingOutputObserver:
    def __init__(self):
        self.commentary_streams: list[str] = []

    async def open_model_stream(self, receipt, spec):
        del receipt, spec

    async def accept_provider_chunk(self, output_stream_id, chunk):
        del output_stream_id, chunk

    async def finish_model_stream(self, output_stream_id, finish_reason):
        del output_stream_id, finish_reason

    async def abort_model_stream(self, output_stream_id, error_code):
        del output_stream_id, error_code

    async def publish_model_stream_commentary(self, output_stream_id):
        self.commentary_streams.append(output_stream_id)


class RecoveryPlanningHook:
    def __init__(self, observer: RecordingObserver):
        self.observer = observer
        self.calls = []

    async def replan_after_tool(
        self,
        messages,
        *,
        round_number,
        remaining_model_rounds,
        outcome,
        signal=None,
    ):
        del signal
        self.calls.append({
            "messages": tuple(messages),
            "round": round_number,
            "remaining": remaining_model_rounds,
            "outcome": outcome,
        })
        self.observer.scope_index += 1
        return AgentMessage(
            role=MessageRole.DEVELOPER,
            content="The failed tool step was replanned; answer from available evidence.",
        )


class ProgressPlanningHook:
    def __init__(self):
        self.calls = []

    async def replan_after_tool(
        self,
        messages,
        *,
        round_number,
        remaining_model_rounds,
        outcome,
        signal=None,
    ):
        del signal
        self.calls.append({
            "messages": tuple(messages),
            "round": round_number,
            "remaining": remaining_model_rounds,
            "outcome": outcome,
        })
        return AgentMessage(
            role=MessageRole.DEVELOPER,
            content="Continue from the latest bounded tool progress.",
        )


def _request(
    *,
    user_text: str = "work",
    tools_enabled: bool = True,
    context_window: int | None = None,
) -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content=user_text),),
        model=ModelRequest(
            provider="openai",
            model="model",
            capability_snapshot=replace(
                generic_capability_snapshot(),
                profile_id="test:model",
                max_output_tokens=32_000,
            ),
        ),
        domain_context=DomainContext(namespace="test"),
        context_window=context_window,
        tools_enabled=tools_enabled,
    )


def _schema(
    name: str,
    *,
    display_names: dict[str, str] | None = None,
) -> ToolSchema:
    return ToolSchema(
        name=name,
        description=f"Tool {name}",
        parameters={"type": "object", "properties": {}},
        display_names=display_names or {},
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


def _output_limit(max_tokens: int = 4_000) -> InvocationOutputLimit:
    return InvocationOutputLimit(
        max_tokens=max_tokens,
        source=InvocationOutputLimitSource.USER_OVERRIDE,
        profile_max_tokens=32_000,
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
    reasoning_delta: str = "",
) -> list[ModelStreamChunk]:
    return [ModelStreamChunk(
        content_delta=content_delta,
        reasoning_delta=reasoning_delta,
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
    effect_state: ToolEffectState = ToolEffectState.NOT_STARTED,
    planning_disposition: ToolPlanningDisposition = (
        ToolPlanningDisposition.KEEP_PLAN
    ),
) -> ToolBatchResult:
    return ToolBatchResult(
        results=(ToolCallResult(
            tool_call_id=call_id,
            tool_name=name,
            content=content,
            error=error,
            approval_status=approval_status,
            planning_disposition=planning_disposition,
        ),),
        outcome=outcome,
        error=error,
        effect_state=effect_state,
    )


@pytest.mark.asyncio
async def test_host_planned_tool_executes_without_upstream_model_round():
    class _HostToolGateway:
        def __init__(self):
            self.requests = []

        async def execute_batch(self, request, event_sink, signal=None):
            del event_sink, signal
            self.requests.append(request)
            call = request.calls[0]
            return _batch(call.id, call.name)

    upstream = ScriptedModelGateway([
        _answer("Finalized from the persisted artifact."),
    ])
    tools = _HostToolGateway()
    observer = RecordingObserver([{"finalizeA"}, set()])

    updates = await _collect(
        AgentRuntime(
            model_gateway=HostPlannedToolGateway(
                upstream,
                {"finalizeA": {}},
            ),
            tool_execution_gateway=tools,
            observer=observer,
        ),
        tools=(_schema("finalizeA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        require_tool_call=True,
    )

    result = _result(updates)
    assert result.outcome is RuntimeOutcome.COMPLETED
    assert result.final_response == "Finalized from the persisted artifact."
    assert len(upstream.invocations) == 1
    assert upstream.invocations[0].tools == ()
    assert len(tools.requests) == 1
    assert tools.requests[0].calls[0].name == "finalizeA"
    assert tools.requests[0].calls[0].arguments_json == "{}"
    event_types = [
        update.type for update in updates if isinstance(update, AgentEvent)
    ]
    assert CoreEventType.HOST_PLANNED_TOOL_DISPATCHED in event_types
    assert sum(
        event_type == CoreEventType.MODEL_CALL_RECORDED
        for event_type in event_types
    ) == 1
    assert any(
        trace.stage == "tool_dispatch"
        and trace.outcome == "host_planned_call"
        for trace in observer.traces
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
async def test_runtime_does_not_publish_provider_text_and_completes_without_tools():
    model = ScriptedModelGateway([[
        ModelStreamChunk(reasoning_delta="brief"),
        ModelStreamChunk(content_delta="answer", finish_reason=ModelFinishReason.STOP),
    ]])
    observer = RecordingObserver()
    runtime = AgentRuntime(model_gateway=model, observer=observer)

    updates = await _collect(runtime)

    assert isinstance(model, ModelGateway)
    assert [update.type for update in updates if isinstance(update, AgentEvent)] == [
        CoreEventType.MODEL_CALL_RECORDED,
    ]
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == "answer"
    assert model.invocations[0].tool_choice is ToolChoiceMode.NONE
    assert observer.model_delta_count == 1
    assert observer.traces[-1].outcome == "stop"


@pytest.mark.asyncio
async def test_runtime_retries_reasoning_only_round_and_returns_visible_answer():
    reasoning_only = [
        ModelStreamChunk(reasoning_delta="I still need to answer."),
        ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
    ]
    model = ScriptedModelGateway([
        _tool_call("call-a", "readA"),
        reasoning_only,
        _answer("给用户的完整答复"),
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

    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == "给用户的完整答复"
    assert len(model.invocations) == 3
    assert "without any official response content" in str(
        model.message_rounds[2][-1].content
    )
    assert any(
        trace.stage == "model_output"
        and trace.outcome == "empty_response_retry"
        for trace in observer.traces
    )


@pytest.mark.asyncio
async def test_runtime_rejects_repeated_reasoning_only_responses():
    reasoning_only = [
        ModelStreamChunk(reasoning_delta="reasoning"),
        ModelStreamChunk(finish_reason=ModelFinishReason.STOP),
    ]
    model = ScriptedModelGateway([
        reasoning_only,
        reasoning_only,
        reasoning_only,
    ])
    runtime = AgentRuntime(
        model_gateway=model,
        limits=RuntimeLimits(max_model_rounds=4),
    )

    updates = await _collect(runtime)

    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "empty_model_response"
    assert len(model.invocations) == 3


@pytest.mark.asyncio
async def test_runtime_does_not_replace_provider_deferred_response():
    deferred = (
        "好的，让我先查看一下当前章节中场景部分的具体内容，"
        "然后给你提供针对性的优化方案。"
    )
    answer = "可以从声场、光线和人物动线三个层次深化场景氛围。"
    model = ScriptedModelGateway([
        _answer(deferred),
        _answer(answer),
    ])
    observer = RecordingObserver()

    updates = await _collect(AgentRuntime(
        model_gateway=model,
        observer=observer,
    ))

    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in updates
    )
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == deferred
    assert len(model.message_rounds) == 1
    assert not any(
        trace.stage == "model_output"
        and trace.outcome == "deferred_action_retry"
        for trace in observer.traces
    )


@pytest.mark.asyncio
async def test_runtime_accepts_provider_deferred_response_without_host_retry():
    deferred = "让我先读取当前章节，然后为您提供完整的分析方案。"
    model = ScriptedModelGateway([
        _answer(deferred),
        _answer(deferred),
    ])

    updates = await _collect(AgentRuntime(model_gateway=model))

    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in updates
    )
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == deferred
    assert len(model.invocations) == 1


@pytest.mark.asyncio
async def test_runtime_accepts_short_explanation_that_starts_with_let_me():
    answer = "让我先说明结论：这是缓存失效导致的。"
    model = ScriptedModelGateway([_answer(answer)])

    updates = await _collect(AgentRuntime(model_gateway=model))

    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == answer
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in updates
    )


@pytest.mark.asyncio
async def test_runtime_emits_first_round_provider_usage_as_context_anchor():
    model = ScriptedModelGateway([[
        ModelStreamChunk(
            content_delta="answer",
            finish_reason=ModelFinishReason.STOP,
            usage=ModelTokenUsage(
                input_tokens=1_234,
                output_tokens=56,
                cached_input_tokens=200,
            ),
        ),
    ]])
    observer = RecordingObserver()
    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
        output_limit=_output_limit(),
    )

    usage_events = [
        update
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.CONTEXT_USAGE_RECORDED
    ]
    assert len(usage_events) == 1
    assert usage_events[0].payload["actualInputTokens"] == 1_234
    assert usage_events[0].payload["actualOutputTokens"] == 56
    assert usage_events[0].payload["cachedInputTokens"] == 200
    assert usage_events[0].payload["usageSource"] == "provider"
    assert usage_events[0].payload["requestedOutputTokens"] == 4_000
    assert usage_events[0].payload["finishReason"] == "stop"
    assert usage_events[0].payload["outputLimit"]["maxTokens"] == 4_000
    usage_trace = next(
        trace for trace in observer.traces
        if trace.stage == "model_usage"
    )
    assert usage_trace.details["actualInputTokens"] == 1_234
    assert usage_trace.details["localInputEstimate"] > 0


@pytest.mark.asyncio
async def test_runtime_does_not_replace_ui_anchor_with_transient_tool_round_usage():
    first_call = _tool_call("call-a", "readA")[0]
    model = ScriptedModelGateway([
        [ModelStreamChunk(
            tool_call_deltas=first_call.tool_call_deltas,
            finish_reason=first_call.finish_reason,
            usage=ModelTokenUsage(input_tokens=1_000, output_tokens=20),
        )],
        [ModelStreamChunk(
            content_delta="final",
            finish_reason=ModelFinishReason.STOP,
            usage=ModelTokenUsage(input_tokens=9_000, output_tokens=30),
        )],
        _answer("final"),
    ])
    tools = ScriptedToolGateway([_batch("call-a", "readA")])

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
        ),
        tools=(_schema("readA"),),
        scope_tools_to_observer=False,
    )

    usage_events = [
        update
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.CONTEXT_USAGE_RECORDED
    ]
    assert len(usage_events) == 1
    assert usage_events[0].payload["actualInputTokens"] == 1_000
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED


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

    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in updates
    )
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
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in updates
    )
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
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in updates
    )
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
        and update.type == "assistant.final_delta"
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
        and update.type == "assistant.final_delta"
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
            "assistant.final_delta",
            CoreEventType.TOOL_CALLS_STARTED,
        }
        for update in updates
    )
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "tool_call_during_response_repair"


@pytest.mark.asyncio
async def test_structured_tool_call_during_public_presentation_is_rejected():
    model = ScriptedModelGateway([
        _answer("private tool-capable answer"),
        _tool_call("remembered-call", "readA"),
    ])
    tools = ScriptedToolGateway([])
    observer = RecordingObserver()

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
            observer=observer,
        ),
        tools=(_schema("readA"),),
        scope_tools_to_observer=False,
    )

    assert len(model.invocations) == 2
    assert model.invocations[0].tools
    assert model.invocations[1].tools == ()
    assert model.invocations[1].tool_choice is ToolChoiceMode.NONE
    assert tools.requests == []
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == CoreEventType.TOOL_CALLS_STARTED
        for update in updates
    )
    assert _result(updates).error_code == (
        "tool_call_during_public_presentation"
    )
    assert any(
        trace.stage == "tool_authorization"
        and trace.outcome == "rejected_during_public_presentation"
        for trace in observer.traces
    )


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
        and update.type == "assistant.final_delta"
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
        and update.type == "assistant.final_delta"
        and update.payload["delta"].startswith("hidden")
        for update in updates
    )
    assert observer.started_tools == [("readA",), ("readB",)]
    assert observer.completed_tool_rounds == 2
    assert _result(updates).final_response == "final"


@pytest.mark.asyncio
async def test_runtime_allows_dynamic_planner_to_recover_from_tool_failure():
    model = ScriptedModelGateway([
        _tool_call("call-a", "readA"),
        _answer("Recovered without retrying the failed tool."),
        _answer("Recovered without retrying the failed tool."),
    ])
    tools = ScriptedToolGateway([_batch(
        "call-a",
        "readA",
        outcome=ToolBatchOutcome.FAILED,
        content='{"error":"temporarily unavailable"}',
        error="temporarily_unavailable",
    )])
    observer = RecordingObserver([{"readA"}, set()])
    hook = RecoveryPlanningHook(observer)

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
            observer=observer,
            limits=RuntimeLimits(
                max_model_rounds=2,
                max_progress_rounds=0,
            ),
        ),
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        planning_hook=hook,
    )

    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == (
        "Recovered without retrying the failed tool."
    )
    assert observer.completed_tool_rounds == 0
    assert len(hook.calls) == 1
    assert hook.calls[0]["outcome"] is ToolBatchOutcome.FAILED
    assert any(
        message.role is MessageRole.TOOL
        and message.tool_call_id == "call-a"
        for message in hook.calls[0]["messages"]
    )
    assert [
        update.payload["outcome"]
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.TOOL_ROUND_COMPLETED
    ] == ["failed"]


@pytest.mark.asyncio
async def test_runtime_keeps_plan_for_successful_progress_and_completion():
    model = ScriptedModelGateway([
        _tool_call("call-partial", "appendBatch"),
        _tool_call("call-complete", "appendBatch"),
        _answer("finalized after all batches"),
    ])
    tools = ScriptedToolGateway([
        _batch(
            "call-partial",
            "appendBatch",
            outcome=ToolBatchOutcome.PROGRESSED,
            content='{"remaining":68}',
        ),
        _batch(
            "call-complete",
            "appendBatch",
            outcome=ToolBatchOutcome.COMPLETED,
            content='{"remaining":0}',
        ),
    ])

    class _ProgressObserver(RecordingObserver):
        async def on_tool_round_completed(
            self,
            outcome: ToolBatchOutcome = ToolBatchOutcome.COMPLETED,
        ) -> None:
            self.completed_tool_rounds += 1
            normalized = ToolBatchOutcome(outcome)
            self.completed_tool_outcomes.append(normalized)
            if normalized is not ToolBatchOutcome.PROGRESSED:
                self.scope_index += 1

    observer = _ProgressObserver([{"appendBatch"}, set()])
    hook = ProgressPlanningHook()
    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
            observer=observer,
        ),
        tools=(_schema("appendBatch"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        planning_hook=hook,
    )

    assert [request.calls[0].name for request in tools.requests] == [
        "appendBatch",
        "appendBatch",
    ]
    assert observer.completed_tool_outcomes == [
        ToolBatchOutcome.PROGRESSED,
        ToolBatchOutcome.COMPLETED,
    ]
    assert hook.calls == []
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == "finalized after all batches"


@pytest.mark.asyncio
async def test_runtime_replans_when_successful_tool_explicitly_changes_path():
    model = ScriptedModelGateway([
        _tool_call("call-branch", "readA"),
        _answer("answered from the selected branch"),
    ])
    tools = ScriptedToolGateway([_batch(
        "call-branch",
        "readA",
        planning_disposition=ToolPlanningDisposition.REPLAN,
    )])
    observer = RecordingObserver([{"readA"}, set()])
    hook = ProgressPlanningHook()

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
            observer=observer,
        ),
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        planning_hook=hook,
    )

    assert len(hook.calls) == 1
    assert hook.calls[0]["outcome"] is ToolBatchOutcome.COMPLETED
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert any(
        trace.stage == "planning"
        and trace.outcome == "replan_requested"
        and trace.details["requestedByTools"] == ["readA"]
        for trace in observer.traces
    )


@pytest.mark.asyncio
async def test_partial_progress_unlocks_bounded_rounds_for_completion():
    model = ScriptedModelGateway([
        _tool_call("call-partial", "appendBatch"),
        _tool_call("call-complete", "appendBatch"),
        _answer("completed after bounded progress"),
    ])
    tools = ScriptedToolGateway([
        _batch(
            "call-partial",
            "appendBatch",
            outcome=ToolBatchOutcome.PROGRESSED,
            content='{"remaining":1}',
        ),
        _batch(
            "call-complete",
            "appendBatch",
            outcome=ToolBatchOutcome.COMPLETED,
            content='{"remaining":0}',
        ),
    ])

    class _ProgressObserver(RecordingObserver):
        async def on_tool_round_completed(
            self,
            outcome: ToolBatchOutcome = ToolBatchOutcome.COMPLETED,
        ) -> None:
            normalized = ToolBatchOutcome(outcome)
            self.completed_tool_rounds += 1
            self.completed_tool_outcomes.append(normalized)
            if normalized is not ToolBatchOutcome.PROGRESSED:
                self.scope_index += 1

    observer = _ProgressObserver([{"appendBatch"}, set()])
    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
            observer=observer,
            limits=RuntimeLimits(
                max_model_rounds=2,
                max_progress_rounds=1,
            ),
        ),
        tools=(_schema("appendBatch"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        planning_hook=ProgressPlanningHook(),
    )

    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).round_count == 3
    assert _result(updates).final_response == (
        "completed after bounded progress"
    )
    assert any(
        trace.stage == "runtime_round_budget"
        and trace.outcome == "progress_extended"
        and trace.details["previousRoundLimit"] == 2
        and trace.details["roundLimit"] == 3
        for trace in observer.traces
    )


@pytest.mark.asyncio
async def test_partial_progress_cannot_exceed_progress_round_cap():
    model = ScriptedModelGateway([
        _tool_call("call-partial", "appendBatch"),
        _tool_call("call-not-executed", "appendBatch"),
    ])
    tools = ScriptedToolGateway([
        _batch(
            "call-partial",
            "appendBatch",
            outcome=ToolBatchOutcome.PROGRESSED,
            content='{"remaining":1}',
        ),
    ])
    class _ProgressObserver(RecordingObserver):
        async def on_tool_round_completed(
            self,
            outcome: ToolBatchOutcome = ToolBatchOutcome.COMPLETED,
        ) -> None:
            normalized = ToolBatchOutcome(outcome)
            self.completed_tool_rounds += 1
            self.completed_tool_outcomes.append(normalized)
            if normalized is not ToolBatchOutcome.PROGRESSED:
                self.scope_index += 1

    observer = _ProgressObserver([{"appendBatch"}, set()])
    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
            observer=observer,
            limits=RuntimeLimits(
                max_model_rounds=2,
                max_progress_rounds=0,
            ),
        ),
        tools=(_schema("appendBatch"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        planning_hook=ProgressPlanningHook(),
    )

    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "max_model_rounds"
    assert len(tools.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe_recovery", [
    (
        "<tool_call>\n<function=proposeSourceAnalysis>\n"
        "<parameter=title>原作范围分析</parameter>\n"
        "<parameter=contentText>正文</parameter>\n"
        "</function>\n</tool_call>"
    ),
    json.dumps({
        "heading": "formal result",
        "content": "body",
        "payload": {"items": []},
    }, ensure_ascii=False),
])
async def test_runtime_replaces_unstructured_output_after_failed_tool_recovery(
    unsafe_recovery,
):
    model = ScriptedModelGateway([
        _tool_call("call-proposal", "proposeSourceAnalysis"),
        _answer(unsafe_recovery),
    ])
    tools = ScriptedToolGateway([_batch(
        "call-proposal",
        "proposeSourceAnalysis",
        outcome=ToolBatchOutcome.FAILED,
        content='{"success":false,"errorCode":"tool_execution_failed"}',
        error="tool_execution_failed",
    )])
    observer = RecordingObserver([{"proposeSourceAnalysis"}, set()])
    hook = RecoveryPlanningHook(observer)

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
            observer=observer,
            limits=RuntimeLimits(max_model_rounds=2),
        ),
        request=_request(user_text="分析原作并生成正式提案"),
        tools=(_schema("proposeSourceAnalysis"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        planning_hook=hook,
    )

    result = _result(updates)
    assert result.outcome is RuntimeOutcome.FAILED
    assert result.error_code == "tool_execution_failed"
    assert result.final_response == ""
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in updates
    )
    assert observer.model_delta_count == 0
    assert any(
        trace.stage == "model_output"
        and trace.outcome == "unstructured_tool_output_replaced"
        and trace.details["primaryErrorCode"] == "tool_execution_failed"
        for trace in observer.traces
    )


@pytest.mark.asyncio
async def test_runtime_preserves_primary_tool_error_when_replanning_fails():
    class FailingPlanningHook:
        async def replan_after_tool(self, *args, **kwargs):
            del args, kwargs
            raise RuntimeError("replanning adapter failed")

    model = ScriptedModelGateway([
        _tool_call("call-a", "readA"),
        _tool_call("call-b", "readA"),
    ])
    tools = ScriptedToolGateway([
        _batch(
            "call-a",
            "readA",
            outcome=ToolBatchOutcome.FAILED,
            content='{"success":false,"error":"invalid input"}',
            error="tool_input_invalid",
        ),
        _batch(
            "call-b",
            "readA",
            outcome=ToolBatchOutcome.FAILED,
            content='{"success":false,"error":"still invalid"}',
            error="tool_input_invalid",
        ),
    ])
    observer = RecordingObserver([{"readA"}])

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
            observer=observer,
        ),
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        planning_hook=FailingPlanningHook(),
    )

    result = _result(updates)
    assert result.outcome is RuntimeOutcome.FAILED
    assert result.error_code == "tool_input_invalid"
    planning_failure = next(
        trace
        for trace in observer.traces
        if trace.stage == "planning" and trace.outcome == "failed"
    )
    assert planning_failure.details["primaryErrorCode"] == "tool_input_invalid"
    assert planning_failure.details["recoveryErrorCode"] == (
        "dynamic_planning_failed"
    )


@pytest.mark.asyncio
async def test_runtime_retries_correctable_tool_input_without_replanning():
    model = ScriptedModelGateway([
        _tool_call("call-invalid", "readA"),
        _tool_call("call-corrected", "readA"),
        _answer("Recovered after correcting the tool input."),
    ])
    tools = ScriptedToolGateway([
        _batch(
            "call-invalid",
            "readA",
            outcome=ToolBatchOutcome.FAILED,
            content='{"success":false,"error":"analysis must be an object"}',
            error="tool_input_invalid",
        ),
        _batch("call-corrected", "readA"),
    ])
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

    result = _result(updates)
    assert result.outcome is RuntimeOutcome.COMPLETED
    assert result.final_response == "Recovered after correcting the tool input."
    assert len(tools.requests) == 2
    assert dict(tools.requests[0].retry_of_tool_call_ids) == {}
    assert dict(tools.requests[1].retry_of_tool_call_ids) == {
        "call-corrected": "call-invalid",
    }
    assert observer.completed_tool_rounds == 1
    assert [
        trace.outcome
        for trace in observer.traces
        if trace.stage == "tool_recovery"
    ] == ["input_retry_scheduled"]


@pytest.mark.asyncio
async def test_runtime_recovers_mid_batch_input_failure_with_trailing_calls():
    executed = []

    async def _handler(state, arguments, signal=None):
        del state, signal
        value = str(arguments["value"])
        executed.append(value)
        if value == "invalid":
            return ToolHandlerResult(
                '{"success":false,"guidance":"use scoped query"}',
                error_code="tool_input_invalid",
                effect_state=ToolEffectState.NOT_STARTED,
            )
        return ToolHandlerResult('{"success":true}')

    schema = ToolSchema(
        name="readA",
        description="read",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    )
    registration = ToolRegistration(
        schema=schema,
        handler=_handler,
        policy=ToolPolicy(mode="read", title="read"),
    )
    first_batch = [ModelStreamChunk(
        tool_call_deltas=tuple(
            ToolCallDelta(
                index=index,
                id=call_id,
                type="function",
                name="readA",
                arguments_fragment=json.dumps({"value": value}),
            )
            for index, (call_id, value) in enumerate((
                ("first", "valid"),
                ("second", "invalid"),
                ("third", "must-not-run"),
            ))
        ),
        finish_reason=ModelFinishReason.TOOL_CALLS,
    )]
    corrected = [ModelStreamChunk(
        tool_call_deltas=(ToolCallDelta(
            index=0,
            id="corrected",
            type="function",
            name="readA",
            arguments_fragment='{"value":"scoped"}',
        ),),
        finish_reason=ModelFinishReason.TOOL_CALLS,
    )]
    model = ScriptedModelGateway([
        first_batch,
        corrected,
        _answer("Recovered without corrupting the tool protocol."),
    ])
    observer = RecordingObserver([{"readA"}, set()])

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=CoreToolExecutor(
                InMemoryToolCatalog((registration,))
            ),
            observer=observer,
        ),
        tools=(schema,),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == (
        "Recovered without corrupting the tool protocol."
    )
    assert executed == ["valid", "invalid", "scoped"]
    assert any(
        trace.stage == "tool_recovery"
        and trace.outcome == "input_retry_scheduled"
        for trace in observer.traces
    )


@pytest.mark.asyncio
async def test_runtime_retries_independent_tool_input_errors_after_progress():
    model = ScriptedModelGateway([
        _tool_call("call-invalid-a", "readA"),
        _tool_call("call-corrected-a", "readA"),
        _tool_call("call-invalid-b", "readA"),
        _tool_call("call-corrected-b", "readA"),
        _answer("Recovered both independent tool-input errors."),
    ])
    tools = ScriptedToolGateway([
        _batch(
            "call-invalid-a",
            "readA",
            outcome=ToolBatchOutcome.FAILED,
            content='{"success":false,"error":"missing synopsis"}',
            error="tool_input_invalid",
        ),
        _batch("call-corrected-a", "readA"),
        _batch(
            "call-invalid-b",
            "readA",
            outcome=ToolBatchOutcome.FAILED,
            content='{"success":false,"error":"too many items"}',
            error="tool_input_invalid",
        ),
        _batch("call-corrected-b", "readA"),
    ])
    observer = RecordingObserver([{"readA"}, {"readA"}, set()])

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

    result = _result(updates)
    assert result.outcome is RuntimeOutcome.COMPLETED
    assert result.final_response == (
        "Recovered both independent tool-input errors."
    )
    decisions = [
        trace
        for trace in observer.traces
        if trace.stage == "recovery_decision"
        and trace.details["cause"] == "tool_input_invalid"
    ]
    assert [decision.outcome for decision in decisions] == [
        "allowed",
        "allowed",
    ]
    assert [decision.details["scope"] for decision in decisions] == [
        "tool-input-sequence:0",
        "tool-input-sequence:1",
    ]


@pytest.mark.asyncio
async def test_runtime_repairs_invalid_json_only_before_tool_handler_starts():
    invalid = [ModelStreamChunk(
        tool_call_deltas=(ToolCallDelta(
            index=0,
            id="call-invalid-json",
            type="function",
            name="readA",
            arguments_fragment='{"value":',
        ),),
        finish_reason=ModelFinishReason.TOOL_CALLS,
    )]
    valid = [ModelStreamChunk(
        tool_call_deltas=(ToolCallDelta(
            index=0,
            id="call-valid-json",
            type="function",
            name="readA",
            arguments_fragment='{"value":"ok"}',
        ),),
        finish_reason=ModelFinishReason.TOOL_CALLS,
    )]
    model = ScriptedModelGateway([invalid, valid, _answer("done")])
    handler_calls = []

    async def handler(state, arguments, signal=None):
        del state, signal
        handler_calls.append(dict(arguments))
        return ToolHandlerResult('{"success":true}')

    registration = ToolRegistration(
        schema=ToolSchema(
            name="readA",
            description="read",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        ),
        handler=handler,
        policy=ToolPolicy(mode="read", title="read"),
    )
    observer = RecordingObserver([{"readA"}, set()])

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=CoreToolExecutor(
                InMemoryToolCatalog((registration,))
            ),
            observer=observer,
        ),
        tools=(registration.schema,),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == "done"
    assert handler_calls == [{"value": "ok"}]
    decision = next(
        trace
        for trace in observer.traces
        if trace.stage == "recovery_decision"
        and trace.details["cause"] == "tool_input_invalid"
    )
    assert decision.outcome == "allowed"
    assert decision.details["sourceErrorCode"] == (
        "invalid_tool_arguments_json"
    )
    assert decision.details["effectState"] == "not_started"


@pytest.mark.asyncio
async def test_runtime_denies_tool_input_recovery_when_effect_state_is_unknown():
    model = ScriptedModelGateway([_tool_call("call-a", "readA")])
    tools = ScriptedToolGateway([_batch(
        "call-a",
        "readA",
        outcome=ToolBatchOutcome.FAILED,
        error="tool_input_invalid",
        effect_state=ToolEffectState.UNKNOWN,
    )])
    observer = RecordingObserver([{"readA"}])

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

    assert _result(updates).error_code == "tool_input_invalid"
    assert len(model.invocations) == 1
    decision = next(
        trace
        for trace in observer.traces
        if trace.stage == "recovery_decision"
        and trace.details["cause"] == "tool_input_invalid"
    )
    assert decision.outcome == "denied"
    assert decision.details["reasonCode"] == "side_effect_state_unknown"


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
                        reasoning_delta="brief",
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
        CoreEventType.MODEL_CALL_RECORDED,
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

    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in updates
    )
    assert model.message_rounds[0] == model.message_rounds[1]
    assert model.invocations[0] == model.invocations[1]
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

    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in updates
    )
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
    assert model.invocations[0] == model.invocations[1]
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
    assert model.invocations[2] == model.invocations[3]
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
async def test_runtime_trace_records_sanitized_model_error_chain_types():
    class APIConnectionError(Exception):
        pass

    socket_error = OSError("private socket detail")
    provider_error = APIConnectionError("private provider detail")
    provider_error.__cause__ = socket_error
    gateway_error = ModelGatewayError("public gateway error")
    gateway_error.__cause__ = provider_error
    observer = RecordingObserver()

    updates = await _collect(AgentRuntime(
        model_gateway=ScriptedModelGateway([gateway_error]),
        observer=observer,
    ))

    trace = next(
        item
        for item in observer.traces
        if item.stage == "model_round" and item.outcome == "exception"
    )
    assert trace.details["errorChainTypes"] == [
        "ModelGatewayError",
        "APIConnectionError",
        "OSError",
    ]
    serialized = repr(trace.details)
    assert "private socket detail" not in serialized
    assert "private provider detail" not in serialized
    assert _result(updates).error_code == "model_gateway_error"


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
        and update.type == "assistant.final_delta"
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
            "assistant.final_delta",
            "model.reasoning_delta",
        }
        for update in updates
    )


@pytest.mark.asyncio
async def test_runtime_replans_after_required_tool_call_retry_is_omitted():
    model = ScriptedModelGateway([
        _answer("fake textual call"),
        _answer("still not a structured call"),
        _answer("Recovered from the evidence already collected."),
        _answer("Recovered from the evidence already collected."),
    ])
    observer = RecordingObserver([{"readA"}, set()])
    hook = RecoveryPlanningHook(observer)

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            observer=observer,
        ),
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        planning_hook=hook,
    )

    result = _result(updates)
    assert result.outcome is RuntimeOutcome.COMPLETED
    assert result.final_response == (
        "Recovered from the evidence already collected."
    )
    assert len(hook.calls) == 1
    assert hook.calls[0]["outcome"] is ToolBatchOutcome.FAILED
    assert [
        trace.outcome
        for trace in observer.traces
        if trace.stage == "tool_round"
    ] == [
        "missing_required_call_retry",
        "missing_required_call_replan",
    ]
    assert model.invocations[-1].tools == ()
    assert model.invocations[-1].tool_choice is ToolChoiceMode.NONE
    assert any(
        message.role is MessageRole.DEVELOPER
        and "Treat the current tool step as failed" in message.content
        for message in hook.calls[0]["messages"]
    )


@pytest.mark.asyncio
async def test_runtime_keeps_logical_required_guard_after_provider_fallback():
    missing_round = [ModelStreamChunk(
        reasoning_delta="private reasoning",
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
        and update.type == "assistant.final_delta"
        for update in updates
    )
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "model.reasoning_delta"
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
async def test_runtime_keeps_required_tool_round_reasoning_diagnostic_only():
    tool_round = [ModelStreamChunk(
        reasoning_delta="private reasoning",
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
        tools=(_schema(
            "readA",
            display_names={
                "zh-CN": "读取资料",
                "en-US": "Read Material",
            },
        ),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        require_tool_call=True,
    )

    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "model.reasoning_delta"
        for update in updates
    )
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.commentary_delta"
        for update in updates
    )
    started = next(
        update
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.TOOL_CALLS_STARTED
    )
    assert "partial_content" not in started.payload
    assert "partial_thinking" not in started.payload
    assert started.payload["calls"][0]["name"] == "readA"
    assert started.payload["calls"][0]["display_names"] == {
        "zh-CN": "读取资料",
        "en-US": "Read Material",
    }
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED


@pytest.mark.asyncio
async def test_runtime_promotes_tool_round_content_to_public_commentary():
    tool_round = [ModelStreamChunk(
        content_delta="我先核对现有资料。",
        tool_call_deltas=(ToolCallDelta(
            index=0,
            id="call-a",
            type="function",
            name="readA",
            arguments_fragment="{}",
        ),),
        finish_reason=ModelFinishReason.TOOL_CALLS,
    )]
    output = RecordingOutputObserver()
    model = ScriptedModelGateway([tool_round, _answer("核对完成。")])
    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=ScriptedToolGateway([_batch("call-a", "readA")]),
            observer=RecordingObserver([{"readA"}, set()]),
            output_observer=output,
        ),
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        require_tool_call=True,
    )

    assert not any(
        isinstance(update, AgentEvent)
        and update.type in {
            "assistant.commentary_delta",
            "assistant.final_delta",
        }
        for update in updates
    )
    assert len(output.commentary_streams) == 1
    assert _result(updates).final_response == "核对完成。"


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
async def test_runtime_repairs_one_unauthorized_batch_with_zero_execution():
    model = ScriptedModelGateway([
        _tool_call("call-b", "readB", content_delta="不应公开。"),
        _tool_call("call-a", "readA", content_delta="现在读取资料。"),
        _answer("done"),
    ])
    tools = ScriptedToolGateway([_batch("call-a", "readA")])
    observer = RecordingObserver([{"readA"}, set()])
    output = RecordingOutputObserver()
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
        output_observer=output,
    )

    updates = await _collect(
        runtime,
        tools=(_schema("readA"), _schema("readB")),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    assert [request.calls[0].name for request in tools.requests] == ["readA"]
    assert observer.started_tools == [("readA",)]
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == "done"
    assert len(output.commentary_streams) == 1
    assert any(
        trace.stage == "tool_authorization"
        and trace.outcome == "unauthorized_tool_retry"
        and trace.details["batchExecuted"] is False
        for trace in observer.traces
    )
    retry_messages = model.message_rounds[1]
    assert retry_messages[-1].role is MessageRole.DEVELOPER
    assert "readA" in str(retry_messages[-1].content)
    assert not any(
        call.name == "readB"
        for message in retry_messages
        for call in message.tool_calls
    )


@pytest.mark.asyncio
async def test_runtime_replans_after_repeated_unauthorized_tool_selection():
    model = ScriptedModelGateway([
        _tool_call("stale-1", "readLegacy"),
        _tool_call("stale-2", "readLegacy"),
        _tool_call("call-b", "readB"),
        _answer("done"),
        _answer("done"),
    ])
    tools = ScriptedToolGateway([_batch("call-b", "readB")])
    observer = RecordingObserver([
        {"readA", "readAlternative"},
        {"readB"},
        set(),
    ])
    planning_hook = RecoveryPlanningHook(observer)
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    )

    updates = await _collect(
        runtime,
        tools=(
            _schema("readA"),
            _schema("readAlternative"),
            _schema("readB"),
        ),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        planning_hook=planning_hook,
    )

    assert [request.calls[0].name for request in tools.requests] == ["readB"]
    assert planning_hook.calls[0]["outcome"] is ToolBatchOutcome.FAILED
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == "done"
    assert any(
        trace.stage == "tool_authorization"
        and trace.outcome == "unauthorized_tool_replan"
        and trace.details["batchExecuted"] is False
        for trace in observer.traces
    )


@pytest.mark.asyncio
async def test_runtime_repairs_one_unauthorized_batch_per_distinct_tool_step():
    model = ScriptedModelGateway([
        _tool_call("legacy-a", "readLegacyA"),
        _tool_call("call-a", "readA"),
        _tool_call("legacy-b", "readLegacyB"),
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

    assert [request.calls[0].name for request in tools.requests] == [
        "readA",
        "readB",
    ]
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    retry_traces = [
        trace
        for trace in observer.traces
        if trace.stage == "tool_authorization"
        and trace.outcome == "unauthorized_tool_retry"
    ]
    assert len(retry_traces) == 2
    recovery_scopes = {
        trace.details["scope"]
        for trace in observer.traces
        if trace.stage == "recovery_decision"
        and trace.details.get("cause") == "unauthorized_tool"
    }
    assert recovery_scopes == {
        "tool-authorization:readA",
        "tool-authorization:readB",
    }


@pytest.mark.asyncio
async def test_runtime_retries_one_current_and_future_batch_with_zero_execution():
    mixed = [ModelStreamChunk(
        content_delta="complete assistant content",
        reasoning_delta="private reasoning",
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
    assert retry_tail[0].reasoning == "private reasoning"
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
    assert "schema is absent" in retry_tail[3].content


@pytest.mark.asyncio
async def test_runtime_repairs_one_future_step_jump_per_distinct_current_step():
    model = ScriptedModelGateway([
        _tool_call("early-b", "readB"),
        _tool_call("call-a", "readA"),
        _tool_call("early-c", "readC"),
        _tool_call("call-b", "readB"),
        _tool_call("call-c", "readC"),
        _answer("done"),
    ])
    tools = ScriptedToolGateway([
        _batch("call-a", "readA"),
        _batch("call-b", "readB"),
        _batch("call-c", "readC"),
    ])
    observer = RecordingObserver([
        {"readA"},
        {"readB"},
        {"readC"},
        set(),
    ])
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    )

    updates = await _collect(
        runtime,
        tools=(
            _schema("readA"),
            _schema("readB"),
            _schema("readC"),
        ),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    assert [request.calls[0].name for request in tools.requests] == [
        "readA",
        "readB",
        "readC",
    ]
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    retry_traces = [
        trace
        for trace in observer.traces
        if trace.stage == "tool_authorization"
        and trace.outcome == "future_step_retry"
    ]
    assert len(retry_traces) == 2
    recovery_scopes = {
        trace.details["scope"]
        for trace in observer.traces
        if trace.stage == "recovery_decision"
        and trace.details.get("cause") == "future_tool_step"
    }
    assert recovery_scopes == {
        "tool-authorization:readA",
        "tool-authorization:readB",
    }


@pytest.mark.asyncio
async def test_runtime_rejects_a_second_future_step_batch_without_advancing():
    model = ScriptedModelGateway([
        _tool_call("early-1", "readB"),
        _tool_call("early-2", "readB"),
    ])
    tools = ScriptedToolGateway([])
    observer = RecordingObserver([
        {"readA", "readAlternative"},
        {"readB"},
        set(),
    ])
    runtime = AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    )

    updates = await _collect(
        runtime,
        tools=(
            _schema("readA"),
            _schema("readAlternative"),
            _schema("readB"),
        ),
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
async def test_runtime_rejects_repeated_unknown_or_past_tools(requested):
    model = ScriptedModelGateway([
        _tool_call("bad-1", requested),
        _tool_call("bad-2", requested),
    ])
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

    assert len(model.invocations) == 2
    assert tools.requests == []
    assert observer.started_tools == []
    assert observer.completed_tool_rounds == 0
    assert _result(updates).error_code == "tool_not_authorized"
    assert [trace.outcome for trace in observer.traces].count(
        "unauthorized_tool_retry"
    ) == 1
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
        limits=RuntimeLimits(max_model_rounds=1),
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
async def test_runtime_repairs_malformed_tool_call_envelope_once_before_execution():
    malformed = [ModelStreamChunk(
        tool_call_deltas=(ToolCallDelta(
            index=0,
            type="function",
            name="readA",
            arguments_fragment="{}",
        ),),
        finish_reason=ModelFinishReason.TOOL_CALLS,
    )]
    model = ScriptedModelGateway([
        malformed,
        _tool_call("call-valid", "readA"),
        _answer("done"),
    ])
    tools = ScriptedToolGateway([_batch("call-valid", "readA")])
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

    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert len(tools.requests) == 1
    decision = next(
        trace
        for trace in observer.traces
        if trace.stage == "recovery_decision"
        and trace.details["cause"] == "malformed_tool_call_batch"
    )
    assert decision.outcome == "allowed"
    assert decision.details["protocolReason"] == "missing_tool_call_id"


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
        limits=RuntimeLimits(max_model_rounds=1),
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
    "truncation_kind",
    ["reasoning", "content", "json", "tool_arguments"],
)
async def test_length_never_replays_the_same_request_fingerprint(
    truncation_kind,
):
    if truncation_kind == "reasoning":
        chunks = [
            ModelStreamChunk(reasoning_delta="unfinished reasoning"),
            ModelStreamChunk(finish_reason=ModelFinishReason.LENGTH),
        ]
    elif truncation_kind == "content":
        chunks = [ModelStreamChunk(
            content_delta="unfinished response",
            finish_reason=ModelFinishReason.LENGTH,
        )]
    elif truncation_kind == "json":
        chunks = [ModelStreamChunk(
            content_delta='{"answer":"unfinished',
            finish_reason=ModelFinishReason.LENGTH,
        )]
    else:
        chunks = [ModelStreamChunk(
            tool_call_deltas=(ToolCallDelta(
                index=0,
                id="partial-call",
                type="function",
                name="readA",
                arguments_fragment='{"query":"unfinished',
            ),),
            finish_reason=ModelFinishReason.LENGTH,
        )]
    model = ScriptedModelGateway([chunks, _answer("must not run")])
    observer = RecordingObserver([{"readA"}])
    tools = (_schema("readA"),) if truncation_kind == "tool_arguments" else ()

    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
        tools=tools,
        scope_tools_to_observer=False,
        force_tool_choice=bool(tools),
    )

    expected_error = (
        "tool_call_truncated"
        if truncation_kind == "tool_arguments"
        else "model_output_truncated"
    )
    assert _result(updates).error_code == expected_error
    assert len(model.invocations) == 1
    call_events = [
        update
        for update in updates
        if isinstance(update, AgentEvent)
        and update.type == CoreEventType.MODEL_CALL_RECORDED
    ]
    assert len(call_events) == 1
    fingerprint = call_events[0].payload["requestFingerprint"]
    assert str(fingerprint).startswith("sha256:")
    trace = next(
        item for item in observer.traces
        if item.stage == "model_output"
    )
    assert trace.details["requestFingerprint"] == fingerprint
    assert trace.details["attempt"] == 1


@pytest.mark.asyncio
async def test_runtime_discards_truncated_tool_call_without_replaying_request():
    truncated = [ModelStreamChunk(
        tool_call_deltas=(ToolCallDelta(
            index=0,
            id="partial-call",
            type="function",
            name="readA",
            arguments_fragment='{"query":"unfinished',
        ),),
        finish_reason=ModelFinishReason.LENGTH,
    )]
    model = ScriptedModelGateway([truncated, _answer("must not run")])
    tools = ScriptedToolGateway([])
    observer = RecordingObserver([{"readA"}])

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

    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "tool_call_truncated"
    assert tools.requests == []
    assert len(model.invocations) == 1
    trace = next(
        item
        for item in observer.traces
        if item.stage == "model_output"
        and item.outcome == "truncated"
    )
    assert trace.details["errorCode"] == "tool_call_truncated"
    assert trace.details["batchExecuted"] is False
    assert trace.details["toolArgumentCharacters"] == len(
        '{"query":"unfinished'
    )


@pytest.mark.asyncio
async def test_resolved_task_budget_never_retries_the_same_truncated_allowance():
    truncated = [ModelStreamChunk(
        tool_call_deltas=(ToolCallDelta(
            index=0,
            id="partial-call",
            type="function",
            name="readA",
            arguments_fragment="{",
        ),),
        finish_reason=ModelFinishReason.LENGTH,
    )]
    model = ScriptedModelGateway([truncated])
    observer = RecordingObserver([{"readA"}])
    updates = await _collect(
        AgentRuntime(model_gateway=model, observer=observer),
        output_limit=_output_limit(),
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    assert len(model.invocations) == 1
    assert _result(updates).error_code == "tool_call_truncated"
    trace = next(
        item
        for item in observer.traces
        if item.stage == "model_output"
    )
    assert trace.outcome == "truncated"
    assert trace.details["outputLimit"]["maxTokens"] == 4_000


@pytest.mark.asyncio
async def test_reasoning_only_truncation_fails_without_replaying_request():
    reasoning_only = [
        ModelStreamChunk(reasoning_delta="spent the whole allowance reasoning"),
        ModelStreamChunk(finish_reason=ModelFinishReason.LENGTH),
    ]
    model = ScriptedModelGateway([reasoning_only, _answer("must not run")])
    tools = ScriptedToolGateway([])
    observer = RecordingObserver()
    request = AgentRunRequest(
        messages=(AgentMessage(role="user", content="write the candidate"),),
        model=ModelRequest(
            provider="openai",
            model="reasoning-model",
            options={"thinking": {"type": "enabled"}},
        ),
        domain_context=DomainContext(namespace="test"),
        tools_enabled=True,
    )

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
            observer=observer,
            limits=RuntimeLimits(
                max_model_rounds=2,
                max_progress_rounds=0,
            ),
        ),
        request=request,
        output_limit=_output_limit(),
        tools=(_schema("writeCandidate"),),
        scope_tools_to_observer=False,
    )

    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "model_output_truncated"
    assert [item.reasoning_mode for item in model.invocations] == [
        ReasoningMode.DEFAULT,
    ]
    assert tools.requests == []
    trace = next(
        item
        for item in observer.traces
        if item.stage == "model_output"
        and item.outcome == "truncated"
    )
    assert trace.details["reasoningOnly"] is True
    assert trace.details["outputLimit"]["maxTokens"] == 4_000


@pytest.mark.asyncio
async def test_reasoning_replay_keeps_requested_mode_across_tool_rounds():
    class ReplayRequiredProtocolGateway(ScriptedModelGateway):
        def __init__(self, rounds):
            super().__init__(rounds)
            self.attempted_invocations = []

        async def stream(self, messages, invocation, signal=None):
            self.attempted_invocations.append(invocation)
            missing_reasoning_tool_turn = any(
                message.tool_calls and not message.reasoning
                for message in messages
                if message.role is MessageRole.ASSISTANT
            )
            if (
                invocation.reasoning_mode is not ReasoningMode.DISABLED
                and missing_reasoning_tool_turn
            ):
                raise ModelGatewayError(
                    "reasoning_content must be passed back in thinking mode",
                    code="provider_bad_request",
                )
            return await super().stream(messages, invocation, signal)

    model = ReplayRequiredProtocolGateway([
        _tool_call(
            "read-call",
            "readA",
            reasoning_delta="reason before reading",
        ),
        _tool_call(
            "write-call",
            "writeCandidate",
            reasoning_delta="reason before writing",
        ),
        _answer("candidate saved"),
        _answer("candidate saved"),
    ])
    tools = ScriptedToolGateway([
        _batch("read-call", "readA", content='{"evidence":"ready"}'),
        _batch("write-call", "writeCandidate"),
    ])
    observer = RecordingObserver()
    request = AgentRunRequest(
        messages=(AgentMessage(role="user", content="review and write"),),
        model=ModelRequest(
            provider="openai",
            model="reasoning-replay-model",
            options={"thinking": {"type": "enabled"}},
        ),
        domain_context=DomainContext(namespace="test"),
        tools_enabled=True,
    )

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
            observer=observer,
            limits=RuntimeLimits(max_model_rounds=3, max_progress_rounds=0),
        ),
        request=request,
        output_limit=_output_limit(),
        tools=(_schema("readA"), _schema("writeCandidate")),
        scope_tools_to_observer=False,
    )

    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == "candidate saved"
    assert [
        item.reasoning_mode for item in model.attempted_invocations
    ] == [
        ReasoningMode.DEFAULT,
        ReasoningMode.DEFAULT,
        ReasoningMode.DEFAULT,
        ReasoningMode.DEFAULT,
    ]
    assert [request.calls[0].name for request in tools.requests] == [
        "readA",
        "writeCandidate",
    ]
    assert not any(trace.stage == "reasoning_mode" for trace in observer.traces)


@pytest.mark.asyncio
async def test_runtime_never_executes_complete_looking_call_finished_by_length():
    length_finished = [ModelStreamChunk(
        tool_call_deltas=(ToolCallDelta(
            index=0,
            id="unsafe-call",
            type="function",
            name="readA",
            arguments_fragment="{}",
        ),),
        finish_reason=ModelFinishReason.LENGTH,
    )]
    tools = ScriptedToolGateway([])
    observer = RecordingObserver([{"readA"}])

    updates = await _collect(
        AgentRuntime(
            model_gateway=ScriptedModelGateway([length_finished]),
            tool_execution_gateway=tools,
            observer=observer,
            limits=RuntimeLimits(max_model_rounds=1),
        ),
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
    )

    assert tools.requests == []
    assert observer.started_tools == []
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == "tool_call_truncated"
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
async def test_truncation_keeps_primary_error_and_skips_replanning():
    truncated = [ModelStreamChunk(
        tool_call_deltas=(ToolCallDelta(
            index=0,
            id="partial-call",
            type="function",
            name="readA",
            arguments_fragment="{",
        ),),
        finish_reason=ModelFinishReason.LENGTH,
    )]
    model = ScriptedModelGateway([truncated, _answer("must not run")])
    tools = ScriptedToolGateway([])
    observer = RecordingObserver([{"readA"}])
    hook = RecoveryPlanningHook(observer)

    updates = await _collect(
        AgentRuntime(
            model_gateway=model,
            tool_execution_gateway=tools,
            observer=observer,
        ),
        tools=(_schema("readA"),),
        scope_tools_to_observer=True,
        force_tool_choice=True,
        planning_hook=hook,
    )

    assert tools.requests == []
    assert hook.calls == []
    assert _result(updates).error_code == "tool_call_truncated"
    truncation_traces = [
        item
        for item in observer.traces
        if item.stage == "model_output"
        and item.outcome in {"truncated_retry", "truncated"}
    ]
    assert [item.outcome for item in truncation_traces] == ["truncated"]
    assert len(model.invocations) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("finish_reason", "expected_error"),
    [
        (ModelFinishReason.FILTERED, "model_output_filtered"),
        (ModelFinishReason.OTHER, "unsupported_model_finish_reason"),
    ],
)
async def test_runtime_fails_closed_for_non_committable_finish_reasons(
    finish_reason,
    expected_error,
):
    model = ScriptedModelGateway([[
        ModelStreamChunk(
            content_delta="partial answer",
            finish_reason=finish_reason,
        ),
    ]])
    tools = ScriptedToolGateway([])
    observer = RecordingObserver()

    updates = await _collect(AgentRuntime(
        model_gateway=model,
        tool_execution_gateway=tools,
        observer=observer,
    ))

    assert len(model.invocations) == 1
    assert tools.requests == []
    assert _result(updates).outcome is RuntimeOutcome.FAILED
    assert _result(updates).error_code == expected_error


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
        rounds.extend((_answer("continued"), _answer("continued")))
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
    assert len(model.message_rounds) == (3 if second_round else 1)
    assert observer.completed_tool_rounds == (1 if second_round else 0)
    assert observer.completed_tool_outcomes == ([outcome] if second_round else [])
    if outcome is ToolBatchOutcome.DECLINED:
        assert any(
            trace.stage == "tool_round"
            and trace.outcome == "declined"
            and trace.details["approvalStatuses"] == ["rejected"]
            for trace in observer.traces
        )
        assert _result(updates).final_response == "continued"
        assert not any(
            isinstance(update, AgentEvent)
            and update.type == "assistant.final_delta"
            for update in updates
        )


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
                reasoning_delta=(
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
        [
            ModelStreamChunk(content_delta="已保留人物，"),
            ModelStreamChunk(
                content_delta="未执行删除。",
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

    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in updates
    )
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "model.reasoning_delta"
        for update in updates
    )
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.commentary_delta"
        for update in updates
    )
    assert len(tools.requests) == 1
    assert len(model.message_rounds) == 4
    assert (
        model.invocations[1].tools
        == model.invocations[2].tools
        == model.invocations[3].tools
        == ()
    )
    assert model.invocations[1].tool_choice is ToolChoiceMode.NONE
    assert model.invocations[2].tool_choice is ToolChoiceMode.NONE
    assert model.invocations[3].tool_choice is ToolChoiceMode.NONE
    assert model.message_rounds[1][-1].role is MessageRole.DEVELOPER
    assert "user rejected" in str(model.message_rounds[1][-1].content)
    assert model.message_rounds[2][-1].role is MessageRole.DEVELOPER
    assert "not shown to the user" in str(model.message_rounds[2][-1].content)
    assert observer.model_delta_count == 2
    assert any(
        trace.stage == "model_output"
        and trace.outcome == "textual_tool_call_retry"
        for trace in observer.traces
    )
    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert _result(updates).final_response == "已保留人物，未执行删除。"


@pytest.mark.asyncio
async def test_runtime_does_not_invent_decline_response_when_model_is_empty():
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

    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in updates
    )
    assert _result(updates).final_response == ""
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
        and update.type == "assistant.final_delta"
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
async def test_runtime_passes_the_exact_output_limit_to_normal_invocation():
    schema = _schema("readA")
    budget = _matching_budget(window=4_096, tools=(schema,), output=321)
    model = ScriptedModelGateway([_answer("done"), _answer("done")])

    updates = await _collect(
        AgentRuntime(model_gateway=model),
        request=_request(context_window=4_096),
        context_budget=budget,
        output_limit=_output_limit(321),
        tools=(schema,),
        scope_tools_to_observer=False,
    )

    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert model.invocations[0].max_output_tokens == 321
    assert model.invocations[0].tools == (schema,)
    assert model.invocations[1].max_output_tokens == 321
    assert model.invocations[1].tools == ()


@pytest.mark.asyncio
async def test_runtime_preserves_exact_output_limit_on_tool_choice_fallback():
    schema = _schema("readA")
    budget = _matching_budget(window=4_096, tools=(schema,), output=257)
    model = ScriptedModelGateway([
        UnsupportedModelFeatureError("unsupported"),
        _tool_call("call-a", "readA"),
        _answer("done"),
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
        output_limit=_output_limit(257),
        tools=(schema,),
        scope_tools_to_observer=False,
        force_tool_choice=True,
    )

    assert _result(updates).outcome is RuntimeOutcome.COMPLETED
    assert [item.tool_choice for item in model.invocations] == [
        ToolChoiceMode.REQUIRED,
        ToolChoiceMode.AUTO,
        ToolChoiceMode.AUTO,
        ToolChoiceMode.NONE,
    ]
    assert [item.max_output_tokens for item in model.invocations] == [
        257,
        257,
        257,
        257,
    ]


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
        ToolChoiceMode.NONE,
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
    assert not any(
        isinstance(update, AgentEvent)
        and update.type == "assistant.final_delta"
        for update in updates
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
