"""Provider-neutral Agent model/tool loop.

This stage-3 runtime starts after the application has built and initially
budgeted context.  It owns model rounds, typed tool continuation, authorization,
round trimming, cancellation and the model-round limit.  HTTP, SSE, writing
concepts and concrete tool handlers stay behind ports.
"""

from __future__ import annotations

import asyncio
import json
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
    MessageRole,
    ModelFinishReason,
    ModelInvocation,
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
from agent_core.errors import ModelGatewayError, UnsupportedModelFeatureError
from agent_core.events import AgentEvent, CoreEventType
from agent_core.ports import (
    CancellationSignal,
    EventSink,
    ModelGateway,
    RuntimeObserver,
    ToolExecutionGateway,
)


RuntimeUpdate = AgentEvent | AgentRuntimeResult


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
        execution_state: ExecutionState | None = None,
        run_id: RunId | None = None,
        context_budget: ContextBudget | None = None,
        round_input_tokens: int | None = None,
        scope_tools_to_observer: bool = True,
        force_tool_choice: bool = False,
        tools_executable: bool = True,
        signal: CancellationSignal | None = None,
    ) -> AsyncIterator[RuntimeUpdate]:
        messages = list(request.messages)
        state = execution_state or ExecutionState()
        configured_tools = tuple(tools) if request.tools_enabled else ()
        used_model = request.model.model
        required_tool_choice_enabled = bool(force_tool_choice)
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

            active_token_budget = (
                (
                    context_budget.provider_input_tokens
                    if round_index == 0
                    else context_budget.round_input_tokens
                )
                if context_budget is not None
                else (round_input_tokens if round_index > 0 else None)
            )
            if active_token_budget is not None:
                trimmed = trim_agent_messages_by_turn(messages, active_token_budget)
                messages = list(trimmed.messages)
                if trimmed.overflow_tokens > 0:
                    initial_round = round_index == 0
                    await self._trace(
                        "context_budget",
                        "overflow_initial" if initial_round else "overflow_after_tool",
                        details={"round": round_number},
                    )
                    yield _runtime_result(
                        run_id,
                        RuntimeOutcome.FAILED,
                        used_model,
                        round_index,
                        error_code=(
                            "context_overflow_initial"
                            if initial_round
                            else "context_overflow_after_tool"
                        ),
                    )
                    return

            allowed_names = self._allowed_names(
                configured_tools,
                scope_tools_to_observer=scope_tools_to_observer,
            )
            visible_tools = tuple(
                schema for schema in configured_tools if schema.name in allowed_names
            )
            require_tool = bool(required_tool_choice_enabled and visible_tools)
            invocation = ModelInvocation(
                request=request.model,
                tools=visible_tools,
                tool_choice=(
                    ToolChoiceMode.REQUIRED
                    if require_tool
                    else (ToolChoiceMode.AUTO if visible_tools else ToolChoiceMode.NONE)
                ),
                max_output_tokens=maximum_output_tokens,
            )

            model_started = perf_counter()
            try:
                try:
                    stream = await await_with_cancellation(
                        self._model_gateway.stream(messages, invocation, signal),
                        signal,
                    )
                except UnsupportedModelFeatureError:
                    if not require_tool:
                        raise
                    required_tool_choice_enabled = False
                    await self._trace(
                        "tool_choice",
                        "provider_fallback_auto",
                        details={"round": round_number},
                    )
                    stream = await await_with_cancellation(
                        self._model_gateway.stream(
                            messages,
                            ModelInvocation(
                                request=request.model,
                                tools=visible_tools,
                                tool_choice=ToolChoiceMode.AUTO,
                                max_output_tokens=maximum_output_tokens,
                            ),
                            signal,
                        ),
                        signal,
                    )
            except OperationCanceled:
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.CANCELED,
                    used_model,
                    round_number,
                    error_code="request_canceled",
                )
                return
            except Exception as error:
                error_code = (
                    error.code
                    if isinstance(error, ModelGatewayError)
                    else "model_gateway_error"
                )
                interrupted = error_code == "upstream_stream_interrupted"
                await self._trace(
                    "stream" if interrupted else "model_round",
                    "interrupted" if interrupted else "exception",
                    details={
                        "round": round_number,
                        "errorType": _root_error_type(error),
                    },
                    duration_ms=_duration_ms(model_started),
                )
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code=error_code,
                )
                return

            used_model = stream.model or used_model
            accumulator = _ModelRoundAccumulator()
            stream_canceled = False
            stream_error: Exception | None = None
            chunks = stream.chunks
            try:
                while True:
                    try:
                        chunk = await await_with_cancellation(anext(chunks), signal)
                    except StopAsyncIteration:
                        break
                    accumulator.add(chunk)
                    if chunk.thinking_delta:
                        yield AgentEvent(
                            type=CoreEventType.MODEL_THINKING_DELTA,
                            run_id=run_id,
                            payload={"delta": chunk.thinking_delta},
                        )
                    if chunk.content_delta and not require_tool:
                        if self._observer is not None:
                            await self._observer.on_model_delta()
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

            if stream_canceled:
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.CANCELED,
                    used_model,
                    round_number,
                    error_code="request_canceled",
                )
                return
            if stream_error is not None:
                error_code = (
                    stream_error.code
                    if isinstance(stream_error, ModelGatewayError)
                    else "model_stream_error"
                )
                interrupted = error_code == "upstream_stream_interrupted"
                await self._trace(
                    "stream" if interrupted else "model_round",
                    "interrupted" if interrupted else "stream_exception",
                    details={
                        "round": round_number,
                        "errorType": _root_error_type(stream_error),
                    },
                    duration_ms=_duration_ms(model_started),
                )
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code=error_code,
                )
                return

            calls = accumulator.valid_tool_calls()
            finish_reason = accumulator.finish_reason
            await self._trace(
                "model_round",
                (finish_reason.value if finish_reason is not None else "stream_end"),
                details={"round": round_number, "toolCallCount": len(calls)},
                duration_ms=_duration_ms(model_started),
            )

            tool_finish = finish_reason is ModelFinishReason.TOOL_CALLS or (
                finish_reason in {ModelFinishReason.STOP, ModelFinishReason.LENGTH}
                and bool(calls)
            )
            if require_tool and not calls:
                await self._trace(
                    "tool_round",
                    "missing_required_call",
                    details={"round": round_number},
                )
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.FAILED,
                    used_model,
                    round_number,
                    error_code="missing_required_tool_call",
                )
                return

            if not tool_finish or not calls:
                yield _runtime_result(
                    run_id,
                    RuntimeOutcome.COMPLETED,
                    used_model,
                    round_number,
                    final_response=accumulator.content,
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

            requested_names = frozenset(call.name for call in calls)
            authorized = requested_names.issubset(allowed_names)
            if (
                authorized
                and scope_tools_to_observer
                and self._observer is not None
            ):
                await self._observer.on_tool_calls_started(tuple(sorted(requested_names)))
            yield AgentEvent(
                type=CoreEventType.TOOL_CALLS_STARTED,
                run_id=run_id,
                payload={
                    "calls": [_tool_call_payload(call) for call in calls],
                    "in_progress": True,
                    "partial_content": "" if require_tool else accumulator.content,
                    "partial_thinking": accumulator.thinking,
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
            if authorized:
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
            else:
                batch_result = _rejected_batch(calls)

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
            outcome = (
                batch_result.outcome
                if authorized
                else ToolBatchOutcome.REJECTED
            )
            await self._trace(
                "tool_round",
                outcome.value,
                details={
                    "round": round_number,
                    "requestedTools": sorted(requested_names),
                    "allowedTools": sorted(allowed_names),
                    "callCount": len(calls),
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
                await self._observer.on_tool_round_completed()
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

    def add(self, chunk) -> None:
        self.content += chunk.content_delta
        self.thinking += chunk.thinking_delta
        if chunk.finish_reason is not None:
            self.finish_reason = chunk.finish_reason
        for delta in chunk.tool_call_deltas:
            current = self._calls.setdefault(delta.index, _ToolCallParts())
            if delta.id is not None:
                current.id = str(delta.id)
            if delta.name is not None:
                current.name = str(delta.name)
            current.arguments += str(delta.arguments_fragment or "")

    def valid_tool_calls(self) -> tuple[ToolCall, ...]:
        return tuple(
            ToolCall(id=parts.id, name=parts.name, arguments_json=parts.arguments)
            for _, parts in sorted(self._calls.items())
            if parts.id.strip() and parts.name.strip()
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
                await _cancel_task_safely(gateway_task)
                raise OperationCanceled("agent run was canceled")

            waiters: set[asyncio.Future[Any]] = {gateway_task}
            if progress_task is not None:
                waiters.add(progress_task)
            if signal_task is not None:
                waiters.add(signal_task)
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
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
    )]
    messages.extend(
        AgentMessage(
            role=MessageRole.TOOL,
            content=result.content,
            tool_call_id=result.tool_call_id,
        )
        for result in results
    )
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


def _rejected_batch(calls: Sequence[ToolCall]) -> ToolBatchResult:
    content = json.dumps(
        {"success": False, "errorCode": "tool_not_authorized"},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return ToolBatchResult(
        results=tuple(
            ToolCallResult(
                tool_call_id=call.id,
                tool_name=call.name,
                content=content,
                error="tool_not_authorized",
            )
            for call in calls
        ),
        outcome=ToolBatchOutcome.REJECTED,
        error="tool_not_authorized",
    )


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


def _root_error_type(error: Exception) -> str:
    cause = error.__cause__
    return type(cause if isinstance(cause, Exception) else error).__name__
