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
from domains.screenplay_agent.contracts import ScreenplayIntent
from domains.screenplay_agent.prompts import (
    build_screenplay_planning_policy,
    build_screenplay_public_progress_policy,
)


ScreenplayPlanningContextLoader = Callable[
    [str], Awaitable[Mapping[str, Any]]
]

SCREENPLAY_PLANNING_FACTS_CONTEXT = "screenplay_planning_facts"
SCREENPLAY_PUBLIC_PROGRESS_CONTEXT = "screenplay_public_progress"


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
    """Plan only the public Root turn, never an internal screenplay Part."""

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
            work_phase = (
                "review"
                if context.stage_command.target_role == "review"
                else "creation"
            )
            phases = tuple(binding.phase.value for binding in intent.plan_bindings)
            if (
                len(phases) < 3
                or phases.count("evidence") != 1
                or phases.count("delivery") != 1
                or set(phases) != {"evidence", work_phase, "delivery"}
            ):
                raise ValueError(
                    f"{context.stage_command.target_role} plan phases must be "
                    f"evidence, {work_phase}, and delivery"
                )
    except (TypeError, ValueError) as error:
        step_ids = [step.id for step in result.work_plan.steps]
        expected_scope = (
            context.stage_command.scope.to_mapping()
            if context.stage_command is not None
            else {"kind": "current_stage"}
        )
        command_rule = (
            "operation must be answer and deliverable must be omitted or empty"
            if str(task_spec.operation or "") == "answer"
            else (
                "operation and deliverable must exactly match stageCommand: "
                f"{context.stage_command.action.value}, "
                f"{context.stage_command.target_role}"
                if context.stage_command is not None
                else (
                    "operation must be answer, create, revise, or review; "
                    "answer omits deliverable and formal operations use a valid role"
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
        raw_bindings = binding_source.get("stepBindings")
        expected_bindings = (
            [
                {
                    "stepId": str(step_id),
                    "phase": str(
                        phase.get("phase")
                        if isinstance(phase, Mapping)
                        else phase
                    ),
                }
                for step_id, phase in raw_bindings.items()
            ]
            if isinstance(raw_bindings, Mapping)
            else raw_bindings
            if isinstance(raw_bindings, list)
            else []
        )
        if {
            str(binding.get("stepId") or "")
            for binding in expected_bindings
            if isinstance(binding, Mapping)
        } != set(step_ids):
            expected_bindings = [
                {
                    "stepId": step.id,
                    "phase": (
                        "evidence"
                        if len(result.work_plan.steps) == 1
                        else "delivery"
                        if index == len(result.work_plan.steps) - 1
                        else "evidence"
                        if step.type.value in {"read", "analyze"}
                        else "review"
                        if step.type.value == "review"
                        else "creation"
                    ),
                }
                for index, step in enumerate(result.work_plan.steps)
            ]
        todo_rule = ""
        if context.stage_command is not None:
            work_phase = (
                "review"
                if context.stage_command.target_role == "review"
                else "creation"
            )
            repair_step_ids = list(step_ids)
            used_step_ids = set(repair_step_ids)

            def unused_step_id(base: str) -> str:
                candidate = base
                suffix = 2
                while candidate in used_step_ids:
                    candidate = f"{base}-{suffix}"
                    suffix += 1
                used_step_ids.add(candidate)
                return candidate

            if not repair_step_ids:
                repair_step_ids = [
                    unused_step_id("stage-evidence"),
                    unused_step_id("stage-work"),
                    unused_step_id("stage-delivery"),
                ]
            elif len(repair_step_ids) == 1:
                repair_step_ids = [
                    unused_step_id("stage-evidence"),
                    repair_step_ids[0],
                    unused_step_id("stage-delivery"),
                ]
            elif len(repair_step_ids) == 2:
                repair_step_ids.insert(1, unused_step_id("stage-work"))
            if repair_step_ids != step_ids:
                todo_rule = (
                    "Replace the todos too; their ids in order must equal exactly "
                    f"{json.dumps(repair_step_ids, ensure_ascii=False, separators=(',', ':'))}. "
                )
            expected_bindings = [
                {
                    "stepId": step_id,
                    "phase": (
                        "evidence"
                        if index == 0
                        else "delivery"
                        if index == len(repair_step_ids) - 1
                        else work_phase
                    ),
                }
                for index, step_id in enumerate(repair_step_ids)
            ]
        expected_target = {"screenplay": {
            "version": 1,
            "scope": expected_scope,
            "stepBindings": expected_bindings,
        }}
        return (
            f"{str(error) or type(error).__name__}. "
            "Replace the entire taskSpec.target; it must equal exactly "
            f"{json.dumps(expected_target, ensure_ascii=False, separators=(',', ':'))}. "
            f"{todo_rule}"
            "Do not keep version, scope, or stepBindings at target top level. "
            "For a formal stage command, keep the first todo for evidence, "
            "the last todo for delivery, and make every middle todo match the "
            "required work phase and title. "
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
        del request, budget, signal
        policy = build_screenplay_public_progress_policy()
        return ContextBundle(
            blocks=(ContextBlock(
                name=SCREENPLAY_PUBLIC_PROGRESS_CONTEXT,
                content=policy,
                token_count=estimate_json_tokens(policy),
                untrusted=False,
            ),),
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
        progress_policy = build_screenplay_public_progress_policy()
        return ContextBundle(
            blocks=(
                ContextBlock(
                    name=SCREENPLAY_PUBLIC_PROGRESS_CONTEXT,
                    content=progress_policy,
                    token_count=estimate_json_tokens(progress_policy),
                    untrusted=False,
                ),
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
    planner_limits: PlannerLimits = PlannerLimits(max_repair_attempts=3)
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
        max_model_rounds=8,
        max_progress_rounds=8,
        provider_invocation_timeout_ms=300_000,
    )
    recovery_policy: RecoveryPolicy = RecoveryPolicy()


__all__ = ["ScreenplayDomainAdapter"]
