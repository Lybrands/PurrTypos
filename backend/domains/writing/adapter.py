"""Composition facade for all writing-domain Agent Core capabilities."""

from __future__ import annotations

from dataclasses import dataclass

from agent_core.ports import ContextProvider, ToolCatalog
from domains.writing.execution_state import WritingExecutionStateFactory
from domains.writing.planning import WritingPlanningPolicy


@dataclass(frozen=True, slots=True)
class WritingDomainAdapter:
    """One explicit bundle injected by the application composition root."""

    planning_policy: WritingPlanningPolicy
    execution_state_factory: WritingExecutionStateFactory
    tool_catalog: ToolCatalog
    context_provider: ContextProvider | None = None

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
