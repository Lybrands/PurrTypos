"""Configurable recovery budgets owned by Agent Core's composition boundary."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from agent_core.recovery.contracts import RecoveryCause


@dataclass(frozen=True, slots=True)
class RecoveryRule:
    cause: RecoveryCause
    max_attempts: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "cause", RecoveryCause(self.cause))
        attempts = int(self.max_attempts)
        if attempts < 0:
            raise ValueError("recovery max attempts must be non-negative")
        object.__setattr__(self, "max_attempts", attempts)


_STANDARD_RULES = (
    RecoveryRule(RecoveryCause.PROVIDER_REQUIRED_TOOL_CHOICE_UNSUPPORTED, 1),
    RecoveryRule(RecoveryCause.PROVIDER_STREAM_INTERRUPTED, 1),
    RecoveryRule(RecoveryCause.MODEL_OUTPUT_TRUNCATED, 1),
    RecoveryRule(RecoveryCause.MALFORMED_TOOL_CALL_BATCH, 1),
    RecoveryRule(RecoveryCause.MISSING_REQUIRED_TOOL_CALL, 1),
    RecoveryRule(RecoveryCause.MISSING_REQUIRED_TOOL_CALL_REPLAN, 1),
    RecoveryRule(RecoveryCause.UNSTRUCTURED_TOOL_PROTOCOL, 1),
    RecoveryRule(RecoveryCause.EMPTY_MODEL_RESPONSE, 2),
    RecoveryRule(RecoveryCause.DEFERRED_MODEL_RESPONSE, 1),
    RecoveryRule(RecoveryCause.RESPONSE_CONSTRAINT_DETERMINISTIC, 1),
    RecoveryRule(RecoveryCause.RESPONSE_CONSTRAINT_SEMANTIC, 1),
    RecoveryRule(RecoveryCause.FUTURE_TOOL_STEP, 1),
    RecoveryRule(RecoveryCause.UNAUTHORIZED_TOOL, 1),
    RecoveryRule(RecoveryCause.UNAUTHORIZED_TOOL_REPLAN, 1),
    RecoveryRule(RecoveryCause.TOOL_INPUT_INVALID, 1),
    RecoveryRule(RecoveryCause.TOOL_EXECUTION_FAILED_REPLAN, 1),
)


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    """Immutable per-profile policy injected into the Core runtime.

    A missing rule is fail-closed (zero attempts). Domains may replace the
    policy at composition time, but the runtime remains the sole decision and
    accounting authority.
    """

    rules: tuple[RecoveryRule, ...] = _STANDARD_RULES

    def __post_init__(self) -> None:
        normalized = tuple(
            rule if isinstance(rule, RecoveryRule) else RecoveryRule(*rule)
            for rule in self.rules
        )
        causes = tuple(rule.cause for rule in normalized)
        if len(causes) != len(set(causes)):
            raise ValueError("recovery policy causes must be unique")
        object.__setattr__(self, "rules", normalized)

    @property
    def attempt_limits(self) -> Mapping[RecoveryCause, int]:
        return MappingProxyType({
            rule.cause: rule.max_attempts for rule in self.rules
        })

    def max_attempts(self, cause: RecoveryCause) -> int:
        return int(self.attempt_limits.get(RecoveryCause(cause), 0))

    def with_overrides(
        self,
        overrides: Mapping[RecoveryCause, int],
    ) -> "RecoveryPolicy":
        limits = dict(self.attempt_limits)
        for cause, attempts in overrides.items():
            limits[RecoveryCause(cause)] = int(attempts)
        return RecoveryPolicy(tuple(
            RecoveryRule(cause, attempts)
            for cause, attempts in limits.items()
        ))
