"""Screenplay-domain capabilities injected into PurrA."""

from __future__ import annotations

from dataclasses import dataclass

from purra.contracts import (
    AgentRunRequest,
    ContextBudget,
    ContextBundle,
    ExecutionState,
    PlanningCapabilities,
    PlanningConstraints,
    RuntimeLimits,
)
from purra.ports import CancellationSignal, ToolCatalog
from purra.recovery import RecoveryPolicy

from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext


class ScreenplayExecutionStateFactory:
    def create(self, request: AgentRunRequest) -> ExecutionState:
        context = ScreenplayAgentDomainContext.from_core_context(
            request.domain_context
        )
        return ExecutionState(domain={
            "projectId": context.project_id,
            "taskId": context.task_id,
            "unitId": context.unit_id,
            "targetRole": context.target_role,
            "expectedPartType": context.expected_part_type,
            "expectedPartKey": context.expected_part_key,
            "toolAccess": context.tool_access,
            "sourceBookId": context.source_book_id,
            "sourceScope": dict(context.source_scope or {}),
            "locale": context.locale,
        })


class ScreenplayToolLoopPolicy:
    """The durable screenplay task is already planned; run one tool loop."""

    def planning_constraints(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> PlanningConstraints:
        del request
        return capabilities.constraints

    def should_plan(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> bool:
        del request, capabilities
        return False


class ScreenplayHostContextProvider:
    async def build_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle:
        del request, budget, signal
        return ContextBundle(diagnostics={"contextMode": "screenplay-tools"})


@dataclass(frozen=True, slots=True)
class ScreenplayDomainAdapter:
    tool_catalog: ToolCatalog
    planning_policy: ScreenplayToolLoopPolicy = ScreenplayToolLoopPolicy()
    execution_state_factory: ScreenplayExecutionStateFactory = (
        ScreenplayExecutionStateFactory()
    )
    context_provider: ScreenplayHostContextProvider = (
        ScreenplayHostContextProvider()
    )
    agent_role_registry: None = None
    runtime_limits: RuntimeLimits = RuntimeLimits(
        max_model_rounds=8,
        max_progress_rounds=8,
    )
    recovery_policy: RecoveryPolicy = RecoveryPolicy()


__all__ = ["ScreenplayDomainAdapter"]
