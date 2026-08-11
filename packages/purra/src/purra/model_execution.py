"""Managed provider calls for host hooks and bounded framework operations.

Applications supply a versioned model snapshot and optional user override.
PurrA resolves the exact provider output limit, creates ``ModelInvocation``,
and classifies the terminal provider reason.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from uuid import uuid4

from purra.cancellation import await_with_cancellation, is_canceled
from purra.contracts import (
    AgentMessage,
    ModelCompletion,
    ModelFinishReason,
    ModelRequest,
    ModelStreamChunk,
    ModelTokenUsage,
    MessageRole,
    ReasoningMode,
)
from purra.errors import ModelGatewayError
from purra.model_invocation import (
    AgentModelCall,
    AgentModelInvocationManager,
    ModelInvocationContext,
)
from purra.model_protocol import (
    InvocationOutputLimit,
    classify_model_termination,
    resolve_invocation_output_limit,
)
from purra.output import AgentOutputIntent, OutputCommitMode
from purra.ports import CancellationSignal, ModelGateway
from purra.recovery import (
    EMPTY_RESPONSE_RETRY_GUIDANCE,
    RecoveryAction,
    RecoveryCause,
    RecoveryLedger,
    RecoveryPolicy,
    RecoveryRequest,
)


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


@dataclass(frozen=True, slots=True)
class ManagedModelTextResult:
    content: str
    reasoning: str
    finish_reason: ModelFinishReason
    usage: ModelTokenUsage | None
    attempts: int


ManagedChunkObserver = Callable[[ModelStreamChunk], Awaitable[None]]


class ManagedModelExecutor:
    """The only public PurrA boundary for host-owned model sub-operations."""

    def __init__(self, gateway: ModelGateway) -> None:
        self._manager = AgentModelInvocationManager(gateway)

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
        managed = await self._manager.complete(
            messages,
            _agent_call(call),
            _detached_context(),
            signal,
            on_attempt=on_attempt,
        )
        return ManagedModelCompletion(
            completion=managed.completion,
            output_limit=managed.receipt.output_limit,
            call_parameters=managed.receipt.call_parameters,
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
        managed = await self._manager.stream(
            messages,
            _agent_call(call),
            _detached_context(),
            signal,
            on_attempt=on_attempt,
        )
        return ManagedModelStream(
            chunks=_validated_chunks(managed.chunks, signal),
            model=managed.receipt.model,
            output_limit=managed.receipt.output_limit,
            call_parameters=managed.receipt.call_parameters,
        )

    async def stream_text(
        self,
        messages: Sequence[AgentMessage],
        call: ManagedModelCall,
        signal: CancellationSignal | None = None,
        *,
        on_attempt: (
            Callable[[Mapping[str, object]], Awaitable[None]] | None
        ) = None,
        on_chunk: ManagedChunkObserver | None = None,
        recovery_policy: RecoveryPolicy = RecoveryPolicy(),
    ) -> ManagedModelTextResult:
        """Collect one no-tool response with bounded empty-output recovery."""

        original_messages = tuple(messages)
        active_messages = original_messages
        recovery_ledger = RecoveryLedger(recovery_policy)
        attempt = 0
        while True:
            attempt += 1
            managed = await self.stream(
                active_messages,
                call,
                signal,
                on_attempt=on_attempt,
            )
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            finish_reason: ModelFinishReason | None = None
            usage: ModelTokenUsage | None = None
            try:
                async for chunk in managed.chunks:
                    if on_chunk is not None:
                        await on_chunk(chunk)
                    content_parts.append(chunk.content_delta)
                    reasoning_parts.append(chunk.reasoning_delta)
                    if chunk.finish_reason is not None:
                        finish_reason = chunk.finish_reason
                    if chunk.usage is not None:
                        usage = chunk.usage
            finally:
                await _close_async_iterator(managed.chunks)

            if finish_reason is None:
                raise ModelGatewayError(
                    "model stream ended without a finish reason",
                    code="upstream_stream_interrupted",
                )
            content = "".join(content_parts)
            reasoning = "".join(reasoning_parts)
            if content.strip():
                return ManagedModelTextResult(
                    content=content,
                    reasoning=reasoning,
                    finish_reason=finish_reason,
                    usage=usage,
                    attempts=attempt,
                )

            remaining_retries = (
                recovery_policy.max_attempts(RecoveryCause.EMPTY_MODEL_RESPONSE)
                - recovery_ledger.attempts(RecoveryCause.EMPTY_MODEL_RESPONSE)
            )
            decision = recovery_ledger.decide(RecoveryRequest(
                cause=RecoveryCause.EMPTY_MODEL_RESPONSE,
                action=RecoveryAction.RETRY_MODEL,
                remaining_model_rounds=remaining_retries,
                cancellation_requested=is_canceled(signal),
            ))
            if not decision.allowed:
                raise ModelGatewayError(
                    "model returned no official response content",
                    code="empty_model_response",
                    retryable=False,
                )
            active_messages = (*original_messages, AgentMessage(
                role=MessageRole.ASSISTANT,
                content="",
                reasoning=reasoning or None,
            ), AgentMessage(
                role=MessageRole.DEVELOPER,
                content=EMPTY_RESPONSE_RETRY_GUIDANCE,
            ))


def _agent_call(call: ManagedModelCall) -> AgentModelCall:
    return AgentModelCall(
        request=call.request,
        output_intent=AgentOutputIntent.STRUCTURED_PRIVATE,
        commit_mode=OutputCommitMode.PRIVATE,
        requires_full_text_validation=True,
        reasoning_mode=call.reasoning_mode,
        output_limit=call.output_limit,
    )


def _detached_context() -> ModelInvocationContext:
    return ModelInvocationContext(run_id=f"detached-model-{uuid4().hex}")


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
    if termination.incomplete:
        raise ModelGatewayError(
            "model output is incomplete",
            code=termination.error_code or "model_output_truncated",
            retryable=False,
        )
    if tool_indices:
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
    "ManagedModelTextResult",
]
