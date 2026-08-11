"""Model-round state that is independent from Runtime orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from purra.contracts import (
    AgentMessage,
    ModelFinishReason,
    ModelInvocation,
    ModelTokenUsage,
    ToolCall,
)
from purra.model_invocation.contracts import AgentModelCall
from purra.output import AgentOutputIntent, OutputCommitMode
from purra.recovery import RecoveryCause, RecoveryPolicy


@dataclass(frozen=True, slots=True)
class PendingProviderAttempt:
    """Frozen inputs for a physical attempt in one logical model round."""

    messages: tuple[AgentMessage, ...]
    invocation: ModelInvocation
    allowed_names: frozenset[str]
    future_names: frozenset[str]
    require_tool: bool
    buffer_model_content: bool
    logical_round: int
    attempt: int


def provider_retry_round_capacity(policy: RecoveryPolicy) -> int:
    """Return the bounded physical-attempt allowance outside logical rounds."""

    return sum(
        policy.max_attempts(cause)
        for cause in (
            RecoveryCause.PROVIDER_REQUIRED_TOOL_CHOICE_UNSUPPORTED,
            RecoveryCause.PROVIDER_STREAM_INTERRUPTED,
        )
    )


def build_agent_model_call(
    invocation: ModelInvocation,
    *,
    require_tool: bool,
    requires_full_text_validation: bool,
) -> AgentModelCall:
    """Assign one model round to the closed public/private output contract."""

    private_round = bool(invocation.tools or require_tool)
    if private_round:
        intent = AgentOutputIntent.STRUCTURED_PRIVATE
        commit_mode = OutputCommitMode.PRIVATE
    elif requires_full_text_validation:
        intent = AgentOutputIntent.STRUCTURED_PRIVATE
        commit_mode = OutputCommitMode.GATED
    else:
        intent = AgentOutputIntent.FINAL_PUBLIC
        commit_mode = OutputCommitMode.LIVE
    return AgentModelCall(
        request=invocation.request,
        output_intent=intent,
        commit_mode=commit_mode,
        requires_full_text_validation=requires_full_text_validation,
        reasoning_mode=invocation.reasoning_mode,
        output_limit=invocation.output_limit,
        tools=invocation.tools,
        tool_choice=invocation.tool_choice,
    )


def truncation_trace_details(
    *,
    accumulator: "ModelRoundAccumulator",
    round_number: int,
    attempt: int,
    request_fingerprint: str,
    finish_reason: ModelFinishReason,
    error_code: str,
    emitted_delta_count: int,
    output_limit: Any | None,
) -> dict[str, Any]:
    return {
        "round": round_number,
        "attempt": attempt,
        "requestFingerprint": request_fingerprint,
        "finishReason": finish_reason.value,
        "errorCode": error_code,
        "retryScheduled": False,
        "batchExecuted": False,
        "toolCallCount": accumulator.tool_call_count,
        "toolNames": list(accumulator.tool_call_names),
        "toolArgumentCharacters": accumulator.tool_argument_characters,
        "contentCharacters": len(accumulator.content),
        "reasoningOnly": bool(
            not accumulator.content.strip()
            and accumulator.reasoning.strip()
        ),
        "emittedDeltaCount": emitted_delta_count,
        "outputLimit": (
            output_limit.to_mapping() if output_limit is not None else None
        ),
    }


@dataclass(slots=True)
class _ToolCallParts:
    id: str = ""
    name: str = ""
    arguments: str = ""


class ModelRoundAccumulator:
    """Accumulate one provider stream without making lifecycle decisions."""

    def __init__(self) -> None:
        self.content = ""
        self.reasoning = ""
        self.finish_reason: ModelFinishReason | None = None
        self.usage: ModelTokenUsage | None = None
        self._calls: dict[int, _ToolCallParts] = {}
        self._malformed_reason: str | None = None

    @property
    def tool_call_count(self) -> int:
        return len(self._calls)

    @property
    def tool_argument_characters(self) -> int:
        return sum(len(parts.arguments) for parts in self._calls.values())

    @property
    def tool_call_names(self) -> tuple[str, ...]:
        return tuple(
            parts.name
            for _, parts in sorted(self._calls.items())
            if parts.name
        )

    def add(self, chunk) -> None:
        self.content += chunk.content_delta
        self.reasoning += chunk.reasoning_delta
        if chunk.finish_reason is not None:
            self.finish_reason = chunk.finish_reason
        if chunk.usage is not None:
            self.usage = chunk.usage
        for delta in chunk.tool_call_deltas:
            current = self._calls.setdefault(delta.index, _ToolCallParts())
            if delta.id is not None:
                call_id = str(delta.id).strip()
                if current.id and call_id != current.id:
                    self._malformed_reason = "conflicting_tool_call_id_for_index"
                else:
                    current.id = call_id
            if delta.name is not None:
                name = str(delta.name).strip()
                if current.name and name != current.name:
                    self._malformed_reason = "conflicting_tool_name_for_index"
                else:
                    current.name = name
            current.arguments += str(delta.arguments_fragment or "")

    def tool_calls(self) -> tuple[tuple[ToolCall, ...], str | None]:
        if self._malformed_reason is not None:
            return (), self._malformed_reason
        if not self._calls:
            return (), None
        ordered = tuple(parts for _, parts in sorted(self._calls.items()))
        if any(not parts.id.strip() for parts in ordered):
            return (), "missing_tool_call_id"
        if any(not parts.name.strip() for parts in ordered):
            return (), "missing_tool_call_name"
        ids = tuple(parts.id for parts in ordered)
        if len(ids) != len(set(ids)):
            return (), "duplicate_tool_call_id"
        return (
            tuple(
                ToolCall(
                    id=parts.id,
                    name=parts.name,
                    arguments_json=parts.arguments,
                )
                for parts in ordered
            ),
            None,
        )
