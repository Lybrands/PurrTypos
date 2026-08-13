"""Screenplay-domain capabilities injected into PurrA."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from purra.contracts import (
    AgentRunRequest,
    ContextBudget,
    ContextBundle,
    ExecutionState,
    PlanningCapabilities,
    PlanningConstraints,
    RuntimeLimits,
    TaskContextRequest,
)
from purra.ports import CancellationSignal, ToolCatalog
from purra.recovery import RecoveryPolicy

from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from domains.screenplay_agent.prompts import build_screenplay_planning_policy


ScreenplayPlanningContextLoader = Callable[
    [str], Awaitable[Mapping[str, Any]]
]


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
            "candidateValidation": (
                dict(context.candidate_validation_contract)
                if context.candidate_validation_contract is not None
                else None
            ),
            "toolAccess": context.tool_access,
            "sourceBookId": context.source_book_id,
            "sourceScope": dict(context.source_scope or {}),
            "locale": context.locale,
        })


class ScreenplayToolLoopPolicy:
    """Plan only the public Root turn, never an internal screenplay Part."""

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
        del capabilities
        context = ScreenplayAgentDomainContext.from_core_context(
            request.domain_context
        )
        return bool(
            context.project_id
            and request.latest_user_text().strip()
            and context.task_id is None
            and context.unit_id is None
        )


class ScreenplayHostContextProvider:
    def __init__(
        self,
        *,
        planning_context_loader: ScreenplayPlanningContextLoader | None = None,
    ) -> None:
        self._planning_context_loader = planning_context_loader

    async def build_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle:
        del request, budget, signal
        return ContextBundle(diagnostics={"contextMode": "screenplay-tools"})

    async def build_planning_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle:
        del budget, signal
        context = ScreenplayAgentDomainContext.from_core_context(
            request.domain_context
        )
        if context.is_child:
            return ContextBundle(diagnostics={
                "contextMode": "screenplay-part",
                "hostPlanningFacts": {},
            })
        if self._planning_context_loader is None:
            raise RuntimeError("screenplay planning context is not configured")
        loaded = await self._planning_context_loader(context.project_id)
        if not isinstance(loaded, Mapping):
            raise TypeError("screenplay planning context must be an object")
        facts = dict(loaded)
        project = facts.get("project")
        facts["project"] = {
            **(dict(project) if isinstance(project, Mapping) else {}),
            "id": context.project_id,
        }
        facts["planningRules"] = [build_screenplay_planning_policy()]
        facts.pop("stageCommand", None)
        if context.stage_command is not None:
            facts["stageCommand"] = context.stage_command.to_mapping()
        return ContextBundle(diagnostics={
            "contextMode": "screenplay-root-planning",
            "hostPlanningFacts": facts,
        })

    async def build_task_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        task: TaskContextRequest,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle:
        del task
        return await self.build_context(request, budget, signal)


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
