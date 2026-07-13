"""The single policy-enforcing execution path for Core tool handlers."""

from __future__ import annotations

import asyncio
from types import MappingProxyType
from typing import Any

from agent_core.cancellation import OperationCanceled, await_with_cancellation
from agent_core.contracts import (
    ApprovalRequest,
    ApprovalStatus,
    DomainEffect,
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolBatchResult,
    ToolCall,
    ToolCallResult,
    ToolExecutionLimits,
    ToolHandlerResult,
)
from agent_core.errors import ContractViolationError
from agent_core.events import AgentEvent, CoreEventType
from agent_core.ports import (
    ApprovalGateway,
    CancellationSignal,
    CONTROLLER_OWNED_RUN_EVENT_TYPES,
    EventSink,
    ToolCatalog,
    ToolRegistration,
)
from agent_core.tools.contract import validate_tool_contract
from agent_core.tools.policy import (
    aggregate_outcomes,
    approval_error_code,
    approval_outcome,
)
from agent_core.tools.security import (
    ParsedToolCall,
    normalize_error_code,
    preflight_tool_calls,
    safe_error_content,
    sanitize_error_message,
    sanitize_tool_result,
    summarize_tool_arguments,
)


class CoreToolExecutor:
    """Execute injected handlers without allowing domains to bypass Core gates."""

    def __init__(
        self,
        catalog: ToolCatalog,
        approval_gateway: ApprovalGateway | None = None,
        limits: ToolExecutionLimits = ToolExecutionLimits(),
    ) -> None:
        registrations = validate_tool_contract(catalog.registrations())
        self._registrations = MappingProxyType({
            registration.schema.name: registration
            for registration in registrations
        })
        self._approval_gateway = approval_gateway
        self._limits = limits

    async def execute_batch(
        self,
        request: ToolBatchRequest,
        event_sink: EventSink,
        signal: CancellationSignal | None = None,
    ) -> ToolBatchResult:
        if _is_canceled(signal):
            return ToolBatchResult(
                results=(),
                outcome=ToolBatchOutcome.CANCELED,
                error="tool_execution_canceled",
            )

        parsed_calls, failure = preflight_tool_calls(request.calls, self._limits)
        if failure is not None:
            return _whole_batch_failure(
                request.calls,
                outcome=ToolBatchOutcome.FAILED,
                code=failure.code,
                message=failure.message,
            )

        requested_names = frozenset(item.call.name for item in parsed_calls)
        if not requested_names.issubset(self._registrations):
            return _whole_batch_failure(
                request.calls,
                outcome=ToolBatchOutcome.REJECTED,
                code="unknown_tool",
                message="The requested tool is not registered.",
            )
        unknown_scope_names = request.allowed_tool_names - self._registrations.keys()
        if unknown_scope_names:
            return _whole_batch_failure(
                request.calls,
                outcome=ToolBatchOutcome.REJECTED,
                code="invalid_tool_scope",
                message="The execution scope contains an unregistered tool.",
            )
        if not requested_names.issubset(request.allowed_tool_names):
            return _whole_batch_failure(
                request.calls,
                outcome=ToolBatchOutcome.REJECTED,
                code="tool_not_authorized",
                message="The requested tool is outside the current execution scope.",
            )

        results: list[ToolCallResult] = []
        outcomes: list[ToolBatchOutcome] = []
        cache_hits: list[bool] = []

        for index, parsed in enumerate(parsed_calls):
            registration = self._registrations[parsed.call.name]
            if _is_canceled(signal):
                return await self._canceled_result(
                    request,
                    event_sink,
                    parsed,
                    index,
                    results,
                    cache_hits,
                )

            scope_failure = await self._validate_scope(
                registration,
                request,
                parsed,
                signal,
            )
            if scope_failure is not None:
                code, message, canceled = scope_failure
                outcome = (
                    ToolBatchOutcome.CANCELED
                    if canceled
                    else (
                        ToolBatchOutcome.REJECTED
                        if code == "tool_scope_violation"
                        else ToolBatchOutcome.FAILED
                    )
                )
                result = _failure_result(
                    parsed.call,
                    code,
                    message,
                    approval_status=(ApprovalStatus.CANCELED if canceled else None),
                )
                results.append(result)
                cache_hits.append(False)
                await _emit_completed(event_sink, request, index, result, outcome)
                return ToolBatchResult(
                    results=tuple(results),
                    outcome=outcome,
                    error=code,
                    cache_hits=tuple(cache_hits),
                )

            # Defense in depth: authorization never comes from mutable state.
            if parsed.call.name not in request.allowed_tool_names:
                result = _failure_result(
                    parsed.call,
                    "tool_not_authorized",
                    "The requested tool is outside the current execution scope.",
                )
                results.append(result)
                cache_hits.append(False)
                await _emit_completed(
                    event_sink, request, index, result, ToolBatchOutcome.REJECTED
                )
                return ToolBatchResult(
                    results=tuple(results),
                    outcome=ToolBatchOutcome.REJECTED,
                    error="tool_not_authorized",
                    cache_hits=tuple(cache_hits),
                )

            policy = registration.policy
            predicted_cache_hit = _probe_cache(registration, request, parsed)
            cache_hits.append(predicted_cache_hit)

            approval_status: ApprovalStatus | None = None
            if policy.requires_user_approval:
                approval_status = await self._request_approval(
                    registration,
                    request,
                    parsed,
                    event_sink,
                    signal,
                )
                approval_batch_outcome = approval_outcome(approval_status)
                if approval_status is not ApprovalStatus.APPROVED:
                    code = approval_error_code(approval_status) or "approval_unavailable"
                    result = _failure_result(
                        parsed.call,
                        code,
                        _approval_message(approval_status),
                        approval_status=approval_status,
                    )
                    results.append(result)
                    outcomes.append(approval_batch_outcome)
                    await _emit_completed(
                        event_sink,
                        request,
                        index,
                        result,
                        approval_batch_outcome,
                    )
                    if approval_batch_outcome in {
                        ToolBatchOutcome.CANCELED,
                        ToolBatchOutcome.FAILED,
                    }:
                        return ToolBatchResult(
                            results=tuple(results),
                            outcome=approval_batch_outcome,
                            error=code,
                            cache_hits=tuple(cache_hits),
                        )
                    continue

            try:
                handler_result = await await_with_cancellation(
                    registration.handler(request.state, parsed.arguments, signal),
                    signal,
                )
            except OperationCanceled:
                return await self._canceled_result(
                    request,
                    event_sink,
                    parsed,
                    index,
                    results,
                    cache_hits,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                result = _failure_result(
                    parsed.call,
                    "tool_execution_failed",
                    "Tool execution failed.",
                    approval_status=approval_status,
                )
                results.append(result)
                await _emit_completed(
                    event_sink, request, index, result, ToolBatchOutcome.FAILED
                )
                return ToolBatchResult(
                    results=tuple(results),
                    outcome=ToolBatchOutcome.FAILED,
                    error="tool_execution_failed",
                    cache_hits=tuple(cache_hits),
                )

            if not isinstance(handler_result, ToolHandlerResult):
                result = _failure_result(
                    parsed.call,
                    "invalid_tool_result",
                    "Tool handler returned an invalid result.",
                    approval_status=approval_status,
                )
                results.append(result)
                await _emit_completed(
                    event_sink, request, index, result, ToolBatchOutcome.FAILED
                )
                return ToolBatchResult(
                    results=tuple(results),
                    outcome=ToolBatchOutcome.FAILED,
                    error="invalid_tool_result",
                    cache_hits=tuple(cache_hits),
                )

            handler_error = (
                normalize_error_code(handler_result.error_code)
                if handler_result.error_code
                else None
            )
            oversized = len(handler_result.content) > self._limits.max_result_chars
            if oversized:
                handler_error = "tool_result_too_large"
            content = sanitize_tool_result(
                handler_result.content,
                max_chars=self._limits.max_result_chars,
            )
            effects = () if handler_error else handler_result.effects
            result = ToolCallResult(
                tool_call_id=parsed.call.id,
                tool_name=parsed.call.name,
                content=content,
                from_cache=handler_result.from_cache,
                approval_status=approval_status,
                error=handler_error,
                effects=effects,
            )
            results.append(result)

            if handler_error:
                await _emit_completed(
                    event_sink, request, index, result, ToolBatchOutcome.FAILED
                )
                return ToolBatchResult(
                    results=tuple(results),
                    outcome=ToolBatchOutcome.FAILED,
                    error=handler_error,
                    cache_hits=tuple(cache_hits),
                )

            await _emit_effects(event_sink, request, effects)
            outcomes.append(ToolBatchOutcome.COMPLETED)
            await _emit_completed(
                event_sink, request, index, result, ToolBatchOutcome.COMPLETED
            )

        outcome = aggregate_outcomes(outcomes)
        return ToolBatchResult(
            results=tuple(results),
            outcome=outcome,
            cache_hits=tuple(cache_hits),
        )

    async def _validate_scope(
        self,
        registration: ToolRegistration,
        request: ToolBatchRequest,
        parsed: ParsedToolCall,
        signal: CancellationSignal | None,
    ) -> tuple[str, str, bool] | None:
        if registration.scope_validator is None:
            return None
        try:
            message = await await_with_cancellation(
                registration.scope_validator(request.state, parsed.arguments, signal),
                signal,
            )
        except OperationCanceled:
            return "tool_execution_canceled", "Tool execution was canceled.", True
        except asyncio.CancelledError:
            raise
        except Exception:
            return (
                "tool_scope_validation_failed",
                "Tool scope validation failed.",
                False,
            )
        if message:
            return "tool_scope_violation", sanitize_error_message(message), False
        return None

    async def _request_approval(
        self,
        registration: ToolRegistration,
        request: ToolBatchRequest,
        parsed: ParsedToolCall,
        event_sink: EventSink,
        signal: CancellationSignal | None,
    ) -> ApprovalStatus:
        if request.run_id is None or self._approval_gateway is None:
            return ApprovalStatus.UNAVAILABLE
        approval = ApprovalRequest(
            tool_call=parsed.call,
            title=registration.policy.title,
            risk_level=registration.policy.risk_level,
            summary=summarize_tool_arguments(
                parsed.arguments,
                max_chars=self._limits.approval_summary_chars,
            ),
            timeout_seconds=self._limits.approval_timeout_seconds,
        )
        try:
            result = await self._approval_gateway.request(
                request.run_id,
                approval,
                event_sink,
                signal,
            )
            return result.status
        except OperationCanceled:
            return ApprovalStatus.CANCELED
        except asyncio.CancelledError:
            raise
        except Exception:
            return ApprovalStatus.UNAVAILABLE

    async def _canceled_result(
        self,
        request: ToolBatchRequest,
        event_sink: EventSink,
        parsed: ParsedToolCall,
        index: int,
        results: list[ToolCallResult],
        cache_hits: list[bool],
    ) -> ToolBatchResult:
        result = _failure_result(
            parsed.call,
            "tool_execution_canceled",
            "Tool execution was canceled.",
            approval_status=ApprovalStatus.CANCELED,
        )
        results.append(result)
        if len(cache_hits) < len(results):
            cache_hits.append(False)
        await _emit_completed(
            event_sink, request, index, result, ToolBatchOutcome.CANCELED
        )
        return ToolBatchResult(
            results=tuple(results),
            outcome=ToolBatchOutcome.CANCELED,
            error="tool_execution_canceled",
            cache_hits=tuple(cache_hits),
        )


def _probe_cache(
    registration: ToolRegistration,
    request: ToolBatchRequest,
    parsed: ParsedToolCall,
) -> bool:
    if registration.cache_probe is None:
        return False
    try:
        return bool(registration.cache_probe.will_hit(request.state, parsed.arguments))
    except Exception:
        return False


async def _emit_effects(
    event_sink: EventSink,
    request: ToolBatchRequest,
    effects: tuple[DomainEffect, ...],
) -> None:
    reserved = next(
        (
            effect.type
            for effect in effects
            if effect.type in CONTROLLER_OWNED_RUN_EVENT_TYPES
        ),
        None,
    )
    if reserved is not None:
        raise ContractViolationError(
            "domain effects cannot use controller-owned event type "
            f"{reserved!r}"
        )
    for effect in effects:
        await event_sink.emit(AgentEvent(
            type=effect.type,
            run_id=request.run_id,
            payload=effect.payload,
        ))


async def _emit_completed(
    event_sink: EventSink,
    request: ToolBatchRequest,
    index: int,
    result: ToolCallResult,
    outcome: ToolBatchOutcome,
) -> None:
    payload: dict[str, Any] = {
        "index": index,
        "toolCallId": result.tool_call_id,
        "toolName": result.tool_name,
        "fromCache": result.from_cache,
        "outcome": outcome.value,
    }
    if result.error:
        payload["errorCode"] = result.error
    if result.approval_status is not None:
        payload["approvalStatus"] = result.approval_status.value
    await event_sink.emit(AgentEvent(
        type=CoreEventType.TOOL_CALL_COMPLETED,
        run_id=request.run_id,
        payload=payload,
    ))


def _whole_batch_failure(
    calls: tuple[ToolCall, ...],
    *,
    outcome: ToolBatchOutcome,
    code: str,
    message: str,
) -> ToolBatchResult:
    normalized = normalize_error_code(code)
    return ToolBatchResult(
        results=tuple(
            _failure_result(call, normalized, message)
            for call in calls
        ),
        outcome=outcome,
        error=normalized,
        cache_hits=tuple(False for _ in calls),
    )


def _failure_result(
    call: ToolCall,
    code: str,
    message: str,
    *,
    approval_status: ApprovalStatus | None = None,
) -> ToolCallResult:
    normalized = normalize_error_code(code)
    return ToolCallResult(
        tool_call_id=call.id,
        tool_name=call.name,
        content=safe_error_content(normalized, message),
        approval_status=approval_status,
        error=normalized,
    )


def _approval_message(status: ApprovalStatus) -> str:
    if status is ApprovalStatus.REJECTED:
        return "The operation was not approved."
    if status is ApprovalStatus.CANCELED:
        return "The approval request was canceled."
    if status is ApprovalStatus.TIMED_OUT:
        return "The approval request timed out."
    return "Approval is unavailable."


def _is_canceled(signal: CancellationSignal | None) -> bool:
    return bool(signal is not None and signal.is_set())
