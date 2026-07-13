"""Stage-1 compatibility policy for the existing writing Planner gate."""

from __future__ import annotations

from agent_core.contracts import AgentRunRequest, PlanningCapabilities
from domains.writing.contracts import WritingDomainContext
from services.task_planner import should_request_task_plan


class WritingPlanningPolicy:
    """Preserve the legacy gate while keeping writing fields outside Core."""

    def should_plan(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> bool:
        # ``available_tool_names`` is intentionally not used as a proxy for the
        # user's tools-enabled setting.  The legacy gate distinguishes them.
        del capabilities
        context = WritingDomainContext.from_core_context(request.domain_context)
        return should_request_task_plan(
            user_text=request.latest_user_text(),
            book_id=context.book_id,
            enable_agent_tools=request.tools_enabled,
            chat_agent_mode=request.mode,
        )
