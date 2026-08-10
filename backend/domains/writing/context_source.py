"""Repository-backed source for WritingContextProvider."""

from __future__ import annotations

from purra.contracts import AgentRunRequest, TaskContextRequest
from purra.ports import CancellationSignal
from domains.writing.associated_context import (
    AssociatedContextBuilder,
    AssociatedContextResult,
    ChapterContextFact,
    OutlineContextFact,
)
from domains.writing.contracts import WritingDomainContext
from domains.writing.memory_context import (
    MemoryContextRequest,
    WritingMemoryContextBuilder,
    unavailable_memory_context,
)
from domains.writing.unified_memory_context import (
    MemoryContextAssembler,
    MemoryContextPack,
    StoryMemoryContextBlock,
    StoryMemoryContextProvider,
    UnifiedMemoryRetriever,
    memory_context_request_from_task,
)
from domains.writing.repositories import (
    AssociatedContextRepository,
    MemoryRecallRepository,
    StoryMemoryRecallRepository,
)
from domains.writing.memory_reranking import MemoryCandidateReranker


class RepositoryWritingContextSource:
    """Own writing retrieval orchestration without infrastructure globals."""

    def __init__(
        self,
        associated_repository: AssociatedContextRepository,
        memory_repository: MemoryRecallRepository,
        story_memory_repository: StoryMemoryRecallRepository | None = None,
        memory_reranker: MemoryCandidateReranker | None = None,
    ):
        self._associated_repository = associated_repository
        self._memory_repository = memory_repository
        self._story_memory_repository = story_memory_repository
        self._associated = AssociatedContextBuilder(associated_repository)
        self._memory = UnifiedMemoryRetriever(
            WritingMemoryContextBuilder(
                memory_repository,
                memory_reranker,
            ),
            (
                StoryMemoryContextProvider(
                    story_memory_repository,
                    memory_reranker,
                )
                if story_memory_repository is not None
                else None
            ),
        )

    def with_memory_reranker(
        self,
        reranker: MemoryCandidateReranker,
    ) -> "RepositoryWritingContextSource":
        """Create a request-scoped source without mutating shared adapters."""

        return RepositoryWritingContextSource(
            self._associated_repository,
            self._memory_repository,
            self._story_memory_repository,
            memory_reranker=reranker,
        )

    async def build_memory(
        self,
        context: WritingDomainContext,
        request: AgentRunRequest,
        token_budget: int,
    ) -> MemoryContextPack:
        memory_request = MemoryContextRequest(
            book_id=context.book_id,
            user_prompt=request.latest_user_text(),
            token_budget=token_budget,
            recall_limit=_memory_recall_limit(request.context_window),
            candidate_limit=_memory_candidate_limit(request.context_window),
            selected_spark_idea_ids=context.selected_memory_ids,
            selected_foreshadowing_ids=context.selected_foreshadowing_ids,
        )
        memory_request = memory_context_request_from_task(
            memory_request,
            None,
            current_chapter_id=context.chapter_id,
        )
        try:
            return await self._memory.build(memory_request)
        except Exception:
            # Retrieval is optional context and must not prevent a chat run.
            return _unavailable_pack(memory_request)

    async def build_memory_for_query(
        self,
        context: WritingDomainContext,
        request: AgentRunRequest,
        token_budget: int,
        query: str,
    ) -> MemoryContextPack:
        """Run formal semantic recall with a host-compiled TaskSpec query."""

        memory_request = MemoryContextRequest(
            book_id=context.book_id,
            user_prompt=str(query or "").strip(),
            token_budget=token_budget,
            recall_limit=_memory_recall_limit(request.context_window),
            candidate_limit=_memory_candidate_limit(request.context_window),
            selected_spark_idea_ids=context.selected_memory_ids,
            selected_foreshadowing_ids=context.selected_foreshadowing_ids,
        )
        memory_request = memory_context_request_from_task(
            memory_request,
            None,
            current_chapter_id=context.chapter_id,
        )
        try:
            return await self._memory.build(memory_request)
        except Exception:
            return _unavailable_pack(memory_request)

    async def build_memory_for_task(
        self,
        context: WritingDomainContext,
        request: AgentRunRequest,
        token_budget: int,
        query: str,
        task: TaskContextRequest,
        signal: CancellationSignal | None = None,
    ) -> MemoryContextPack:
        """Compile planner semantic declarations into bounded memory recall."""

        memory_request = memory_context_request_from_task(
            MemoryContextRequest(
                book_id=context.book_id,
                user_prompt=str(query or "").strip(),
                token_budget=token_budget,
                recall_limit=_memory_recall_limit(request.context_window),
                candidate_limit=_memory_candidate_limit(
                    request.context_window
                ),
                selected_spark_idea_ids=context.selected_memory_ids,
                selected_foreshadowing_ids=context.selected_foreshadowing_ids,
            ),
            task,
            current_chapter_id=context.chapter_id,
        )
        try:
            return await self._memory.build(
                memory_request,
                model_request=request.model,
                signal=signal,
            )
        except Exception:
            return _unavailable_pack(memory_request)

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


def _memory_candidate_limit(context_window: int | None) -> int:
    window = int(context_window or 200_000)
    if window >= 1_000_000:
        return 96
    if window >= 300_000:
        return 64
    return 40


def _unavailable_pack(request: MemoryContextRequest) -> MemoryContextPack:
    semantic = unavailable_memory_context(request)
    story = StoryMemoryContextBlock(diagnostics={
        "recalled": 0,
        "included": 0,
        "deferred": 0,
        "unavailable": True,
    })
    return MemoryContextAssembler().assemble(
        story,
        semantic,
        token_budget=request.token_budget,
    )
