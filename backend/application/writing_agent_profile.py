"""Writing-product capabilities installed at the application boundary."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from purra.contracts import AgentRunRequest
from application.agent_profile_registry import AgentProfileRegistration
from application.memory_reranking import ModelBackedMemoryReranker
from domains.writing.adapter import WritingDomainAdapter
from domains.writing.context import WritingContextProvider
from domains.writing.context_source import RepositoryWritingContextSource
from domains.writing.contracts import (
    WRITING_DOMAIN_NAMESPACE,
    WritingDomainContext,
)
from domains.writing.response import writing_atomic_continuity_judge_policy
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


class WritingAgentProfileExtension:
    def __init__(self, db, *, skills_dir: Path) -> None:
        self._catalog_repository = SqliteWritingCatalogRepository(db)
        self._context_source = RepositoryWritingContextSource(
            SqliteAssociatedContextRepository(db),
            SqliteMemoryRecallRepository(db),
            SqliteStoryMemoryRecallRepository(db),
        )
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

    def profile_registration(self) -> AgentProfileRegistration:
        return AgentProfileRegistration(
            id="writing",
            domain_namespace=WRITING_DOMAIN_NAMESPACE,
            adapter=self._adapter,
        )

    async def prepare_request(
        self,
        request: AgentRunRequest,
    ) -> AgentRunRequest:
        context = WritingDomainContext.from_core_context(
            request.domain_context
        )
        book_id = str(context.book_id or "").strip()
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
        )
        return replace(request, domain_context=hydrated.to_core_context())

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
        work_item_repository=None,
        long_task_repository=None,
        executor=None,
    ):
        del work_item_repository, long_task_repository, executor
        return None

    def clear_active_executions(self) -> None:
        return None


def build_writing_profile_extension(
    *,
    db,
    skills_dir: Path | None = None,
    **_dependencies,
) -> WritingAgentProfileExtension:
    return WritingAgentProfileExtension(
        db,
        skills_dir=(
            skills_dir
            or Path(__file__).resolve().parent.parent / "skills"
        ),
    )


__all__ = [
    "WritingAgentProfileExtension",
    "build_writing_profile_extension",
]
