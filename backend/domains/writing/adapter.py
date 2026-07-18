"""Composition facade for all writing-domain Agent Core capabilities."""

from __future__ import annotations

from dataclasses import dataclass

from agent_core.ports import ContextProvider, ToolCatalog
from domains.agent_roles import AgentRoleRegistry
from domains.writing.agent_roles import build_writing_agent_role_registry
from domains.writing.execution_state import WritingExecutionStateFactory
from domains.writing.planning import WritingPlanningPolicy


@dataclass(frozen=True, slots=True)
class WritingDomainAdapter:
    """One explicit bundle injected by the application composition root."""

    planning_policy: WritingPlanningPolicy
    execution_state_factory: WritingExecutionStateFactory
    tool_catalog: ToolCatalog
    agent_role_registry: AgentRoleRegistry
    context_provider: ContextProvider | None = None

    @classmethod
    def build(
        cls,
        *,
        tool_catalog: ToolCatalog,
        context_provider: ContextProvider,
        agent_role_registry: AgentRoleRegistry | None = None,
    ) -> "WritingDomainAdapter":
        return cls(
            planning_policy=WritingPlanningPolicy(),
            execution_state_factory=WritingExecutionStateFactory(),
            tool_catalog=tool_catalog,
            agent_role_registry=(
                agent_role_registry or build_writing_agent_role_registry()
            ),
            context_provider=context_provider,
        )
