"""Provider-neutral Agent model/tool loop.

This stage-3 runtime starts after the application has built and initially
budgeted context.  It owns model rounds, typed tool continuation, authorization,
round trimming, cancellation and the model-round limit.  HTTP, SSE, writing
concepts and concrete tool handlers stay behind ports.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any, AsyncIterator, Sequence

from agent_core.cancellation import (
    OperationCanceled,
    await_with_cancellation,
)
from agent_core.context_budget import (
    estimate_tool_schema_tokens,
    trim_agent_messages_by_turn,
)
from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRuntimeResult,
    ContextBudget,
    ExecutionState,
    MessageOrigin,
    MessageRole,
    ModelFinishReason,
    ModelInvocation,
    ResponseConstraints,
    ResponseValidationResult,
    RuntimeLimits,
    RuntimeOutcome,
    RunId,
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolBatchResult,
    ToolCall,
    ToolCallResult,
    ToolChoiceMode,
    ToolSchema,
    TraceRecord,
)
from agent_core.errors import (
    ModelGatewayError,
    ResponseJudgeContractError,
    UnsupportedModelFeatureError,
)
from agent_core.events import AgentEvent, CoreEventType
from agent_core.ports import (
    CancellationSignal,
    EventSink,
    ModelGateway,
    ResponseJudge,
    ResponseValidator,
    RuntimeObserver,
    ToolExecutionGateway,
)


RuntimeUpdate = AgentEvent | AgentRuntimeResult


_DECLINED_TOOL_GUIDANCE = (
    "The preceding tool result is authoritative: the user rejected the "
    "approval, so the operation was not executed and must not be retried in "
    "this run. Respond in the user's language with a plain-language summary "
    "of that outcome. Do not call or imitate a tool, and do not emit tool-call "
    "markup as text."
)
_TEXTUAL_TOOL_CALL_RETRY_GUIDANCE = (
    "Your preceding response imitated a tool call using plain-text markup. "
    "That text was not executable and was not shown to the user. The rejected "
    "approval remains authoritative and the operation was not executed. Give "
    "one concise plain-language response in the user's language. Do not call, "
    "retry, or imitate any tool."
)
_MISSING_REQUIRED_TOOL_CALL_RETRY_GUIDANCE = (
    "The preceding model round did not return the structured tool call required "
    "by the current approved plan step. That text was discarded and no tool was "
    "executed. Retry this step now by returning exactly one valid structured call "
    "to one of the tools currently exposed by the host. Do not describe, imitate, "
    "or wrap the call in ordinary text."
)
_DECLINED_FINAL_RESPONSE_ZH = "您已拒绝审批；操作未执行，相关数据仍保留。"
_DECLINED_FINAL_RESPONSE_EN = (
    "You rejected the approval. The operation was not executed, and the "
    "related data remains unchanged."
)
_CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_TEXTUAL_TOOL_PROTOCOL_MARKER = re.compile(
    r"<\s*/?\s*(?:"
    r"tool(?:\s*[_-]?\s*(?:c(?:a(?:l(?:l)?)?)?)?)?(?=\s|>|/|$)"
    r"|function\s*="
    r"|parameter\s*="
    r")",
    re.IGNORECASE,
)
_TOP_LEVEL_NUMBERED_ITEM = re.compile(
    r"^(?P<number>[1-9][0-9]*)[.\u3001\uff0e)]\s+\S",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class _PendingProviderAttempt:
    """Frozen inputs for a provider retry that consumes the next round slot."""

    messages: tuple[AgentMessage, ...]
    invocation: ModelInvocation
    allowed_names: frozenset[str]
    future_names: frozenset[str]
    require_tool: bool
    buffer_model_content: bool
    logical_round: int
    attempt: int


class AgentRuntime:
    def __init__(
        self,
        *,
        model_gateway: ModelGateway,
        tool_execution_gateway: ToolExecutionGateway | None = None,
        observer: RuntimeObserver | None = None,
        limits: RuntimeLimits = RuntimeLimits(),
    ):
        self._model_gateway = model_gateway
        self._tool_execution_gateway = tool_execution_gateway
        self._observer = observer
        self._limits = limits

    async def run(
        self,
        request: AgentRunRequest,
        *,
        tools: Sequence[ToolSchema] = (),
        response_constraints: ResponseConstraints = ResponseConstraints(),
        response_validators: Sequence[ResponseValidator] = (),
        response_judges: Sequence[ResponseJudge] = (),
        execution_state: ExecutionState | None = None,
        run_id: RunId | None = None,
        context_budget: ContextBudget | None = None,
        round_input_tokens: int | None = None,
        scope_tools_to_observer: bool = True,
        force_tool_choice: bool = False,
        require_tool_call: bool | None = None,
        tools_executable: bool = True,
        signal: CancellationSignal | None = None,
    ) -> AsyncIterator[RuntimeUpdate]:
        messages = list(request.messages)
        validators = tuple(response_validators)
        judges = tuple(response_judges)
        state = execution_state or ExecutionState()
        configured_tools = tuple(tools) if request.tools_enabled else ()
        used_model = request.model.model
        # A provider-level REQUIRED hint can never weaken the host guard.
        # ``require_tool_call=True`` also keeps that guard when the provider
        # capability cache already requires AUTO transport.
        logical_required_tool_call_enabled = bool(
            force_tool_choice or require_tool_call
        )
        provider_required_tool_choice_enabled = bool(force_tool_choice)
        missing_required_call_retry_used = False
        future_step_retry_used = False
        declined_response_pending = False
        textual_tool_call_retry_used = False
        response_repair_phases_used: set[str] = set()
        response_repair_pending = False
        provider_interruption_retry_used = False
        pending_provider_attempt: _PendingProviderAttempt | None = None
        logical_round_number = 0
        budget_contract_error = _context_budget_contract_error(
            request,
            context_budget,
            configured_tools,
        )
        if budget_contract_error is not None:
            await self._trace(
                "context_budget",
                "contract_mismatch",
                details={"errorCode": budget_contract_error},
            )
            yield _runtime_result(
                run_id,
                RuntimeOutcome.FAILED,
                used_model,
                0,
                error_code=budget_contract_error,
            )
            return

        maximum_output_tokens = (
            context_budget.output_reserve_tokens
            if context_budget is not None
            else None
        )

        for round_index in range(self._limits.max_model_rounds):
            round_number = round_index + 1
            if _is_canceled(signal):
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.CANCELED,
                    used_model,
                    round_index,
                    error_code="request_canceled",
                )
                return

            if pending_provider_attempt is not None:
                provider_attempt = pending_provider_attempt
                pending_provider_attempt = None
            else:
                initial_logical_round = logical_round_number == 0
                active_token_budget = (
                    (
                        context_budget.provider_input_tokens
                        if initial_logical_round
                        else context_budget.round_input_tokens
                    )
                    if context_budget is not None
                    else (
                        None if initial_logical_round else round_input_tokens
                    )
                )
                if active_token_budget is not None:
                    trimmed = trim_agent_messages_by_turn(
                        messages,
                        active_token_budget,
                    )
                    messages = list(trimmed.messages)
                    if trimmed.overflow_tokens > 0:
                        await self._trace(
                            "context_budget",
                            (
                                "overflow_initial"
                                if initial_logical_round
                                else "overflow_after_tool"
                            ),
                            details={"round": round_number},
                        )
                        yield _runtime_result(
                            run_id,
                            RuntimeOutcome.FAILED,
                            used_model,
                            round_index,
                            error_code=(
                                "context_overflow_initial"
                                if initial_logical_round
                                else "context_overflow_after_tool"
                            ),
                        )
                        return

                allowed_names = self._allowed_names(
                    configured_tools,
                    scope_tools_to_observer=scope_tools_to_observer,
                )
                future_names = self._future_allowed_names(
                    configured_tools,
                    scope_tools_to_observer=scope_tools_to_observer,
                )
                visible_tools = (
                    ()
                    if declined_response_pending or response_repair_pending
                    else tuple(
                        schema
                        for schema in configured_tools
                        if schema.name in allowed_names
                    )
                )
                require_tool = bool(
                    logical_required_tool_call_enabled and visible_tools
                )
                force_required_tool_choice = bool(
                    provider_required_tool_choice_enabled and visible_tools
                )
                buffer_model_content = bool(
                    require_tool
                    or declined_response_pending
                    or response_constraints.exact_top_level_item_count is not None
                    or validators
                    or judges
                )
                logical_round_number += 1
                provider_attempt = _PendingProviderAttempt(
                    messages=tuple(messages),
                    invocation=ModelInvocation(
                        request=request.model,
                        tools=visible_tools,
                        tool_choice=(
                            ToolChoiceMode.REQUIRED
                            if force_required_tool_choice
                            else (
                                ToolChoiceMode.AUTO
                                if visible_tools
                                else ToolChoiceMode.NONE
                            )
                        ),
                        max_output_tokens=maximum_output_tokens,
                    ),
                    allowed_names=allowed_names,
                    future_names=future_names,
                    require_tool=require_tool,
                    buffer_model_content=buffer_model_content,
                    logical_round=logical_round_number,
                    attempt=1,
                )

            round_messages = provider_attempt.messages
            invocation = provider_attempt.invocation
            allowed_names = provider_attempt.allowed_names
            future_names = provider_attempt.future_names
            require_tool = provider_attempt.require_tool
            buffer_model_content = provider_attempt.buffer_model_content

            model_started = perf_counter()
            accumulator = _ModelRoundAccumulator()
            received_chunk_count = 0
            emitted_delta_count = 0
            try:
                stream = await await_with_cancellation(
                    self._model_gateway.stream(
                        round_messages,
                        invocation,
                        signal,
                    ),
                    signal,
                )
            except OperationCanceled:
                await self._trace(
                    "model_round",
                    "canceled",
                    details={
                        "round": round_number,
                        "attempt": provider_attempt.attempt,
                        "logicalRound": provider_attempt.logical_round,
                        "receivedChunkCount": 0,
                        "emittedDeltaCount": 0,
                        "retryScheduled": False,
                        "providerAttemptTerminal": True,
                        "batchExecuted": False,
                    },
                    duration_ms=_duration_ms(model_started),
                )
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.CANCELED,
                    used_model,
                    round_number,
                    error_code="request_canceled",
                )
                return
            except UnsupportedModelFeatureError as error:
                if _is_canceled(signal):
                    await self._trace(
                        "model_round",
                        "canceled",
                        details={
                            "round": round_number,
                            "attempt": provider_attempt.attempt,
                            "logicalRound": provider_attempt.logical_round,
                            "receivedChunkCount": 0,
                            "emittedDeltaCount": 0,
                            "retryScheduled": False,
                            "providerAttemptTerminal": True,
                            "batchExecuted": False,
                        },
                        duration_ms=_duration_ms(model_started),
                    )
                    yield _runtime_result(
                        run_id,
                        RuntimeOutcome.CANCELED,
                        used_model,
                        round_number,
                        error_code="request_canceled",
                    )
                    return
                can_fallback = bool(
                    invocation.tool_choice is ToolChoiceMode.REQUIRED
                    and round_index < self._limits.max_model_rounds - 1
                )
                if invocation.tool_choice is ToolChoiceMode.REQUIRED:
                    provider_required_tool_choice_enabled = False
                await self._trace(
                    "model_round",
                    "unsupported_model_feature",
                    details={
                        "round": round_number,
                        "attempt": provider_attempt.attempt,
                        "logicalRound": provider_attempt.logical_round,
                        "receivedChunkCount": 0,
                        "emittedDeltaCount": 0,
                        "retryScheduled": can_fallback,
                        "providerAttemptTerminal": True,
                        "batchExecuted": False,
                        "errorType": _root_error_type(error),
                    },
                    duration_ms=_duration_ms(model_started),
                )
                if can_fallback:
                    fallback_invocation = ModelInvocation(
                        request=invocation.request,
                        tools=invocation.tools,
                        tool_choice=ToolChoiceMode.AUTO,
                        max_output_tokens=invocation.max_output_tokens,
                        reasoning_mode=invocation.reasoning_mode,
                    )
                    pending_provider_attempt = _PendingProviderAttempt(
                        messages=round_messages,
                        invocation=fallback_invocation,
                        allowed_names=allowed_names,
                        future_names=future_names,
                        require_tool=require_tool,
                        buffer_model_content=buffer_model_content,
                        logical_round=provider_attempt.logical_round,
                        attempt=provider_attempt.attempt + 1,
                    )
                    await self._trace(
                        "tool_choice",
                        "provider_fallback_auto",
                        details={
                            "round": round_number,
                            "attempt": provider_attempt.attempt,
                            "logicalRound": provider_attempt.logical_round,
                            "retryScheduled": True,
                            "batchExecuted": False,
                        },
                    )
                    continue
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code=error.code,
                )
                return
            except Exception as error:
                if _is_canceled(signal):
                    await self._trace(
                        "model_round",
                        "canceled",
                        details={
                            "round": round_number,
                            "attempt": provider_attempt.attempt,
                            "logicalRound": provider_attempt.logical_round,
                            "receivedChunkCount": 0,
                            "emittedDeltaCount": 0,
                            "retryScheduled": False,
                            "providerAttemptTerminal": True,
                            "batchExecuted": False,
                        },
                        duration_ms=_duration_ms(model_started),
                    )
                    yield _runtime_result(
                        run_id,
                        RuntimeOutcome.CANCELED,
                        used_model,
                        round_number,
                        error_code="request_canceled",
                    )
                    return
                error_code = (
                    error.code
                    if isinstance(error, ModelGatewayError)
                    else "model_gateway_error"
                )
                retry_scheduled = bool(
                    _is_retryable_stream_interruption(error)
                    and not provider_interruption_retry_used
                    and not _is_canceled(signal)
                    and round_index < self._limits.max_model_rounds - 1
                )
                interrupted = error_code == "upstream_stream_interrupted"
                await self._trace(
                    "stream" if interrupted else "model_round",
                    (
                        (
                            "interrupted_retry"
                            if retry_scheduled
                            else "interrupted"
                        )
                        if interrupted
                        else "exception"
                    ),
                    details={
                        "round": round_number,
                        "attempt": provider_attempt.attempt,
                        "logicalRound": provider_attempt.logical_round,
                        "receivedChunkCount": 0,
                        "emittedDeltaCount": 0,
                        "retryScheduled": retry_scheduled,
                        "providerAttemptTerminal": True,
                        "batchExecuted": False,
                        "errorType": _root_error_type(error),
                    },
                    duration_ms=_duration_ms(model_started),
                )
                if retry_scheduled:
                    provider_interruption_retry_used = True
                    pending_provider_attempt = _PendingProviderAttempt(
                        messages=round_messages,
                        invocation=invocation,
                        allowed_names=allowed_names,
                        future_names=future_names,
                        require_tool=require_tool,
                        buffer_model_content=buffer_model_content,
                        logical_round=provider_attempt.logical_round,
                        attempt=provider_attempt.attempt + 1,
                    )
                    continue
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code=error_code,
                )
                return

            used_model = stream.model or used_model
            stream_canceled = False
            stream_error: Exception | None = None
            chunks = stream.chunks
            try:
                while True:
                    try:
                        chunk = await await_with_cancellation(anext(chunks), signal)
                    except StopAsyncIteration:
                        break
                    received_chunk_count += 1
                    accumulator.add(chunk)
                    if chunk.thinking_delta and not buffer_model_content:
                        emitted_delta_count += 1
                        yield AgentEvent(
                            type=CoreEventType.MODEL_THINKING_DELTA,
                            run_id=run_id,
                            payload={"delta": chunk.thinking_delta},
                        )
                    if (
                        chunk.content_delta
                        and not require_tool
                        and not buffer_model_content
                    ):
                        if self._observer is not None:
                            await self._observer.on_model_delta()
                        emitted_delta_count += 1
                        yield AgentEvent(
                            type=CoreEventType.MODEL_DELTA,
                            run_id=run_id,
                            payload={"delta": chunk.content_delta},
                        )
                    if chunk.finish_reason is not None:
                        break
            except OperationCanceled:
                stream_canceled = True
            except Exception as error:
                stream_error = error
            finally:
                await _close_async_iterator(chunks)

            if stream_canceled or (
                accumulator.finish_reason is None and _is_canceled(signal)
            ):
                await self._trace(
                    "stream",
                    "canceled",
                    details={
                        "round": round_number,
                        "attempt": provider_attempt.attempt,
                        "logicalRound": provider_attempt.logical_round,
                        "receivedChunkCount": received_chunk_count,
                        "emittedDeltaCount": emitted_delta_count,
                        "retryScheduled": False,
                        "providerAttemptTerminal": True,
                        "batchExecuted": False,
                    },
                    duration_ms=_duration_ms(model_started),
                )
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.CANCELED,
                    used_model,
                    round_number,
                    error_code="request_canceled",
                )
                return
            if stream_error is None and accumulator.finish_reason is None:
                stream_error = ModelGatewayError(
                    "model stream ended without a finish reason",
                    code="upstream_stream_interrupted",
                    retryable=True,
                )
            if stream_error is not None:
                error_code = (
                    stream_error.code
                    if isinstance(stream_error, ModelGatewayError)
                    else "model_stream_error"
                )
                retry_scheduled = bool(
                    _is_retryable_stream_interruption(stream_error)
                    and not provider_interruption_retry_used
                    and emitted_delta_count == 0
                    and accumulator.finish_reason is None
                    and not _is_canceled(signal)
                    and round_index < self._limits.max_model_rounds - 1
                )
                interrupted = error_code == "upstream_stream_interrupted"
                await self._trace(
                    "stream" if interrupted else "model_round",
                    (
                        (
                            "interrupted_retry"
                            if retry_scheduled
                            else "interrupted"
                        )
                        if interrupted
                        else "stream_exception"
                    ),
                    details={
                        "round": round_number,
                        "attempt": provider_attempt.attempt,
                        "logicalRound": provider_attempt.logical_round,
                        "receivedChunkCount": received_chunk_count,
                        "emittedDeltaCount": emitted_delta_count,
                        "retryScheduled": retry_scheduled,
                        "providerAttemptTerminal": True,
                        "batchExecuted": False,
                        "errorType": _root_error_type(stream_error),
                    },
                    duration_ms=_duration_ms(model_started),
                )
                if retry_scheduled:
                    provider_interruption_retry_used = True
                    pending_provider_attempt = _PendingProviderAttempt(
                        messages=round_messages,
                        invocation=invocation,
                        allowed_names=allowed_names,
                        future_names=future_names,
                        require_tool=require_tool,
                        buffer_model_content=buffer_model_content,
                        logical_round=provider_attempt.logical_round,
                        attempt=provider_attempt.attempt + 1,
                    )
                    continue
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code=error_code,
                )
                return

            calls, malformed_call_error = accumulator.tool_calls()
            finish_reason = accumulator.finish_reason
            await self._trace(
                "model_round",
                (finish_reason.value if finish_reason is not None else "stream_end"),
                details={
                    "round": round_number,
                    "attempt": provider_attempt.attempt,
                    "logicalRound": provider_attempt.logical_round,
                    "toolCallCount": accumulator.tool_call_count,
                    "receivedChunkCount": received_chunk_count,
                    "emittedDeltaCount": emitted_delta_count,
                    "retryScheduled": False,
                    "providerAttemptTerminal": True,
                    "batchExecuted": False,
                },
                duration_ms=_duration_ms(model_started),
            )

            if malformed_call_error is not None:
                await self._trace(
                    "tool_authorization",
                    "malformed_batch",
                    details={
                        "round": round_number,
                        "callCount": accumulator.tool_call_count,
                        "reason": malformed_call_error,
                    },
                )
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code="malformed_tool_call_batch",
                )
                return

            tool_finish = finish_reason is ModelFinishReason.TOOL_CALLS or (
                finish_reason in {ModelFinishReason.STOP, ModelFinishReason.LENGTH}
                and bool(calls)
            )
            if require_tool and not calls:
                can_retry = (
                    not missing_required_call_retry_used
                    and round_index < self._limits.max_model_rounds - 2
                )
                await self._trace(
                    "tool_round",
                    (
                        "missing_required_call_retry"
                        if can_retry
                        else "missing_required_call"
                    ),
                    details={
                        "round": round_number,
                        "retryUsed": missing_required_call_retry_used,
                        "retryAttempt": 1 if can_retry else 0,
                        "roundsRemaining": (
                            self._limits.max_model_rounds - round_number
                        ),
                        "batchExecuted": False,
                    },
                )
                if can_retry:
                    missing_required_call_retry_used = True
                    messages.append(AgentMessage(
                        role=MessageRole.DEVELOPER,
                        content=_MISSING_REQUIRED_TOOL_CALL_RETRY_GUIDANCE,
                    ))
                    continue
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code="missing_required_tool_call",
                )
                return

            if calls and not tool_finish:
                await self._trace(
                    "tool_authorization",
                    "malformed_batch",
                    details={
                        "round": round_number,
                        "callCount": len(calls),
                        "reason": "tool_calls_without_supported_finish_reason",
                    },
                )
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code="malformed_tool_call_batch",
                )
                return

            if declined_response_pending and calls:
                await self._trace(
                    "tool_authorization",
                    "rejected_after_approval_decline",
                    details={
                        "round": round_number,
                        "requestedTools": sorted(call.name for call in calls),
                        "callCount": len(calls),
                    },
                )
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code="tool_call_after_approval_rejection",
                )
                return

            if response_repair_pending and calls:
                await self._trace(
                    "tool_authorization",
                    "rejected_during_response_repair",
                    details={
                        "round": round_number,
                        "requestedTools": sorted(call.name for call in calls),
                        "callCount": len(calls),
                    },
                )
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code="tool_call_during_response_repair",
                )
                return

            if not tool_finish or not calls:
                if (
                    declined_response_pending
                    and _is_textual_tool_call(accumulator.content)
                ):
                    can_retry = (
                        not textual_tool_call_retry_used
                        and round_index < self._limits.max_model_rounds - 1
                    )
                    await self._trace(
                        "model_output",
                        (
                            "textual_tool_call_retry"
                            if can_retry
                            else "textual_tool_call_rejected"
                        ),
                        details={
                            "round": round_number,
                            "retryUsed": textual_tool_call_retry_used,
                        },
                    )
                    if can_retry:
                        textual_tool_call_retry_used = True
                        messages.append(AgentMessage(
                            role=MessageRole.DEVELOPER,
                            content=_TEXTUAL_TOOL_CALL_RETRY_GUIDANCE,
                        ))
                        continue
                    yield _runtime_result(
                        run_id,
                        RuntimeOutcome.FAILED,
                        used_model,
                        round_number,
                        error_code="unstructured_tool_call_after_rejection",
                    )
                    return

                if not declined_response_pending:
                    exact_item_count = (
                        response_constraints.exact_top_level_item_count
                    )
                    observed_items = _top_level_numbered_items(
                        accumulator.content
                    )
                    violation_codes: list[str] = []
                    repair_guidance: list[str] = []
                    validation_details: list[dict[str, Any]] = []
                    if exact_item_count is not None:
                        expected_items = tuple(range(1, exact_item_count + 1))
                        if observed_items != expected_items:
                            violation_codes.append(
                                "core.exact_top_level_item_count"
                            )
                            repair_guidance.append(
                                _exact_item_count_repair_guidance(exact_item_count)
                            )

                    for validator in validators:
                        try:
                            result = validator.validate(
                                content=accumulator.content,
                                messages=tuple(messages),
                            )
                        except Exception as error:
                            await self._trace(
                                "model_output",
                                "response_validator_exception",
                                details={
                                    "round": round_number,
                                    "errorType": _root_error_type(error),
                                },
                            )
                            yield _runtime_result(
                                run_id,
                                RuntimeOutcome.FAILED,
                                used_model,
                                round_number,
                                error_code="response_validator_error",
                            )
                            return
                        if not isinstance(result, ResponseValidationResult):
                            await self._trace(
                                "model_output",
                                "response_validator_contract_violation",
                                details={"round": round_number},
                            )
                            yield _runtime_result(
                                run_id,
                                RuntimeOutcome.FAILED,
                                used_model,
                                round_number,
                                error_code=(
                                    "response_validator_contract_violation"
                                ),
                            )
                            return
                        if result.accepted:
                            continue
                        violation_codes.append(str(result.violation_code))
                        repair_guidance.append(str(result.repair_guidance))
                        validation_details.append({
                            "source": "validator",
                            "code": result.violation_code,
                            "details": dict(result.details),
                        })

                    # Semantic judges are potentially expensive and receive a
                    # structurally valid candidate only. Their domain logic
                    # stays behind the injected async port; Core owns
                    # cancellation and fail-closed behavior. Deterministic and
                    # semantic violations each receive at most one tool-free
                    # repair, with two repairs total across the response.
                    if not violation_codes:
                        for judge_index, judge in enumerate(judges):
                            judge_started = perf_counter()
                            try:
                                result = await await_with_cancellation(
                                    judge.judge(
                                        content=accumulator.content,
                                        messages=tuple(messages),
                                        signal=signal,
                                    ),
                                    signal,
                                )
                            except OperationCanceled:
                                await self._trace(
                                    "model_output",
                                    "response_judge_canceled",
                                    details={
                                        "round": round_number,
                                        "judgeIndex": judge_index,
                                    },
                                    duration_ms=_duration_ms(judge_started),
                                )
                                yield _runtime_result(
                                    run_id,
                                    RuntimeOutcome.CANCELED,
                                    used_model,
                                    round_number,
                                    error_code="request_canceled",
                                )
                                return
                            except ResponseJudgeContractError as error:
                                await self._trace(
                                    "model_output",
                                    "response_judge_contract_violation",
                                    details={
                                        "round": round_number,
                                        "judgeIndex": judge_index,
                                        "errorType": _root_error_type(error),
                                    },
                                    duration_ms=_duration_ms(judge_started),
                                )
                                yield _runtime_result(
                                    run_id,
                                    RuntimeOutcome.FAILED,
                                    used_model,
                                    round_number,
                                    error_code=(
                                        "response_judge_contract_violation"
                                    ),
                                )
                                return
                            except Exception as error:
                                await self._trace(
                                    "model_output",
                                    "response_judge_exception",
                                    details={
                                        "round": round_number,
                                        "judgeIndex": judge_index,
                                        "errorType": _root_error_type(error),
                                    },
                                    duration_ms=_duration_ms(judge_started),
                                )
                                yield _runtime_result(
                                    run_id,
                                    RuntimeOutcome.FAILED,
                                    used_model,
                                    round_number,
                                    error_code="response_judge_error",
                                )
                                return
                            if not isinstance(result, ResponseValidationResult):
                                await self._trace(
                                    "model_output",
                                    "response_judge_contract_violation",
                                    details={
                                        "round": round_number,
                                        "judgeIndex": judge_index,
                                    },
                                    duration_ms=_duration_ms(judge_started),
                                )
                                yield _runtime_result(
                                    run_id,
                                    RuntimeOutcome.FAILED,
                                    used_model,
                                    round_number,
                                    error_code=(
                                        "response_judge_contract_violation"
                                    ),
                                )
                                return
                            await self._trace(
                                "model_output",
                                (
                                    "response_judge_passed"
                                    if result.accepted
                                    else "response_judge_rejected"
                                ),
                                details={
                                    "round": round_number,
                                    "judgeIndex": judge_index,
                                    **(
                                        {}
                                        if result.accepted
                                        else {
                                            "violationCode": result.violation_code
                                        }
                                    ),
                                },
                                duration_ms=_duration_ms(judge_started),
                            )
                            if result.accepted:
                                continue
                            violation_codes.append(str(result.violation_code))
                            repair_guidance.append(str(result.repair_guidance))
                            validation_details.append({
                                "source": "judge",
                                "code": result.violation_code,
                                "details": dict(result.details),
                            })

                    if violation_codes:
                        repair_phase = (
                            "semantic"
                            if validation_details
                            and all(
                                item.get("source") == "judge"
                                for item in validation_details
                            )
                            else "deterministic"
                        )
                        phase_retry_used = (
                            repair_phase in response_repair_phases_used
                        )
                        can_retry = (
                            not phase_retry_used
                            and len(response_repair_phases_used) < 2
                            and round_index < self._limits.max_model_rounds - 1
                        )
                        trace_details: dict[str, Any] = {
                            "round": round_number,
                            "violationCodes": violation_codes,
                            "retryUsed": phase_retry_used,
                            "repairPhase": repair_phase,
                            "repairAttempts": len(response_repair_phases_used),
                            "repairPhasesUsed": sorted(
                                response_repair_phases_used
                            ),
                        }
                        if exact_item_count is not None:
                            trace_details.update({
                                "expectedItemCount": exact_item_count,
                                "observedItems": list(observed_items),
                            })
                        if validation_details:
                            trace_details["validatorResults"] = validation_details
                        await self._trace(
                            "model_output",
                            (
                                "response_constraint_retry"
                                if can_retry
                                else "response_constraint_rejected"
                            ),
                            details=trace_details,
                        )
                        if can_retry:
                            response_repair_phases_used.add(repair_phase)
                            response_repair_pending = True
                            messages.extend((
                                AgentMessage(
                                    role=MessageRole.ASSISTANT,
                                    content=accumulator.content,
                                    thinking=accumulator.thinking or None,
                                ),
                                AgentMessage(
                                    role=MessageRole.DEVELOPER,
                                    content=_response_constraint_repair_guidance(
                                        repair_guidance
                                    ),
                                ),
                            ))
                            continue
                        yield _runtime_result(
                            run_id,
                            RuntimeOutcome.FAILED,
                            used_model,
                            round_number,
                            error_code="response_constraint_violation",
                        )
                        return

                final_response = (
                    _declined_final_response(request.messages)
                    if declined_response_pending
                    else accumulator.content
                )
                if buffer_model_content:
                    if self._observer is not None:
                        await self._observer.on_model_delta()
                    yield AgentEvent(
                        type=CoreEventType.MODEL_DELTA,
                        run_id=run_id,
                        payload={"delta": final_response},
                    )
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.COMPLETED,
                    used_model,
                    round_number,
                    final_response=final_response,
                )
                return

            requested_names = frozenset(call.name for call in calls)
            current_authorized = requested_names.issubset(allowed_names)
            includes_future = bool(requested_names - allowed_names) and bool(
                requested_names & future_names
            )
            future_batch = (
                includes_future
                and requested_names.issubset(allowed_names | future_names)
            )

            if future_batch:
                can_retry = (
                    not future_step_retry_used
                    and len(allowed_names) == 1
                    and round_index < self._limits.max_model_rounds - 2
                )
                if can_retry:
                    future_step_retry_used = True
                    await self._trace(
                        "tool_authorization",
                        "future_step_retry",
                        details={
                            "round": round_number,
                            "requestedTools": sorted(requested_names),
                            "currentTools": sorted(allowed_names),
                            "futureTools": sorted(future_names),
                            "callCount": len(calls),
                            "batchExecuted": False,
                            "executed": False,
                        },
                    )
                    messages.extend(_future_step_retry_messages(
                        calls,
                        current_allowed=allowed_names,
                        content=accumulator.content,
                        thinking=accumulator.thinking,
                    ))
                    continue

                await self._trace(
                    "tool_authorization",
                    "future_step_rejected",
                    details={
                        "round": round_number,
                        "requestedTools": sorted(requested_names),
                        "currentTools": sorted(allowed_names),
                        "futureTools": sorted(future_names),
                        "retryAlreadyUsed": future_step_retry_used,
                        "roundsAvailable": (
                            round_index < self._limits.max_model_rounds - 2
                        ),
                    },
                )
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code="tool_step_out_of_order",
                )
                return

            if not current_authorized:
                await self._trace(
                    "tool_authorization",
                    "rejected",
                    details={
                        "round": round_number,
                        "requestedTools": sorted(requested_names),
                        "currentTools": sorted(allowed_names),
                        "futureTools": sorted(future_names),
                    },
                )
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code="tool_not_authorized",
                )
                return

            if round_index >= self._limits.max_model_rounds - 1:
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code="max_model_rounds",
                )
                return

            if scope_tools_to_observer and self._observer is not None:
                await self._observer.on_tool_calls_started(tuple(sorted(requested_names)))
            yield AgentEvent(
                type=CoreEventType.TOOL_CALLS_STARTED,
                run_id=run_id,
                payload={
                    "calls": [_tool_call_payload(call) for call in calls],
                    "in_progress": True,
                    "partial_content": (
                        ""
                        if require_tool or buffer_model_content
                        else accumulator.content
                    ),
                    "partial_thinking": (
                        ""
                        if require_tool or buffer_model_content
                        else accumulator.thinking
                    ),
                    "model": used_model,
                    "required": require_tool,
                },
            )

            if not tools_executable or self._tool_execution_gateway is None:
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.COMPLETED,
                    used_model,
                    round_number,
                    final_response=accumulator.content,
                )
                return

            tool_started = perf_counter()
            try:
                batch_result: ToolBatchResult | None = None
                batch_stream = _stream_tool_batch(
                    self._tool_execution_gateway,
                    ToolBatchRequest(
                        run_id=run_id,
                        calls=calls,
                        allowed_tool_names=allowed_names,
                        state=state,
                    ),
                    signal,
                )
                try:
                    async for update in batch_stream:
                        if isinstance(update, AgentEvent):
                            yield update
                        else:
                            batch_result = update
                finally:
                    await _close_async_iterator(batch_stream)
                if batch_result is None:
                    raise RuntimeError("tool gateway returned no result")
            except OperationCanceled:
                await self._trace(
                    "tool_round",
                    "canceled",
                    details={"round": round_number},
                    duration_ms=_duration_ms(tool_started),
                )
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.CANCELED,
                    used_model,
                    round_number,
                    error_code="request_canceled",
                )
                return
            except Exception as error:
                await self._trace(
                    "tool_round",
                    "exception",
                    details={
                        "round": round_number,
                        "errorType": type(error).__name__,
                    },
                    duration_ms=_duration_ms(tool_started),
                )
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code="tool_execution_error",
                )
                return

            yield AgentEvent(
                type=CoreEventType.TOOL_RESULTS,
                run_id=run_id,
                payload={
                    "results": [
                        _tool_result_payload(result)
                        for result in batch_result.results
                    ],
                },
            )
            outcome = batch_result.outcome
            approval_statuses = sorted({
                result.approval_status.value
                for result in batch_result.results
                if result.approval_status is not None
            })
            await self._trace(
                "tool_round",
                outcome.value,
                details={
                    "round": round_number,
                    "requestedTools": sorted(requested_names),
                    "allowedTools": sorted(allowed_names),
                    "callCount": len(calls),
                    "approvalStatuses": approval_statuses,
                },
                duration_ms=_duration_ms(tool_started),
            )
            if outcome is ToolBatchOutcome.CANCELED:
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.CANCELED,
                    used_model,
                    round_number,
                    error_code=batch_result.error or "tool_execution_canceled",
                )
                return
            if outcome in {ToolBatchOutcome.FAILED, ToolBatchOutcome.REJECTED}:
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code=batch_result.error or "tool_execution_failed",
                )
                return

            if not _results_match_calls(calls, batch_result.results):
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code="invalid_tool_results",
                )
                return
            if scope_tools_to_observer and self._observer is not None:
                await self._observer.on_tool_round_completed(outcome)
            elif not scope_tools_to_observer:
                # Caller-owned REQUIRED applies to the initial selection. A
                # successful unscoped tool round must still leave room for the
                # model to consume its result and answer. Planned runs keep
                # the logical requirement active and advance via the observer.
                logical_required_tool_call_enabled = False
            yield AgentEvent(
                type=CoreEventType.TOOL_ROUND_COMPLETED,
                run_id=run_id,
                payload={"outcome": outcome.value},
            )
            messages.extend(_continuation_messages(
                calls,
                batch_result.results,
                content="" if require_tool else accumulator.content,
                thinking=accumulator.thinking,
            ))
            if outcome is ToolBatchOutcome.DECLINED:
                declined_response_pending = True
                messages.append(AgentMessage(
                    role=MessageRole.DEVELOPER,
                    content=_DECLINED_TOOL_GUIDANCE,
                ))

        yield _runtime_result(
            run_id,
            RuntimeOutcome.FAILED,
            used_model,
            self._limits.max_model_rounds,
            error_code="max_model_rounds",
        )

    def _allowed_names(
        self,
        tools: Sequence[ToolSchema],
        *,
        scope_tools_to_observer: bool,
    ) -> frozenset[str]:
        all_names = frozenset(schema.name for schema in tools)
        if not scope_tools_to_observer:
            return all_names
        if self._observer is None:
            return frozenset()
        return frozenset(self._observer.current_allowed_tool_names()) & all_names

    def _future_allowed_names(
        self,
        tools: Sequence[ToolSchema],
        *,
        scope_tools_to_observer: bool,
    ) -> frozenset[str]:
        if not scope_tools_to_observer or self._observer is None:
            return frozenset()
        all_names = frozenset(schema.name for schema in tools)
        return frozenset(self._observer.future_allowed_tool_names()) & all_names

    async def _trace(
        self,
        stage: str,
        outcome: str,
        *,
        details: dict | None = None,
        duration_ms: int | None = None,
    ) -> None:
        if self._observer is None:
            return
        await self._observer.record_trace(TraceRecord(
            stage=stage,
            outcome=outcome,
            details=details or {},
            duration_ms=duration_ms,
        ))


@dataclass(slots=True)
class _ToolCallParts:
    id: str = ""
    name: str = ""
    arguments: str = ""


class _ModelRoundAccumulator:
    def __init__(self) -> None:
        self.content = ""
        self.thinking = ""
        self.finish_reason: ModelFinishReason | None = None
        self._calls: dict[int, _ToolCallParts] = {}
        self._malformed_reason: str | None = None

    @property
    def tool_call_count(self) -> int:
        return len(self._calls)

    def add(self, chunk) -> None:
        self.content += chunk.content_delta
        self.thinking += chunk.thinking_delta
        if chunk.finish_reason is not None:
            self.finish_reason = chunk.finish_reason
        for delta in chunk.tool_call_deltas:
            current = self._calls.setdefault(delta.index, _ToolCallParts())
            if delta.id is not None:
                call_id = str(delta.id).strip()
                if current.id and call_id != current.id:
                    self._malformed_reason = "conflicting_tool_call_id_for_index"
                else:
                    current.id = call_id
            if delta.name is not None:
                name = str(delta.name).strip()
                if current.name and name != current.name:
                    self._malformed_reason = "conflicting_tool_name_for_index"
                else:
                    current.name = name
            current.arguments += str(delta.arguments_fragment or "")

    def tool_calls(self) -> tuple[tuple[ToolCall, ...], str | None]:
        if self._malformed_reason is not None:
            return (), self._malformed_reason
        if not self._calls:
            return (), None
        ordered = tuple(parts for _, parts in sorted(self._calls.items()))
        if any(not parts.id.strip() for parts in ordered):
            return (), "missing_tool_call_id"
        if any(not parts.name.strip() for parts in ordered):
            return (), "missing_tool_call_name"
        ids = tuple(parts.id for parts in ordered)
        if len(ids) != len(set(ids)):
            return (), "duplicate_tool_call_id"
        return (
            tuple(
                ToolCall(
                    id=parts.id,
                    name=parts.name,
                    arguments_json=parts.arguments,
                )
                for parts in ordered
            ),
            None,
        )


class _QueueEventSink:
    def __init__(self, queue: asyncio.Queue[AgentEvent]):
        self._queue = queue

    async def emit(self, event: AgentEvent) -> None:
        await self._queue.put(event)


async def _stream_tool_batch(
    gateway: ToolExecutionGateway,
    request: ToolBatchRequest,
    signal: CancellationSignal | None,
) -> AsyncIterator[AgentEvent | ToolBatchResult]:
    queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
    gateway_task = asyncio.create_task(
        gateway.execute_batch(request, _QueueEventSink(queue), signal)
    )
    progress_task: asyncio.Task[AgentEvent] | None = asyncio.create_task(queue.get())
    signal_task: asyncio.Task[bool] | None = (
        asyncio.create_task(signal.wait()) if signal is not None else None
    )
    gateway_terminal_selected = False
    try:
        while True:
            # A committed result/exception wins over same-tick progress and
            # cancellation. Progress already emitted by that gateway is still
            # drained before its terminal update.
            if gateway_task.done():
                gateway_terminal_selected = True
                terminal_result: ToolBatchResult | None = None
                terminal_error: BaseException | None = None
                try:
                    terminal_result = gateway_task.result()
                except BaseException as error:
                    terminal_error = error

                if progress_task is not None and progress_task.done():
                    try:
                        yield progress_task.result()
                    except BaseException:
                        pass
                    progress_task = None
                while not queue.empty():
                    yield queue.get_nowait()
                if terminal_error is not None:
                    raise terminal_error
                if terminal_result is None:
                    raise RuntimeError("tool gateway returned no result")
                yield terminal_result
                return

            # Progress wins over cancellation when both become observable in
            # the same event-loop turn.
            if progress_task is not None and progress_task.done():
                yield progress_task.result()
                progress_task = asyncio.create_task(queue.get())
                continue

            if _is_canceled(signal):
                # The gateway owns the authoritative tool outcome. A
                # cancellation-linearizable handler may already be committing;
                # after cancellation is forwarded, preserve a durable receipt
                # instead of unconditionally rewriting it as canceled.
                terminal_result = await _cancel_gateway_for_result(gateway_task)
                if terminal_result is None:
                    raise OperationCanceled("agent run was canceled")
                gateway_terminal_selected = True
                if progress_task is not None and progress_task.done():
                    try:
                        yield progress_task.result()
                    except BaseException:
                        pass
                    progress_task = None
                while not queue.empty():
                    yield queue.get_nowait()
                yield terminal_result
                return

            waiters: set[asyncio.Future[Any]] = {gateway_task}
            if progress_task is not None:
                waiters.add(progress_task)
            if signal_task is not None:
                waiters.add(signal_task)
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        # A caller cancellation is also forwarded to the gateway. If the
        # gateway suppresses it because a durable tool commit is already in
        # flight, finish delivering that authoritative receipt instead of
        # exposing a false non-commit to the caller.
        terminal_result = await _cancel_gateway_for_result(gateway_task)
        if terminal_result is None:
            raise
        gateway_terminal_selected = True
        if progress_task is not None and progress_task.done():
            try:
                yield progress_task.result()
            except BaseException:
                pass
            progress_task = None
        while not queue.empty():
            yield queue.get_nowait()
        yield terminal_result
        return
    finally:
        await _cancel_task_safely(progress_task)
        await _cancel_task_safely(signal_task)
        if not gateway_terminal_selected:
            await _cancel_task_safely(gateway_task)


async def _cancel_task_safely(task: asyncio.Future[Any] | None) -> None:
    """Cleanup must never replace the already selected runtime outcome."""

    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except BaseException:
        pass


async def _cancel_gateway_for_result(
    task: asyncio.Task[ToolBatchResult],
) -> ToolBatchResult | None:
    """Cancel a gateway but retain a commit-wins result if it returns one."""

    if not task.done():
        task.cancel()
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                return None
            if task.done():
                return task.result()
            task.cancel()
        except BaseException:
            # Once the signal branch has selected cancellation, cleanup
            # failures are private unless the gateway returns an authoritative
            # ToolBatchResult receipt.
            return None


async def _close_async_iterator(iterator: AsyncIterator) -> None:
    close = getattr(iterator, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except (GeneratorExit, StopAsyncIteration):
        pass
    except Exception:
        # Stream termination has already been classified by the runtime. A
        # provider cleanup failure must not replace that public outcome.
        pass


def _continuation_messages(
    calls: Sequence[ToolCall],
    results: Sequence[ToolCallResult],
    *,
    content: str,
    thinking: str,
) -> list[AgentMessage]:
    messages = [AgentMessage(
        role=MessageRole.ASSISTANT,
        content=content,
        thinking=thinking or None,
        tool_calls=tuple(calls),
        origin=MessageOrigin.MODEL,
    )]
    messages.extend(
        AgentMessage(
            role=MessageRole.TOOL,
            content=result.content,
            tool_call_id=result.tool_call_id,
            origin=MessageOrigin.HOST_TOOL_RESULT,
            host_metadata={"agent_core_tool_name": result.tool_name},
        )
        for result in results
    )
    return messages


def _is_textual_tool_call(content: str) -> bool:
    """Recognize textual markers that imitate an unavailable tool protocol.

    This deliberately does not parse or execute the markup.  The check is
    only used after an approval rejection, where the model is required to give
    a plain-language outcome and no protocol marker is valid.  Searching the
    buffered response fails closed for prefixes, Markdown fences and truncated
    envelopes without changing ordinary model streaming elsewhere.
    """

    return bool(_TEXTUAL_TOOL_PROTOCOL_MARKER.search(str(content or "")))


def _top_level_numbered_items(content: str) -> tuple[int, ...]:
    """Return column-zero Arabic list markers from one buffered response."""

    return tuple(
        int(match.group("number"))
        for match in _TOP_LEVEL_NUMBERED_ITEM.finditer(str(content or ""))
    )


def _exact_item_count_repair_guidance(expected_count: int) -> str:
    return (
        f"Rewrite the complete answer with exactly {expected_count} top-level "
        "items. Each item must "
        "start at column zero with consecutive Arabic markers "
        f"1. through {expected_count}. Use bullets, not numbered sublists, for "
        "details inside an item. Do not add another top-level table, list, "
        "appendix, or optional item."
    )


def _response_constraint_repair_guidance(
    requirements: Sequence[str],
) -> str:
    joined = "\n".join(
        f"- {str(requirement).strip()}"
        for requirement in requirements
        if str(requirement).strip()
    )
    return (
        "The preceding final response was withheld because it violated one "
        "or more host-owned response constraints. Rewrite the complete answer "
        "to satisfy every requirement below:\n"
        f"{joined}\n"
        "Preserve the user's requested language and all other host "
        "instructions. Do not make a tool call. Return the answer only."
    )


def _declined_final_response(messages: Sequence[AgentMessage]) -> str:
    user_text = next(
        (
            str(message.content or "")
            for message in reversed(messages)
            if message.role is MessageRole.USER
        ),
        "",
    )
    if _CJK_CHARACTER.search(user_text):
        return _DECLINED_FINAL_RESPONSE_ZH
    return _DECLINED_FINAL_RESPONSE_EN


def _future_step_retry_messages(
    calls: Sequence[ToolCall],
    *,
    current_allowed: frozenset[str],
    content: str,
    thinking: str,
) -> list[AgentMessage]:
    error_code = "tool_step_out_of_order"
    error_content = json.dumps(
        {
            "success": False,
            "errorCode": error_code,
            "batchExecuted": False,
            "retryable": True,
            "currentAllowed": sorted(current_allowed),
            "error": (
                "The whole batch was not executed because it included a tool "
                "from a future plan step."
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    messages = _continuation_messages(
        calls,
        tuple(
            ToolCallResult(
                tool_call_id=call.id,
                tool_name=call.name,
                content=error_content,
                error=error_code,
            )
            for call in calls
        ),
        content=content,
        thinking=thinking,
    )
    messages.append(AgentMessage(
        role=MessageRole.DEVELOPER,
        content=(
            "The preceding tool-call batch had zero execution. In the next "
            "round, the actual tool schema supplied with the invocation is the "
            "only authorized tool. Do not call a tool whose schema is absent."
        ),
    ))
    return messages


def _tool_call_payload(call: ToolCall) -> dict:
    return {
        "id": call.id,
        "name": call.name,
        "arguments_json": call.arguments_json,
    }


def _tool_result_payload(result: ToolCallResult) -> dict:
    payload = {
        "tool_call_id": result.tool_call_id,
        "tool_name": result.tool_name,
        "content": result.content,
        "from_cache": result.from_cache,
    }
    if result.approval_status is not None:
        payload["approval_status"] = result.approval_status.value
    if result.error is not None:
        payload["error"] = result.error
    return payload


def _results_match_calls(
    calls: Sequence[ToolCall],
    results: Sequence[ToolCallResult],
) -> bool:
    return [call.id for call in calls] == [result.tool_call_id for result in results]


def _context_budget_contract_error(
    request: AgentRunRequest,
    budget: ContextBudget | None,
    configured_tools: Sequence[ToolSchema],
) -> str | None:
    if budget is None:
        return None
    if (
        request.context_window is None
        or int(request.context_window) != budget.window_tokens
    ):
        return "context_budget_window_mismatch"
    if estimate_tool_schema_tokens(configured_tools) != budget.tool_schema_tokens:
        return "context_budget_tool_schema_mismatch"
    if budget.output_reserve_tokens <= 0:
        return "context_budget_output_reserve_invalid"
    return None


def _runtime_result(
    run_id: RunId | None,
    outcome: RuntimeOutcome,
    model: str,
    round_count: int,
    *,
    final_response: str = "",
    error_code: str | None = None,
) -> AgentRuntimeResult:
    return AgentRuntimeResult(
        run_id=run_id,
        outcome=outcome,
        final_response=final_response,
        model=model,
        round_count=round_count,
        error_code=error_code,
    )


def _duration_ms(started: float) -> int:
    return round((perf_counter() - started) * 1000)


def _is_canceled(signal: CancellationSignal | None) -> bool:
    return bool(signal is not None and signal.is_set())


def _is_retryable_stream_interruption(error: Exception) -> bool:
    return bool(
        isinstance(error, ModelGatewayError)
        and error.code == "upstream_stream_interrupted"
        and error.retryable
    )


def _root_error_type(error: Exception) -> str:
    cause = error.__cause__
    return type(cause if isinstance(cause, Exception) else error).__name__
