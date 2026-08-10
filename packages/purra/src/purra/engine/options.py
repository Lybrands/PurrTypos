"""Per-run options for the high-level PurrA pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from purra.contracts import (
    ContextBudgetClaim,
    ReasoningMode,
    ResponseConstraints,
    RunBinding,
    RunLineage,
    RunProvenance,
)
from purra.normalization import (
    optional_non_negative_int,
    positive_int,
)
from purra.output_budget import ResolvedOutputBudget
from purra.ports import ResponseJudge, ResponseValidator


@dataclass(frozen=True, slots=True)
class AgentCoreRunOptions:
    """Per-run generic limits; domain content remains in injected adapters."""

    context_claims: tuple[ContextBudgetClaim, ...] = ()
    output_reserve_tokens: int = 8_192
    output_budget: ResolvedOutputBudget | None = None
    default_context_window_tokens: int = 128_000
    safety_reserve_tokens: int | None = None
    runtime_reserve_tokens: int | None = None
    minimum_message_tokens: int | None = None
    model_supports_tools: bool = True
    force_planned_tool_choice: bool = True
    require_tool_call: bool | None = None
    reasoning_mode: ReasoningMode = ReasoningMode.DEFAULT
    provenance: RunProvenance | None = None
    lineage: RunLineage | None = None
    binding: RunBinding | None = None
    response_constraints: ResponseConstraints = ResponseConstraints()
    response_validators: tuple[ResponseValidator, ...] = ()
    response_judges: tuple[ResponseJudge, ...] = ()

    def __post_init__(self) -> None:
        claims = tuple(self.context_claims)
        names = [claim.name for claim in claims]
        if len(names) != len(set(names)):
            raise ValueError("context claim names must be unique")
        object.__setattr__(self, "context_claims", claims)
        for name in ("output_reserve_tokens", "default_context_window_tokens"):
            object.__setattr__(self, name, positive_int(getattr(self, name), name))
        if self.output_budget is not None:
            if not isinstance(self.output_budget, ResolvedOutputBudget):
                raise TypeError("output budget must be ResolvedOutputBudget")
            if self.output_reserve_tokens != self.output_budget.effective_tokens:
                raise ValueError(
                    "output reserve must match the resolved output budget"
                )
        for name in (
            "safety_reserve_tokens",
            "runtime_reserve_tokens",
            "minimum_message_tokens",
        ):
            object.__setattr__(self, name, optional_non_negative_int(
                getattr(self, name), name
            ))
        object.__setattr__(self, "model_supports_tools", bool(self.model_supports_tools))
        object.__setattr__(
            self,
            "force_planned_tool_choice",
            bool(self.force_planned_tool_choice),
        )
        if self.require_tool_call is not None:
            object.__setattr__(
                self,
                "require_tool_call",
                bool(self.require_tool_call),
            )
        object.__setattr__(self, "reasoning_mode", ReasoningMode(self.reasoning_mode))
        if self.provenance is not None and not isinstance(
            self.provenance,
            RunProvenance,
        ):
            raise TypeError("run provenance must be a RunProvenance value")
        if self.lineage is not None and not isinstance(self.lineage, RunLineage):
            raise TypeError("run lineage must be a RunLineage value")
        if self.binding is not None and not isinstance(self.binding, RunBinding):
            raise TypeError("run binding must be a RunBinding value")
        if not isinstance(self.response_constraints, ResponseConstraints):
            raise TypeError("response constraints must be ResponseConstraints")
        validators = tuple(self.response_validators)
        if any(not isinstance(item, ResponseValidator) for item in validators):
            raise TypeError("response validators must implement ResponseValidator")
        object.__setattr__(self, "response_validators", validators)
        judges = tuple(self.response_judges)
        if any(not isinstance(item, ResponseJudge) for item in judges):
            raise TypeError("response judges must implement ResponseJudge")
        object.__setattr__(self, "response_judges", judges)
