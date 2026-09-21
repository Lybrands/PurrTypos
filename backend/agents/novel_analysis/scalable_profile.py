"""Isolated v2 Profile for scalable whole-book analysis."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

from agents.novel_analysis.dispatcher import (
    create_novel_analysis_replacement_dispatcher,
)
from agents.novel_analysis.domain import (
    NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE,
    NovelAnalysisRequestScope,
)
from agents.novel_analysis.follow_up_tools import (
    READ_ANALYSIS_REVIEW_ARTIFACT,
    build_novel_analysis_follow_up_tool_catalog,
)
from agents.novel_analysis.follow_up_runtime import (
    NovelAnalysisFollowUpContextProvider,
    NovelAnalysisFollowUpExecutionStateFactory,
)
from agents.novel_analysis.child_submission import (
    SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
    build_novel_analysis_child_submission_registration,
)
from agents.novel_analysis.source_model import SqliteNovelAnalysisSourceRepository
from agents.novel_analysis.source_tools import build_novel_analysis_source_tool_catalog
from agents.novel_analysis.map_execution import (
    READ_NOVEL_SOURCE_SLICE,
    SCALABLE_MAP_SCOPE_STATE_KEY,
    build_scalable_map_source_tool_catalog,
)
from agents.novel_analysis.planner_contract import (
    AnalysisPlanningLimits,
    NovelAnalysisPlannerContractError,
    SCALABLE_ANALYSIS_RECIPE_VERSION,
    ScalableAnalysisPlan,
    compile_scalable_analysis_recipe,
    planner_context_mapping,
)
from agents.novel_analysis.reduce_execution import (
    READ_NOVEL_ANALYSIS_REDUCE_INPUTS,
    build_scalable_reduce_input_tool_catalog,
)
from agents.novel_analysis.review_execution import (
    READ_NOVEL_ANALYSIS_REVIEW_INPUT,
    build_review_input_tool_catalog,
)
from agents.novel_analysis.source_slicing import (
    compile_persisted_source_slice_manifest,
)
from agents.novel_analysis.synthesize_execution import (
    READ_NOVEL_ANALYSIS_SYNTHESIS_INPUTS,
    build_synthesis_input_tool_catalog,
)
from agents.novel_analysis.skill_creation import (
    READ_NOVEL_ANALYSIS_SKILL_INPUT,
    build_skill_input_tool_catalog,
)
from agents.shared.implementation import (
    AgentImplementationIdentity,
    AgentKind,
    REPLACEMENT_IMPLEMENTATION_ID,
)
from agents.shared.implementation_registry import AgentImplementationProfile
from purra.agent_tree_policy import AgentTreePolicy
from purra.cancellation import raise_if_stopped
from purra.context_strategies import ContextStrategy
from purra.contracts import (
    AgentRunRequest,
    ContextBlock,
    ContextBudgetClaim,
    ContextBundle,
    ExecutionPlan,
    ExecutionRecipe,
    ExecutionState,
    PlanningCapabilities,
    PlanningConstraints,
    PlanningKind,
    PlanningMode,
    PlanningResult,
    PlannerLimits,
    RuntimeLimits,
    StepExecutor,
)
from purra.recovery import RecoveryPolicy
from purra.task_admission import ExecutionMode, TaskAdmissionDecision
from purra.json_values import thaw_json_mapping
from purra.tools import InMemoryToolCatalog


NOVEL_ANALYSIS_SCALABLE_PROFILE_ID = "novel_analysis.scalable.v2"
_PLANNING_LIMITS = AnalysisPlanningLimits()


@dataclass(frozen=True, slots=True)
class ScalableAnalysisRequestScope:
    source_revision_id: str
    command_id: str

    def __post_init__(self) -> None:
        revision_id = str(self.source_revision_id or "").strip()
        command_id = str(self.command_id or "").strip()
        if not revision_id or not command_id:
            raise ValueError("scalable analysis requires revision and command identity")
        object.__setattr__(self, "source_revision_id", revision_id)
        object.__setattr__(self, "command_id", command_id)

    @classmethod
    def from_request(cls, request: AgentRunRequest):
        if request.domain_context.namespace != NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE:
            raise ValueError("unsupported scalable analysis namespace")
        payload = thaw_json_mapping(request.domain_context.payload)
        if set(payload) != {"sourceRevisionId", "commandId"}:
            raise ValueError("scalable analysis request must use the canonical v2 shape")
        return cls(payload["sourceRevisionId"], payload["commandId"])


def _is_map_child(request: AgentRunRequest) -> bool:
    return bool(
        str(request.metadata.get("parentRunId") or "").strip()
        and str(request.metadata.get("agentId") or "").strip()
    )


def _is_follow_up(request: AgentRunRequest) -> bool:
    return str(request.metadata.get("interactionKind") or "") == "follow_up"


def _map_scope_from_child_message(
    request: AgentRunRequest,
) -> Mapping[str, object] | None:
    """Project Child input into display state without granting read authority."""

    _, marker, serialized = request.latest_user_text().rpartition("\n\nInput:\n")
    if not marker:
        return None
    try:
        child_input = json.loads(serialized)
    except (TypeError, ValueError):
        return None
    if not isinstance(child_input, Mapping):
        return None
    raw = child_input.get("mapSliceScope")
    return raw if isinstance(raw, Mapping) else None


def validate_scalable_analysis_plan(
    request: AgentRunRequest,
    result: PlanningResult,
) -> str | None:
    ScalableAnalysisRequestScope.from_request(request)
    if result.kind is not PlanningKind.PLANNED:
        return "scalable novel analysis requires a model-authored plan"
    task_spec = result.work_plan.task_spec
    if task_spec is None or task_spec.operation != "analyze":
        return "scalable novel analysis requires an analyze TaskSpec"
    try:
        ScalableAnalysisPlan.from_mapping(task_spec.target)
    except NovelAnalysisPlannerContractError as error:
        return str(error)
    if not 1 <= len(result.work_plan.steps) <= 3:
        return (
            "scalable novel analysis requires one to three semantic Planner "
            "steps; Host expands all slice-specific work"
        )
    if any(
        step.executor is not StepExecutor.MODEL or step.capability_names
        for step in result.work_plan.steps
    ):
        return "scalable novel analysis Planner steps must describe model work"
    return None


class ScalableAnalysisPlanningPolicy:
    def planning_constraints(
        self, request, capabilities: PlanningCapabilities
    ) -> PlanningConstraints:
        ScalableAnalysisRequestScope.from_request(request)
        return replace(
            capabilities.constraints,
            allow_model_only_fallback=False,
            min_initial_visible_steps=1,
            planning_excluded_tool_names=(
                capabilities.constraints.planning_excluded_tool_names
                | capabilities.available_tool_names
            ),
            planning_excluded_executors=(
                capabilities.constraints.planning_excluded_executors
                | {StepExecutor.TOOL}
            ),
        )


class ScalableAnalysisExecutionStateFactory:
    def __init__(self) -> None:
        self._follow_up = NovelAnalysisFollowUpExecutionStateFactory()

    def create(self, request: AgentRunRequest) -> ExecutionState:
        if _is_follow_up(request):
            return self._follow_up.create(request)
        scope = ScalableAnalysisRequestScope.from_request(request)
        map_scope = request.metadata.get(SCALABLE_MAP_SCOPE_STATE_KEY)
        if _is_map_child(request) and not isinstance(map_scope, Mapping):
            map_scope = _map_scope_from_child_message(request)
        return ExecutionState(domain={
            "sourceRevisionId": scope.source_revision_id,
            **(
                {"sliceManifest": request.metadata["sliceManifest"]}
                if isinstance(request.metadata.get("sliceManifest"), Mapping)
                else {}
            ),
            "executionRole": "map_child" if _is_map_child(request) else "root",
            **(
                {SCALABLE_MAP_SCOPE_STATE_KEY: map_scope}
                if isinstance(map_scope, Mapping)
                else {}
            ),
        })


class ScalableAnalysisContextProvider:
    def __init__(self) -> None:
        self._follow_up = NovelAnalysisFollowUpContextProvider()

    async def build_context(self, request, budget, signal=None):
        if _is_follow_up(request):
            return await self._follow_up.build_context(request, budget, signal)
        raise_if_stopped(signal)
        if _is_map_child(request):
            return ContextBundle(blocks=(ContextBlock(
                name="novel_analysis_map_child_policy",
                content=(
                    "Execute only the current Host-bound Child objective. "
                    "Call exactly once the sole domain read tool granted by the Host, "
                    "then return the JSON object requested by the Child instruction."
                ),
                token_count=32,
                untrusted=False,
            ),))
        manifest = request.metadata.get("sliceManifest")
        if not isinstance(manifest, Mapping):
            raise ValueError("scalable analysis SliceManifest is unavailable")
        content = json.dumps(
            planner_context_mapping_from_request(manifest),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return ContextBundle(blocks=(ContextBlock(
            name="novel_analysis_scalable_planner",
            content=content,
            token_count=max(1, len(content) // 4),
            untrusted=False,
            host_metadata={"recipeVersion": SCALABLE_ANALYSIS_RECIPE_VERSION},
        ),))

    async def build_planning_context(self, request, budget, signal=None):
        return await self.build_context(request, budget, signal)

    async def build_task_context(self, request, budget, task, signal=None):
        del task
        return await self.build_context(request, budget, signal)

    async def describe_context_demands(self, request, signal=None):
        if _is_follow_up(request):
            return await self._follow_up.describe_context_demands(request, signal)
        raise_if_stopped(signal)
        return (ContextBudgetClaim(
            name=(
                "novel_analysis_map_child_policy"
                if _is_map_child(request)
                else "novel_analysis_scalable_planner"
            ),
            desired_tokens=2_048,
            minimum_tokens=32,
            maximum_tokens=8_192,
            priority=100,
        ),)


def planner_context_mapping_from_request(raw_manifest: Mapping) -> dict[str, object]:
    """Rehydrate only the fields needed by the already validated helper."""
    from agents.novel_analysis.source_slicing import (
        NovelAnalysisSliceManifest,
        NovelAnalysisSourceSlice,
        SliceSourceRange,
    )

    slices = tuple(NovelAnalysisSourceSlice(
        id=item["sliceId"],
        position=item["position"],
        character_count=item["characterCount"],
        token_count=item["tokenCount"],
        ranges=tuple(SliceSourceRange(
            section_id=source_range["sectionId"],
            section_ordinal=source_range["sectionOrdinal"],
            start_character=source_range["startCharacter"],
            end_character=source_range["endCharacter"],
            character_count=source_range["characterCount"],
            token_count=source_range["tokenCount"],
            content_digest=source_range["contentDigest"],
        ) for source_range in item["ranges"]),
    ) for item in raw_manifest["slices"])
    manifest = NovelAnalysisSliceManifest(
        source_revision_id=raw_manifest["sourceRevisionId"],
        source_revision_digest=raw_manifest["sourceRevisionDigest"],
        context_window_tokens=raw_manifest["contextWindowTokens"],
        source_token_limit=raw_manifest["sourceTokenLimit"],
        packing_token_limit=raw_manifest["packingTokenLimit"],
        tokenizer_id=raw_manifest["tokenizer"]["id"],
        tokenizer_version=raw_manifest["tokenizer"]["version"],
        token_count_kind=raw_manifest["tokenizer"]["countKind"],
        total_character_count=raw_manifest["totalCharacterCount"],
        total_token_count=raw_manifest["totalTokenCount"],
        slices=slices,
    )
    return planner_context_mapping(manifest=manifest, limits=_PLANNING_LIMITS)


@dataclass(frozen=True, slots=True)
class ScalableAnalysisAdapter:
    planning_policy: object = ScalableAnalysisPlanningPolicy()
    planner: None = None
    planner_limits: PlannerLimits = PlannerLimits(
        max_steps=3,
        max_tool_steps=0,
    )
    planning_result_validator = staticmethod(validate_scalable_analysis_plan)
    context_strategy: ContextStrategy = ContextStrategy.STAGED
    execution_state_factory: object = ScalableAnalysisExecutionStateFactory()
    tool_catalog: object = None
    context_provider: object = ScalableAnalysisContextProvider()
    runtime_limits: RuntimeLimits = RuntimeLimits(
        max_run_generation_tokens=None,
        max_model_rounds=None,
        max_progress_rounds=0,
    )
    recovery_policy: RecoveryPolicy = RecoveryPolicy()
    agent_tree_policy: AgentTreePolicy = AgentTreePolicy(
        max_children_per_call=4,
        max_parallel_runs=4,
        max_agents_per_root=1_024,
        allow_recursive_agents=False,
        result_presentation_instruction=None,
    )


class ScalableNovelAnalysisProfile:
    id = NOVEL_ANALYSIS_SCALABLE_PROFILE_ID
    domain_namespace = NOVEL_ANALYSIS_REPLACEMENT_DOMAIN_NAMESPACE

    def __init__(self, db) -> None:
        self._db = db
        self._source = SqliteNovelAnalysisSourceRepository(db)
        map_catalog = build_scalable_map_source_tool_catalog(db)
        reduce_catalog = build_scalable_reduce_input_tool_catalog(db)
        synthesis_catalog = build_synthesis_input_tool_catalog(db)
        skill_catalog = build_skill_input_tool_catalog(db)
        review_catalog = build_review_input_tool_catalog(db)
        child_submission = build_novel_analysis_child_submission_registration(db)
        source_catalog = build_novel_analysis_source_tool_catalog(db)
        follow_up_catalog = build_novel_analysis_follow_up_tool_catalog(db)
        self._adapter = ScalableAnalysisAdapter(
            tool_catalog=InMemoryToolCatalog(
                (
                    *map_catalog.registrations(),
                    *reduce_catalog.registrations(),
                    *synthesis_catalog.registrations(),
                    *skill_catalog.registrations(),
                    *review_catalog.registrations(),
                    child_submission,
                    *source_catalog.registrations(),
                    *follow_up_catalog.registrations(),
                )
            ),
        )

    @property
    def adapter(self):
        return self._adapter

    async def prepare_request(self, request: AgentRunRequest) -> AgentRunRequest:
        if _is_follow_up(request):
            scope = NovelAnalysisRequestScope.from_request(request)
            await self._source.validate_scope(scope)
            return replace(
                request,
                planning_mode=PlanningMode.REACTIVE,
                tools_enabled=True,
            )
        scope = ScalableAnalysisRequestScope.from_request(request)
        revision = await self._db.fetch_one(
            "SELECT id FROM novel_source_revisions WHERE id = ?",
            [scope.source_revision_id],
        )
        if revision is None:
            raise ValueError("scalable analysis source revision does not exist")
        if _is_map_child(request):
            return replace(
                request,
                planning_mode=PlanningMode.REACTIVE,
                tools_enabled=True,
            )
        context_window = request.context_window or (
            request.model.capability_snapshot.context_window_tokens
        )
        manifest = await compile_persisted_source_slice_manifest(
            self._db,
            source_revision_id=scope.source_revision_id,
            context_window_tokens=context_window,
        )
        return replace(
            request,
            planning_mode=PlanningMode.PLANNED,
            # The Root model receives no domain tools through enablement, but
            # the active Core must retain Agent-tree authority for Host-spawned
            # Map children during durable execution.
            tools_enabled=True,
            metadata={**request.metadata, "sliceManifest": manifest.to_mapping()},
        )

    def run_binding_attributes(self, request):
        if _is_follow_up(request):
            scope = NovelAnalysisRequestScope.from_request(request)
            return {
                "novelAnalysisBinding": scope.to_mapping(),
                "agentImplementation": scalable_novel_analysis_implementation(
                    recipe_version=SCALABLE_ANALYSIS_RECIPE_VERSION,
                ).to_mapping(),
                "interactionKind": "follow_up",
                **(
                    {"analysisArtifactId": str(request.metadata["analysisArtifactId"])}
                    if request.metadata.get("analysisArtifactId") else {}
                ),
            }
        scope = ScalableAnalysisRequestScope.from_request(request)
        return {
            "novelAnalysisBinding": {
                "sourceRevisionId": scope.source_revision_id,
                "commandId": scope.command_id,
            },
            "agentImplementation": scalable_novel_analysis_implementation(
                recipe_version=SCALABLE_ANALYSIS_RECIPE_VERSION,
            ).to_mapping(),
            **(
                {"automaticRecovery": True}
                if request.metadata.get("recoverySource") == "automatic"
                else {}
            ),
        }

    def context_provider_factory(self):
        return None

    def response_judge_policies(self, request):
        del request
        return ()

    def task_admission(self):
        return self

    async def evaluate(self, request, plan: ExecutionPlan, signal=None):
        raise_if_stopped(signal)
        if _is_map_child(request):
            raise ValueError("Map Child cannot create a durable analysis task")
        if plan.task_spec is None:
            raise ValueError("scalable analysis requires a TaskSpec")
        semantic_plan = ScalableAnalysisPlan.from_mapping(plan.task_spec.target)
        raw_manifest = request.metadata.get("sliceManifest")
        manifest = _manifest_from_mapping(raw_manifest)
        recipe = compile_scalable_analysis_recipe(
            manifest=manifest,
            plan=semantic_plan,
            limits=_PLANNING_LIMITS,
        )
        recipe = _bind_recipe_to_planner_steps(recipe, plan)
        return TaskAdmissionDecision(
            mode=ExecutionMode.DURABLE,
            reason_code="novel_analysis_scalable_requires_durable_execution",
            estimated_units=len(recipe.steps),
            estimated_model_calls=recipe.metadata["estimatedModelCalls"],
            covered_step_ids=tuple(plan.work_step_ids or ()),
            execution_recipe=recipe,
            metadata={
                "sourceRevisionId": manifest.source_revision_id,
                "commandId": ScalableAnalysisRequestScope.from_request(request).command_id,
                "recipeVersion": SCALABLE_ANALYSIS_RECIPE_VERSION,
                "recipeDigest": recipe.metadata["recipeDigest"],
                "sliceManifest": raw_manifest,
                "analysisPlan": semantic_plan.to_mapping(),
                "modelAttemptBudget": int(recipe.metadata["estimatedModelCalls"]) * 2,
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
            worker_id="novel-analysis-scalable-v2",
            executor_id="novel_analysis.scalable.v2",
        )

    def clear_active_executions(self):
        return None


def _manifest_from_mapping(raw):
    if not isinstance(raw, Mapping):
        raise ValueError("scalable analysis SliceManifest is unavailable")
    mapping = raw
    from agents.novel_analysis.source_slicing import NovelAnalysisSliceManifest, NovelAnalysisSourceSlice, SliceSourceRange
    return NovelAnalysisSliceManifest(
        source_revision_id=mapping["sourceRevisionId"],
        source_revision_digest=mapping["sourceRevisionDigest"],
        context_window_tokens=mapping["contextWindowTokens"],
        source_token_limit=mapping["sourceTokenLimit"],
        packing_token_limit=mapping["packingTokenLimit"],
        tokenizer_id=mapping["tokenizer"]["id"],
        tokenizer_version=mapping["tokenizer"]["version"],
        token_count_kind=mapping["tokenizer"]["countKind"],
        total_character_count=mapping["totalCharacterCount"],
        total_token_count=mapping["totalTokenCount"],
        slices=tuple(NovelAnalysisSourceSlice(
            id=item["sliceId"], position=item["position"],
            character_count=item["characterCount"], token_count=item["tokenCount"],
            ranges=tuple(SliceSourceRange(
                section_id=value["sectionId"], section_ordinal=value["sectionOrdinal"],
                start_character=value["startCharacter"], end_character=value["endCharacter"],
                character_count=value["characterCount"], token_count=value["tokenCount"],
                content_digest=value["contentDigest"],
            ) for value in item["ranges"]),
        ) for item in mapping["slices"]),
    )


def _bind_recipe_to_planner_steps(
    recipe: ExecutionRecipe,
    plan: ExecutionPlan,
) -> ExecutionRecipe:
    """Bind Host-expanded units to the Planner steps admitted by PurrA.

    The Planner describes three semantic phases while the Host may expand each
    phase into many durable units.  PurrA intentionally rejects recipe units
    that do not name an admitted Planner step, so this projection is part of
    the v2 compilation boundary rather than a dispatcher workaround.
    """
    planner_step_ids = tuple(plan.work_step_ids or ())
    if not planner_step_ids:
        raise ValueError("scalable analysis has no admitted Planner steps")
    if len(planner_step_ids) > 3:
        raise ValueError("scalable analysis supports at most three Planner steps")
    has_reduce = any(step.kind == "reduce" for step in recipe.steps)

    def planner_step_id(kind: str) -> str:
        if len(planner_step_ids) == 1:
            return planner_step_ids[0]
        if kind == "map":
            return planner_step_ids[0]
        if len(planner_step_ids) == 2:
            return planner_step_ids[1]
        if kind == "reduce":
            return planner_step_ids[1]
        if kind == "synthesize" and not has_reduce:
            return planner_step_ids[1]
        return planner_step_ids[2]

    return replace(recipe, steps=tuple(
        replace(step, plan_step_id=planner_step_id(step.kind))
        for step in recipe.steps
    ))


def _enabled_tools(request):
    if _is_follow_up(request):
        return frozenset({
            "listAnalysisSourceSegments",
            "readAnalysisSourceSegment",
            *(
                {READ_ANALYSIS_REVIEW_ARTIFACT}
                if request.metadata.get("analysisArtifactId") else set()
            ),
        })
    return frozenset({
        READ_NOVEL_SOURCE_SLICE,
        READ_NOVEL_ANALYSIS_REDUCE_INPUTS,
        READ_NOVEL_ANALYSIS_SYNTHESIS_INPUTS,
        READ_NOVEL_ANALYSIS_SKILL_INPUT,
        READ_NOVEL_ANALYSIS_REVIEW_INPUT,
        SUBMIT_NOVEL_ANALYSIS_CHILD_RESULT,
    }) if _is_map_child(request) else frozenset()


def build_scalable_novel_analysis_profile(*, db, **_dependencies):
    profile = ScalableNovelAnalysisProfile(db)
    # Preserve the catalog registrations while applying request-aware enablement.
    profile._adapter = replace(profile._adapter, tool_catalog=InMemoryToolCatalog(
        profile._adapter.tool_catalog.registrations(), enablement=_enabled_tools
    ))
    return profile


def scalable_novel_analysis_implementation_profile():
    return AgentImplementationProfile(
        identity=scalable_novel_analysis_implementation(),
        runtime_profile_id=NOVEL_ANALYSIS_SCALABLE_PROFILE_ID,
    )


def scalable_novel_analysis_implementation(
    *, recipe_version: int | None = None,
) -> AgentImplementationIdentity:
    """Persistent identity for scalable v2, distinct from historical native v1."""

    return AgentImplementationIdentity(
        agent_kind=AgentKind.NOVEL_ANALYSIS,
        implementation_id=REPLACEMENT_IMPLEMENTATION_ID,
        implementation_version=2,
        tool_contract_version=2,
        artifact_schema_version=1,
        recipe_version=recipe_version,
    )


__all__ = [
    "NOVEL_ANALYSIS_SCALABLE_PROFILE_ID",
    "ScalableNovelAnalysisProfile",
    "ScalableAnalysisRequestScope",
    "build_scalable_novel_analysis_profile",
    "scalable_novel_analysis_implementation",
    "scalable_novel_analysis_implementation_profile",
    "validate_scalable_analysis_plan",
]
