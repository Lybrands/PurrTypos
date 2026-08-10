"""Provider-neutral output budget contracts and resolution.

Model capability answers what the provider can support.  Task policy answers
what one unit of work should be allowed to produce.  A resolved budget is the
only value that may become a provider ``max_tokens`` style parameter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from purra.normalization import (
    non_negative_int,
    optional_positive_int,
    positive_int,
    required_text,
)

class ThinkingTokenAccounting(StrEnum):
    INCLUDED = "included"
    SEPARATE = "separate"
    UNKNOWN = "unknown"


class OutputBudgetLimit(StrEnum):
    TASK_ESTIMATE = "task_estimate"
    TASK_HARD_CAP = "task_hard_cap"
    MODEL_CAPABILITY = "model_capability"
    CONTEXT_AVAILABLE = "context_available"


@dataclass(frozen=True, slots=True)
class ModelOutputCapabilities:
    """Stable model capability facts, never per-task preferences."""

    max_output_tokens: int | None = None
    thinking_token_accounting: ThinkingTokenAccounting = (
        ThinkingTokenAccounting.UNKNOWN
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_output_tokens", optional_positive_int(
            self.max_output_tokens, "model max output tokens"
        ))
        object.__setattr__(
            self,
            "thinking_token_accounting",
            ThinkingTokenAccounting(self.thinking_token_accounting),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "maxOutputTokens": self.max_output_tokens,
            "thinkingTokenAccounting": self.thinking_token_accounting.value,
        }


@dataclass(frozen=True, slots=True)
class OutputBudgetPolicy:
    """Task-owned sizing policy for one model invocation."""

    key: str
    base_tokens: int
    per_work_unit_tokens: int
    safety_factor: float
    hard_cap_tokens: int
    reasoning_reserve_tokens: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", required_text(
            self.key, "output budget policy key"
        ))
        for name in ("base_tokens", "per_work_unit_tokens", "reasoning_reserve_tokens"):
            object.__setattr__(self, name, non_negative_int(getattr(self, name), name))
        object.__setattr__(self, "hard_cap_tokens", positive_int(
            self.hard_cap_tokens, "hard_cap_tokens"
        ))
        factor = float(self.safety_factor)
        if not math.isfinite(factor) or factor < 1:
            raise ValueError("output budget safety factor must be at least one")
        object.__setattr__(self, "safety_factor", factor)
    def target_tokens(self, work_units: int) -> int:
        units = max(1, int(work_units))
        return max(1, self.base_tokens + self.per_work_unit_tokens * units)


@dataclass(frozen=True, slots=True)
class ResolvedOutputBudget:
    policy_key: str
    work_units: int
    target_tokens: int
    requested_tokens: int
    effective_tokens: int
    reasoning_reserve_tokens: int
    thinking_enabled: bool
    task_hard_cap_tokens: int
    model_max_output_tokens: int | None
    context_max_output_tokens: int
    limiting_factor: OutputBudgetLimit

    def __post_init__(self) -> None:
        for name in (
            "work_units",
            "target_tokens",
            "requested_tokens",
            "effective_tokens",
            "reasoning_reserve_tokens",
            "task_hard_cap_tokens",
            "context_max_output_tokens",
        ):
            normalizer = (
                non_negative_int
                if name == "reasoning_reserve_tokens"
                else positive_int
            )
            object.__setattr__(self, name, normalizer(getattr(self, name), name))
        object.__setattr__(
            self,
            "model_max_output_tokens",
            optional_positive_int(
                self.model_max_output_tokens,
                "model max output tokens",
            ),
        )
        object.__setattr__(
            self,
            "limiting_factor",
            OutputBudgetLimit(self.limiting_factor),
        )
    def to_mapping(self) -> dict[str, object]:
        return {
            "policyKey": self.policy_key,
            "workUnits": self.work_units,
            "targetTokens": self.target_tokens,
            "requestedTokens": self.requested_tokens,
            "effectiveTokens": self.effective_tokens,
            "reasoningReserveTokens": self.reasoning_reserve_tokens,
            "thinkingEnabled": self.thinking_enabled,
            "taskHardCapTokens": self.task_hard_cap_tokens,
            "modelMaxOutputTokens": self.model_max_output_tokens,
            "contextMaxOutputTokens": self.context_max_output_tokens,
            "limitingFactor": self.limiting_factor.value,
        }


def resolve_output_budget(
    *,
    policy: OutputBudgetPolicy,
    capabilities: ModelOutputCapabilities,
    context_window_tokens: int,
    work_units: int = 1,
    thinking_enabled: bool = False,
) -> ResolvedOutputBudget:
    """Resolve the one authoritative output allowance for an invocation."""

    window = int(context_window_tokens)
    if window <= 0:
        raise ValueError("context window tokens must be positive")
    units = max(1, int(work_units))
    target = policy.target_tokens(units)
    # Providers differ on whether hidden reasoning consumes the same output
    # allowance as the visible completion.  Unknown accounting is treated
    # conservatively when thinking is enabled; a separate allowance must be
    # explicitly declared by the model profile before Core omits this reserve.
    reasoning_reserve = (
        policy.reasoning_reserve_tokens
        if thinking_enabled
        and capabilities.thinking_token_accounting
        is not ThinkingTokenAccounting.SEPARATE
        else 0
    )
    requested = max(
        1,
        math.ceil(target * policy.safety_factor) + reasoning_reserve,
    )

    # Keep enough space for provider input plus Core's safety/runtime envelopes.
    # The context allocator performs the exact schema-aware calculation later;
    # this upper bound prevents an output request from making that allocation
    # impossible before the actual tool set is known.
    safety_reserve = min(64_000, max(4_096, math.ceil(window * 0.05)))
    runtime_reserve = min(64_000, max(4_096, window // 10))
    minimum_input = min(8_192, max(1_024, window // 100))
    context_maximum = min(
        max(1, window // 2),
        max(
            1,
            window - safety_reserve - runtime_reserve - minimum_input,
        ),
    )

    candidates = [
        (OutputBudgetLimit.TASK_ESTIMATE, requested),
        (OutputBudgetLimit.TASK_HARD_CAP, policy.hard_cap_tokens),
    ]
    if capabilities.max_output_tokens is not None:
        candidates.append((
            OutputBudgetLimit.MODEL_CAPABILITY,
            capabilities.max_output_tokens,
        ))
    candidates.append((OutputBudgetLimit.CONTEXT_AVAILABLE, context_maximum))
    limiting_factor, effective = min(candidates, key=lambda item: item[1])

    return ResolvedOutputBudget(
        policy_key=policy.key,
        work_units=units,
        target_tokens=target,
        requested_tokens=requested,
        effective_tokens=effective,
        reasoning_reserve_tokens=reasoning_reserve,
        thinking_enabled=bool(thinking_enabled),
        task_hard_cap_tokens=policy.hard_cap_tokens,
        model_max_output_tokens=capabilities.max_output_tokens,
        context_max_output_tokens=context_maximum,
        limiting_factor=limiting_factor,
    )


__all__ = [
    "ModelOutputCapabilities",
    "OutputBudgetLimit",
    "OutputBudgetPolicy",
    "ResolvedOutputBudget",
    "ThinkingTokenAccounting",
    "resolve_output_budget",
]
