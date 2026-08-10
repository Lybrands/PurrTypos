"""Host-owned context contracts for every Writing tool.

These contracts describe dependency and evidence policy only.  They never
grant authority; scope validators and tool policies remain authoritative.
"""

from __future__ import annotations

from purra.contracts import ToolContextContract, ToolResultProjection
from domains.writing.planning import WRITING_TOOL_PLANNING_DEPENDENCIES
from domains.writing.policies import WRITING_TOOL_POLICIES


_CHAPTER_TOOLS = frozenset({
    "getChapterContent",
    "editChapterContent",
    "addForeshadowing",
})
_MEMORY_AWARE_TOOLS: dict[str, tuple[str, ...]] = {
    "editChapterContent": (
        "canon",
        "plot",
        "character",
        "foreshadowing",
        "style",
        "summary",
    ),
    "editGlobalOutline": ("canon", "plot", "character", "foreshadowing", "summary"),
    "updateOutline": ("canon", "plot", "character", "foreshadowing", "summary"),
    "updateCharacter": ("canon", "character", "plot"),
    "editStoryBackground": ("canon", "world"),
    "updateSettingEntity": ("canon", "world"),
}
_PRODUCES: dict[str, tuple[str, ...]] = {
    "getChapterContent": ("chapter.current", "chapter.currentVersion"),
    "batchGetChapterContents": ("chapter.batch",),
    "listWritingChapters": ("chapter.catalog",),
    "editChapterContent": ("chapter.proposal",),
    "queryOutline": ("outline.current",),
    "getGlobalOutline": ("outline.global",),
    "listOutlines": ("outline.catalog",),
    "updateOutline": ("outline.updated",),
    "editGlobalOutline": ("outline.globalUpdated",),
    "getBookCharacters": ("character.current",),
    "listBookCharacters": ("character.catalog",),
    "getStoryBackground": ("world.background",),
    "getSettingEntities": ("world.entities",),
    "listSettingEntities": ("world.entityCatalog",),
    "searchMemories": ("memory.matches",),
    "searchSparkIdeas": ("sparkIdea.matches",),
}
_PREREQUISITE_OVERRIDES: dict[str, tuple[str, ...]] = {
    # Editing requires the current source, not merely a locator catalogue.
    # getChapterContent owns locator discovery when the current binding does
    # not already satisfy it.
    "editChapterContent": ("getChapterContent",),
    "updateCharacter": ("getBookCharacters",),
    "updateSettingEntity": ("getSettingEntities",),
    "editStoryBackground": ("getStoryBackground",),
}
_RECEIPT_AFTER_CONSUMPTION = frozenset({
    "listWritingChapters",
    "listBookCharacters",
    "listOutlines",
    "listSettingEntities",
})


def _contract(tool_name: str) -> ToolContextContract:
    mandatory = ["taskSpec", "binding.bookId"]
    if tool_name in _CHAPTER_TOOLS:
        mandatory.append("binding.chapterId")
    return ToolContextContract(
        prerequisite_tools=_PREREQUISITE_OVERRIDES.get(
            tool_name,
            WRITING_TOOL_PLANNING_DEPENDENCIES.get(tool_name, ()),
        ),
        mandatory_context_keys=tuple(mandatory),
        required_context_blocks=(
            ("writing_retrieval",)
            if tool_name in _MEMORY_AWARE_TOOLS
            else ()
        ),
        evidence_kinds=_MEMORY_AWARE_TOOLS.get(tool_name, ()),
        produces=_PRODUCES.get(tool_name, (f"tool.{tool_name}.result",)),
        result_projection=(
            ToolResultProjection.RECEIPT
            if tool_name in _RECEIPT_AFTER_CONSUMPTION
            else ToolResultProjection.FULL
        ),
    )


WRITING_TOOL_CONTEXT_CONTRACTS = {
    name: _contract(name)
    for name in WRITING_TOOL_POLICIES
}


__all__ = ["WRITING_TOOL_CONTEXT_CONTRACTS"]
