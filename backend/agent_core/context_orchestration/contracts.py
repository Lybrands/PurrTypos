"""Provider-neutral contracts for context compression orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from agent_core.context_orchestration.ledger import ContextCompactionBudget
from agent_core.contracts import AgentRunRequest


@dataclass(frozen=True, slots=True)
class ContextCompressionSettings:
    """Generic Core timing and fallback-window settings.

    These values deliberately contain no semantic-summary policy.  Core uses
    them only to decide when a reduction hook must be offered and, when no
    hook is installed, how many recent raw messages the built-in trimmer may
    retain before fitting them to the actual token budget.
    """

    trigger_ratio: float = 0.85
    default_keep_recent_messages: int = 20

    def __post_init__(self) -> None:
        trigger = float(self.trigger_ratio)
        keep = int(self.default_keep_recent_messages)
        if not 0 < trigger <= 1:
            raise ValueError(
                "trigger_ratio must be greater than zero and at most one"
            )
        if keep <= 0:
            raise ValueError("default_keep_recent_messages must be positive")
        object.__setattr__(self, "trigger_ratio", trigger)
        object.__setattr__(self, "default_keep_recent_messages", keep)


@dataclass(frozen=True, slots=True)
class ContextCompressionRequest:
    """Immutable facts passed from Core to an application compression hook."""

    request: AgentRunRequest
    budget: ContextCompactionBudget
    message_tokens: int
    projected_input_tokens: int
    available_message_tokens: int
    pressure_ratio: float
    compression_required: bool
    trigger_reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.request, AgentRunRequest):
            raise TypeError("compression request requires an AgentRunRequest")
        if not isinstance(self.budget, ContextCompactionBudget):
            raise TypeError("compression request requires a budget snapshot")
        for name in (
            "message_tokens",
            "projected_input_tokens",
            "available_message_tokens",
        ):
            value = int(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)
        pressure = float(self.pressure_ratio)
        if pressure < 0:
            raise ValueError("pressure_ratio must be non-negative")
        object.__setattr__(self, "pressure_ratio", pressure)
        object.__setattr__(
            self,
            "compression_required",
            bool(self.compression_required),
        )
        reason = str(self.trigger_reason or "").strip()
        if not reason:
            raise ValueError("trigger_reason is required")
        object.__setattr__(self, "trigger_reason", reason)


@dataclass(frozen=True, slots=True)
class ConversationCompactionResult:
    """Validated output of one Core compaction decision and execution."""

    request: AgentRunRequest
    outcome: str
    compression_state_version: int | None = None
    compacted_turn_count: int = 0
    retained_raw_turn_count: int = 0
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.request, AgentRunRequest):
            raise TypeError("compaction request must be an AgentRunRequest")
        outcome = str(self.outcome or "").strip()
        if not outcome:
            raise ValueError("compaction outcome is required")
        object.__setattr__(self, "outcome", outcome)
        if self.compression_state_version is not None:
            state_version = int(self.compression_state_version)
            if state_version <= 0:
                raise ValueError(
                    "compression_state_version must be positive"
                )
            object.__setattr__(
                self,
                "compression_state_version",
                state_version,
            )
        for name in ("compacted_turn_count", "retained_raw_turn_count"):
            value = int(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "diagnostics",
            MappingProxyType(dict(self.diagnostics)),
        )


__all__ = [
    "ContextCompressionRequest",
    "ContextCompressionSettings",
    "ConversationCompactionResult",
]
