from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from purra.contracts import (
    AgentMessage,
    ModelCompletion,
    ModelFinishReason,
    ModelRequest,
    ModelStream,
    ModelStreamChunk,
    ReasoningMode,
)
from purra.errors import ModelGatewayError, UnsupportedModelFeatureError
from purra.model_execution import (
    ManagedModelCall,
    ManagedModelExecutor,
)
from purra.model_protocol import generic_capability_snapshot


def _call() -> ManagedModelCall:
    return ManagedModelCall(
        request=ModelRequest(
            provider="test",
            model="model",
            capability_snapshot=replace(
                generic_capability_snapshot(),
                profile_id="test:model",
                max_output_tokens=200,
            ),
        ),
        reasoning_mode=ReasoningMode.DISABLED,
    )


class _Gateway:
    def __init__(self, *, finish_reason=ModelFinishReason.STOP):
        self.finish_reason = finish_reason
        self.invocations = []

    async def complete(self, messages, invocation, signal=None):
        del messages, signal
        self.invocations.append(invocation)
        return ModelCompletion(
            message=AgentMessage(role="assistant", content="done"),
            model="model",
            finish_reason=self.finish_reason,
        )

    async def stream(self, messages, invocation, signal=None):
        del messages, signal
        self.invocations.append(invocation)

        async def chunks():
            yield ModelStreamChunk(content_delta="done")
            yield ModelStreamChunk(finish_reason=self.finish_reason)

        return ModelStream(chunks=chunks(), model="model")


def test_complete_resolves_the_exact_provider_output_limit():
    async def run():
        gateway = _Gateway()
        result = await ManagedModelExecutor(gateway).complete((), _call())
        assert result.completion.message.content == "done"
        assert result.output_limit.max_tokens == 200
        assert gateway.invocations[0].output_limit == result.output_limit
        assert gateway.invocations[0].max_output_tokens == 200

    asyncio.run(run())


def test_reasoning_incompatibility_is_not_replayed_with_another_mode():
    class Gateway(_Gateway):
        async def complete(self, messages, invocation, signal=None):
            if not self.invocations:
                self.invocations.append(invocation)
                raise UnsupportedModelFeatureError()
            return await super().complete(messages, invocation, signal)

    async def run():
        gateway = Gateway()
        with pytest.raises(UnsupportedModelFeatureError):
            await ManagedModelExecutor(gateway).complete((), _call())
        assert [item.reasoning_mode for item in gateway.invocations] == [
            ReasoningMode.DISABLED,
        ]

    asyncio.run(run())


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        (ModelFinishReason.LENGTH, "model_output_truncated"),
        (None, "upstream_stream_interrupted"),
    ],
)
def test_complete_rejects_non_terminal_or_incomplete_output_without_retry(
    reason,
    code,
):
    async def run():
        gateway = _Gateway(finish_reason=reason)
        with pytest.raises(ModelGatewayError) as captured:
            await ManagedModelExecutor(gateway).complete((), _call())
        assert captured.value.code == code
        assert len(gateway.invocations) == 1

    asyncio.run(run())


def test_stream_rejects_truncation_after_preserving_diagnostic_chunks():
    async def run():
        gateway = _Gateway(finish_reason=ModelFinishReason.LENGTH)
        stream = await ManagedModelExecutor(gateway).stream((), _call())
        observed = []
        with pytest.raises(ModelGatewayError) as captured:
            async for chunk in stream.chunks:
                observed.append(chunk)
        assert captured.value.code == "model_output_truncated"
        assert "".join(chunk.content_delta for chunk in observed) == "done"
        assert len(gateway.invocations) == 1

    asyncio.run(run())
