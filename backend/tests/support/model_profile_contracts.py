"""One provider-neutral contract executed against every registered profile."""

from __future__ import annotations

from collections.abc import Mapping

from infrastructure.models import provider_model_gateway
from infrastructure.models.profiles.base import ModelProfile
from purra.contracts import (
    AgentMessage,
    MessageRole,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ReasoningMode,
    ToolChoiceMode,
    ToolSchema,
)
from purra.model_protocol import resolve_invocation_output_limit


async def assert_profile_adapter_contract(profile: ModelProfile) -> None:
    snapshot = profile.capability_snapshot(context_window_tokens=1_000_000)
    assert snapshot.profile_id == profile.profile_id
    assert snapshot.provider_protocol == profile.provider_protocol
    assert snapshot.digest() == profile.capability_snapshot(
        context_window_tokens=1_000_000,
    ).digest()

    model = sorted(profile.model_names)[0]
    invocation = ModelInvocation(
        request=ModelRequest(
            provider="openai",
            model=model,
            capability_snapshot=snapshot,
            options={"model_profile": profile.profile_id},
        ),
        tools=(ToolSchema(
            name="publish_part",
            description="Publish one validated Part",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        ),),
        tool_choice=ToolChoiceMode.AUTO,
        output_limit=(
            resolve_invocation_output_limit(
                snapshot,
                explicit_user_override=None,
            )
            if snapshot.max_output_tokens is not None
            else None
        ),
        reasoning_mode=ReasoningMode.DEFAULT,
    )
    options = provider_model_gateway._provider_options(invocation)
    assert options["model"] == model
    assert options["model_profile"] == profile.profile_id
    assert options["tools"][0]["function"]["name"] == "publish_part"
    if snapshot.max_output_tokens is not None:
        assert options["max_tokens"] == snapshot.max_output_tokens
    else:
        assert "max_tokens" not in options

    raw_chunks = (
        profile.normalize_openai_chunk({
            "choices": [{
                "delta": {
                    "content": "done",
                    "reasoning": "reason",
                    "tool_calls": [{
                        "index": 0,
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "publish_part",
                            "arguments": '{"value":',
                        },
                    }],
                },
                "finish_reason": None,
            }],
        }),
        profile.normalize_openai_chunk({
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "function": {"arguments": '"ok"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {
                "prompt_tokens": 21,
                "completion_tokens": 8,
                "total_tokens": 29,
                "completion_tokens_details": {"reasoning_tokens": 3},
            },
        }),
    )

    closed = False

    async def raw_stream():
        nonlocal closed
        try:
            for chunk in raw_chunks:
                yield chunk
        finally:
            closed = True

    normalized = provider_model_gateway._normalize_openai_stream(raw_stream())
    chunks = [chunk async for chunk in normalized]
    assert closed is True
    assert "".join(chunk.content_delta for chunk in chunks) == "done"
    assert "".join(chunk.reasoning_delta for chunk in chunks) == "reason"
    assert "".join(
        delta.arguments_fragment
        for chunk in chunks
        for delta in chunk.tool_call_deltas
    ) == '{"value":"ok"}'
    assert chunks[-1].finish_reason is ModelFinishReason.TOOL_CALLS
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.input_tokens == 21
    assert chunks[-1].usage.output_tokens == 8
    assert chunks[-1].usage.reasoning_output_tokens == 3

    canceled = False

    async def cancelable_stream():
        nonlocal canceled
        try:
            yield profile.normalize_openai_chunk({
                "choices": [{"delta": {"content": "partial"}}],
            })
            yield profile.normalize_openai_chunk({
                "choices": [{"delta": {"content": "unreachable"}}],
            })
        finally:
            canceled = True

    owned = provider_model_gateway._normalize_openai_stream(cancelable_stream())
    iterator = owned.__aiter__()
    first = await iterator.__anext__()
    assert first.content_delta == "partial"
    await owned.aclose()
    assert canceled is True

    replayed = provider_model_gateway._provider_message(
        AgentMessage(
            role=MessageRole.ASSISTANT,
            content="answer",
            reasoning="reason",
        ),
        reasoning_replay=snapshot.protocol.reasoning_replay,
    )
    if snapshot.protocol.reasoning_replay.value == "required":
        assert replayed["reasoning_content"] == "reason"
    else:
        assert "reasoning_content" not in replayed


def assert_core_has_no_registered_profile_names(
    profile: ModelProfile,
    core_sources: Mapping[str, str],
) -> None:
    needles = {
        profile.profile_id.lower(),
        *(name.lower() for name in profile.model_names),
    }
    for path, source in core_sources.items():
        lowered = source.lower()
        for needle in needles:
            assert needle not in lowered, (
                f"PurrA Core reads registered profile identity {needle!r} in {path}"
            )
