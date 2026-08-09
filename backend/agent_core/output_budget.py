"""Provider-neutral output budget contracts and resolution.

Model capability answers what the provider can support.  Task policy answers
what one unit of work should be allowed to produce.  A resolved budget is the
only value that may become a provider ``max_tokens`` style parameter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class ThinkingTokenAccounting(StrEnum):
    INCLUDED = "included"
    SEPARATE = "separate"
    UNKNOWN = "unknown"


class OutputExecutionMode(StrEnum):
    SINGLE = "single"
    CHUNKED = "chunked"


class OutputLengthStrategy(StrEnum):
    FAIL = "fail"
    CONTINUE = "continue"
    RETRY_LARGER = "retry_larger"


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
        if self.max_output_tokens is not None:
            maximum = int(self.max_output_tokens)
            if maximum <= 0:
                raise ValueError("model max output tokens must be positive")
            object.__setattr__(self, "max_output_tokens", maximum)
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
    execution_mode: OutputExecutionMode = OutputExecutionMode.SINGLE
    length_strategy: OutputLengthStrategy = OutputLengthStrategy.FAIL

    def __post_init__(self) -> None:
        key = str(self.key or "").strip()
        if not key:
            raise ValueError("output budget policy key is required")
        object.__setattr__(self, "key", key)
        for name in (
            "base_tokens",
            "per_work_unit_tokens",
            "hard_cap_tokens",
            "reasoning_reserve_tokens",
        ):
            value = int(getattr(self, name))
            if value < 0 or (name == "hard_cap_tokens" and value <= 0):
                raise ValueError(f"{name} has an invalid output budget value")
            object.__setattr__(self, name, value)
        factor = float(self.safety_factor)
        if not math.isfinite(factor) or factor < 1:
            raise ValueError("output budget safety factor must be at least one")
        object.__setattr__(self, "safety_factor", factor)
        object.__setattr__(
            self,
            "execution_mode",
            OutputExecutionMode(self.execution_mode),
        )
        object.__setattr__(
            self,
            "length_strategy",
            OutputLengthStrategy(self.length_strategy),
        )

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
    execution_mode: OutputExecutionMode
    length_strategy: OutputLengthStrategy

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
            value = int(getattr(self, name))
            if value < 0 or (name != "reasoning_reserve_tokens" and value == 0):
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)
        if self.model_max_output_tokens is not None:
            model_maximum = int(self.model_max_output_tokens)
            if model_maximum <= 0:
                raise ValueError("model max output tokens must be positive")
            object.__setattr__(
                self,
                "model_max_output_tokens",
                model_maximum,
            )
        object.__setattr__(
            self,
            "limiting_factor",
            OutputBudgetLimit(self.limiting_factor),
        )
        object.__setattr__(
            self,
            "execution_mode",
            OutputExecutionMode(self.execution_mode),
        )
        object.__setattr__(
            self,
            "length_strategy",
            OutputLengthStrategy(self.length_strategy),
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
            "executionMode": self.execution_mode.value,
            "lengthStrategy": self.length_strategy.value,
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

    effective = min(
        requested,
        policy.hard_cap_tokens,
        context_maximum,
        *(
            (capabilities.max_output_tokens,)
            if capabilities.max_output_tokens is not None
            else ()
        ),
    )
    if effective == requested:
        limiting_factor = OutputBudgetLimit.TASK_ESTIMATE
    elif effective == policy.hard_cap_tokens:
        limiting_factor = OutputBudgetLimit.TASK_HARD_CAP
    elif (
        capabilities.max_output_tokens is not None
        and effective == capabilities.max_output_tokens
    ):
        limiting_factor = OutputBudgetLimit.MODEL_CAPABILITY
    else:
        limiting_factor = OutputBudgetLimit.CONTEXT_AVAILABLE

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
        execution_mode=policy.execution_mode,
        length_strategy=policy.length_strategy,
    )


__all__ = [
    "ModelOutputCapabilities",
    "OutputBudgetLimit",
    "OutputBudgetPolicy",
    "OutputExecutionMode",
    "OutputLengthStrategy",
    "ResolvedOutputBudget",
    "ThinkingTokenAccounting",
    "resolve_output_budget",
]
