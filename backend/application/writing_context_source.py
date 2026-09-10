"""Repository-backed source for WritingContextProvider."""

from __future__ import annotations

from purra.cancellation import raise_if_stopped
from purra.contracts import AgentRunRequest, TaskContextRequest
from purra.ports import CancellationSignal
from application.memory_operations import MemoryOperationError
from domains.writing.associated_context import (
    AssociatedContextBuilder,
    AssociatedContextResult,
    ChapterContextFact,
    OutlineContextFact,
)
from domains.writing.contracts import WritingDomainContext
from domains.writing.memory_context import (
    MemoryContextRequest,
)
from domains.writing.unified_memory_context import (
    MemoryContextPack,
    StoryMemoryContextProvider,
    UnifiedMemoryRetriever,
    memory_context_request_from_task,
    unavailable_memory_context_pack,
)
from domains.writing.repositories import (
    AssociatedContextRepository,
    StoryMemoryRecallRepository,
)
from domains.writing.memory_reranking import MemoryCandidateReranker


class RepositoryWritingContextSource:
    """Own writing retrieval orchestration without infrastructure globals."""

    def __init__(
        self,
        associated_repository: AssociatedContextRepository,
        memory_operations,
        source_repository,
        story_memory_repository: StoryMemoryRecallRepository,
        memory_reranker: MemoryCandidateReranker | None = None,
        run_id: str | None = None,
        knowledge=None,
        technique_access=None,
    ):
        self._knowledge = knowledge
        self._technique_access = technique_access
        self._associated_repository = associated_repository
        self._memory_operations = memory_operations
        self._source_repository = source_repository
        self._story_memory_repository = story_memory_repository
        self._associated = AssociatedContextBuilder(associated_repository)
        self._memory_reranker = memory_reranker
        self._run_id = str(run_id or "").strip()

    def with_memory_reranker(
        self,
        reranker: MemoryCandidateReranker,
        *,
        run_id: str,
    ) -> "RepositoryWritingContextSource":
        """Create a request-scoped source without mutating shared adapters."""

        return RepositoryWritingContextSource(
            self._associated_repository,
            self._memory_operations,
            self._source_repository,
            self._story_memory_repository,
            memory_reranker=reranker,
            run_id=run_id,
            knowledge=self._knowledge,
            technique_access=self._technique_access,
        )

    async def build_techniques(self, context, token_budget):
        from application.writing_technique_runs import WritingTechniqueRuns
        from domains.writing.techniques import TechniqueError
        from purra.context_budget import estimate_json_tokens
        from purra.evidence import ContextEvidenceReceipt
        import json

        snapshot = dict(context.writing_technique_snapshot or {})
        if not self._technique_access or not snapshot:
            return {"content": "", "tokens": 0, "receipts": []}
        access = self._technique_access
        runs = WritingTechniqueRuns(access.db)
        state = await runs.state(self._run_id) if self._run_id else {"automaticRefs": []}
        resolution = access.resolve(snapshot, state["automaticRefs"])
        await access.validate(snapshot, selected=resolution["selected"])
        if self._run_id:
            await runs.record_budget(self._run_id, token_budget)
        entries, receipts = [], []
        for member in resolution["members"]:
            ref = member["ref"]
            file = await access.library.read_version_file(ref, "SKILL.md")
            entries.append({"ref": ref, "path": "SKILL.md", "content": file["content"], "sources": member["sources"]})
            receipts.append(ContextEvidenceReceipt(evidence_id=f"technique:{ref['id']}:{ref['versionId']}:SKILL.md",
                context_block="writing_techniques",
                source="writing_technique/v1", item_id=ref["id"],
                metadata={"ref": ref, "path": "SKILL.md", "sha256": file["sha256"], "selections": member["sources"]}))
        if not entries:
            return {"content": "", "tokens": 0, "receipts": []}
        content = json.dumps({"entries": entries, "schemes": [{"ref": c["ref"], "composition": c["composition"]}
            for c in resolution["selected"] if c["ref"]["kind"] == "scheme"]}, ensure_ascii=False)
        tokens = estimate_json_tokens(content)
        if tokens > token_budget:
            raise TechniqueError("file_exceeds_budget", "所选写作技法入口无法完整放入当前上下文，请减少选择")
        if self._run_id:
            await runs.mark_entries(self._run_id, [m["ref"] for m in resolution["members"]])
        return {"content": content, "tokens": tokens, "receipts": receipts}

    async def build_memory(
        self,
        context: WritingDomainContext,
        request: AgentRunRequest,
        token_budget: int,
        *,
        query: str,
        task: TaskContextRequest | None = None,
        signal: CancellationSignal | None = None,
    ) -> MemoryContextPack:
        """Recall with the explicit host query and optional task evidence policy."""

        raise_if_stopped(signal)
        if context.knowledge_scope and context.knowledge_scope.get('purpose') != 'discussion':
            return unavailable_memory_context_pack(MemoryContextRequest(
                book_id=context.book_id, user_prompt=query, token_budget=token_budget,
            ))
        recall_limit, candidate_limit = _memory_recall_limits(request.context_window)
        memory_request = memory_context_request_from_task(
            MemoryContextRequest(
                book_id=context.book_id,
                user_prompt=str(query or "").strip(),
                token_budget=token_budget,
                recall_limit=recall_limit,
                candidate_limit=candidate_limit,
                selected_spark_idea_ids=context.selected_memory_ids,
                selected_foreshadowing_ids=context.selected_foreshadowing_ids,
                selected_memory_item_ids=(
                    context.selected_long_term_memory_ids
                ),
            ),
            task,
            current_chapter_id=context.chapter_id,
        )
        try:
            from application.component_memory_context import (
                ComponentWritingMemoryContextBuilder,
            )

            retriever = UnifiedMemoryRetriever(
                ComponentWritingMemoryContextBuilder(
                    self._memory_operations,
                    self._source_repository,
                    run_id=self._run_id,
                    reranker=self._memory_reranker,
                ),
                StoryMemoryContextProvider(
                    self._story_memory_repository,
                    self._memory_reranker,
                ),
            )
            result = await retriever.build(
                memory_request,
                model_request=request.model if task is not None else None,
                signal=signal,
            )
            raise_if_stopped(signal)
            return result
        except MemoryOperationError:
            # Optional retrieval can be unavailable, but cancellation must escape.
            raise_if_stopped(signal)
            return unavailable_memory_context_pack(memory_request)

    async def build_knowledge(self, context, query, token_budget, *, signal=None):
        if not self._knowledge or not context.knowledge_scope:
            return {'items': [], 'receipts': [], 'tokens': 0}
        from domains.writing.knowledge import KnowledgeError
        try:
            return await self._knowledge.search(
                context.book_id, query or '资料', scope=dict(context.knowledge_scope),
                token_budget=token_budget, signal=signal,
            )
        except KnowledgeError as error:
            raise_if_stopped(signal)
            return {'items': [], 'receipts': [], 'tokens': 0, 'state': error.code}

    async def build_associated(
        self,
        context: WritingDomainContext,
        request: AgentRunRequest,
        token_budget: int,
    ) -> AssociatedContextResult:
        del request
        if context.knowledge_scope and context.knowledge_scope.get('purpose') != 'discussion':
            # An explicit associated selection does not establish a historical/POV snapshot.
            return AssociatedContextResult()
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


def _memory_recall_limits(context_window: int | None) -> tuple[int, int]:
    window = int(context_window or 200_000)
    if window >= 1_000_000:
        return 32, 32
    if window >= 300_000:
        return 32, 32
    return 16, 32
