"""Screenplay-domain capabilities injected into PurrA."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from purra.contracts import (
    AgentRunRequest,
    ContextBlock,
    ContextBudget,
    ContextBundle,
    ExecutionState,
    PlanningCapabilities,
    PlanningConstraints,
    PlanningResult,
    PlannerLimits,
    RuntimeLimits,
    TaskContextRequest,
)
from purra.context_budget import estimate_json_tokens
from purra.context_strategies import ContextStrategy
from purra.json_values import thaw_json_mapping
from purra.ports import CancellationSignal, ToolCatalog
from purra.recovery import RecoveryPolicy

from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from domains.screenplay_agent.contracts import (
    SCREENPLAY_DELIVERABLE_ROLES,
    ScreenplayIntent,
    ScreenplayIntentScope,
)
from domains.screenplay_agent.prompts import build_screenplay_planning_policy


ScreenplayPlanningContextLoader = Callable[
    [str], Awaitable[Mapping[str, Any]]
]

SCREENPLAY_PLANNING_FACTS_CONTEXT = "screenplay_planning_facts"


class ScreenplayExecutionStateFactory:
    def create(self, request: AgentRunRequest) -> ExecutionState:
        context = ScreenplayAgentDomainContext.from_core_context(
            request.domain_context
        )
        return ExecutionState(domain={
            "projectId": context.project_id,
            "taskId": context.task_id or context.turn_id,
            "unitId": context.unit_id,
            "targetRole": context.target_role,
            "expectedPartType": context.expected_part_type,
            "expectedPartKey": context.expected_part_key,
            "dependencyPartKeys": list(context.dependency_part_keys),
            "sceneIds": list(request.metadata.get("screenplaySceneIds") or ()),
            **(
                {
                    "deliverableRevisionScope": dict(
                        context.deliverable_revision_scope
                    )
                }
                if context.deliverable_revision_scope is not None
                else {}
            ),
            **(
                {"boundEpisodeNumber": context.episode_number}
                if context.episode_number is not None
                else {}
            ),
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
    """Constrain screenplay runs after the Root selects Planned mode."""

    def planning_constraints(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> PlanningConstraints:
        del request
        return replace(
            capabilities.constraints,
            allow_model_only_fallback=False,
        )

def validate_screenplay_planning_result(
    request: AgentRunRequest,
    result: PlanningResult,
) -> str | None:
    """Return repair guidance for model-authored screenplay semantics."""

    context = ScreenplayAgentDomainContext.from_core_context(
        request.domain_context
    )
    if not context.is_root:
        return None
    task_spec = result.work_plan.task_spec
    if task_spec is None:
        return "screenplay Root plan requires a TaskSpec"
    try:
        intent = ScreenplayIntent.from_task_spec(
            task_spec,
            result.work_plan.steps,
        )
        if context.stage_command is not None:
            context.stage_command.require_compatible(intent)
    except (TypeError, ValueError) as error:
        operation = str(task_spec.operation or "").strip()
        deliverable_roles = ", ".join(sorted(SCREENPLAY_DELIVERABLE_ROLES))
        command_rule = (
            "operation must be answer and deliverable must be omitted or empty"
            if operation == "answer"
            else (
                "operation and deliverable must exactly match stageCommand: "
                f"{context.stage_command.action.value}, "
                f"{context.stage_command.target_role}"
                if context.stage_command is not None
                else (
                    "operation must be answer, create, revise, or review; "
                    "answer omits deliverable and formal operations must use "
                    "exactly one of these deliverable roles: "
                    f"{deliverable_roles}; episode screenplay drafting uses "
                    "screenplayDraft"
                )
            )
        )
        raw_target = thaw_json_mapping(task_spec.target)
        raw_screenplay = raw_target.get("screenplay")
        binding_source = (
            raw_screenplay
            if isinstance(raw_screenplay, Mapping)
            else raw_target
        )
        expected_scope = None
        if context.stage_command is not None:
            expected_scope = context.stage_command.scope.to_mapping()
        else:
            raw_scope = binding_source.get("scope")
            if isinstance(raw_scope, Mapping):
                try:
                    expected_scope = ScreenplayIntentScope.from_task_spec_mapping(
                        raw_scope
                    ).to_mapping()
                except ValueError:
                    pass
        if expected_scope is None:
            target_rule = (
                "Replace the entire taskSpec.target with exactly one legal "
                "shape: {\"screenplay\":{\"version\":1,\"scope\":"
                "{\"kind\":\"current_stage\"}}}, "
                "{\"screenplay\":{\"version\":1,\"scope\":"
                "{\"kind\":\"next_episodes\",\"count\":N}}}, "
                "{\"screenplay\":{\"version\":1,\"scope\":"
                "{\"kind\":\"episodes\",\"episodeNumbers\":[...]}}}, or "
                "{\"screenplay\":{\"version\":1,\"scope\":"
                "{\"kind\":\"all_remaining\"}}}. Choose the scope from "
                "the original request; consecutive N-episode creation uses "
                "next_episodes with count N. "
            )
        else:
            expected_target = {"screenplay": {
                "version": 1,
                "scope": expected_scope,
            }}
            target_rule = (
                "Replace the entire taskSpec.target; it must equal exactly "
                f"{json.dumps(expected_target, ensure_ascii=False, separators=(',', ':'))}. "
            )
        return (
            f"{str(error) or type(error).__name__}. "
            f"{target_rule}"
            "Do not change, add, remove, reorder, or rename the model-authored todos. "
            f"{command_rule}."
        )
    return None


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
        context = ScreenplayAgentDomainContext.from_core_context(
            request.domain_context
        )
        if context.is_root:
            return await self.build_planning_context(request, budget, signal)
        return ContextBundle(
            diagnostics={"contextMode": "screenplay-tools"},
        )

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
        planning_facts = json.dumps(
            facts,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return ContextBundle(
            blocks=(
                ContextBlock(
                    name=SCREENPLAY_PLANNING_FACTS_CONTEXT,
                    content=planning_facts,
                    token_count=estimate_json_tokens(facts),
                    untrusted=False,
                ),
            ),
            diagnostics={
                "contextMode": "screenplay-root-planning",
                "hostPlanningFacts": facts,
            },
        )

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
    planner_limits: PlannerLimits = PlannerLimits(max_repair_attempts=2)
    planning_result_validator = staticmethod(
        validate_screenplay_planning_result
    )
    context_strategy: ContextStrategy = ContextStrategy.STAGED
    execution_state_factory: ScreenplayExecutionStateFactory = (
        ScreenplayExecutionStateFactory()
    )
    context_provider: ScreenplayHostContextProvider = (
        ScreenplayHostContextProvider()
    )
    runtime_limits: RuntimeLimits = RuntimeLimits(
        max_run_output_tokens=None,
        max_model_rounds=8,
        max_progress_rounds=8,
        root_run_timeout_ms=None,
        provider_invocation_timeout_ms=None,
    )
    recovery_policy: RecoveryPolicy = RecoveryPolicy()


__all__ = ["ScreenplayDomainAdapter"]
