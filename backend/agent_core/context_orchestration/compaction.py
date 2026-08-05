"""Generic Core timing, hook invocation, and compression-result validation."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Awaitable, Callable, Mapping, Sequence

from agent_core.context_budget import (
    allocate_context_budget,
    estimate_agent_messages_tokens,
    trim_agent_messages_by_turn,
)
from agent_core.context_orchestration.contracts import (
    ContextCompressionRequest,
    ContextCompressionSettings,
    ConversationCompactionResult,
)
from agent_core.context_orchestration.ledger import (
    ContextCompactionBudget,
    ContextCompactionPhase,
)
from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    MessageOrigin,
    MessageRole,
    PostPlanningContextOptimizationResult,
)
from agent_core.errors import ContextOverflowError, ContractViolationError
from agent_core.ports import CancellationSignal, ContextCompressionHook


class ContextCompressionCoordinator:
    """Own compression timing while delegating all semantic reduction.

    When no application hook is installed, Core uses a bounded recent-message
    trimmer.  The default never generates a summary, mutates canonical
    conversation storage, or infers semantic importance.
    """

    def __init__(
        self,
        hook: ContextCompressionHook | None = None,
        settings: ContextCompressionSettings = ContextCompressionSettings(),
    ) -> None:
        if hook is not None and not isinstance(hook, ContextCompressionHook):
            raise TypeError("context compression hook has an invalid contract")
        if not isinstance(settings, ContextCompressionSettings):
            raise TypeError("context compression settings are required")
        self._hook = hook
        self._settings = settings

    @property
    def hook(self) -> ContextCompressionHook | None:
        return self._hook

    @property
    def settings(self) -> ContextCompressionSettings:
        return self._settings

    async def prepare(
        self,
        request: AgentRunRequest,
        signal: CancellationSignal | None = None,
        *,
        on_compaction_started: (
            Callable[[Mapping[str, Any]], Awaitable[None]] | None
        ) = None,
        budget: ContextCompactionBudget | None = None,
        anticipated_context_tokens: int = 0,
        resolved_context_tokens: int | None = None,
        output_reserve_tokens: int | None = None,
        provider_input_tokens: int | None = None,
        planned_step_count: int | None = None,
        planned_tool_count: int | None = None,
        selected_tool_count: int | None = None,
    ) -> ConversationCompactionResult:
        del planned_step_count, planned_tool_count, selected_tool_count
        snapshot = budget or _legacy_budget(
            request,
            anticipated_context_tokens=anticipated_context_tokens,
            resolved_context_tokens=resolved_context_tokens,
            output_reserve_tokens=output_reserve_tokens,
            provider_input_tokens=provider_input_tokens,
        )
        message_tokens = estimate_agent_messages_tokens(request.messages)
        context_tokens = min(
            snapshot.provider_input_tokens,
            max(0, snapshot.context_tokens),
        )
        available_message_tokens = max(
            0,
            snapshot.provider_input_tokens - context_tokens,
        )
        projected_input_tokens = message_tokens + context_tokens
        pressure_ratio = (
            projected_input_tokens / snapshot.provider_input_tokens
        )
        over_message_budget = message_tokens > available_message_tokens
        threshold_reached = pressure_ratio >= self._settings.trigger_ratio
        compression_required = over_message_budget or threshold_reached
        trigger_reason = (
            "message_budget_exceeded"
            if over_message_budget
            else "pressure_threshold"
            if threshold_reached
            else "below_threshold"
        )
        compression = ContextCompressionRequest(
            request=request,
            budget=snapshot,
            message_tokens=message_tokens,
            projected_input_tokens=projected_input_tokens,
            available_message_tokens=available_message_tokens,
            pressure_ratio=pressure_ratio,
            compression_required=compression_required,
            trigger_reason=trigger_reason,
        )

        if compression_required and on_compaction_started is not None:
            await on_compaction_started({
                "strategy": (
                    "application_hook"
                    if self._hook is not None
                    else "recent_messages"
                ),
                "triggerReason": trigger_reason,
                "pressureRatio": round(pressure_ratio, 4),
                "triggerRatio": self._settings.trigger_ratio,
                "availableMessageTokens": available_message_tokens,
                "messageTokens": message_tokens,
            })

        if self._hook is not None:
            result = await self._hook.compress(compression, signal)
        elif compression_required:
            result = _default_trim(compression, self._settings)
        else:
            result = ConversationCompactionResult(
                request=request,
                outcome="below_threshold",
                retained_raw_turn_count=_conversation_turn_count(
                    request.messages
                ),
            )

        _validate_result_contract(request, result)
        candidate_tokens = estimate_agent_messages_tokens(
            result.request.messages
        )
        overflow_tokens = max(
            0,
            candidate_tokens - available_message_tokens,
        )
        if overflow_tokens:
            raise ContextOverflowError(
                "context compression result exceeds the message budget",
                reason_code="context_compression_result_exceeds_budget",
                details={
                    "phase": snapshot.phase.value,
                    "strategy": (
                        "application_hook"
                        if self._hook is not None
                        else "recent_messages"
                    ),
                    "availableMessageTokens": available_message_tokens,
                    "compressedMessageTokens": candidate_tokens,
                    "overflowTokens": overflow_tokens,
                    "compressionOutcome": result.outcome,
                },
            )

        diagnostics = {
            **dict(result.diagnostics),
            "compactionPhase": snapshot.phase.value,
            "strategy": (
                "application_hook"
                if self._hook is not None
                else "recent_messages"
            ),
            "triggerReason": trigger_reason,
            "compressionRequired": compression_required,
            "triggerRatio": self._settings.trigger_ratio,
            "pressureRatio": round(pressure_ratio, 4),
            "providerInputTokens": snapshot.provider_input_tokens,
            "contextReserveTokens": context_tokens,
            "availableMessageTokens": available_message_tokens,
            "messageTokensBefore": message_tokens,
            "messageTokensAfter": candidate_tokens,
            "projectedInputTokens": projected_input_tokens,
            "projectedInputTokensAfter": candidate_tokens + context_tokens,
            "droppedMessageCount": max(
                0,
                len(request.messages) - len(result.request.messages),
            ),
        }
        return ConversationCompactionResult(
            request=result.request,
            outcome=result.outcome,
            compression_state_version=result.compression_state_version,
            compacted_turn_count=result.compacted_turn_count,
            retained_raw_turn_count=result.retained_raw_turn_count,
            diagnostics=diagnostics,
        )


class PostPlanningConversationContextOptimizer:
    """Compatibility adapter for callers that still use the older hook."""

    def __init__(
        self,
        service: ContextCompressionCoordinator,
        source_request: AgentRunRequest,
    ) -> None:
        self._service = service
        self._source_request = source_request

    async def optimize(
        self,
        request: AgentRunRequest,
        *,
        provider_input_tokens: int,
        resolved_context_tokens: int,
        output_reserve_tokens: int,
        planned_step_count: int,
        planned_tool_count: int,
        selected_tool_names: Sequence[str],
        signal: CancellationSignal | None = None,
        on_compaction_started: (
            Callable[[Mapping[str, Any]], Awaitable[None]] | None
        ) = None,
    ) -> PostPlanningContextOptimizationResult:
        source = replace(
            self._source_request,
            metadata={
                **dict(self._source_request.metadata),
                **dict(request.metadata),
            },
        )
        result = await self._service.prepare(
            source,
            signal,
            budget=ContextCompactionBudget(
                phase=ContextCompactionPhase.POST_PLANNING,
                provider_input_tokens=provider_input_tokens,
                context_tokens=resolved_context_tokens,
                context_tokens_are_resolved=True,
                output_reserve_tokens=output_reserve_tokens,
                planned_step_count=planned_step_count,
                planned_tool_count=planned_tool_count,
                selected_tool_count=len(tuple(selected_tool_names)),
            ),
            on_compaction_started=on_compaction_started,
        )
        optimized = replace(
            result.request,
            metadata={
                **dict(request.metadata),
                **dict(result.request.metadata),
            },
        )
        return PostPlanningContextOptimizationResult(
            request=optimized,
            outcome=result.outcome,
            compacted_turn_count=result.compacted_turn_count,
            retained_raw_turn_count=result.retained_raw_turn_count,
            summary_version=result.compression_state_version,
            diagnostics=result.diagnostics,
        )


def _legacy_budget(
    request: AgentRunRequest,
    *,
    anticipated_context_tokens: int,
    resolved_context_tokens: int | None,
    output_reserve_tokens: int | None,
    provider_input_tokens: int | None,
) -> ContextCompactionBudget:
    output = max(1, int(output_reserve_tokens or 8_192))
    if provider_input_tokens is None:
        allocated = allocate_context_budget(
            window_tokens=max(1, int(request.context_window or 128_000)),
            output_reserve_tokens=output,
        )
        provider_input = allocated.provider_input_tokens
    else:
        provider_input = max(1, int(provider_input_tokens))
    resolved = resolved_context_tokens is not None
    return ContextCompactionBudget(
        phase=(
            ContextCompactionPhase.POST_PLANNING
            if resolved
            else ContextCompactionPhase.PRE_PLANNING
        ),
        provider_input_tokens=provider_input,
        context_tokens=max(
            0,
            int(
                resolved_context_tokens
                if resolved_context_tokens is not None
                else anticipated_context_tokens
            ),
        ),
        context_tokens_are_resolved=resolved,
        output_reserve_tokens=output,
    )


def _default_trim(
    compression: ContextCompressionRequest,
    settings: ContextCompressionSettings,
) -> ConversationCompactionResult:
    trimmed = trim_agent_messages_by_turn(
        compression.request.messages,
        compression.available_message_tokens,
        max_recent_messages=settings.default_keep_recent_messages,
    )
    if trimmed.overflow_tokens:
        raise ContextOverflowError(
            "protected messages exceed the default compression budget",
            reason_code="protected_messages_exceed_compression_budget",
            details={
                "availableMessageTokens": (
                    compression.available_message_tokens
                ),
                "estimatedInputTokens": trimmed.token_estimate,
                "overflowTokens": trimmed.overflow_tokens,
            },
        )
    metadata = dict(compression.request.metadata)
    metadata["conversationCompaction"] = {
        "outcome": "compacted_default_trim",
        "strategy": "recent_messages",
        "keepRecentMessages": settings.default_keep_recent_messages,
        "droppedMessageCount": trimmed.dropped_count,
    }
    projected = replace(
        compression.request,
        messages=trimmed.messages,
        metadata=metadata,
    )
    return ConversationCompactionResult(
        request=projected,
        outcome="compacted_default_trim",
        retained_raw_turn_count=_conversation_turn_count(trimmed.messages),
        diagnostics={
            "keepRecentMessages": settings.default_keep_recent_messages,
            "droppedMessageCount": trimmed.dropped_count,
        },
    )


def _validate_result_contract(
    source: AgentRunRequest,
    result: ConversationCompactionResult,
) -> None:
    if not isinstance(result, ConversationCompactionResult):
        raise ContractViolationError(
            "context compression hook returned an invalid result"
        )
    candidate = result.request
    immutable_fields = (
        "model",
        "domain_context",
        "session_id",
        "mode",
        "context_window",
        "tools_enabled",
    )
    if any(
        getattr(candidate, name) != getattr(source, name)
        for name in immutable_fields
    ):
        raise ContractViolationError(
            "context compression hook changed immutable request fields"
        )

    source_privileged = tuple(
        message
        for message in source.messages
        if message.role in {MessageRole.SYSTEM, MessageRole.DEVELOPER}
    )
    candidate_privileged = tuple(
        message
        for message in candidate.messages
        if message.role in {MessageRole.SYSTEM, MessageRole.DEVELOPER}
    )
    if candidate_privileged != source_privileged:
        raise ContractViolationError(
            "context compression hook changed privileged instructions"
        )

    current = _last_caller_user_message(source.messages)
    if current is not None and current not in candidate.messages:
        raise ContractViolationError(
            "context compression hook removed the current user request"
        )

    source_messages = tuple(source.messages)
    for message in candidate.messages:
        if message in source_messages:
            continue
        if message.origin not in {
            MessageOrigin.HOST_CONTEXT,
            MessageOrigin.HOST_TOOL_RESULT,
        }:
            raise ContractViolationError(
                "context compression hook introduced untrusted caller content"
            )
    _validate_tool_protocol(candidate.messages)


def _validate_tool_protocol(messages: Sequence[AgentMessage]) -> None:
    pending: set[str] = set()
    for message in messages:
        if pending and message.role is not MessageRole.TOOL:
            raise ContractViolationError(
                "tool calls must be followed by their complete tool results"
            )
        if message.role is MessageRole.TOOL:
            tool_call_id = str(message.tool_call_id or "")
            if tool_call_id not in pending:
                raise ContractViolationError(
                    "context compression produced an orphan tool result"
                )
            pending.remove(tool_call_id)
            continue
        if message.tool_calls:
            ids = {call.id for call in message.tool_calls}
            if len(ids) != len(message.tool_calls):
                raise ContractViolationError(
                    "context compression produced duplicate tool call ids"
                )
            pending.update(ids)
    if pending:
        raise ContractViolationError(
            "context compression produced tool calls without results"
        )


def _last_caller_user_message(
    messages: Sequence[AgentMessage],
) -> AgentMessage | None:
    return next(
        (
            message
            for message in reversed(messages)
            if message.role is MessageRole.USER
            and message.origin is MessageOrigin.CALLER
        ),
        None,
    )


def _conversation_turn_count(messages: Sequence[AgentMessage]) -> int:
    return sum(
        message.role is MessageRole.USER
        and message.origin is MessageOrigin.CALLER
        for message in messages
    )


# The old name remains as a source-compatible alias for one migration cycle.
ConversationContextCompactor = ContextCompressionCoordinator


__all__ = [
    "ContextCompressionCoordinator",
    "ConversationContextCompactor",
    "PostPlanningConversationContextOptimizer",
]
