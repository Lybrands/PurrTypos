"""Model-round state that is independent from Runtime orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from agent_core.contracts import (
    AgentMessage,
    ModelFinishReason,
    ModelInvocation,
    ModelTokenUsage,
    ToolCall,
)


@dataclass(frozen=True, slots=True)
class PendingProviderAttempt:
    """Frozen inputs for a provider retry that consumes the next round slot."""

    messages: tuple[AgentMessage, ...]
    invocation: ModelInvocation
    allowed_names: frozenset[str]
    future_names: frozenset[str]
    require_tool: bool
    buffer_model_content: bool
    logical_round: int
    attempt: int


@dataclass(slots=True)
class _ToolCallParts:
    id: str = ""
    name: str = ""
    arguments: str = ""


class ModelRoundAccumulator:
    """Accumulate one provider stream without making lifecycle decisions."""

    def __init__(self) -> None:
        self.content = ""
        self.thinking = ""
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
        self.thinking += chunk.thinking_delta
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
