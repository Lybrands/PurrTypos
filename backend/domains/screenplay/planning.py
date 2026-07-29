"""Planning policy for the first screenplay Agent milestone."""

from __future__ import annotations

from agent_core.contracts import (
    AgentRunRequest,
    PlanningCapabilities,
    PlanningConstraints,
)
from domains.screenplay.contracts import ScreenplayDomainContext


class ScreenplayPlanningPolicy:
    """Plan only explicit Agent runs that can use the screenplay catalog."""

    def planning_constraints(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> PlanningConstraints:
        ScreenplayDomainContext.from_core_context(request.domain_context)
        return capabilities.constraints

    def should_plan(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> bool:
        ScreenplayDomainContext.from_core_context(request.domain_context)
        if not request.tools_enabled:
            return False
        if not capabilities.available_tool_names:
            return False
        return (
            str(request.mode or "").strip().lower() == "agent"
            and len(request.latest_user_text().strip()) >= 4
        )
