"""Managed provider calls for host hooks and bounded framework operations.

Applications supply a versioned model snapshot and optional user override.
PurrA resolves the exact provider output limit, creates ``ModelInvocation``,
and classifies the terminal provider reason.
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
from purra.model_protocol import (
    InvocationOutputLimit,
    classify_model_termination,
    resolve_invocation_output_limit,
)
from purra.ports import CancellationSignal, ModelGateway


@dataclass(frozen=True, slots=True)
class ManagedModelCall:
    """Host-declared intent for one no-tool model operation."""

    request: ModelRequest
    output_limit: InvocationOutputLimit | None = None
    reasoning_mode: ReasoningMode = ReasoningMode.DEFAULT

    def __post_init__(self) -> None:
        if not isinstance(self.request, ModelRequest):
            raise TypeError("managed model call requires a ModelRequest")
        limit = self.output_limit or resolve_invocation_output_limit(
            self.request.capability_snapshot,
            self.request.options.get("max_tokens"),
        )
        if not isinstance(limit, InvocationOutputLimit):
            raise TypeError("managed model call requires an InvocationOutputLimit")
        object.__setattr__(self, "output_limit", limit)
        object.__setattr__(
            self,
            "reasoning_mode",
            ReasoningMode(self.reasoning_mode),
        )


@dataclass(frozen=True, slots=True)
class ManagedModelCompletion:
    completion: ModelCompletion
    output_limit: InvocationOutputLimit
    call_parameters: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class ManagedModelStream:
    chunks: AsyncIterator[ModelStreamChunk]
    model: str
    output_limit: InvocationOutputLimit
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
        invocation, output_limit = _resolve_invocation(
            call,
            call.reasoning_mode,
        )
        parameters = describe_model_call(
            self._gateway,
            messages,
            invocation,
        )
        if on_attempt is not None:
            await on_attempt(parameters)
        completion = await await_with_cancellation(
            self._gateway.complete(messages, invocation, signal),
            signal,
        )
        _validate_completion(completion)
        return ManagedModelCompletion(
            completion=completion,
            output_limit=output_limit,
            call_parameters=(parameters,),
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
        invocation, output_limit = _resolve_invocation(
            call,
            call.reasoning_mode,
        )
        parameters = describe_model_call(
            self._gateway,
            messages,
            invocation,
        )
        if on_attempt is not None:
            await on_attempt(parameters)
        stream = await await_with_cancellation(
            self._gateway.stream(messages, invocation, signal),
            signal,
        )
        return ManagedModelStream(
            chunks=_validated_chunks(stream.chunks, signal),
            model=stream.model,
            output_limit=output_limit,
            call_parameters=(parameters,),
        )


def _resolve_invocation(
    call: ManagedModelCall,
    mode: ReasoningMode,
) -> tuple[ModelInvocation, InvocationOutputLimit]:
    if not call.request.protocol_capabilities.reasoning_mode_is_supported(mode):
        raise UnsupportedModelFeatureError(
            "selected reasoning mode is incompatible with model capabilities"
        )
    output_limit = call.output_limit
    if output_limit is None:  # normalized by ManagedModelCall.__post_init__
        raise TypeError("managed model call output limit was not resolved")
    return ModelInvocation(
        request=call.request,
        tools=(),
        tool_choice=ToolChoiceMode.NONE,
        output_limit=output_limit,
        reasoning_mode=mode,
    ), output_limit


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
