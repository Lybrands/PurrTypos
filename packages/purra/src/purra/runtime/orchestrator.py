"""Provider-neutral Agent model/tool loop orchestration.

This stage-3 runtime starts after the host has built and initially
budgeted context.  It owns model rounds, typed tool continuation, authorization,
round trimming, cancellation and the model-round limit. Host transports,
domain concepts and concrete tool handlers stay behind ports.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from time import perf_counter
from typing import Any, AsyncIterator, Mapping, Sequence

from purra.cancellation import (
    OperationCanceled,
    await_with_cancellation,
    is_canceled as _is_canceled,
)
from purra.context_budget import (
    estimate_agent_messages_tokens,
    estimate_tool_schema_tokens,
    trim_agent_messages_by_turn,
)
from purra.context_orchestration.ledger import (
    ContextCompactionBudget,
    ContextCompactionPhase,
)
from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    AgentRuntimeResult,
    ContextBudget,
    ExecutionState,
    MessageOrigin,
    MessageRole,
    ModelFinishReason,
    ModelInvocation,
    ModelTokenUsage,
    ReasoningMode,
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
    ToolContextContract,
    ToolPlanningDisposition,
    ToolSchema,
    TraceRecord,
)
from purra.errors import (
    ModelGatewayError,
    ResponseJudgeContractError,
    UnsupportedModelFeatureError,
)
from purra.events import AgentEvent, CoreEventType
from purra.evidence import RunEvidenceStore
from purra.host_planned_tool_gateway import (
    HOST_PLANNED_EXECUTION_ROUTE,
)
from purra.model_call_parameters import describe_model_call
from purra.model_protocol import classify_model_termination
from purra.output_budget import ResolvedOutputBudget
from purra.runtime_context import project_intermediate_tool_context
from purra.runtime.model_round import (
    ModelRoundAccumulator as _ModelRoundAccumulator,
    PendingProviderAttempt as _PendingProviderAttempt,
    is_reasoning_only_truncation, provider_retry_round_capacity,
    retry_provider_attempt, truncation_trace_details,
)
from purra.runtime.response_finalization import (
    declined_final_response as _declined_final_response,
    exact_item_count_repair_guidance as _exact_item_count_repair_guidance,
    failed_tool_final_response as _failed_tool_final_response,
    is_deferred_action_only_response as _is_deferred_action_only_response,
    is_textual_tool_call as _is_textual_tool_call,
    is_unstructured_tool_output as _is_unstructured_tool_output,
    response_constraint_repair_guidance as _response_constraint_repair_guidance,
    should_hold_potential_deferred_response as _should_hold_potential_deferred_response,
    top_level_numbered_items as _top_level_numbered_items,
)
from purra.runtime.tool_round import (
    close_async_iterator as _close_async_iterator,
    extend_progress_round_budget as _extend_progress_round_budget,
    results_match_calls as _results_match_calls,
    stream_tool_batch as _stream_tool_batch,
    tool_call_payload as _tool_call_payload,
    tool_result_payload as _tool_result_payload,
    tool_round_trace_details as _tool_round_trace_details,
)
from purra.timing import duration_ms as _duration_ms
from purra.recovery import (
    RecoveryAction,
    RecoveryCause,
    RecoveryDecision,
    RecoveryEffectState,
    RecoveryLedger,
    RecoveryPolicy,
    RecoveryReason,
    RecoveryRequest,
)
from purra.ports import (
    CancellationSignal,
    ConversationCompactor,
    EventSink,
    ModelGateway,
    ResponseJudge,
    ResponseValidator,
    RuntimeObserver,
    RuntimePlanningHook,
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
_FAILED_TOOL_OUTPUT_RETRY_GUIDANCE = (
    "The preceding recovery response imitated a tool call or dumped tool "
    "arguments as plain text. It was withheld because plain text cannot "
    "execute the failed tool. Give one concise plain-language summary in the "
    "user's language. State that the tool did not complete and do not emit, "
    "retry, or imitate a tool call or its JSON arguments."
)
_MISSING_REQUIRED_TOOL_CALL_RETRY_GUIDANCE = (
    "The preceding model round did not return the structured tool call required "
    "by the current approved plan step. That text was discarded and no tool was "
    "executed. Retry this step now by returning exactly one valid structured call "
    "to one of the tools currently exposed by the host. Do not describe, imitate, "
    "or wrap the call in ordinary text."
)
_MISSING_REQUIRED_TOOL_CALL_REPLAN_GUIDANCE = (
    "The model still omitted the required structured tool call after one retry. "
    "Treat the current tool step as failed and revise the remaining plan from "
    "the evidence already collected. Do not claim that the omitted tool ran, "
    "invent its result, or repeat an equivalent completed read step."
)
_UNAUTHORIZED_TOOL_REPLAN_GUIDANCE = (
    "The current plan step could not be completed because the model repeatedly "
    "selected a tool outside the host-authorized set. Replan this unexecuted "
    "step without weakening tool authorization."
)
_TOOL_INPUT_RETRY_GUIDANCE = (
    "The preceding tool call was rejected because its input did not satisfy "
    "the tool contract. The tool did not complete and produced no successful "
    "evidence. Correct the arguments from the structured error result and retry "
    "the same currently exposed tool exactly once. Return a real structured "
    "tool call, not a textual imitation, and do not invent a successful result."
)
_MALFORMED_TOOL_CALL_RETRY_GUIDANCE = (
    "The preceding structured tool-call envelope was malformed and was rejected "
    "before any tool handler ran. Retry the current step exactly once using the "
    "provider's native structured tool-call protocol. Include one stable call id, "
    "one currently exposed tool name, and one complete JSON object for arguments. "
    "Do not emit XML-like tool markup or an argument dump as ordinary text."
)
_TRUNCATED_TOOL_CALL_RETRY_GUIDANCE = (
    "The preceding model output reached its output limit while constructing a "
    "tool call. Core discarded the entire partial call and no tool was executed. "
    "Retry the current step once with a bounded structured payload. Return only "
    "the fields required for the current tool, do not duplicate the same result "
    "as explanatory prose, and use a host-provided batch or append capability if "
    "one is exposed."
)
_TRUNCATED_MODEL_OUTPUT_RETRY_GUIDANCE = (
    "The preceding model output reached its output limit and was discarded as "
    "incomplete. Retry the current step once with a bounded complete response. "
    "Do not repeat project state or other content that the host already supplied."
)
_EMPTY_RESPONSE_RETRY_GUIDANCE = (
    "Your preceding model round ended after internal reasoning without any "
    "user-visible response. Continue the task now. If more evidence is required, "
    "use one of the currently exposed tools through a valid structured call; "
    "otherwise provide a complete visible answer in the user's language. Do not "
    "return reasoning alone."
)
_DEFERRED_ACTION_RETRY_GUIDANCE = (
    "Your preceding response only announced work you intended to do later and "
    "did not deliver the result requested by the user. That incomplete response "
    "was withheld. Continue the task now: use an exposed tool through a valid "
    "structured call if evidence is still required, otherwise provide the "
    "complete answer in the user's language. Do not repeat a process "
    "announcement or promise a later response."
)
_RECOVERABLE_TOOL_INPUT_ERROR_CODES = frozenset({
    "duplicate_tool_call_id",
    "invalid_tool_arguments_json",
    "invalid_tool_arguments_schema",
    "invalid_tool_arguments_shape",
    "invalid_tool_arguments_type",
    "invalid_tool_arguments_value",
    "invalid_tool_call_id",
    "invalid_tool_name",
    "too_many_tool_calls",
    "tool_arguments_too_large",
    # Compatibility for gateways that already normalize preflight failures.
    # They must explicitly report NOT_STARTED before policy permits recovery.
    "tool_input_invalid",
})
class AgentRuntime:
    def __init__(
        self,
        *,
        model_gateway: ModelGateway,
        tool_execution_gateway: ToolExecutionGateway | None = None,
        observer: RuntimeObserver | None = None,
        context_compressor: ConversationCompactor | None = None,
        limits: RuntimeLimits = RuntimeLimits(),
        recovery_policy: RecoveryPolicy = RecoveryPolicy(),
    ):
        self._model_gateway = model_gateway
        self._tool_execution_gateway = tool_execution_gateway
        self._observer = observer
        self._context_compressor = context_compressor
        self._limits = limits
        self._recovery_policy = recovery_policy

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
        output_budget: ResolvedOutputBudget | None = None,
        round_input_tokens: int | None = None,
        scope_tools_to_observer: bool = True,
        force_tool_choice: bool = False,
        reasoning_mode: ReasoningMode = ReasoningMode.DEFAULT,
        require_tool_call: bool | None = None,
        tools_executable: bool = True,
        planning_hook: RuntimePlanningHook | None = None,
        tool_context_contracts: Mapping[str, ToolContextContract] | None = None,
        stage_context_projection_enabled: bool = False,
        signal: CancellationSignal | None = None,
    ) -> AsyncIterator[RuntimeUpdate]:
        messages = list(request.messages)
        if not request.model.protocol_capabilities.reasoning_mode_is_supported(
            reasoning_mode
        ):
            yield _runtime_result(
                run_id,
                RuntimeOutcome.FAILED,
                request.model.model,
                0,
                error_code="unsupported_reasoning_selection",
            )
            return
        validators = tuple(response_validators)
        judges = tuple(response_judges)
        state = execution_state or ExecutionState()
        evidence_store = RunEvidenceStore()
        context_receipts = evidence_store.record_context_messages(messages)
        if context_receipts:
            await self._trace(
                "context_evidence",
                "recorded",
                details={
                    "receiptCount": len(context_receipts),
                    "storyReceiptCount": sum(
                        receipt.source == "story_state"
                        for receipt in context_receipts
                    ),
                    "semanticReceiptCount": sum(
                        receipt.source == "semantic"
                        for receipt in context_receipts
                    ),
                },
            )
        context_contracts = dict(tool_context_contracts or {})
        configured_tools = tuple(tools) if request.tools_enabled else ()
        tool_display_names = {
            schema.name: schema.display_names
            for schema in configured_tools
            if schema.display_names
        }
        used_model = request.model.model
        # A provider-level REQUIRED hint can never weaken the host guard.
        # ``require_tool_call=True`` also keeps that guard when the provider
        # capability cache already requires AUTO transport.
        logical_required_tool_call_enabled = bool(
            force_tool_choice or require_tool_call
        )
        provider_required_tool_choice_enabled = bool(force_tool_choice)
        recovery_ledger = RecoveryLedger(self._recovery_policy)
        declined_response_pending = False
        response_repair_pending = False
        pending_provider_attempt: _PendingProviderAttempt | None = None
        logical_round_number = 0
        dynamic_replan_pending = False
        last_tool_outcome = ToolBatchOutcome.COMPLETED
        pending_recovery_error_code: str | None = None
        failed_tool_recovery_error_code: str | None = None
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
        round_limit = self._limits.max_model_rounds
        progress_rounds = 0
        # Tool-input repair is bounded per contiguous invalid-input sequence,
        # not once for the entire Run. A successful tool round starts a new
        # sequence so a later, independent schema mistake can be corrected
        # without granting repeated retries to the same invalid call.
        tool_input_recovery_epoch = 0

        def remaining_model_rounds(completed_rounds: int) -> int:
            return max(0, round_limit - int(completed_rounds))

        absolute_round_limit = self._limits.max_model_rounds + (
            self._limits.max_progress_rounds
            + provider_retry_round_capacity(self._recovery_policy)
        )

        for round_index in range(absolute_round_limit):
            round_number = round_index + 1
            if round_index >= round_limit:
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_index,
                    error_code="max_model_rounds",
                )
                return
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
                if dynamic_replan_pending and planning_hook is not None:
                    replanning_started = perf_counter()
                    try:
                        revised_guidance = await planning_hook.replan_after_tool(
                            evidence_store.project_messages_for_planning(messages),
                            round_number=round_number,
                            remaining_model_rounds=remaining_model_rounds(
                                round_index
                            ),
                            outcome=last_tool_outcome,
                            signal=signal,
                        )
                        if revised_guidance is not None:
                            messages.append(revised_guidance)
                    except OperationCanceled:
                        await self._trace(
                            "planning",
                            "canceled",
                            details={
                                "dynamic": True,
                                "round": round_number,
                            },
                            duration_ms=_duration_ms(replanning_started),
                        )
                        yield _runtime_result(
                            run_id,
                            RuntimeOutcome.CANCELED,
                            used_model,
                            round_index,
                            error_code="request_canceled",
                        )
                        return
                    except Exception as error:
                        primary_error_code = pending_recovery_error_code
                        await self._trace(
                            "planning",
                            "failed",
                            details={
                                "dynamic": True,
                                "round": round_number,
                                "errorType": _root_error_type(error),
                                "primaryErrorCode": primary_error_code,
                                "recoveryErrorCode": "dynamic_planning_failed",
                            },
                            duration_ms=_duration_ms(replanning_started),
                        )
                        yield _runtime_result(
                            run_id,
                            RuntimeOutcome.FAILED,
                            used_model,
                            round_index,
                            error_code=(
                                primary_error_code
                                or "dynamic_planning_failed"
                            ),
                        )
                        return
                    dynamic_replan_pending = False
                    pending_recovery_error_code = None
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
                    or failed_tool_recovery_error_code is not None
                    or response_constraints.exact_top_level_item_count is not None
                    or validators
                    or judges
                )
                projection = project_intermediate_tool_context(
                    messages,
                    visible_tool_names=frozenset(
                        schema.name for schema in visible_tools
                    ),
                    contracts=context_contracts,
                    evidence_store=evidence_store,
                    enabled=stage_context_projection_enabled,
                    initial_round=initial_logical_round,
                    token_budget=active_token_budget,
                )
                canonical_tokens = estimate_agent_messages_tokens(messages)
                projected_tokens = estimate_agent_messages_tokens(
                    projection.messages
                )
                compression_outcome: str | None = None
                compression_strategy: str | None = None
                if (
                    active_token_budget is not None
                    and self._context_compressor is not None
                ):
                    runtime_request = replace(
                        request,
                        messages=projection.messages,
                        metadata={
                            **request.metadata,
                            "contextCompressionScope": "runtime",
                            "runtimeLogicalRound": logical_round_number + 1,
                        },
                    )
                    compressed = await await_with_cancellation(
                        self._context_compressor.prepare(
                            runtime_request,
                            signal,
                            budget=ContextCompactionBudget(
                                phase=ContextCompactionPhase.MODEL_CALL,
                                provider_input_tokens=active_token_budget,
                                context_tokens=0,
                                context_tokens_are_resolved=True,
                                output_reserve_tokens=(
                                    context_budget.output_reserve_tokens
                                    if context_budget is not None
                                    else 1
                                ),
                            ),
                        ),
                        signal,
                    )
                    round_context_messages = compressed.request.messages
                    sent_tokens = estimate_agent_messages_tokens(
                        round_context_messages
                    )
                    dropped_messages = max(
                        0,
                        len(projection.messages)
                        - len(round_context_messages),
                    )
                    overflow_tokens = max(
                        0,
                        sent_tokens - active_token_budget,
                    )
                    compression_outcome = compressed.outcome
                    compression_strategy = str(
                        compressed.diagnostics.get("strategy") or ""
                    )
                elif active_token_budget is not None:
                    trimmed = trim_agent_messages_by_turn(
                        projection.messages,
                        active_token_budget,
                    )
                    round_context_messages = trimmed.messages
                    sent_tokens = trimmed.token_estimate
                    dropped_messages = trimmed.dropped_count
                    overflow_tokens = trimmed.overflow_tokens
                else:
                    round_context_messages = projection.messages
                    sent_tokens = projected_tokens
                    dropped_messages = 0
                    overflow_tokens = 0
                if (
                    projection.dropped_context_blocks
                    or projection.compacted_tool_results
                ):
                    await self._trace(
                        "context_projection",
                        projection.mode,
                        details={
                            "round": round_number,
                            "toolNames": sorted(
                                schema.name for schema in visible_tools
                            ),
                            "droppedContextBlocks": list(
                                projection.dropped_context_blocks
                            ),
                            "compactedToolResults": list(
                                projection.compacted_tool_results
                            ),
                            "savedTokens": projection.saved_tokens,
                            "targetTokens": projection.target_tokens,
                            "canonicalTokens": canonical_tokens,
                            "projectedTokens": projected_tokens,
                        },
                    )
                runtime_budget_outcome = (
                    "overflow"
                    if overflow_tokens
                    else "rebalanced"
                    if (
                        projection.saved_tokens > 0
                        or dropped_messages > 0
                    )
                    else "within_budget"
                )
                await self._trace(
                    "runtime_context_budget",
                    runtime_budget_outcome,
                    details={
                        "round": round_number,
                        "logicalRound": logical_round_number + 1,
                        "initialRound": initial_logical_round,
                        "tokenBudget": active_token_budget,
                        "canonicalTokens": canonical_tokens,
                        "projectedTokens": projected_tokens,
                        "sentTokens": sent_tokens,
                        "pressureRatio": (
                            round(
                                sent_tokens / active_token_budget,
                                4,
                            )
                            if active_token_budget
                            else None
                        ),
                        "projectionMode": projection.mode,
                        "projectionSavedTokens": projection.saved_tokens,
                        "compressionOutcome": compression_outcome,
                        "compressionStrategy": compression_strategy,
                        "droppedMessages": dropped_messages,
                        "overflowTokens": overflow_tokens,
                        "completeEvidenceTokens": evidence_store.token_estimate,
                    },
                )
                if overflow_tokens > 0:
                    overflow_outcome = (
                        "overflow_initial"
                        if initial_logical_round
                        else "overflow_after_tool"
                    )
                    await self._trace(
                        "context_budget",
                        overflow_outcome,
                        details={
                            "round": round_number,
                            "tokenBudget": active_token_budget,
                            "canonicalTokens": canonical_tokens,
                            "projectedTokens": projected_tokens,
                            "sentTokens": sent_tokens,
                            "projectionMode": projection.mode,
                            "projectionSavedTokens": (
                                projection.saved_tokens
                            ),
                            "overflowTokens": overflow_tokens,
                        },
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
                logical_round_number += 1
                provider_attempt = _PendingProviderAttempt(
                    messages=round_context_messages,
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
                        output_budget=output_budget,
                        reasoning_mode=reasoning_mode,
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
            direct_content_released = False
            invocation_parameters = describe_model_call(
                self._model_gateway,
                round_messages,
                invocation,
            )
            host_planned_dispatch = (
                invocation_parameters.get("executionRoute")
                == HOST_PLANNED_EXECUTION_ROUTE
            )
            yield AgentEvent(
                type=(
                    CoreEventType.HOST_PLANNED_TOOL_DISPATCHED
                    if host_planned_dispatch
                    else CoreEventType.MODEL_CALL_RECORDED
                ),
                run_id=run_id,
                payload={
                    "phase": "generation",
                    "count": 0 if host_planned_dispatch else 1,
                    "toolNames": [
                        schema.name for schema in invocation.tools
                    ],
                    "toolChoice": invocation.tool_choice.value,
                    "round": round_number,
                    "logicalRound": provider_attempt.logical_round,
                    "attempt": provider_attempt.attempt,
                    "parameters": invocation_parameters,
                },
            )
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
                fallback_decision: RecoveryDecision | None = None
                if invocation.tool_choice is ToolChoiceMode.REQUIRED:
                    fallback_decision = await self._decide_recovery(
                        recovery_ledger,
                        RecoveryRequest(
                            cause=(
                                RecoveryCause.PROVIDER_REQUIRED_TOOL_CHOICE_UNSUPPORTED
                            ),
                            action=RecoveryAction.FALLBACK_PROVIDER_MODE,
                            remaining_model_rounds=remaining_model_rounds(
                                round_number
                            ),
                            cancellation_requested=_is_canceled(signal),
                        ),
                        round_number=round_number,
                    )
                can_fallback = bool(
                    fallback_decision is not None
                    and fallback_decision.allowed
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
                        "errorChainTypes": _error_chain_types(error),
                    },
                    duration_ms=_duration_ms(model_started),
                )
                if can_fallback:
                    round_limit += 1
                    fallback_invocation = ModelInvocation(
                        request=invocation.request,
                        tools=invocation.tools,
                        tool_choice=ToolChoiceMode.AUTO,
                        max_output_tokens=invocation.max_output_tokens,
                        output_budget=invocation.output_budget,
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
                retry_scheduled = False
                if _is_retryable_stream_interruption(error):
                    retry_scheduled = (
                        await self._decide_recovery(
                            recovery_ledger,
                            RecoveryRequest(
                                cause=RecoveryCause.PROVIDER_STREAM_INTERRUPTED,
                                action=RecoveryAction.RETRY_MODEL,
                                remaining_model_rounds=remaining_model_rounds(
                                    round_number
                                ),
                                cancellation_requested=_is_canceled(signal),
                            ),
                            round_number=round_number,
                        )
                    ).allowed
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
                        "errorChainTypes": _error_chain_types(error),
                    },
                    duration_ms=_duration_ms(model_started),
                )
                if retry_scheduled:
                    round_limit += 1
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
                    if chunk.reasoning_delta:
                        emitted_delta_count += 1
                        yield AgentEvent(
                            type=CoreEventType.MODEL_REASONING_DELTA,
                            run_id=run_id,
                            payload={"delta": chunk.reasoning_delta},
                        )
                    if (
                        chunk.content_delta
                        and not require_tool
                        and not buffer_model_content
                        and not invocation.tools
                    ):
                        should_hold = bool(
                            not direct_content_released
                            and _should_hold_potential_deferred_response(
                                accumulator.content
                            )
                        )
                        if not should_hold:
                            visible_delta = chunk.content_delta
                            if not direct_content_released:
                                visible_delta = accumulator.content
                                direct_content_released = True
                            if self._observer is not None:
                                await self._observer.on_model_delta()
                            emitted_delta_count += 1
                            yield AgentEvent(
                                type=CoreEventType.ASSISTANT_FINAL_DELTA,
                                run_id=run_id,
                                payload={"delta": visible_delta},
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
                retry_scheduled = False
                if _is_retryable_stream_interruption(stream_error):
                    retry_scheduled = (
                        await self._decide_recovery(
                            recovery_ledger,
                            RecoveryRequest(
                                cause=RecoveryCause.PROVIDER_STREAM_INTERRUPTED,
                                action=RecoveryAction.RETRY_MODEL,
                                remaining_model_rounds=remaining_model_rounds(
                                    round_number
                                ),
                                retryable=(
                                    accumulator.finish_reason is None
                                ),
                                cancellation_requested=_is_canceled(signal),
                                visible_output_emitted=direct_content_released,
                            ),
                            round_number=round_number,
                        )
                    ).allowed
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
                        "errorChainTypes": _error_chain_types(stream_error),
                    },
                    duration_ms=_duration_ms(model_started),
                )
                if retry_scheduled:
                    round_limit += 1
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
            local_input_estimate = (
                estimate_agent_messages_tokens(round_messages)
                + estimate_tool_schema_tokens(invocation.tools)
            )
            if accumulator.usage is not None:
                usage = accumulator.usage
                await self._trace(
                    "model_usage",
                    "provider_reported",
                    details={
                        "round": round_number,
                        "attempt": provider_attempt.attempt,
                        "logicalRound": provider_attempt.logical_round,
                        "actualInputTokens": usage.input_tokens,
                        "actualOutputTokens": usage.output_tokens,
                        "actualTotalTokens": usage.total_tokens,
                        "cachedInputTokens": usage.cached_input_tokens,
                        "reasoningOutputTokens": (
                            usage.reasoning_output_tokens
                        ),
                        "requestedOutputTokens": invocation.max_output_tokens,
                        "finishReason": (
                            finish_reason.value
                            if finish_reason is not None
                            else None
                        ),
                        "outputBudget": (
                            invocation.output_budget.to_mapping()
                            if invocation.output_budget is not None
                            else None
                        ),
                        "localInputEstimate": local_input_estimate,
                    },
                )
                # Later tool rounds can contain transient EvidenceStore
                # projections that are not retained by the conversation.
                # Only the first logical request is a valid UI anchor.
                if provider_attempt.logical_round == 1:
                    yield AgentEvent(
                        type=CoreEventType.CONTEXT_USAGE_RECORDED,
                        run_id=run_id,
                        payload={
                            "actualInputTokens": usage.input_tokens,
                            "actualOutputTokens": usage.output_tokens,
                            "actualTotalTokens": usage.total_tokens,
                            "cachedInputTokens": (
                                usage.cached_input_tokens
                            ),
                            "reasoningOutputTokens": (
                                usage.reasoning_output_tokens
                            ),
                            "actualUsageRound": (
                                provider_attempt.logical_round
                            ),
                            "inputTokenEstimateAtUsage": (
                                local_input_estimate
                            ),
                            "usageSource": "provider",
                            "requestedOutputTokens": (
                                invocation.max_output_tokens
                            ),
                            "finishReason": (
                                finish_reason.value
                                if finish_reason is not None
                                else None
                            ),
                            "outputBudget": (
                                invocation.output_budget.to_mapping()
                                if invocation.output_budget is not None
                                else None
                            ),
                        },
                    )
            await self._trace(
                "tool_dispatch" if host_planned_dispatch else "model_round",
                (
                    "host_planned_call"
                    if host_planned_dispatch
                    else (
                        finish_reason.value
                        if finish_reason is not None
                        else "stream_end"
                    )
                ),
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

            # A provider-declared output limit is never a commit boundary.  It
            # may arrive after ids, names, and syntactically valid-looking
            # argument fragments, but the provider has explicitly declared the
            # generation incomplete.  Classify this before malformed-call or
            # tool-input handling so truncation remains the primary cause and
            # no partial assistant/tool continuation can pollute the next round.
            if finish_reason is None:  # Defensive; stream handling rejects this.
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code="upstream_stream_interrupted",
                )
                return
            termination = classify_model_termination(
                finish_reason,
                tool_call_count=accumulator.tool_call_count,
            )
            if termination.incomplete:
                error_code = termination.error_code or "model_output_truncated"
                reasoning_only_truncation = is_reasoning_only_truncation(
                    accumulator, invocation, error_code
                )
                truncation_decision = await self._decide_recovery(
                    recovery_ledger,
                    RecoveryRequest(
                        cause=RecoveryCause.MODEL_OUTPUT_TRUNCATED,
                        action=(
                            RecoveryAction.RETRY_MODEL
                        ),
                        remaining_model_rounds=remaining_model_rounds(
                            round_number
                        ),
                        retryable=(
                            reasoning_only_truncation
                            or termination.retryable and output_budget is None
                        ),
                        cancellation_requested=_is_canceled(signal),
                        visible_output_emitted=direct_content_released,
                    ),
                    round_number=round_number,
                )
                can_retry = truncation_decision.allowed
                await self._trace(
                    "model_output",
                    (
                        "truncated_reasoning_retry"
                        if can_retry and reasoning_only_truncation
                        else "truncated_retry"
                        if can_retry
                        else "truncated"
                    ),
                    details=truncation_trace_details(
                        accumulator=accumulator,
                        round_number=round_number,
                        finish_reason=finish_reason,
                        error_code=error_code,
                        can_retry=can_retry,
                        retry_used=(
                            truncation_decision.attempt > 1
                            or truncation_decision.reason_code
                            is RecoveryReason.ATTEMPT_BUDGET_EXHAUSTED
                        ),
                        reasoning_only=reasoning_only_truncation,
                        emitted_delta_count=emitted_delta_count,
                        output_budget=output_budget,
                    ),
                )
                if can_retry:
                    if reasoning_only_truncation:
                        round_limit += 1
                        pending_provider_attempt = retry_provider_attempt(
                            provider_attempt
                        )
                        continue
                    messages.append(AgentMessage(
                        role=MessageRole.DEVELOPER,
                        content=(
                            _TRUNCATED_TOOL_CALL_RETRY_GUIDANCE
                            if accumulator.tool_call_count
                            else _TRUNCATED_MODEL_OUTPUT_RETRY_GUIDANCE
                        ),
                    ))
                    continue
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code=error_code,
                )
                return

            if malformed_call_error is not None:
                malformed_decision = await self._decide_recovery(
                    recovery_ledger,
                    RecoveryRequest(
                        cause=RecoveryCause.MALFORMED_TOOL_CALL_BATCH,
                        action=RecoveryAction.RETRY_MODEL,
                        remaining_model_rounds=remaining_model_rounds(
                            round_number
                        ),
                        cancellation_requested=_is_canceled(signal),
                        visible_output_emitted=direct_content_released,
                    ),
                    round_number=round_number,
                    details={"protocolReason": malformed_call_error},
                )
                await self._trace(
                    "tool_authorization",
                    (
                        "malformed_batch_retry"
                        if malformed_decision.allowed
                        else "malformed_batch"
                    ),
                    details={
                        "round": round_number,
                        "callCount": accumulator.tool_call_count,
                        "reason": malformed_call_error,
                        "retryScheduled": malformed_decision.allowed,
                    },
                )
                if malformed_decision.allowed:
                    messages.append(AgentMessage(
                        role=MessageRole.DEVELOPER,
                        content=_MALFORMED_TOOL_CALL_RETRY_GUIDANCE,
                    ))
                    continue
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code="malformed_tool_call_batch",
                )
                return

            tool_finish = termination.authorizes_tool_calls
            if require_tool and not calls:
                retry_decision = await self._decide_recovery(
                    recovery_ledger,
                    RecoveryRequest(
                        cause=RecoveryCause.MISSING_REQUIRED_TOOL_CALL,
                        action=RecoveryAction.RETRY_MODEL,
                        remaining_model_rounds=remaining_model_rounds(
                            round_number
                        ),
                        minimum_remaining_rounds=2,
                        cancellation_requested=_is_canceled(signal),
                        visible_output_emitted=direct_content_released,
                    ),
                    round_number=round_number,
                )
                can_retry = retry_decision.allowed
                replan_decision: RecoveryDecision | None = None
                if not can_retry and planning_hook is not None:
                    replan_decision = await self._decide_recovery(
                        recovery_ledger,
                        RecoveryRequest(
                            cause=(
                                RecoveryCause.MISSING_REQUIRED_TOOL_CALL_REPLAN
                            ),
                            action=RecoveryAction.REPLAN,
                            remaining_model_rounds=remaining_model_rounds(
                                round_number
                            ),
                            cancellation_requested=_is_canceled(signal),
                            visible_output_emitted=direct_content_released,
                        ),
                        round_number=round_number,
                    )
                can_replan = bool(
                    replan_decision is not None and replan_decision.allowed
                )
                await self._trace(
                    "tool_round",
                    (
                        "missing_required_call_retry"
                        if can_retry
                        else (
                            "missing_required_call_replan"
                            if can_replan
                            else "missing_required_call"
                        )
                    ),
                    details={
                        "round": round_number,
                        "retryUsed": (
                            retry_decision.attempt > 1
                            or (
                                not retry_decision.allowed
                                and retry_decision.attempt > 0
                            )
                        ),
                        "retryAttempt": (
                            retry_decision.attempt if can_retry else 0
                        ),
                        "replanUsed": bool(
                            replan_decision is not None
                            and (
                                replan_decision.attempt > 1
                                or (
                                    not replan_decision.allowed
                                    and replan_decision.attempt > 0
                                )
                            )
                        ),
                        "replanScheduled": can_replan,
                        "roundsRemaining": remaining_model_rounds(
                            round_number
                        ),
                        "batchExecuted": False,
                    },
                )
                if can_retry:
                    messages.append(AgentMessage(
                        role=MessageRole.DEVELOPER,
                        content=_MISSING_REQUIRED_TOOL_CALL_RETRY_GUIDANCE,
                    ))
                    continue
                if can_replan:
                    last_tool_outcome = ToolBatchOutcome.FAILED
                    pending_recovery_error_code = "missing_required_tool_call"
                    failed_tool_recovery_error_code = (
                        "missing_required_tool_call"
                    )
                    dynamic_replan_pending = True
                    messages.append(AgentMessage(
                        role=MessageRole.DEVELOPER,
                        content=_MISSING_REQUIRED_TOOL_CALL_REPLAN_GUIDANCE,
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
                    textual_decision = await self._decide_recovery(
                        recovery_ledger,
                        RecoveryRequest(
                            cause=RecoveryCause.UNSTRUCTURED_TOOL_PROTOCOL,
                            action=RecoveryAction.RETRY_MODEL,
                            remaining_model_rounds=remaining_model_rounds(
                                round_number
                            ),
                            cancellation_requested=_is_canceled(signal),
                            visible_output_emitted=direct_content_released,
                        ),
                        round_number=round_number,
                    )
                    can_retry = textual_decision.allowed
                    await self._trace(
                        "model_output",
                        (
                            "textual_tool_call_retry"
                            if can_retry
                            else "textual_tool_call_rejected"
                        ),
                        details={
                            "round": round_number,
                            "retryUsed": (
                                textual_decision.attempt > 1
                                or textual_decision.reason_code
                                is RecoveryReason.ATTEMPT_BUDGET_EXHAUSTED
                            ),
                        },
                    )
                    if can_retry:
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

                if (
                    failed_tool_recovery_error_code is not None
                    and _is_unstructured_tool_output(accumulator.content)
                ):
                    textual_decision = await self._decide_recovery(
                        recovery_ledger,
                        RecoveryRequest(
                            cause=RecoveryCause.UNSTRUCTURED_TOOL_PROTOCOL,
                            action=RecoveryAction.RETRY_MODEL,
                            remaining_model_rounds=remaining_model_rounds(
                                round_number
                            ),
                            cancellation_requested=_is_canceled(signal),
                            visible_output_emitted=direct_content_released,
                        ),
                        round_number=round_number,
                    )
                    can_retry = textual_decision.allowed
                    await self._trace(
                        "model_output",
                        (
                            "unstructured_tool_output_retry"
                            if can_retry
                            else "unstructured_tool_output_replaced"
                        ),
                        details={
                            "round": round_number,
                            "retryUsed": (
                                textual_decision.attempt > 1
                                or textual_decision.reason_code
                                is RecoveryReason.ATTEMPT_BUDGET_EXHAUSTED
                            ),
                            "primaryErrorCode": (
                                failed_tool_recovery_error_code
                            ),
                        },
                    )
                    if can_retry:
                        messages.append(AgentMessage(
                            role=MessageRole.DEVELOPER,
                            content=_FAILED_TOOL_OUTPUT_RETRY_GUIDANCE,
                        ))
                        continue
                    final_response = _failed_tool_final_response(
                        request.messages
                    )
                    if self._observer is not None:
                        await self._observer.on_model_delta()
                    yield AgentEvent(
                        type=CoreEventType.ASSISTANT_FINAL_DELTA,
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

                if (
                    not declined_response_pending
                    and not accumulator.content.strip()
                ):
                    empty_retry_count = recovery_ledger.attempts(
                        RecoveryCause.EMPTY_MODEL_RESPONSE
                    )
                    empty_decision = await self._decide_recovery(
                        recovery_ledger,
                        RecoveryRequest(
                            cause=RecoveryCause.EMPTY_MODEL_RESPONSE,
                            action=RecoveryAction.RETRY_MODEL,
                            remaining_model_rounds=remaining_model_rounds(
                                round_number
                            ),
                            cancellation_requested=_is_canceled(signal),
                            visible_output_emitted=direct_content_released,
                        ),
                        round_number=round_number,
                    )
                    can_retry = empty_decision.allowed
                    await self._trace(
                        "model_output",
                        (
                            "empty_response_retry"
                            if can_retry
                            else "empty_response_rejected"
                        ),
                        details={
                            "round": round_number,
                            "retryCount": empty_retry_count,
                            "retryScheduled": can_retry,
                            "reasoningCharacters": len(
                                accumulator.reasoning or ""
                            ),
                        },
                    )
                    if can_retry:
                        messages.extend((
                            AgentMessage(
                                role=MessageRole.ASSISTANT,
                                content="",
                                reasoning=accumulator.reasoning or None,
                            ),
                            AgentMessage(
                                role=MessageRole.DEVELOPER,
                                content=_EMPTY_RESPONSE_RETRY_GUIDANCE,
                            ),
                        ))
                        continue
                    yield _runtime_result(
                        run_id,
                        RuntimeOutcome.FAILED,
                        used_model,
                        round_number,
                        error_code="empty_model_response",
                    )
                    return

                if (
                    not declined_response_pending
                    and _is_deferred_action_only_response(accumulator.content)
                ):
                    deferred_retry_count = recovery_ledger.attempts(
                        RecoveryCause.DEFERRED_MODEL_RESPONSE
                    )
                    deferred_decision = await self._decide_recovery(
                        recovery_ledger,
                        RecoveryRequest(
                            cause=RecoveryCause.DEFERRED_MODEL_RESPONSE,
                            action=RecoveryAction.RETRY_MODEL,
                            remaining_model_rounds=remaining_model_rounds(
                                round_number
                            ),
                            cancellation_requested=_is_canceled(signal),
                            visible_output_emitted=direct_content_released,
                        ),
                        round_number=round_number,
                    )
                    can_retry = deferred_decision.allowed
                    await self._trace(
                        "model_output",
                        (
                            "deferred_action_retry"
                            if can_retry
                            else "deferred_action_rejected"
                        ),
                        details={
                            "round": round_number,
                            "retryCount": deferred_retry_count,
                            "retryScheduled": can_retry,
                            "responseCharacters": len(accumulator.content),
                        },
                    )
                    if can_retry:
                        messages.extend((
                            AgentMessage(
                                role=MessageRole.ASSISTANT,
                                content=accumulator.content,
                                reasoning=accumulator.reasoning or None,
                            ),
                            AgentMessage(
                                role=MessageRole.DEVELOPER,
                                content=_DEFERRED_ACTION_RETRY_GUIDANCE,
                            ),
                        ))
                        continue
                    yield _runtime_result(
                        run_id,
                        RuntimeOutcome.FAILED,
                        used_model,
                        round_number,
                        error_code="incomplete_model_response",
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
                            yield AgentEvent(
                                type=CoreEventType.MODEL_CALL_RECORDED,
                                run_id=run_id,
                                payload={
                                    "phase": "response_judge",
                                    "count": 1,
                                    "toolNames": [],
                                    "toolChoice": "none",
                                    "round": round_number,
                                    "judgeIndex": judge_index,
                                },
                            )
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
                        repair_cause = (
                            RecoveryCause.RESPONSE_CONSTRAINT_SEMANTIC
                            if repair_phase == "semantic"
                            else RecoveryCause.RESPONSE_CONSTRAINT_DETERMINISTIC
                        )
                        repair_phases_used = tuple(
                            phase
                            for phase, cause in (
                                (
                                    "deterministic",
                                    RecoveryCause.RESPONSE_CONSTRAINT_DETERMINISTIC,
                                ),
                                (
                                    "semantic",
                                    RecoveryCause.RESPONSE_CONSTRAINT_SEMANTIC,
                                ),
                            )
                            if recovery_ledger.attempts(cause) > 0
                        )
                        phase_retry_used = (
                            recovery_ledger.attempts(repair_cause) > 0
                        )
                        repair_decision = await self._decide_recovery(
                            recovery_ledger,
                            RecoveryRequest(
                                cause=repair_cause,
                                action=RecoveryAction.RETRY_MODEL,
                                remaining_model_rounds=remaining_model_rounds(
                                    round_number
                                ),
                                retryable=len(repair_phases_used) < 2,
                                cancellation_requested=_is_canceled(signal),
                                visible_output_emitted=direct_content_released,
                            ),
                            round_number=round_number,
                        )
                        can_retry = repair_decision.allowed
                        trace_details: dict[str, Any] = {
                            "round": round_number,
                            "violationCodes": violation_codes,
                            "retryUsed": phase_retry_used,
                            "repairPhase": repair_phase,
                            "repairAttempts": len(repair_phases_used),
                            "repairPhasesUsed": sorted(repair_phases_used),
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
                            response_repair_pending = True
                            messages.extend((
                                AgentMessage(
                                    role=MessageRole.ASSISTANT,
                                    content=accumulator.content,
                                    reasoning=accumulator.reasoning or None,
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
                        type=CoreEventType.ASSISTANT_FINAL_DELTA,
                        run_id=run_id,
                        payload={"delta": final_response},
                    )
                elif not direct_content_released and final_response:
                    if self._observer is not None:
                        await self._observer.on_model_delta()
                    yield AgentEvent(
                        type=CoreEventType.ASSISTANT_FINAL_DELTA,
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
                future_decision = await self._decide_recovery(
                    recovery_ledger,
                    RecoveryRequest(
                        cause=RecoveryCause.FUTURE_TOOL_STEP,
                        action=RecoveryAction.RETRY_MODEL,
                        # A later plan step gets its own correction budget.
                        # Repeating the same out-of-order jump while the same
                        # current tool set is authorized still fails closed.
                        scope=_tool_authorization_recovery_scope(allowed_names),
                        remaining_model_rounds=remaining_model_rounds(
                            round_number
                        ),
                        minimum_remaining_rounds=2,
                        retryable=bool(allowed_names),
                        cancellation_requested=_is_canceled(signal),
                        visible_output_emitted=direct_content_released,
                    ),
                    round_number=round_number,
                )
                can_retry = future_decision.allowed
                if can_retry:
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
                        reasoning=accumulator.reasoning,
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
                        "retryAlreadyUsed": (
                            future_decision.attempt > 0
                            and not future_decision.allowed
                        ),
                        "roundsAvailable": (
                            future_decision.remaining_model_rounds >= 2
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
                unauthorized_decision = await self._decide_recovery(
                    recovery_ledger,
                    RecoveryRequest(
                        cause=RecoveryCause.UNAUTHORIZED_TOOL,
                        action=RecoveryAction.RETRY_MODEL,
                        # One model correction is allowed for each distinct
                        # host-authorized tool step. A correction consumed by
                        # an earlier step must not make a later, still
                        # side-effect-free step unrecoverable. Repeating an
                        # invalid name against the same authorization set
                        # remains fail-closed.
                        scope=_tool_authorization_recovery_scope(allowed_names),
                        remaining_model_rounds=remaining_model_rounds(
                            round_number
                        ),
                        minimum_remaining_rounds=2,
                        retryable=(require_tool and bool(allowed_names)),
                        cancellation_requested=_is_canceled(signal),
                        visible_output_emitted=direct_content_released,
                    ),
                    round_number=round_number,
                )
                can_retry = unauthorized_decision.allowed
                if can_retry:
                    await self._trace(
                        "tool_authorization",
                        "unauthorized_tool_retry",
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
                    messages.extend(_unauthorized_tool_retry_messages(
                        current_allowed=allowed_names,
                    ))
                    continue

                # An unauthorized batch has zero side effects. If a focused
                # correction still leaves the model stuck on a stale tool,
                # give the dynamic planner one bounded chance to replace the
                # failed step instead of terminating the whole run. The host
                # authorization set remains authoritative throughout.
                unauthorized_replan_decision: RecoveryDecision | None = None
                if (
                    planning_hook is not None
                    and unauthorized_decision.reason_code
                    is RecoveryReason.ATTEMPT_BUDGET_EXHAUSTED
                ):
                    unauthorized_replan_decision = await self._decide_recovery(
                        recovery_ledger,
                        RecoveryRequest(
                            cause=RecoveryCause.UNAUTHORIZED_TOOL_REPLAN,
                            action=RecoveryAction.REPLAN,
                            scope=_tool_authorization_recovery_scope(
                                allowed_names
                            ),
                            remaining_model_rounds=remaining_model_rounds(
                                round_number
                            ),
                            minimum_remaining_rounds=2,
                            retryable=(require_tool and bool(allowed_names)),
                            cancellation_requested=_is_canceled(signal),
                            visible_output_emitted=direct_content_released,
                        ),
                        round_number=round_number,
                    )
                if (
                    unauthorized_replan_decision is not None
                    and unauthorized_replan_decision.allowed
                ):
                    await self._trace(
                        "tool_authorization",
                        "unauthorized_tool_replan",
                        details={
                            "round": round_number,
                            "requestedTools": sorted(requested_names),
                            "currentTools": sorted(allowed_names),
                            "futureTools": sorted(future_names),
                            "batchExecuted": False,
                            "executed": False,
                        },
                    )
                    last_tool_outcome = ToolBatchOutcome.FAILED
                    pending_recovery_error_code = "tool_not_authorized"
                    failed_tool_recovery_error_code = "tool_not_authorized"
                    dynamic_replan_pending = True
                    messages.append(AgentMessage(
                        role=MessageRole.DEVELOPER,
                        content=_UNAUTHORIZED_TOOL_REPLAN_GUIDANCE,
                    ))
                    continue

                await self._trace(
                    "tool_authorization",
                    "rejected",
                    details={
                        "round": round_number,
                        "requestedTools": sorted(requested_names),
                        "currentTools": sorted(allowed_names),
                        "futureTools": sorted(future_names),
                        "retryAlreadyUsed": (
                            unauthorized_decision.attempt > 0
                            and not unauthorized_decision.allowed
                        ),
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

            if round_index >= round_limit - 1:
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
            if accumulator.content.strip() and not direct_content_released:
                yield AgentEvent(
                    type=CoreEventType.ASSISTANT_COMMENTARY_DELTA,
                    run_id=run_id,
                    payload={"delta": accumulator.content},
                )
            yield AgentEvent(
                type=CoreEventType.TOOL_CALLS_STARTED,
                run_id=run_id,
                payload={
                    "calls": [
                        _tool_call_payload(
                            call,
                            display_names=tool_display_names.get(call.name),
                        )
                        for call in calls
                    ],
                    "in_progress": True,
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

            receipts = (
                evidence_store.record_batch(calls, batch_result)
                if _results_match_calls(calls, batch_result.results)
                else ()
            )
            yield AgentEvent(
                type=CoreEventType.TOOL_RESULTS,
                run_id=run_id,
                payload={
                    "results": [
                        _tool_result_payload(result)
                        for result in batch_result.results
                    ],
                    "toolResultReceipts": [
                        receipt.to_mapping()
                        for receipt in receipts
                    ],
                },
            )
            outcome = batch_result.outcome
            await self._trace(
                "tool_round",
                outcome.value,
                details=_tool_round_trace_details(
                    round_number=round_number,
                    requested_names=requested_names,
                    allowed_names=allowed_names,
                    calls=calls,
                    batch_result=batch_result,
                    evidence_record_count=len(evidence_store.tool_result_receipts()),
                    evidence_tokens=evidence_store.token_estimate,
                ),
                duration_ms=_duration_ms(tool_started),
            )
            progress_rounds, round_limit, progress_extension = (
                _extend_progress_round_budget(
                    outcome,
                    progress_rounds=progress_rounds,
                    round_limit=round_limit,
                    max_progress_rounds=self._limits.max_progress_rounds,
                    round_number=round_number,
                )
            )
            if progress_extension is not None:
                await self._trace(
                    "runtime_round_budget",
                    "progress_extended",
                    details=progress_extension,
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
            if outcome is ToolBatchOutcome.REJECTED:
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
                if outcome is not ToolBatchOutcome.FAILED:
                    await self._observer.on_tool_round_completed(outcome)
            elif not scope_tools_to_observer:
                # Caller-owned REQUIRED applies to the initial selection. A
                # successful unscoped tool round must still leave room for the
                # model to consume its result and answer. Planned runs keep
                # the logical requirement active and advance via the observer.
                logical_required_tool_call_enabled = False
            if outcome is not ToolBatchOutcome.FAILED:
                tool_input_recovery_epoch += 1
            yield AgentEvent(
                type=CoreEventType.TOOL_ROUND_COMPLETED,
                run_id=run_id,
                payload={"outcome": outcome.value},
            )
            messages.extend(_continuation_messages(
                calls,
                batch_result.results,
                content="" if require_tool else accumulator.content,
                reasoning=accumulator.reasoning,
            ))
            tool_input_decision: RecoveryDecision | None = None
            if (
                outcome is ToolBatchOutcome.FAILED
                and _is_recoverable_tool_input_error(batch_result.error)
            ):
                tool_input_decision = await self._decide_recovery(
                    recovery_ledger,
                    RecoveryRequest(
                        cause=RecoveryCause.TOOL_INPUT_INVALID,
                        action=RecoveryAction.RETRY_MODEL,
                        scope=(
                            "tool-input-sequence:"
                            f"{tool_input_recovery_epoch}"
                        ),
                        remaining_model_rounds=remaining_model_rounds(
                            round_number
                        ),
                        cancellation_requested=_is_canceled(signal),
                        effect_state=RecoveryEffectState(
                            batch_result.effect_state.value
                        ),
                        may_repeat_side_effect=True,
                    ),
                    round_number=round_number,
                    details={"sourceErrorCode": batch_result.error},
                )
            retry_tool_input = bool(
                tool_input_decision is not None
                and tool_input_decision.allowed
            )
            if retry_tool_input:
                messages.append(AgentMessage(
                    role=MessageRole.DEVELOPER,
                    content=_TOOL_INPUT_RETRY_GUIDANCE,
                ))
                await self._trace(
                    "tool_recovery",
                    "input_retry_scheduled",
                    details={
                        "round": round_number,
                        "errorCode": batch_result.error,
                        "retryAttempt": tool_input_decision.attempt,
                        "requestedTools": sorted(requested_names),
                        "effectState": batch_result.effect_state.value,
                    },
                )
                continue
            if outcome is ToolBatchOutcome.FAILED and planning_hook is None:
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code=batch_result.error or "tool_execution_failed",
                )
                return
            if outcome is ToolBatchOutcome.FAILED and planning_hook is not None:
                failed_replan_decision = await self._decide_recovery(
                    recovery_ledger,
                    RecoveryRequest(
                        cause=RecoveryCause.TOOL_EXECUTION_FAILED_REPLAN,
                        action=RecoveryAction.REPLAN,
                        scope=f"tool-round:{round_number}",
                        remaining_model_rounds=remaining_model_rounds(
                            round_number
                        ),
                        cancellation_requested=_is_canceled(signal),
                        effect_state=RecoveryEffectState(
                            batch_result.effect_state.value
                        ),
                        may_repeat_side_effect=True,
                    ),
                    round_number=round_number,
                    details={"sourceErrorCode": batch_result.error},
                )
                if not failed_replan_decision.allowed:
                    yield _runtime_result(
                        run_id,
                        RuntimeOutcome.FAILED,
                        used_model,
                        round_number,
                        error_code=(
                            batch_result.error or "tool_execution_failed"
                        ),
                    )
                    return
            successful_replan_requested = bool(
                outcome in {
                    ToolBatchOutcome.PROGRESSED,
                    ToolBatchOutcome.COMPLETED,
                }
                and batch_result.replan_requested
            )
            if (
                planning_hook is not None
                and (
                    outcome is ToolBatchOutcome.FAILED
                    or successful_replan_requested
                )
            ):
                last_tool_outcome = outcome
                pending_recovery_error_code = (
                    batch_result.error
                    if outcome is ToolBatchOutcome.FAILED
                    else None
                )
                failed_tool_recovery_error_code = (
                    batch_result.error or "tool_execution_failed"
                    if outcome is ToolBatchOutcome.FAILED
                    else None
                )
                dynamic_replan_pending = True
                if successful_replan_requested:
                    await self._trace(
                        "planning",
                        "replan_requested",
                        details={
                            "round": round_number,
                            "outcome": outcome.value,
                            "requestedByTools": sorted({
                                result.tool_name
                                for result in batch_result.results
                                if result.planning_disposition
                                is ToolPlanningDisposition.REPLAN
                            }),
                        },
                    )
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
            round_limit,
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
        return self._observer.current_allowed_tool_names() & all_names

    def _future_allowed_names(
        self,
        tools: Sequence[ToolSchema],
        *,
        scope_tools_to_observer: bool,
    ) -> frozenset[str]:
        if not scope_tools_to_observer or self._observer is None:
            return frozenset()
        all_names = frozenset(schema.name for schema in tools)
        return self._observer.future_allowed_tool_names() & all_names

    async def _decide_recovery(
        self,
        ledger: RecoveryLedger,
        request: RecoveryRequest,
        *,
        round_number: int,
        details: Mapping[str, object] | None = None,
    ) -> RecoveryDecision:
        decision = ledger.decide(request)
        trace_details = {
            "round": int(round_number),
            **decision.to_trace_details(),
        }
        if details:
            trace_details.update(details)
        await self._trace(
            "recovery_decision",
            "allowed" if decision.allowed else "denied",
            details=trace_details,
        )
        return decision

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


def _continuation_messages(
    calls: Sequence[ToolCall],
    results: Sequence[ToolCallResult],
    *,
    content: str,
    reasoning: str,
) -> list[AgentMessage]:
    messages = [AgentMessage(
        role=MessageRole.ASSISTANT,
        content=content,
        reasoning=reasoning or None,
        tool_calls=tuple(calls),
        origin=MessageOrigin.MODEL,
    )]
    messages.extend(
        AgentMessage(
            role=MessageRole.TOOL,
            content=result.content,
            tool_call_id=result.tool_call_id,
            origin=MessageOrigin.HOST_TOOL_RESULT,
            host_metadata={"purra_tool_name": result.tool_name},
        )
        for result in results
    )
    return messages


def _future_step_retry_messages(
    calls: Sequence[ToolCall],
    *,
    current_allowed: frozenset[str],
    content: str,
    reasoning: str,
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
        reasoning=reasoning,
    )
    messages.append(AgentMessage(
        role=MessageRole.DEVELOPER,
        content=(
            "The preceding tool-call batch had zero execution. In the next "
            "round, the tool schemas supplied with the invocation are the "
            "complete authorized set. Do not call a tool whose schema is "
            "absent."
        ),
    ))
    return messages


def _unauthorized_tool_retry_messages(
    *,
    current_allowed: frozenset[str],
) -> list[AgentMessage]:
    # Do not echo a rejected tool call into the next model round. Although a
    # synthetic tool error keeps the transcript protocol-shaped, it also puts
    # the stale method name immediately before the retry and can cause some
    # providers to repeat it. Nothing executed, so a clean developer repair is
    # both truthful and less likely to anchor the model on the invalid call.
    authorized = ", ".join(sorted(current_allowed))
    selection = (
        f"this host-authorized tool: {authorized}"
        if len(current_allowed) == 1
        else f"one of these host-authorized tools: {authorized}"
    )
    return [AgentMessage(
        role=MessageRole.DEVELOPER,
        content=(
            "The preceding tool-call batch was rejected with zero execution "
            "and has been removed from the retry context. Retry the current "
            f"plan step by calling {selection}. Do not call or imitate "
            "any other tool, including tools remembered from prior rounds or "
            "conversations."
        ),
    )]


def _tool_authorization_recovery_scope(
    current_allowed: frozenset[str],
) -> str:
    return "tool-authorization:" + ",".join(sorted(current_allowed))


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


def _is_retryable_stream_interruption(error: Exception) -> bool:
    return bool(
        isinstance(error, ModelGatewayError)
        and error.code == "upstream_stream_interrupted"
        and error.retryable
    )


def _is_recoverable_tool_input_error(error_code: str | None) -> bool:
    return str(error_code or "").strip() in _RECOVERABLE_TOOL_INPUT_ERROR_CODES


def _root_error_type(error: Exception) -> str:
    cause = error.__cause__
    return type(cause if isinstance(cause, Exception) else error).__name__


def _error_chain_types(error: BaseException, *, limit: int = 8) -> list[str]:
    types: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and len(types) < max(1, int(limit)):
        identity = id(current)
        if identity in seen:
            break
        seen.add(identity)
        types.append(type(current).__name__)
        cause = current.__cause__
        current = cause if cause is not None else current.__context__
    return types
