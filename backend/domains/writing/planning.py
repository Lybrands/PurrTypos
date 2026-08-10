"""Writing-domain eligibility policy for Agent planning."""

from __future__ import annotations

from collections.abc import Mapping

from purra.contracts import (
    AgentRunRequest,
    PlanningCapabilities,
    PlanningConstraints,
)
from domains.writing.contracts import WritingDomainContext


WRITING_TOOL_PLANNING_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "editGlobalOutline": ("getGlobalOutline",),
    "queryOutline": ("listOutlines",),
    "updateOutline": ("listOutlines", "queryOutline"),
    "createWritingChapter": ("listWritingChapters",),
    "getChapterContent": ("listWritingChapters",),
    "batchGetChapterContents": ("listWritingChapters",),
    "editChapterContent": ("listWritingChapters",),
    "addForeshadowing": ("listWritingChapters",),
    "getBookCharacters": ("listBookCharacters",),
    "createCharacter": ("listBookCharacters",),
    "updateCharacter": ("listBookCharacters",),
    "deleteCharacter": ("listBookCharacters",),
    "editStoryBackground": ("getStoryBackground",),
    "getSettingEntities": ("listSettingEntities",),
    "createSettingEntity": ("listSettingEntities",),
    "updateSettingEntity": ("listSettingEntities",),
    "deleteSettingEntity": ("listSettingEntities",),
}


class WritingPlanningPolicy:
    def planning_constraints(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> PlanningConstraints:
        base = capabilities.constraints
        satisfied_edges = set(base.satisfied_tool_dependency_edges)
        context = WritingDomainContext.from_core_context(request.domain_context)

        if _bound_current_chapter_satisfies_catalog_dependency(
            context,
            capabilities,
        ):
            satisfied_edges.add((
                "getChapterContent",
                "listWritingChapters",
            ))

        return PlanningConstraints(
            context_satisfied_tool_names=base.context_satisfied_tool_names,
            planning_excluded_tool_names=base.planning_excluded_tool_names,
            satisfied_tool_dependency_edges=frozenset(satisfied_edges),
            required_any_tool_names=base.required_any_tool_names,
            execution_satisfied_tool_names=(
                base.execution_satisfied_tool_names
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

    def should_plan(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> bool:
        context = WritingDomainContext.from_core_context(request.domain_context)
        text = request.latest_user_text().strip()
        if not text or not context.book_id:
            return False
        mode = (request.mode or "").strip().lower()
        del capabilities
        if mode == "ask" and not request.tools_enabled:
            return False
        if mode != "agent" and not request.tools_enabled:
            return False
        return len(text) >= 8


def _bound_current_chapter_satisfies_catalog_dependency(
    context: WritingDomainContext,
    capabilities: PlanningCapabilities,
) -> bool:
    available = capabilities.available_tool_names
    if not {"getChapterContent", "listWritingChapters"} <= available:
        return False
    if not str(context.chapter_id or "").strip():
        return False
    if not _bound_chapter_locator_is_consistent(context):
        return False

    current_fact = capabilities.host_planning_facts.get("currentChapter")
    if not isinstance(current_fact, Mapping):
        return False
    return (
        current_fact.get("bound") is True
        and current_fact.get("singleChapterToolsMayOmitChapterId") is True
    )


def _bound_chapter_locator_is_consistent(
    context: WritingDomainContext,
) -> bool:
    if not context.writing_chapters:
        return True
    current_id = str(context.chapter_id or "").strip()
    current = next(
        (
            item
            for item in context.writing_chapters
            if str(item.get("id") or "").strip() == current_id
        ),
        None,
    )
    if current is None or not str(current.get("title") or "").strip():
        return False
    parent_ids = {
        str(
            item.get("parent_id")
            if item.get("parent_id") is not None
            else item.get("parentId")
        ).strip()
        for item in context.writing_chapters
        if (
            item.get("parent_id") is not None
            or item.get("parentId") is not None
        )
        and str(
            item.get("parent_id")
            if item.get("parent_id") is not None
            else item.get("parentId")
        ).strip()
    }
    return current_id not in parent_ids


__all__ = [
    "WRITING_TOOL_PLANNING_DEPENDENCIES",
    "WritingPlanningPolicy",
]
