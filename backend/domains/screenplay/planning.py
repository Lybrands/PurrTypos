"""Planning policy for the first screenplay Agent milestone."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent_core.contracts import (
    AgentRunRequest,
    PlanningCapabilities,
    PlanningConstraints,
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
        if str(selected.get("action") or "") != "continue":
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
        tool_chain = _ARTIFACT_TOOL_CHAINS.get(
            str(candidate.get("kind") or "")
        )
        if tool_chain is None:
            return base
        begin_tool, append_tools, finalize_tool = tool_chain
        satisfied = set(base.satisfied_tool_dependency_edges)
        satisfied.update((append_tool, begin_tool) for append_tool in append_tools)
        expected = candidate.get("expectedItemCount")
        committed = candidate.get("committedItemCount")
        if (
            isinstance(expected, int)
            and not isinstance(expected, bool)
            and isinstance(committed, int)
            and not isinstance(committed, bool)
            and committed == expected
        ):
            satisfied.update(
                (finalize_tool, append_tool) for append_tool in append_tools
            )
        return PlanningConstraints(
            context_satisfied_tool_names=base.context_satisfied_tool_names,
            planning_excluded_tool_names=base.planning_excluded_tool_names,
            satisfied_tool_dependency_edges=frozenset(satisfied),
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
