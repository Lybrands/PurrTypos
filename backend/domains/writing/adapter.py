"""Composition facade for all writing-domain PurrA capabilities."""

from __future__ import annotations

from dataclasses import dataclass

from purra.context_strategies import ContextStrategy
from purra.contracts import RuntimeLimits
from purra.ports import ContextProvider, ToolCatalog
from purra.recovery import RecoveryPolicy
from domains.writing.execution_state import WritingExecutionStateFactory
from domains.writing.planning import WritingPlanningPolicy


@dataclass(frozen=True, slots=True)
class WritingDomainAdapter:
    """One explicit bundle injected by the application composition root."""

    planning_policy: WritingPlanningPolicy
    execution_state_factory: WritingExecutionStateFactory
    tool_catalog: ToolCatalog
    context_strategy: ContextStrategy = ContextStrategy.STAGED
    context_provider: ContextProvider | None = None
    # History discovery, source/material reads, chapter creation and the final
    # content proposal can require more than the Core's six model rounds.
    runtime_limits: RuntimeLimits = RuntimeLimits(max_run_generation_tokens=None, max_model_rounds=12)
    recovery_policy: RecoveryPolicy = RecoveryPolicy()

    @classmethod
    def build(
        cls,
        *,
        tool_catalog: ToolCatalog,
        context_provider: ContextProvider,
    ) -> "WritingDomainAdapter":
        return cls(
            planning_policy=WritingPlanningPolicy(),
            execution_state_factory=WritingExecutionStateFactory(),
            tool_catalog=tool_catalog,
            context_provider=context_provider,
        )
