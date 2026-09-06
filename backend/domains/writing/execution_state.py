"""Factory for the mutable state consumed by Writing tool handlers."""

from __future__ import annotations

from copy import deepcopy

from purra.contracts import AgentRunRequest, ExecutionState
from domains.writing.contracts import WritingDomainContext


_WINDOW_LABELS = {
    32_000: "32k",
    64_000: "64k",
    128_000: "128k",
    200_000: "200k",
    256_000: "256k",
    300_000: "300k",
    1_000_000: "1m",
}


class WritingExecutionStateFactory:
    """Create one isolated Writing state object per Agent Run."""

    def create(self, request: AgentRunRequest) -> ExecutionState:
        context = WritingDomainContext.from_core_context(request.domain_context)
        return ExecutionState(domain={
            "bookId": context.book_id,
            "chapterId": context.chapter_id,
            "currentChapterTitle": context.current_chapter_title,
            "writingChapters": deepcopy(list(context.writing_chapters)),
            "availableOutlines": deepcopy(list(context.available_outlines)),
            "associatedChapterIds": list(context.associated_chapter_ids),
            "associatedOutlineIds": list(context.associated_outline_ids),
            "selectedMemoryIds": list(context.selected_memory_ids),
            "selectedLongTermMemoryIds": list(
                context.selected_long_term_memory_ids
            ),
            "selectedForeshadowingIds": list(context.selected_foreshadowing_ids),
            "chatAgentMode": request.mode or "",
            "contextWindow": (
                context.context_window_label
                or _WINDOW_LABELS.get(request.context_window or 0)
            ),
            "creationMode": context.creation_mode,
            "continuationBinding": deepcopy(dict(context.continuation_binding or {})),
        })
