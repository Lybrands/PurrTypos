"""Single Provider invocation boundary owned by PurrA."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Protocol
from uuid import uuid4

from purra.cancellation import await_with_cancellation
from purra.contracts import (
    AgentMessage,
    ModelFinishReason,
    ModelInvocation,
    ModelStreamChunk,
)
from purra.errors import ContractViolationError, ModelGatewayError
from purra.model_call_parameters import describe_model_call
from purra.model_invocation.contracts import (
    AgentModelCall,
    ManagedInvocationCompletion,
    ManagedInvocationStream,
    ModelInvocationContext,
    ModelInvocationReceipt,
)
from purra.model_protocol import classify_model_termination
from purra.output import AgentOutputIntent, OutputCommitMode, OutputStreamSpec
from purra.ports import CancellationSignal, ModelGateway


class ModelInvocationOutputObserver(Protocol):
    async def open_model_stream(
        self,
        receipt: ModelInvocationReceipt,
        spec: OutputStreamSpec,
    ) -> object: ...

    async def accept_provider_chunk(
        self,
        output_stream_id: str,
        chunk: ModelStreamChunk,
    ) -> object: ...

    async def finish_model_stream(
        self,
        output_stream_id: str,
        finish_reason: ModelFinishReason,
    ) -> object: ...

    async def abort_model_stream(
        self,
        output_stream_id: str,
        error_code: str,
    ) -> object: ...


class _NullOutputObserver:
    async def open_model_stream(self, receipt, spec):
        del receipt, spec

    async def accept_provider_chunk(self, output_stream_id, chunk):
        del output_stream_id, chunk

    async def finish_model_stream(self, output_stream_id, finish_reason):
        del output_stream_id, finish_reason

    async def abort_model_stream(self, output_stream_id, error_code):
        del output_stream_id, error_code


class AgentModelInvocationManager:
    """Authorize, identify, execute, observe, and classify Provider calls."""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        output_observer: ModelInvocationOutputObserver | None = None,
    ) -> None:
        if not isinstance(gateway, ModelGateway):
            raise TypeError("model invocation manager requires a ModelGateway")
        self._gateway = gateway
        self._output = output_observer or _NullOutputObserver()

    async def stream(
        self,
        messages: Sequence[AgentMessage],
        call: AgentModelCall,
        context: ModelInvocationContext,
        signal: CancellationSignal | None = None,
        *,
        on_attempt: Callable[[Mapping[str, object]], Awaitable[None]] | None = None,
    ) -> ManagedInvocationStream:
        self._validate_call(call, public_stream_allowed=True)
        invocation = _invocation(call)
        receipt, spec = self._receipt_and_spec(messages, call, context, invocation)
        if on_attempt is not None:
            await on_attempt(receipt.call_parameters[0])
        await self._output.open_model_stream(receipt, spec)
        try:
            stream = await await_with_cancellation(
                self._gateway.stream(messages, invocation, signal),
                signal,
            )
        except BaseException as error:
            await self._output.abort_model_stream(
                receipt.output_stream_id,
                _error_code(error),
            )
            raise
        return ManagedInvocationStream(
            chunks=self._observe_chunks(stream.chunks, receipt, signal),
            receipt=receipt,
        )

    async def complete(
        self,
        messages: Sequence[AgentMessage],
        call: AgentModelCall,
        context: ModelInvocationContext,
        signal: CancellationSignal | None = None,
        *,
        on_attempt: Callable[[Mapping[str, object]], Awaitable[None]] | None = None,
    ) -> ManagedInvocationCompletion:
        self._validate_call(call, public_stream_allowed=False)
        invocation = _invocation(call)
        receipt, spec = self._receipt_and_spec(messages, call, context, invocation)
        if on_attempt is not None:
            await on_attempt(receipt.call_parameters[0])
        await self._output.open_model_stream(receipt, spec)
        try:
            completion = await await_with_cancellation(
                self._gateway.complete(messages, invocation, signal),
                signal,
            )
            reason = completion.finish_reason
            if reason is None:
                raise ModelGatewayError(
                    "model completion ended without a finish reason",
                    code="upstream_stream_interrupted",
                )
            termination = classify_model_termination(
                reason,
                tool_call_count=len(completion.message.tool_calls),
            )
            if termination.incomplete:
                raise ModelGatewayError(
                    "model output is incomplete",
                    code=termination.error_code or "model_output_truncated",
                    retryable=False,
                )
            if completion.message.tool_calls and not call.tools:
                raise ModelGatewayError(
                    "managed no-tool model call returned tool calls",
                    code="unexpected_model_tool_calls",
                    retryable=False,
                )
            await self._output.finish_model_stream(
                receipt.output_stream_id,
                reason,
            )
            return ManagedInvocationCompletion(
                completion=completion,
                receipt=receipt,
            )
        except BaseException as error:
            await self._output.abort_model_stream(
                receipt.output_stream_id,
                _error_code(error),
            )
            raise

    @staticmethod
    def _validate_call(
        call: AgentModelCall,
        *,
        public_stream_allowed: bool,
    ) -> None:
        if not isinstance(call, AgentModelCall):
            raise TypeError("model invocation manager requires an AgentModelCall")
        if call.requires_full_text_validation and call.commit_mode is OutputCommitMode.LIVE:
            raise ContractViolationError(
                "full-text validation cannot use live output"
            )
        if (
            not public_stream_allowed
            and call.output_intent
            in {
                AgentOutputIntent.EXECUTION_PUBLIC,
                AgentOutputIntent.FINAL_PUBLIC,
            }
        ):
            raise ContractViolationError(
                "public output requires the streaming Provider boundary"
            )

    def _receipt_and_spec(
        self,
        messages: Sequence[AgentMessage],
        call: AgentModelCall,
        context: ModelInvocationContext,
        invocation: ModelInvocation,
    ) -> tuple[ModelInvocationReceipt, OutputStreamSpec]:
        if not isinstance(context, ModelInvocationContext):
            raise TypeError("model invocation requires a ModelInvocationContext")
        invocation_id = f"invocation-{uuid4().hex}"
        output_stream_id = f"output-{uuid4().hex}"
        parameters = describe_model_call(self._gateway, messages, invocation)
        receipt = ModelInvocationReceipt(
            invocation_id=invocation_id,
            output_stream_id=output_stream_id,
            run_id=context.run_id,
            turn_id=context.turn_id,
            model=call.request.model,
            output_intent=call.output_intent,
            commit_mode=call.commit_mode,
            output_limit=call.output_limit,
            call_parameters=(parameters,),
        )
        return receipt, OutputStreamSpec(
            output_stream_id=output_stream_id,
            run_id=context.run_id,
            turn_id=context.turn_id,
            invocation_id=invocation_id,
            intent=call.output_intent,
            commit_mode=call.commit_mode,
        )

    async def _observe_chunks(
        self,
        chunks: AsyncIterator[ModelStreamChunk],
        receipt: ModelInvocationReceipt,
        signal: CancellationSignal | None,
    ) -> AsyncIterator[ModelStreamChunk]:
        finish_reason: ModelFinishReason | None = None
        tool_indices: set[int] = set()
        settled = False
        try:
            while True:
                try:
                    chunk = await await_with_cancellation(anext(chunks), signal)
                except StopAsyncIteration:
                    break
                tool_indices.update(delta.index for delta in chunk.tool_call_deltas)
                await self._output.accept_provider_chunk(
                    receipt.output_stream_id,
                    chunk,
                )
                if chunk.finish_reason is not None:
                    finish_reason = chunk.finish_reason
                    termination = classify_model_termination(
                        finish_reason,
                        tool_call_count=len(tool_indices),
                    )
                    if termination.incomplete:
                        await self._output.abort_model_stream(
                            receipt.output_stream_id,
                            termination.error_code or "model_output_truncated",
                        )
                    else:
                        await self._output.finish_model_stream(
                            receipt.output_stream_id,
                            finish_reason,
                        )
                    settled = True
                    yield chunk
                    break
                yield chunk
            if finish_reason is None:
                raise ModelGatewayError(
                    "model stream ended without a finish reason",
                    code="upstream_stream_interrupted",
                    retryable=True,
                )
        except BaseException as error:
            await self._output.abort_model_stream(
                receipt.output_stream_id,
                _error_code(error),
            )
            settled = True
            raise
        finally:
            await _close_async_iterator(chunks)
            if not settled:
                await self._output.abort_model_stream(
                    receipt.output_stream_id,
                    "invocation_consumer_closed",
                )


def _invocation(call: AgentModelCall) -> ModelInvocation:
    if not call.request.protocol_capabilities.reasoning_mode_is_supported(
        call.reasoning_mode
    ):
        from purra.errors import UnsupportedModelFeatureError

        raise UnsupportedModelFeatureError(
            "selected reasoning mode is incompatible with model capabilities"
        )
    return ModelInvocation(
        request=call.request,
        tools=call.tools,
        tool_choice=call.tool_choice,
        output_limit=call.output_limit,
        reasoning_mode=call.reasoning_mode,
    )


def _error_code(error: BaseException) -> str:
    code = str(getattr(error, "code", "") or "").strip()
    return code or "model_invocation_failed"


async def _close_async_iterator(iterator: object) -> None:
    close = getattr(iterator, "aclose", None)
    if callable(close):
        await close()


__all__ = ["AgentModelInvocationManager", "ModelInvocationOutputObserver"]
