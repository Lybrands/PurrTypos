"""Repository-backed source for WritingContextProvider."""

from __future__ import annotations

from agent_core.contracts import AgentRunRequest
from domains.writing.associated_context import (
    AssociatedContextBuilder,
    AssociatedContextResult,
    ChapterContextFact,
    OutlineContextFact,
)
from domains.writing.contracts import WritingDomainContext
from domains.writing.memory_context import (
    MemoryContextBlock,
    MemoryContextRequest,
    WritingMemoryContextBuilder,
    unavailable_memory_context,
)
from domains.writing.repositories import (
    AssociatedContextRepository,
    MemoryRecallRepository,
)


class RepositoryWritingContextSource:
    """Own writing retrieval orchestration without infrastructure globals."""

    def __init__(
        self,
        associated_repository: AssociatedContextRepository,
        memory_repository: MemoryRecallRepository,
    ):
        self._associated = AssociatedContextBuilder(associated_repository)
        self._memory = WritingMemoryContextBuilder(memory_repository)

    async def build_memory(
        self,
        context: WritingDomainContext,
        request: AgentRunRequest,
        token_budget: int,
    ) -> MemoryContextBlock:
        memory_request = MemoryContextRequest(
            book_id=context.book_id,
            user_prompt=request.latest_user_text(),
            token_budget=token_budget,
            recall_limit=_memory_recall_limit(request.context_window),
            selected_spark_idea_ids=context.selected_memory_ids,
            selected_foreshadowing_ids=context.selected_foreshadowing_ids,
        )
        try:
            return await self._memory.build(memory_request)
        except Exception:
            # Retrieval is optional context and must not prevent a chat run.
            return unavailable_memory_context(memory_request)

    async def build_associated(
        self,
        context: WritingDomainContext,
        request: AgentRunRequest,
        token_budget: int,
    ) -> AssociatedContextResult:
        del request
        try:
            return await self._associated.build(context, token_budget)
        except Exception:
            outline_ids = tuple(dict.fromkeys(
                str(value).strip()
                for value in context.associated_outline_ids
                if str(value).strip()
            ))
            return AssociatedContextResult(
                chapter_facts=tuple(
                    ChapterContextFact(chapter_id, "not_injected")
                    for chapter_id in tuple(dict.fromkeys(
                        str(value).strip()
                        for value in context.associated_chapter_ids
                        if str(value).strip()
                    ))
                ),
                outline_facts=tuple(
                    OutlineContextFact(outline_id, "not_injected")
                    for outline_id in outline_ids
                ),
            )


def _memory_recall_limit(context_window: int | None) -> int:
    window = int(context_window or 200_000)
    if window >= 1_000_000:
        return 64
    if window >= 300_000:
        return 32
    return 16
