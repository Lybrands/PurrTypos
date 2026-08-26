"""Writing-product capabilities installed at the application boundary."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from purra.contracts import AgentRunRequest
from application.memory_reranking import ModelBackedMemoryReranker
from application.writing_method_service import WritingMethodService
from application.continuation_context import ContinuationContextService
from domains.writing.adapter import WritingDomainAdapter
from domains.writing.context import WritingContextProvider, writing_context_claims
from domains.writing.context_source import RepositoryWritingContextSource
from domains.writing.contracts import (
    WRITING_DOMAIN_NAMESPACE,
    WritingDomainContext,
)
from domains.writing.response import writing_atomic_continuity_judge_policy
from domains.writing.method_resolution import (
    build_writing_method_binding_snapshot,
    public_binding_snapshot,
)
from infrastructure.persistence.writing import (
    SqliteAssociatedContextRepository,
    SqliteMemoryRecallRepository,
    SqliteStoryMemoryRecallRepository,
    SqliteWritingCatalogRepository,
    SqliteWritingToolMemoryRepository,
)
from infrastructure.writing import (
    WritingSkillCatalog,
    WritingToolDependencies,
    build_writing_tool_catalog,
)


class WritingAgentProfile:
    id = "writing"
    domain_namespace = WRITING_DOMAIN_NAMESPACE

    def __init__(self, db, *, skills_dir: Path) -> None:
        self._catalog_repository = SqliteWritingCatalogRepository(db)
        self._context_source = RepositoryWritingContextSource(
            SqliteAssociatedContextRepository(db),
            SqliteMemoryRecallRepository(db),
            SqliteStoryMemoryRecallRepository(db),
        )
        self._writing_methods = WritingMethodService(db)
        self._continuations = ContinuationContextService(db)
        skill_catalog = WritingSkillCatalog(skills_dir)
        self._adapter = WritingDomainAdapter.build(
            tool_catalog=build_writing_tool_catalog(
                dependencies=WritingToolDependencies(
                    db,
                    SqliteWritingToolMemoryRepository(db),
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
        overrides = dict(context.writing_method_overrides or {})
        force_revision_ids = tuple(overrides.get("forceRevisionIds") or ())
        exclude_revision_ids = tuple(overrides.get("excludeRevisionIds") or ())
        method_snapshot = (
            await self._writing_methods.resolve_book_binding_snapshot(
                book_id,
                force_revision_ids=force_revision_ids,
                exclude_revision_ids=exclude_revision_ids,
            )
            if book_id
            else build_writing_method_binding_snapshot(
                "",
                (),
                force_revision_ids=force_revision_ids,
                exclude_revision_ids=exclude_revision_ids,
            )
        )
        continuation = (
            await self._continuations.load_for_writing(book_id)
            if book_id
            else {"creationMode": "original", "binding": None, "canonRecords": []}
        )
        hydrated = replace(
            context,
            writing_chapters=(
                await self._catalog_repository.load_writing_chapters(book_id)
                if book_id else ()
            ),
            available_outlines=(
                await self._catalog_repository.load_available_outlines(book_id)
                if book_id else ()
            ),
            writing_method_binding_snapshot=method_snapshot,
            creation_mode=str(continuation["creationMode"]),
            continuation_binding=continuation.get("binding"),
            inherited_canon_records=tuple(continuation.get("canonRecords") or ()),
        )
        return replace(request, domain_context=hydrated.to_core_context())

    def run_binding_attributes(self, request: AgentRunRequest):
        context = WritingDomainContext.from_core_context(request.domain_context)
        snapshot = public_binding_snapshot(
            context.writing_method_binding_snapshot or {}
        )
        attributes = {
            **({"writingMethodBindingSnapshot": snapshot} if snapshot else {}),
        }
        if context.creation_mode == "continuation" and context.continuation_binding:
            attributes["creationMode"] = "continuation"
            attributes["continuationBinding"] = dict(context.continuation_binding)
        return attributes

    def context_budget_claims(self, request: AgentRunRequest):
        return writing_context_claims(request)

    def context_provider_factory(self):
        return lambda model_tasks: WritingContextProvider(
            self._context_source.with_memory_reranker(
                ModelBackedMemoryReranker(model_tasks)
            )
        )

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
    **_dependencies,
) -> WritingAgentProfile:
    return WritingAgentProfile(
        db,
        skills_dir=(
            skills_dir
            or Path(__file__).resolve().parent.parent / "skills"
        ),
    )


__all__ = [
    "WritingAgentProfile",
    "build_writing_agent_profile",
]
