"""Writing-product capabilities installed at the application boundary."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from purra.contracts import AgentRunRequest
from application.memory_reranking import ModelBackedMemoryReranker
from application.memory_operations import MemoryApplicationService
from application.memory_evidence import BookMemoryEvidenceValidator
from application.writing_technique_access import WritingTechniqueAccess, TechniqueEvidenceValidator
from application.continuation_context import ContinuationContextService
from domains.writing.adapter import WritingDomainAdapter
from domains.writing.context import WritingContextProvider, writing_context_claims
from application.writing_context_source import RepositoryWritingContextSource
from domains.writing.contracts import (
    WRITING_DOMAIN_NAMESPACE,
    WritingDomainContext,
)
from domains.writing.response import writing_atomic_continuity_judge_policy
from infrastructure.persistence.writing import (
    SqliteAssociatedContextRepository,
    SqliteStoryMemoryRecallRepository,
    SqliteWritingCatalogRepository,
    SqliteWritingSourceRepository,
)
from infrastructure.writing import (
    WritingSkillCatalog,
    WritingToolDependencies,
    build_writing_tool_catalog,
)


class WritingAgentProfile:
    id = "writing"
    domain_namespace = WRITING_DOMAIN_NAMESPACE

    def __init__(self, db, *, skills_dir: Path, memory_resource=None) -> None:
        from application.novel_knowledge_service import get_novel_knowledge_service
        self._knowledge = get_novel_knowledge_service(db)
        self._catalog_repository = SqliteWritingCatalogRepository(db)
        memory_operations = MemoryApplicationService(db, memory_resource)
        self._memory_operations = memory_operations
        source_repository = SqliteWritingSourceRepository(db)
        self._technique_access = WritingTechniqueAccess(db)
        self._context_source = RepositoryWritingContextSource(
            SqliteAssociatedContextRepository(db),
            memory_operations,
            source_repository,
            SqliteStoryMemoryRecallRepository(db),
            knowledge=self._knowledge,
            technique_access=self._technique_access,
        )
        self._continuations = ContinuationContextService(db)
        skill_catalog = WritingSkillCatalog(skills_dir)
        self._adapter = WritingDomainAdapter.build(
            tool_catalog=build_writing_tool_catalog(
                dependencies=WritingToolDependencies(
                    db,
                    source_repository,
                    memory_operations,
                    knowledge=self._knowledge,
                ),
                skill_items=tuple(skill_catalog.skill_items()),
            ),
            context_provider=WritingContextProvider(self._context_source),
        )

    @property
    def adapter(self) -> WritingDomainAdapter:
        return self._adapter

    async def prepare_request(
        self,
        request: AgentRunRequest,
    ) -> AgentRunRequest:
        context = WritingDomainContext.from_core_context(
            request.domain_context
        )
        book_id = str(context.book_id or "").strip()
        technique_snapshot = (await self._technique_access.load_input(
            context.writing_technique_input_id, book_id, str(request.session_id)
        ) if context.writing_technique_input_id else
            await self._technique_access.freeze(book_id=book_id,
                manual=await self._technique_access.library.get_selection("session", str(request.session_id)) if request.session_id else await self._technique_access.library.get_selection("book", book_id)))
        continuation = (
            await self._continuations.load_for_writing(book_id)
            if book_id
            else {"creationMode": "original", "binding": None, "canonRecords": []}
        )
        hydrated = replace(
            context,
            knowledge_scope=(await self._knowledge.scope_snapshot(book_id, context.chapter_id) if book_id else {}),
            writing_chapters=(
                await self._catalog_repository.load_writing_chapters(book_id)
                if book_id else ()
            ),
            available_outlines=(
                await self._catalog_repository.load_available_outlines(book_id)
                if book_id else ()
            ),
            writing_technique_snapshot=technique_snapshot,
            creation_mode=str(continuation["creationMode"]),
            continuation_binding=continuation.get("binding"),
            inherited_canon_records=tuple(record for record in continuation.get("canonRecords") or ()
                if (continuation.get("binding") or {}).get("materialAuthority") != "shared_markdown"
                or record.get("factKind") in {"event", "timeline", "unresolved_plot", "foreshadowing"}),
        )
        return replace(request, domain_context=hydrated.to_core_context())

    def run_binding_attributes(self, request: AgentRunRequest):
        context = WritingDomainContext.from_core_context(request.domain_context)
        attributes = {
            **({"writingTechniqueSnapshot": dict(context.writing_technique_snapshot)} if context.writing_technique_snapshot else {}),
            **({"bookId": context.book_id} if context.book_id else {}),
        }
        if context.knowledge_scope:
            attributes["novelKnowledgeScope"] = dict(context.knowledge_scope)
        if context.creation_mode == "continuation" and context.continuation_binding:
            attributes["creationMode"] = "continuation"
            attributes["continuationBinding"] = dict(context.continuation_binding)
        return attributes

    def context_budget_claims(self, request: AgentRunRequest):
        return writing_context_claims(request)

    def context_provider_factory(self):
        return lambda model_tasks: WritingContextProvider(
            self._context_source.with_memory_reranker(
                ModelBackedMemoryReranker(model_tasks), run_id=model_tasks.run_id,
            ),
        )

    def model_input_evidence_validator(self, request: AgentRunRequest):
        context = WritingDomainContext.from_core_context(request.domain_context)
        book_id = str(context.book_id or "").strip()
        from application.novel_knowledge_evidence import NovelKnowledgeEvidenceValidator
        delegate = (NovelKnowledgeEvidenceValidator(
            self._knowledge, BookMemoryEvidenceValidator(self._memory_operations, book_id), book_id,
            scope=context.knowledge_scope,
        ) if book_id else None)
        return TechniqueEvidenceValidator(self._technique_access, dict(context.writing_technique_snapshot or {}), delegate)

    def response_judge_policies(
        self,
        request: AgentRunRequest,
    ):
        policy = writing_atomic_continuity_judge_policy(request)
        return () if policy is None else (policy,)

    def task_admission(self):
        return None

    def create_long_task_dispatcher(
        self,
        *,
        long_task_repository=None,
        executor=None,
    ):
        del long_task_repository, executor
        return None

    def clear_active_executions(self) -> None:
        return None


def build_writing_agent_profile(
    *,
    db,
    skills_dir: Path | None = None,
    memory_resource=None,
    **_dependencies,
) -> WritingAgentProfile:
    return WritingAgentProfile(
        db,
        skills_dir=(
            skills_dir
            or Path(__file__).resolve().parent.parent / "skills"
        ),
        memory_resource=memory_resource,
    )


__all__ = [
    "WritingAgentProfile",
    "build_writing_agent_profile",
]
