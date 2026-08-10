"""Managed provider calls for host hooks and bounded framework operations.

Applications declare a task policy; PurrA alone resolves the provider output
allowance, creates ``ModelInvocation``, applies safe capability fallback, and
classifies the terminal provider reason. A resolved-budget call is never
replayed after truncation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from purra.cancellation import await_with_cancellation
from purra.contracts import (
    AgentMessage,
    ModelCompletion,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelStreamChunk,
    ReasoningMode,
    ToolChoiceMode,
)
from purra.errors import ModelGatewayError, UnsupportedModelFeatureError
from purra.model_call_parameters import describe_model_call
from purra.model_protocol import classify_model_termination
from purra.output_budget import (
    OutputBudgetPolicy,
    ResolvedOutputBudget,
    resolve_output_budget,
)
from purra.ports import CancellationSignal, ModelGateway


@dataclass(frozen=True, slots=True)
class ManagedModelCall:
    """Host-declared intent for one no-tool model operation."""

    request: ModelRequest
    output_policy: OutputBudgetPolicy
    context_window_tokens: int
    work_units: int = 1
    reasoning_mode: ReasoningMode = ReasoningMode.DEFAULT
    allow_reasoning_fallback: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.request, ModelRequest):
            raise TypeError("managed model call requires a ModelRequest")
        if not isinstance(self.output_policy, OutputBudgetPolicy):
            raise TypeError("managed model call requires an OutputBudgetPolicy")
        window = int(self.context_window_tokens)
        if window <= 0:
            raise ValueError("managed model context window must be positive")
        units = int(self.work_units)
        if units <= 0:
            raise ValueError("managed model work units must be positive")
        object.__setattr__(self, "context_window_tokens", window)
        object.__setattr__(self, "work_units", units)
        object.__setattr__(
            self,
            "reasoning_mode",
            ReasoningMode(self.reasoning_mode),
        )
        object.__setattr__(
            self,
            "allow_reasoning_fallback",
            bool(self.allow_reasoning_fallback),
        )


@dataclass(frozen=True, slots=True)
class ManagedModelCompletion:
    completion: ModelCompletion
    output_budget: ResolvedOutputBudget
    call_parameters: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class ManagedModelStream:
    chunks: AsyncIterator[ModelStreamChunk]
    model: str
    output_budget: ResolvedOutputBudget
    call_parameters: tuple[Mapping[str, object], ...]


class ManagedModelExecutor:
    """The only public PurrA boundary for host-owned model sub-operations."""

    def __init__(self, gateway: ModelGateway) -> None:
        if not isinstance(gateway, ModelGateway):
            raise TypeError("managed model executor requires a ModelGateway")
        self._gateway = gateway

    async def complete(
        self,
        messages: Sequence[AgentMessage],
        call: ManagedModelCall,
        signal: CancellationSignal | None = None,
        *,
        on_attempt: (
            Callable[[Mapping[str, object]], Awaitable[None]] | None
        ) = None,
    ) -> ManagedModelCompletion:
        attempts: list[Mapping[str, object]] = []
        for mode in _attempt_modes(call):
            invocation, budget = _resolve_invocation(call, mode)
            parameters = describe_model_call(
                self._gateway,
                messages,
                invocation,
            )
            attempts.append(parameters)
            if on_attempt is not None:
                await on_attempt(parameters)
            try:
                completion = await await_with_cancellation(
                    self._gateway.complete(messages, invocation, signal),
                    signal,
                )
            except UnsupportedModelFeatureError:
                if mode is not ReasoningMode.DISABLED or len(attempts) > 1:
                    raise
                continue
            _validate_completion(completion)
            return ManagedModelCompletion(
                completion=completion,
                output_budget=budget,
                call_parameters=tuple(attempts),
            )
        raise UnsupportedModelFeatureError(
            "model rejected the managed reasoning configuration"
        )

    async def stream(
        self,
        messages: Sequence[AgentMessage],
        call: ManagedModelCall,
        signal: CancellationSignal | None = None,
        *,
        on_attempt: (
            Callable[[Mapping[str, object]], Awaitable[None]] | None
        ) = None,
    ) -> ManagedModelStream:
        attempts: list[Mapping[str, object]] = []
        for mode in _attempt_modes(call):
            invocation, budget = _resolve_invocation(call, mode)
            parameters = describe_model_call(
                self._gateway,
                messages,
                invocation,
            )
            attempts.append(parameters)
            if on_attempt is not None:
                await on_attempt(parameters)
            try:
                stream = await await_with_cancellation(
                    self._gateway.stream(messages, invocation, signal),
                    signal,
                )
            except UnsupportedModelFeatureError:
                if mode is not ReasoningMode.DISABLED or len(attempts) > 1:
                    raise
                continue
            return ManagedModelStream(
                chunks=_validated_chunks(stream.chunks, signal),
                model=stream.model,
                output_budget=budget,
                call_parameters=tuple(attempts),
            )
        raise UnsupportedModelFeatureError(
            "model rejected the managed reasoning configuration"
        )


def _attempt_modes(call: ManagedModelCall) -> tuple[ReasoningMode, ...]:
    primary = call.reasoning_mode
    if primary is ReasoningMode.DISABLED and call.allow_reasoning_fallback:
        return primary, ReasoningMode.DEFAULT
    return (primary,)


def _resolve_invocation(
    call: ManagedModelCall,
    mode: ReasoningMode,
) -> tuple[ModelInvocation, ResolvedOutputBudget]:
    budget = resolve_output_budget(
        policy=call.output_policy,
        capabilities=call.request.output_capabilities,
        context_window_tokens=call.context_window_tokens,
        work_units=call.work_units,
        thinking_enabled=(
            mode is not ReasoningMode.DISABLED
            and _request_enables_thinking(call.request)
        ),
    )
    return ModelInvocation(
        request=call.request,
        tools=(),
        tool_choice=ToolChoiceMode.NONE,
        output_budget=budget,
        reasoning_mode=mode,
    ), budget


def _request_enables_thinking(request: ModelRequest) -> bool:
    thinking = request.options.get("thinking")
    return bool(
        request.options.get("thinking_enabled") is True
        or (
            isinstance(thinking, Mapping)
            and thinking.get("type") == "enabled"
        )
    )


def _validate_completion(completion: ModelCompletion) -> None:
    reason = completion.finish_reason
    if reason is None:
        raise ModelGatewayError(
            "model completion ended without a finish reason",
            code="upstream_stream_interrupted",
        )
    tool_count = len(completion.message.tool_calls)
    termination = classify_model_termination(reason, tool_call_count=tool_count)
    _raise_for_termination(termination, tool_count=tool_count)


async def _validated_chunks(
    chunks: AsyncIterator[ModelStreamChunk],
    signal: CancellationSignal | None,
) -> AsyncIterator[ModelStreamChunk]:
    finish_reason: ModelFinishReason | None = None
    tool_indices: set[int] = set()
    try:
        while True:
            try:
                chunk = await await_with_cancellation(anext(chunks), signal)
            except StopAsyncIteration:
                break
            tool_indices.update(delta.index for delta in chunk.tool_call_deltas)
            if chunk.finish_reason is not None:
                finish_reason = chunk.finish_reason
            yield chunk
            if finish_reason is not None:
                break
    finally:
        await _close_async_iterator(chunks)
    if finish_reason is None:
        raise ModelGatewayError(
            "model stream ended without a finish reason",
            code="upstream_stream_interrupted",
        )
    termination = classify_model_termination(
        finish_reason,
        tool_call_count=len(tool_indices),
    )
    _raise_for_termination(termination, tool_count=len(tool_indices))


def _raise_for_termination(termination, *, tool_count: int) -> None:
    if termination.incomplete:
        raise ModelGatewayError(
            "model output is incomplete",
            code=termination.error_code or "model_output_truncated",
            retryable=False,
        )
    if tool_count:
        raise ModelGatewayError(
            "managed no-tool model call returned tool calls",
            code="unexpected_model_tool_calls",
            retryable=False,
        )


async def _close_async_iterator(iterator: object) -> None:
    close = getattr(iterator, "aclose", None)
    if callable(close):
        await close()


__all__ = [
    "ManagedModelCall",
    "ManagedModelCompletion",
    "ManagedModelExecutor",
    "ManagedModelStream",
]
