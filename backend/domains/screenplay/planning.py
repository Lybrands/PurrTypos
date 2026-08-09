"""Planning policy for the first screenplay Agent milestone."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent_core.contracts import (
    AgentRunRequest,
    PlanningCapabilities,
    PlanningConstraints,
    StepExecutor,
    TaskSpec,
)
from domains.screenplay.contracts import ScreenplayDomainContext


class ScreenplayPlanningPolicy:
    """Plan only explicit Agent runs that can use the screenplay catalog."""

    def planning_constraints(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> PlanningConstraints:
        context = ScreenplayDomainContext.from_core_context(
            request.domain_context
        )
        base = capabilities.constraints
        required = set(base.required_any_tool_names)
        planning_excluded = set(base.planning_excluded_tool_names)
        excluded_agent_roles = set(base.planning_excluded_agent_roles)
        excluded_executors = set(base.planning_excluded_executors)
        allow_model_only_fallback = base.allow_model_only_fallback

        requested_scene_count = capabilities.host_planning_facts.get(
            "requestedSceneCount"
        )
        multi_scene_draft = (
            context.requested_stage == "draft"
            and isinstance(requested_scene_count, int)
            and not isinstance(requested_scene_count, bool)
            and requested_scene_count > 1
        )
        if not multi_scene_draft:
            excluded_agent_roles.update(capabilities.available_agent_roles)

        if context.task_intent == "stage_deliverable":
            completion_capability = str(
                capabilities.host_planning_facts.get(
                    "requestedCompletionCapability"
                )
                or ""
            ).strip()
            if completion_capability:
                required.add(completion_capability)
            allow_model_only_fallback = False
        elif (
            context.requested_stage == "draft"
            and context.draft_scope != "planner"
            and "continueScreenplayDraft" in capabilities.available_tool_names
        ):
            # Explicit draft actions have a host-bound range. The shared Core
            # Planner still authors the user-visible execution steps; these
            # constraints limit their authority to the draft capability while
            # the domain only validates and binds that same execution graph.
            required.add("continueScreenplayDraft")
            planning_excluded.update(
                capabilities.available_tool_names
                - {"continueScreenplayDraft"}
                - base.context_satisfied_tool_names
            )
            if (
                isinstance(requested_scene_count, int)
                and not isinstance(requested_scene_count, bool)
                and requested_scene_count > 1
            ):
                # The model owns semantic scope, not the stable Writer /
                # Reviewer / Rewriter topology.  Once the host has bound the
                # selected scenes, task admission compiles that mechanical DAG
                # deterministically and exposes its progress separately.
                excluded_executors.add(StepExecutor.AGENT)
                excluded_agent_roles.update(
                    capabilities.available_agent_roles
                )
            allow_model_only_fallback = False

        return PlanningConstraints(
            context_satisfied_tool_names=base.context_satisfied_tool_names,
            planning_excluded_tool_names=frozenset(planning_excluded),
            satisfied_tool_dependency_edges=(
                base.satisfied_tool_dependency_edges
            ),
            required_any_tool_names=frozenset(required),
            execution_satisfied_tool_names=(
                base.execution_satisfied_tool_names
            ),
            planning_excluded_agent_roles=frozenset(
                excluded_agent_roles
            ),
            required_any_agent_roles=base.required_any_agent_roles,
            minimum_root_agent_count=base.minimum_root_agent_count,
            agent_assignment_coverages=base.agent_assignment_coverages,
            planning_excluded_executors=frozenset(excluded_executors),
            allow_model_only_fallback=allow_model_only_fallback,
        )

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

    def planning_constraints_for_task(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
        task_spec: TaskSpec,
    ) -> PlanningConstraints:
        ScreenplayDomainContext.from_core_context(request.domain_context)
        base = capabilities.constraints
        selected = task_spec.target.get("artifactContinuity")
        if not isinstance(selected, Mapping):
            return base
        action = str(selected.get("action") or "")
        if action not in {"continue", "reference"}:
            return base
        artifact_id = str(selected.get("artifactId") or "").strip()
        work_item_id = str(selected.get("workItemId") or "").strip()
        candidate = _selected_candidate(
            capabilities.host_planning_facts,
            artifact_id=artifact_id,
            work_item_id=work_item_id,
        )
        if candidate is None:
            return base
        replay_finalization = (
            action == "reference"
            and str(candidate.get("nextAction") or "")
            == "replay_finalization"
        )
        if action != "continue" and not replay_finalization:
            return base
        tool_chain = _ARTIFACT_TOOL_CHAINS.get(
            str(candidate.get("kind") or "")
        )
        if tool_chain is None:
            return base
        begin_tool, append_tools, _finalize_tool = tool_chain
        execution_satisfied = set(base.execution_satisfied_tool_names)
        execution_satisfied.add(begin_tool)
        expected = candidate.get("expectedItemCount")
        committed = candidate.get("committedItemCount")
        if replay_finalization or (
            isinstance(expected, int)
            and not isinstance(expected, bool)
            and isinstance(committed, int)
            and not isinstance(committed, bool)
            and committed == expected
        ):
            execution_satisfied.update(append_tools)
        return PlanningConstraints(
            context_satisfied_tool_names=base.context_satisfied_tool_names,
            planning_excluded_tool_names=base.planning_excluded_tool_names,
            satisfied_tool_dependency_edges=(
                base.satisfied_tool_dependency_edges
            ),
            required_any_tool_names=base.required_any_tool_names,
            execution_satisfied_tool_names=frozenset(
                execution_satisfied
            ),
            planning_excluded_agent_roles=(
                base.planning_excluded_agent_roles
            ),
            required_any_agent_roles=base.required_any_agent_roles,
            minimum_root_agent_count=(
                base.minimum_root_agent_count
            ),
            agent_assignment_coverages=(
                base.agent_assignment_coverages
            ),
            planning_excluded_executors=(
                base.planning_excluded_executors
            ),
            allow_model_only_fallback=base.allow_model_only_fallback,
        )


_ARTIFACT_TOOL_CHAINS = {
    "source_analysis_entries": (
        "beginSourceAnalysisArtifact",
        ("appendSourceAnalysisBatch",),
        "finalizeSourceAnalysisProposal",
    ),
    "creative_brief_entries": (
        "beginCreativeBriefArtifact",
        ("appendCreativeBriefBatch",),
        "finalizeCreativeBriefProposal",
    ),
    "screenplay_structure_units": (
        "beginScreenplayStructureArtifact",
        ("appendScreenplayStructureBatch",),
        "finalizeScreenplayStructureProposal",
    ),
    "scene_list_batches": (
        "beginSceneListArtifact",
        ("appendSceneListBatch",),
        "finalizeSceneListProposal",
    ),
    "screenplay_review_entries": (
        "beginScreenplayReviewArtifact",
        ("appendScreenplayReviewBatch",),
        "finalizeScreenplayReviewProposal",
    ),
    "screenplay_revision_changes": (
        "beginScreenplayRevisionArtifact",
        (
            "appendScreenplayRevisionBatch",
            "appendScreenplayRevisionResolutionBatch",
        ),
        "finalizeScreenplayRevisionProposal",
    ),
}


def _selected_candidate(
    facts: Mapping[str, object],
    *,
    artifact_id: str,
    work_item_id: str,
) -> Mapping[str, object] | None:
    continuity = facts.get("artifactContinuity")
    if not isinstance(continuity, Mapping):
        return None
    candidates = continuity.get("candidates")
    if not isinstance(candidates, Sequence) or isinstance(
        candidates,
        (str, bytes, bytearray),
    ):
        return None
    return next((
        candidate
        for candidate in candidates
        if isinstance(candidate, Mapping)
        and str(candidate.get("artifactId") or "") == artifact_id
        and str(candidate.get("workItemId") or "") == work_item_id
    ), None)
