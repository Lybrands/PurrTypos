"""Composition facade for screenplay-domain Agent Core capabilities."""

from __future__ import annotations

from dataclasses import dataclass

from agent_core.contracts import RuntimeLimits
from agent_core.ports import ContextProvider, ToolCatalog
from agent_core.recovery import RecoveryPolicy
from agent_core.tools import InMemoryToolCatalog
from domains.agent_roles import AgentRoleRegistry
from domains.screenplay.agent_roles import (
    build_screenplay_agent_role_registry,
)
from domains.screenplay.context import ScreenplayContextProvider
from domains.screenplay.execution_state import ScreenplayExecutionStateFactory
from domains.screenplay.planning import ScreenplayPlanningPolicy


@dataclass(frozen=True, slots=True)
class ScreenplayDomainAdapter:
    planning_policy: ScreenplayPlanningPolicy
    execution_state_factory: ScreenplayExecutionStateFactory
    tool_catalog: ToolCatalog
    agent_role_registry: AgentRoleRegistry
    context_provider: ContextProvider
    runtime_limits: RuntimeLimits = RuntimeLimits(max_model_rounds=12)
    recovery_policy: RecoveryPolicy = RecoveryPolicy()

    @classmethod
    def build(
        cls,
        db,
        *,
        tool_catalog: ToolCatalog | None = None,
        artifact_continuity=None,
    ) -> "ScreenplayDomainAdapter":
        return cls(
            planning_policy=ScreenplayPlanningPolicy(),
            execution_state_factory=ScreenplayExecutionStateFactory(),
            tool_catalog=tool_catalog or InMemoryToolCatalog(()),
            agent_role_registry=build_screenplay_agent_role_registry(),
            context_provider=ScreenplayContextProvider(
                db,
                artifact_continuity=artifact_continuity,
            ),
        )
