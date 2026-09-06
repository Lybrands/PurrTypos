"""Writing-domain constraints for explicitly planned Agent runs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace

from purra.contracts import (
    AgentRunRequest,
    PlanningCapabilities,
    PlanningConstraints,
)
from domains.writing.contracts import WritingDomainContext
from domains.writing.context import WRITING_PLANNING_FACTS_CONTEXT


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

        return replace(
            base,
            satisfied_tool_dependency_edges=frozenset(satisfied_edges),
        )

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

    planning_facts: Mapping[str, object] = {}
    block = next(
        (
            item
            for item in capabilities.planning_context_blocks
            if item.name == WRITING_PLANNING_FACTS_CONTEXT
        ),
        None,
    )
    if block is not None:
        try:
            parsed = json.loads(block.content)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, Mapping):
            planning_facts = parsed
    current_fact = planning_facts.get("currentChapter")
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
