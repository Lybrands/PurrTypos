"""Fail-closed PurrA profile for Screenplay replacement v1."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from uuid import uuid4

from agents.screenplay.contracts import (
    SCREENPLAY_OPERATION_MAX_MODEL_ROUNDS,
    SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE,
    SCREENPLAY_REPLACEMENT_RECIPE_VERSION,
    SCREENPLAY_REPLACEMENT_SCHEMA_VERSION,
    ScreenplayPartCompletion,
    screenplay_part_definition,
)
from agents.shared.implementation import AgentKind, replacement_implementation
from agents.shared.implementation_registry import AgentImplementationProfile
from purra.cancellation import raise_if_stopped
from purra.context_strategies import ContextStrategy
from purra.contracts import (
    AgentRunRequest,
    ContextBlock,
    ContextBudgetClaim,
    ContextBundle,
    ExecutionPlan,
    ExecutionState,
    PlanningCapabilities,
    PlanningConstraints,
    PlanningKind,
    PlanningMode,
    PlanningResult,
    PlannerLimits,
    RuntimeLimits,
    StepExecutor,
    StepType,
    TaskSpec,
    WorkPlan,
    WorkStep,
)
from purra.json_values import thaw_json_mapping
from purra.recovery import RecoveryPolicy
from purra.tools import InMemoryToolCatalog
from purra.task_admission import ExecutionMode, TaskAdmissionDecision
from agents.screenplay.access_contract import (
    screenplay_part_tool_guidance,
    screenplay_part_tool_names,
    validate_screenplay_capability_manifest,
)
from agents.screenplay.read_tools import (
    SCREENPLAY_ROOT_SCOPE_STATE_KEY,
    build_screenplay_replacement_read_registrations,
    SCREENPLAY_OPERATION_SCOPE_STATE_KEY,
)
from agents.screenplay.unit_context import screenplay_unit_scope_from_request
from agents.screenplay.submission_tool import (
    build_screenplay_replacement_submission_registration,
)
from agents.screenplay.recipe import ScreenplayHostRecipeSpec


SCREENPLAY_REPLACEMENT_PROFILE_ID = "screenplay.purra-native.v1"
SCREENPLAY_HOST_RECIPE_METADATA_KEY = "screenplayRecipe"
SCREENPLAY_ORDINARY_INTERACTION = "ordinary"


def _ordinary(request: AgentRunRequest) -> bool:
    return request.metadata.get("interactionKind") == SCREENPLAY_ORDINARY_INTERACTION


def _host_recipe_spec(request: AgentRunRequest) -> ScreenplayHostRecipeSpec:
    value = request.metadata.get(SCREENPLAY_HOST_RECIPE_METADATA_KEY)
    if not isinstance(value, Mapping):
        raise ValueError("Screenplay replacement host recipe is required")
    return ScreenplayHostRecipeSpec.from_mapping(value)


def validate_screenplay_replacement_plan(
    request: AgentRunRequest,
    result: PlanningResult,
) -> str | None:
    ScreenplayRootScope.from_request(request)
    if _ordinary(request):
        return None
    spec = _host_recipe_spec(request)
    if result.kind is not PlanningKind.PLANNED:
        return "replacement Screenplay requires a model-authored plan"
    task_spec = result.work_plan.task_spec
    if task_spec is None:
        return "replacement Screenplay requires a TaskSpec"
    if (
        task_spec.operation != spec.operation
        or task_spec.deliverable != spec.target_role
        or task_spec.target
    ):
        return "replacement Screenplay TaskSpec changed host intent"
    if any(
        step.executor is not StepExecutor.MODEL
        or step.type not in {StepType.ANALYZE, StepType.WRITE, StepType.REVIEW}
        or step.capability_names
        for step in result.work_plan.steps
    ):
        return "replacement Screenplay plan may only describe model work"
    return None


class ScreenplayReplacementPlanningPolicy:
    def planning_constraints(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> PlanningConstraints:
        ScreenplayRootScope.from_request(request)
        if _ordinary(request):
            return capabilities.constraints
        _host_recipe_spec(request)
        return replace(
            capabilities.constraints,
            allow_model_only_fallback=False,
            planning_excluded_executors=(
                capabilities.constraints.planning_excluded_executors
                | {StepExecutor.TOOL}
            ),
        )


_ROLE_LABELS = {
    "sourceAnalysis": "原作分析",
    "creativeBrief": "创作简报",
    "structure": "结构设计",
    "sceneList": "场景规划",
    "screenplayDraft": "剧本正文",
    "review": "审阅修订",
}


class ScreenplayHostPlanner:
    """Project host-owned intent into stable, presentation-only WorkSteps."""

    async def create_plan(
        self,
        request,
        capabilities,
        signal=None,
        *,
        run_id=None,
        turn_id=None,
        reasoning_mode=None,
    ):
        del capabilities, run_id, turn_id, reasoning_mode
        raise_if_stopped(signal)
        spec = _host_recipe_spec(request)
        label = _ROLE_LABELS.get(spec.target_role, spec.target_role)
        scope_id = spec.target_role
        return PlanningResult(
            kind=PlanningKind.PLANNED,
            work_plan=WorkPlan(
                title=label,
                goal=f"完成{label}并形成可审核候选版本",
                task_spec=TaskSpec(
                    goal=f"完成{label}",
                    target={},
                    operation=spec.operation,
                    instruction=str(request.messages[-1].content or "").strip(),
                    deliverable=spec.target_role,
                ),
                steps=(
                    WorkStep(
                        id=f"prepare-{scope_id}",
                        title=f"梳理{label}目标",
                        type=StepType.ANALYZE,
                        executor=StepExecutor.MODEL,
                    ),
                    WorkStep(
                        id=f"produce-{scope_id}",
                        title=f"生成{label}候选",
                        type=StepType.WRITE,
                        executor=StepExecutor.MODEL,
                        depends_on=(f"prepare-{scope_id}",),
                    ),
                    WorkStep(
                        id=f"verify-{scope_id}",
                        title="校验并形成候选版本",
                        type=StepType.REVIEW,
                        executor=StepExecutor.MODEL,
                        depends_on=(f"produce-{scope_id}",),
                    ),
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class ScreenplayRootScope:
    project_id: str
    turn_id: str
    command_id: str

    @classmethod
    def from_request(cls, request: AgentRunRequest) -> "ScreenplayRootScope":
        if request.domain_context.namespace != SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE:
            raise ValueError("unsupported Screenplay replacement domain namespace")
        payload = thaw_json_mapping(request.domain_context.payload)
        if set(payload) != {"schemaVersion", "projectId", "turnId", "commandId"}:
            raise ValueError("Screenplay replacement Root scope shape is invalid")
        if payload["schemaVersion"] != SCREENPLAY_REPLACEMENT_SCHEMA_VERSION:
            raise ValueError("Screenplay replacement Root schema version is invalid")
        values = [str(payload[key] or "").strip() for key in ("projectId", "turnId", "commandId")]
        if any(not value for value in values):
            raise ValueError("Screenplay replacement Root scope is incomplete")
        if request.session_id is None:
            raise ValueError("Screenplay replacement Root session is required")
        return cls(*values)

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": SCREENPLAY_REPLACEMENT_SCHEMA_VERSION,
            "projectId": self.project_id,
            "turnId": self.turn_id,
            "commandId": self.command_id,
        }


class _ExecutionStateFactory:
    def create(self, request: AgentRunRequest) -> ExecutionState:
        unit = screenplay_unit_scope_from_request(request)
        if unit is not None:
            return ExecutionState(domain={
                SCREENPLAY_OPERATION_SCOPE_STATE_KEY: unit.to_mapping(),
            })
        return ExecutionState(domain={
            SCREENPLAY_ROOT_SCOPE_STATE_KEY: ScreenplayRootScope.from_request(
                request
            ).to_mapping(),
        })


class _ContextProvider:
    async def build_context(self, request, budget, signal=None):
        raise_if_stopped(signal)
        unit = screenplay_unit_scope_from_request(request)
        scope = unit or ScreenplayRootScope.from_request(request)
        if budget.allocation_for("screenplay_replacement_policy") < 1:
            return ContextBundle()
        ordinary = unit is None and _ordinary(request)
        host_recipe = (
            _host_recipe_spec(request) if unit is None and not ordinary else None
        )
        content = json.dumps({
            "schemaVersion": SCREENPLAY_REPLACEMENT_SCHEMA_VERSION,
            "scope": scope.to_mapping(),
            "rules": [
                "The host owns Part DAGs and all revision admissibility.",
                "A Part attempt is an Operation scope, never a synthetic Agent Run.",
                *((
                    "Answer with read-only project facts. "
                    "Do not create or revise deliverables.",
                ) if ordinary else ()),
            ],
            **({
                "plannerAuthority": "presentation_mapping_only",
                "taskSpec": {
                    "operation": host_recipe.operation,
                    "target": {},
                    "deliverable": host_recipe.target_role,
                },
            } if unit is None and not ordinary else {}),
            **({
                "partGuidance": screenplay_part_tool_guidance(unit.part_kind),
            } if unit is not None else {}),
        }, ensure_ascii=False, separators=(",", ":"))
        return ContextBundle(blocks=(ContextBlock(
            name="screenplay_replacement_policy",
            content=content,
            token_count=max(1, len(content) // 4),
            untrusted=False,
            host_metadata={"schemaVersion": SCREENPLAY_REPLACEMENT_SCHEMA_VERSION},
        ),))

    async def build_planning_context(self, request, budget, signal=None):
        return await self.build_context(request, budget, signal)

    async def build_task_context(self, request, budget, task, signal=None):
        del task
        return await self.build_context(request, budget, signal)

    async def describe_context_demands(self, request, signal=None):
        screenplay_unit_scope_from_request(request) or ScreenplayRootScope.from_request(
            request
        )
        raise_if_stopped(signal)
        return (ContextBudgetClaim(name="screenplay_replacement_policy", desired_tokens=384, minimum_tokens=192, maximum_tokens=384, priority=100),)


@dataclass(frozen=True, slots=True)
class _Adapter:
    tool_catalog: object = InMemoryToolCatalog(())
    planning_policy: object | None = ScreenplayReplacementPlanningPolicy()
    planner: object = ScreenplayHostPlanner()
    planner_limits: PlannerLimits = PlannerLimits(max_tool_steps=0)
    planning_result_validator = staticmethod(validate_screenplay_replacement_plan)
    context_strategy: ContextStrategy = ContextStrategy.STAGED
    execution_state_factory: object = _ExecutionStateFactory()
    context_provider: object = _ContextProvider()
    runtime_limits: RuntimeLimits = RuntimeLimits(
        max_run_generation_tokens=None,
        max_model_rounds=SCREENPLAY_OPERATION_MAX_MODEL_ROUNDS,
    )
    recovery_policy: RecoveryPolicy = RecoveryPolicy()


class ScreenplayReplacementProfile:
    id = SCREENPLAY_REPLACEMENT_PROFILE_ID
    domain_namespace = SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE

    def __init__(self, db) -> None:
        self._db = db
        self._worker_id = f"screenplay-replacement-{uuid4().hex}"
        validate_screenplay_capability_manifest()
        registrations = (
            *build_screenplay_replacement_read_registrations(db),
            build_screenplay_replacement_submission_registration(db),
        )
        installed = frozenset(item.schema.name for item in registrations)

        def enabled_tools(request):
            unit = screenplay_unit_scope_from_request(request)
            if unit is None:
                return frozenset({"inspectScreenplayProjectV1"})
            required = frozenset(screenplay_part_tool_names(unit.part_kind))
            missing = required - installed
            if missing:
                raise ValueError(
                    "Screenplay replacement Unit capability is not installed: "
                    + ", ".join(sorted(missing))
                )
            return required

        self._adapter = _Adapter(tool_catalog=InMemoryToolCatalog(
            registrations,
            enablement=enabled_tools,
        ))

    @property
    def adapter(self):
        return self._adapter

    async def prepare_request(self, request: AgentRunRequest) -> AgentRunRequest:
        unit = screenplay_unit_scope_from_request(request)
        scope = unit or ScreenplayRootScope.from_request(request)
        if unit is None and not _ordinary(request):
            _host_recipe_spec(request)
        row = await self._db.fetch_one("SELECT id FROM screenplay_projects WHERE id = ?", [scope.project_id])
        if row is None:
            raise ValueError("Screenplay replacement project does not exist")
        if unit is None:
            return replace(
                request,
                planning_mode=(
                    PlanningMode.REACTIVE if _ordinary(request)
                    else PlanningMode.PLANNED
                ),
            )
        return request

    def run_binding_attributes(self, request: AgentRunRequest) -> dict[str, object]:
        unit = screenplay_unit_scope_from_request(request)
        if unit is not None:
            return {
                "agentImplementation": replacement_implementation(
                    AgentKind.SCREENPLAY
                ).to_mapping(),
            }
        scope = ScreenplayRootScope.from_request(request)
        if _ordinary(request):
            return {
                "screenplayRootScope": scope.to_mapping(),
                "interactionKind": SCREENPLAY_ORDINARY_INTERACTION,
                "agentImplementation": replacement_implementation(
                    AgentKind.SCREENPLAY,
                    recipe_version=SCREENPLAY_REPLACEMENT_RECIPE_VERSION,
                ).to_mapping(),
            }
        spec = _host_recipe_spec(request)
        recipe = spec.compile(project_id=scope.project_id)
        return {
            "screenplayRootScope": scope.to_mapping(),
            "screenplayRecipeBinding": {
                "recipeVersion": recipe.metadata["recipeVersion"],
                "recipeDigest": recipe.metadata["recipeDigest"],
                "operation": spec.operation,
                "targetRole": spec.target_role,
            },
            "agentImplementation": replacement_implementation(
                AgentKind.SCREENPLAY,
                recipe_version=SCREENPLAY_REPLACEMENT_RECIPE_VERSION,
            ).to_mapping(),
        }

    def context_provider_factory(self):
        return None

    def response_judge_policies(self, request):
        del request
        return ()

    def task_admission(self):
        return self

    async def evaluate(self, request, plan, signal=None):
        raise_if_stopped(signal)
        if _ordinary(request):
            raise ValueError("ordinary Screenplay conversation cannot create a Task")
        scope = ScreenplayRootScope.from_request(request)
        spec = _host_recipe_spec(request)
        if not isinstance(plan, ExecutionPlan) or plan.task_spec is None:
            raise ValueError("replacement Screenplay requires a planned TaskSpec")
        if (
            plan.task_spec.operation != spec.operation
            or plan.task_spec.deliverable != spec.target_role
            or plan.task_spec.target
        ):
            raise ValueError("replacement Screenplay TaskSpec changed host intent")
        plan_step_ids = tuple(plan.work_step_ids or ())
        if not plan_step_ids:
            raise ValueError("replacement Screenplay requires visible plan steps")
        recipe = spec.compile(
            project_id=scope.project_id,
            plan_step_ids=plan_step_ids,
        )
        model_steps = tuple(
            step for step in recipe.steps
            if screenplay_part_definition(step.kind).completion
            is not ScreenplayPartCompletion.HOST_ONLY
        )
        return TaskAdmissionDecision(
            mode=ExecutionMode.DURABLE,
            reason_code="screenplay_replacement_requires_durable_execution",
            estimated_units=len(recipe.steps),
            estimated_model_calls=len(model_steps),
            covered_step_ids=plan_step_ids,
            execution_recipe=recipe,
            metadata={
                **scope.to_mapping(),
                "targetRole": spec.target_role,
                "operation": spec.operation,
                "recipeVersion": recipe.metadata["recipeVersion"],
                "recipeDigest": recipe.metadata["recipeDigest"],
                "plannerAuthority": "presentation_mapping_only",
                "modelAttemptBudget": sum(
                    step.max_attempts * SCREENPLAY_OPERATION_MAX_MODEL_ROUNDS
                    for step in model_steps
                ),
                "failedResumeAttempts": int(
                    request.metadata.get("failedResumeAttempts") or 0
                ),
                **(
                    {"runtimeBinding": request.metadata["runtimeBinding"]}
                    if "runtimeBinding" in request.metadata
                    else {}
                ),
            },
        )

    def create_long_task_dispatcher(self, *, long_task_repository=None, executor=None):
        if executor is None:
            return None
        from agents.screenplay.dispatcher import (
            create_screenplay_replacement_dispatcher,
        )

        return create_screenplay_replacement_dispatcher(
            long_task_repository=long_task_repository,
            executor=executor,
            worker_id=self._worker_id,
        )

    def clear_active_executions(self) -> None:
        return None


def build_screenplay_replacement_profile(*, db, **_dependencies):
    return ScreenplayReplacementProfile(db)


def screenplay_replacement_implementation_profile():
    return AgentImplementationProfile(
        identity=replacement_implementation(AgentKind.SCREENPLAY),
        runtime_profile_id=SCREENPLAY_REPLACEMENT_PROFILE_ID,
    )


__all__ = [
    "SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE",
    "SCREENPLAY_REPLACEMENT_PROFILE_ID",
    "SCREENPLAY_HOST_RECIPE_METADATA_KEY",
    "ScreenplayReplacementProfile",
    "ScreenplayRootScope",
    "build_screenplay_replacement_profile",
    "screenplay_replacement_implementation_profile",
    "validate_screenplay_replacement_plan",
]
