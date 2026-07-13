"""Compatibility factory for the current mutable writing tool context."""

from __future__ import annotations

from copy import deepcopy

from agent_core.contracts import AgentRunRequest, ExecutionState
from domains.writing.contracts import WritingDomainContext


_WINDOW_LABELS = {
    32_000: "32k",
    64_000: "64k",
    128_000: "128k",
    200_000: "200k",
    300_000: "300k",
    1_000_000: "1m",
}


class WritingExecutionStateFactory:
    """Create one legacy-shaped state object per Agent Run."""

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
            "contextWindow": (
                context.context_window_label
                or _WINDOW_LABELS.get(request.context_window or 0)
            ),
        })
