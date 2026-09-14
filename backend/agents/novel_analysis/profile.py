"""PurrA-native admission profile for replacement Novel Analysis Runs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from uuid import uuid4

from agents.novel_analysis.domain import (
    NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
    NovelAnalysisRequestScope,
)
from agents.novel_analysis.recipe import (
    AnalysisUnitKind,
    NOVEL_ANALYSIS_REPLACEMENT_RECIPE_VERSION,
    NOVEL_ANALYSIS_REPLACEMENT_SCHEMA_VERSION,
    compile_analysis_recipe,
)
from agents.novel_analysis.source_model import SqliteNovelAnalysisSourceRepository
from agents.novel_analysis.source_tools import (
    NOVEL_ANALYSIS_SOURCE_SCOPE_STATE_KEY,
    build_novel_analysis_source_tool_catalog,
)
from agents.novel_analysis.observation_tools import (
    NOVEL_ANALYSIS_OBSERVATIONS_STATE_KEY,
    build_novel_analysis_observation_tool_catalog,
)
from agents.novel_analysis.access_contract import (
    analysis_model_tool_names,
    validate_analysis_capabilities,
)
from agents.novel_analysis.submission_tool import (
    build_novel_analysis_submission_tool_catalog,
)
from agents.novel_analysis.follow_up_tools import (
    READ_ANALYSIS_REVIEW_ARTIFACT,
    build_novel_analysis_follow_up_tool_catalog,
)
from agents.novel_analysis.unit_context import NovelAnalysisUnitContext
from agents.novel_analysis.dispatcher import (
    create_novel_analysis_replacement_dispatcher,
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
)
from purra.recovery import RecoveryPolicy
from purra.task_admission import (
    ExecutionMode,
    TaskAdmissionDecision,
)
from purra.tools import InMemoryToolCatalog


NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID = "novel_analysis.purra-native.v1"
_MODEL_UNIT_KINDS = frozenset({
    AnalysisUnitKind.EXTRACT.value,
    AnalysisUnitKind.NORMALIZE.value,
    AnalysisUnitKind.OVERVIEW.value,
    AnalysisUnitKind.DISTILL_TECHNIQUE.value,
})


def validate_novel_analysis_replacement_plan(
    request: AgentRunRequest,
    result: PlanningResult,
) -> str | None:
    NovelAnalysisRequestScope.from_request(request)
    if result.kind is not PlanningKind.PLANNED:
        return "replacement novel analysis requires a model-authored plan"
    task_spec = result.work_plan.task_spec
    if task_spec is None:
        return "replacement novel analysis requires a TaskSpec"
    if task_spec.operation != "analyze" or task_spec.target:
        return "replacement novel analysis TaskSpec must analyze an empty target"
    if len(result.work_plan.steps) > len(AnalysisUnitKind):
        return "replacement novel analysis plan exceeds host presentation stages"
    if any(
        step.executor is not StepExecutor.MODEL
        or step.type not in {StepType.ANALYZE, StepType.WRITE, StepType.REVIEW}
        or step.capability_names
        for step in result.work_plan.steps
    ):
        return "replacement novel analysis plan may only describe model work"
    return None


class NovelAnalysisReplacementPlanningPolicy:
    def planning_constraints(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> PlanningConstraints:
        NovelAnalysisRequestScope.from_request(request)
        return replace(
            capabilities.constraints,
            allow_model_only_fallback=False,
            planning_excluded_executors=(
                capabilities.constraints.planning_excluded_executors
                | {StepExecutor.TOOL}
            ),
        )


class NovelAnalysisReplacementExecutionStateFactory:
    def create(self, request: AgentRunRequest) -> ExecutionState:
        scope = NovelAnalysisRequestScope.from_request(request)
        unit = NovelAnalysisUnitContext.from_request(request)
        domain = {
            NOVEL_ANALYSIS_SOURCE_SCOPE_STATE_KEY: scope.to_mapping(),
            "sourceRevisionId": scope.source_revision_id,
            "commandId": scope.command_id,
            "sectionIds": list(scope.section_ids),
            "segments": [item.to_mapping() for item in scope.segments],
            "analysisSchemaVersion": NOVEL_ANALYSIS_REPLACEMENT_SCHEMA_VERSION,
        }
        if _is_follow_up(request):
            domain.update({
                "interactionKind": "follow_up",
                **(
                    {
                        "analysisArtifactId": str(
                            request.metadata["analysisArtifactId"]
                        ),
                    }
                    if request.metadata.get("analysisArtifactId")
                    else {}
                ),
            })
        if unit is not None:
            domain["unitKind"] = unit.kind.value
            domain[NOVEL_ANALYSIS_OBSERVATIONS_STATE_KEY] = list(unit.observations)
            domain["operationScopeId"] = request.metadata.get("operationScopeId")
            domain["operationBinding"] = request.metadata.get("operationBinding")
        return ExecutionState(domain=domain)


class NovelAnalysisReplacementContextProvider:
    async def build_context(self, request, budget, signal=None) -> ContextBundle:
        raise_if_stopped(signal)
        scope = NovelAnalysisRequestScope.from_request(request)
        if budget.allocation_for("novel_analysis_replacement_policy") < 1:
            return ContextBundle()
        follow_up = _is_follow_up(request)
        content = json.dumps({
            "schemaVersion": NOVEL_ANALYSIS_REPLACEMENT_SCHEMA_VERSION,
            "scope": {
                "sourceRevisionId": scope.source_revision_id,
                "sectionIds": list(scope.section_ids),
                "segmentCount": len(scope.segments),
            },
            **({
                "interactionKind": "follow_up",
                "rules": [
                    "Answer the current question, not a new full-book analysis.",
                    *(
                        ["Read the bound review Artifact before relying on prior conclusions."]
                        if request.metadata.get("analysisArtifactId")
                        else []
                    ),
                    "Read source segments when the question requires original evidence.",
                    "Do not invent omitted facts, quotations, or source locations.",
                ],
            } if follow_up else {
                "plannerAuthority": "presentation_mapping_only",
                "taskSpec": {"operation": "analyze", "target": {}},
                "rules": [
                    "The host owns the source scope and execution DAG.",
                    "Plan steps describe analysis or review goals only.",
                    "Do not invent source facts or request additional capabilities.",
                ],
            }),
        }, ensure_ascii=False, separators=(",", ":"))
        return ContextBundle(blocks=(ContextBlock(
            name="novel_analysis_replacement_policy",
            content=content,
            token_count=max(1, len(content) // 4),
            untrusted=False,
            host_metadata={"recipeVersion": NOVEL_ANALYSIS_REPLACEMENT_RECIPE_VERSION},
        ),))

    async def build_planning_context(self, request, budget, signal=None):
        return await self.build_context(request, budget, signal)

    async def build_task_context(self, request, budget, task, signal=None):
        del task
        return await self.build_context(request, budget, signal)

    async def describe_context_demands(self, request, signal=None):
        NovelAnalysisRequestScope.from_request(request)
        raise_if_stopped(signal)
        return (ContextBudgetClaim(
            name="novel_analysis_replacement_policy",
            desired_tokens=512,
            minimum_tokens=256,
            maximum_tokens=512,
            priority=100,
        ),)


@dataclass(frozen=True, slots=True)
class NovelAnalysisReplacementAdapter:
    planning_policy: object = NovelAnalysisReplacementPlanningPolicy()
    planner: None = None
    planner_limits: PlannerLimits = PlannerLimits(max_tool_steps=0)
    planning_result_validator = staticmethod(validate_novel_analysis_replacement_plan)
    context_strategy: ContextStrategy = ContextStrategy.STAGED
    execution_state_factory: object = NovelAnalysisReplacementExecutionStateFactory()
    tool_catalog: InMemoryToolCatalog = InMemoryToolCatalog(())
    context_provider: object = NovelAnalysisReplacementContextProvider()
    runtime_limits: RuntimeLimits = RuntimeLimits(
        max_run_generation_tokens=None,
        # Two bounded reads, submit, one contract repair, and final receipt.
        max_model_rounds=5,
        max_progress_rounds=0,
    )
    recovery_policy: RecoveryPolicy = RecoveryPolicy()


class NovelAnalysisReplacementProfile:
    id = NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID
    domain_namespace = NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE
    def __init__(self, db) -> None:
        self._worker_id = f"novel-analysis-replacement-{uuid4().hex}"
        self._source = SqliteNovelAnalysisSourceRepository(db)
        source_catalog = build_novel_analysis_source_tool_catalog(db)
        observation_catalog = build_novel_analysis_observation_tool_catalog()
        submission_catalog = build_novel_analysis_submission_tool_catalog(db)
        follow_up_catalog = build_novel_analysis_follow_up_tool_catalog(db)
        tool_catalog = InMemoryToolCatalog((
                *source_catalog.registrations(),
                *observation_catalog.registrations(),
                *submission_catalog.registrations(),
                *follow_up_catalog.registrations(),
        ), enablement=_enabled_unit_tools)
        validate_analysis_capabilities(tool_catalog)
        self._adapter = NovelAnalysisReplacementAdapter(
            tool_catalog=tool_catalog,
        )

    @property
    def adapter(self):
        return self._adapter

    async def prepare_request(self, request: AgentRunRequest) -> AgentRunRequest:
        unit = NovelAnalysisUnitContext.from_request(request)
        await self._source.validate_scope(
            NovelAnalysisRequestScope.from_request(request)
        )
        if _is_follow_up(request):
            if unit is not None:
                raise ValueError("follow-up request cannot be a durable Unit")
            return replace(
                request,
                planning_mode=PlanningMode.REACTIVE,
                tools_enabled=True,
            )
        return (
            replace(request, planning_mode=PlanningMode.PLANNED)
            if unit is None
            else request
        )

    def run_binding_attributes(self, request: AgentRunRequest) -> dict[str, object]:
        scope = NovelAnalysisRequestScope.from_request(request)
        return {
            "novelAnalysisBinding": scope.to_mapping(),
            "agentImplementation": replacement_implementation(
                AgentKind.NOVEL_ANALYSIS,
                recipe_version=NOVEL_ANALYSIS_REPLACEMENT_RECIPE_VERSION,
            ).to_mapping(),
            **({
                "interactionKind": "follow_up",
                **(
                    {
                        "analysisArtifactId": str(
                            request.metadata["analysisArtifactId"]
                        ),
                    }
                    if request.metadata.get("analysisArtifactId")
                    else {}
                ),
            } if _is_follow_up(request) else {}),
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
        scope = NovelAnalysisRequestScope.from_request(request)
        if not isinstance(plan, ExecutionPlan) or plan.task_spec is None:
            raise ValueError("replacement novel analysis requires a planned TaskSpec")
        if plan.task_spec.operation != "analyze" or plan.task_spec.target:
            raise ValueError("replacement novel analysis TaskSpec changed host intent")
        plan_step_ids = tuple(plan.work_step_ids or ())
        if not plan_step_ids:
            raise ValueError("replacement novel analysis requires visible plan steps")
        recipe = compile_analysis_recipe(
            source_revision_id=scope.source_revision_id,
            segments=scope.segments,
            plan_step_ids=plan_step_ids,
        )
        model_calls = sum(
            1 for step in recipe.steps if step.kind in _MODEL_UNIT_KINDS
        )
        model_attempt_budget = sum(
            step.max_attempts
            for step in recipe.steps
            if step.kind in _MODEL_UNIT_KINDS
        )
        return TaskAdmissionDecision(
            mode=ExecutionMode.DURABLE,
            reason_code="novel_analysis_replacement_requires_durable_execution",
            estimated_units=len(recipe.steps),
            estimated_model_calls=model_calls,
            covered_step_ids=plan_step_ids,
            execution_recipe=recipe,
            metadata={
                **scope.to_mapping(),
                "recipeVersion": NOVEL_ANALYSIS_REPLACEMENT_RECIPE_VERSION,
                "analysisSchemaVersion": NOVEL_ANALYSIS_REPLACEMENT_SCHEMA_VERSION,
                "recipeDigest": recipe.metadata["recipeDigest"],
                "plannerAuthority": "presentation_mapping_only",
                "modelAttemptBudget": model_attempt_budget,
                "failedResumeAttempts": int(
                    request.metadata.get("failedResumeAttempts") or 0
                ),
                **(
                    {"runtimeBinding": request.metadata["runtimeBinding"]}
                    if isinstance(request.metadata.get("runtimeBinding"), Mapping)
                    else {}
                ),
            },
        )

    def create_long_task_dispatcher(self, *, long_task_repository=None, executor=None):
        if executor is None:
            return None
        return create_novel_analysis_replacement_dispatcher(
            long_task_repository=long_task_repository,
            executor=executor,
            worker_id=self._worker_id,
        )

    def clear_active_executions(self) -> None:
        return None


def build_novel_analysis_replacement_profile(*, db, **_dependencies):
    return NovelAnalysisReplacementProfile(db)


def novel_analysis_replacement_implementation_profile():
    return AgentImplementationProfile(
        identity=replacement_implementation(AgentKind.NOVEL_ANALYSIS),
        runtime_profile_id=NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID,
    )


def _enabled_unit_tools(request) -> frozenset[str]:
    if _is_follow_up(request):
        return frozenset({
            "listAnalysisSourceSegments",
            "readAnalysisSourceSegment",
            *(
                (READ_ANALYSIS_REVIEW_ARTIFACT,)
                if request.metadata.get("analysisArtifactId")
                else ()
            ),
        })
    unit = NovelAnalysisUnitContext.from_request(request)
    return (
        frozenset()
        if unit is None
        else frozenset(analysis_model_tool_names(unit.kind))
    )


def _is_follow_up(request: AgentRunRequest) -> bool:
    kind = str(request.metadata.get("interactionKind") or "").strip()
    if not kind:
        return False
    if kind != "follow_up":
        raise ValueError("replacement analysis interaction kind is invalid")
    artifact_id = str(request.metadata.get("analysisArtifactId") or "").strip()
    if "://" in artifact_id:
        raise ValueError("replacement analysis follow-up Artifact id is invalid")
    return True


__all__ = [
    "NOVEL_ANALYSIS_REPLACEMENT_PROFILE_ID",
    "NovelAnalysisReplacementProfile",
    "build_novel_analysis_replacement_profile",
    "novel_analysis_replacement_implementation_profile",
    "validate_novel_analysis_replacement_plan",
]
