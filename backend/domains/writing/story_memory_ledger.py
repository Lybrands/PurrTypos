"""Application-facing orchestration for versioned Story Memory."""

from __future__ import annotations

from typing import Sequence

from domains.writing.story_memory import (
    ChapterMemoryInvalidationReceipt,
    StoryMemoryApplyReceipt,
    StoryMemoryDelta,
    StoryMemoryDeltaDraft,
    StoryMemoryRecord,
    StoryMemoryVersion,
    validate_delta_draft,
)
from domains.writing.story_memory_repository import StoryMemoryRepository
from domains.writing.story_settings import StorySettingChange


class StoryMemoryLedger:
    """Keep workflow decisions in the application layer.

    The repository owns atomic SQLite transitions; this facade is the stable
    boundary used later by chapter analysis, review UI, and context assembly.
    """

    def __init__(self, repository: StoryMemoryRepository):
        self._repository = repository

    async def stage_delta(self, draft: StoryMemoryDeltaDraft) -> StoryMemoryDelta:
        validate_delta_draft(draft)
        return await self._repository.create_delta(draft)

    async def stage_settings(
        self,
        *,
        book_id: str,
        chapter_id: str,
        changes: Sequence[StorySettingChange],
        source_revision: str = "",
        note: str = "",
        source_type: str = "manual",
    ) -> StoryMemoryDelta:
        """Stage typed settings as a reviewable, chapter-scoped delta."""

        return await self.stage_delta(
            StoryMemoryDeltaDraft(
                book_id=book_id,
                chapter_id=chapter_id,
                changes=tuple(change.to_memory_change() for change in changes),
                source_revision=source_revision,
                note=note,
                source_type=source_type,
            )
        )

    async def get_delta(self, delta_id: str) -> StoryMemoryDelta | None:
        return await self._repository.get_delta(delta_id)

    async def approve_delta(self, delta_id: str) -> StoryMemoryApplyReceipt:
        return await self._repository.apply_delta(delta_id)

    async def revert_delta(self, delta_id: str) -> StoryMemoryApplyReceipt:
        return await self._repository.rollback_delta(delta_id)

    async def current_state(
        self,
        book_id: str,
        *,
        kinds: Sequence[str] = (),
    ) -> tuple[StoryMemoryRecord, ...]:
        return await self._repository.list_records(book_id, kinds=kinds)

    async def history(
        self,
        book_id: str,
        memory_key: str,
    ) -> tuple[StoryMemoryVersion, ...]:
        return await self._repository.list_versions(book_id, memory_key)

    async def chapter_changed(
        self,
        book_id: str,
        chapter_id: str,
        *,
        current_revision: str | None = None,
    ) -> ChapterMemoryInvalidationReceipt:
        return await self._repository.invalidate_chapter(
            book_id,
            chapter_id,
            current_revision=current_revision,
        )


__all__ = ["StoryMemoryLedger"]
