"""Persistence port for the versioned Story Memory ledger."""

from __future__ import annotations

from typing import Protocol, Sequence

from domains.writing.story_memory import (
    ChapterMemoryInvalidationReceipt,
    StoryMemoryApplyReceipt,
    StoryMemoryDelta,
    StoryMemoryDeltaDraft,
    StoryMemoryRecord,
    StoryMemoryVersion,
)


class StoryMemoryRepository(Protocol):
    async def create_delta(self, draft: StoryMemoryDeltaDraft) -> StoryMemoryDelta: ...

    async def get_delta(self, delta_id: str) -> StoryMemoryDelta | None: ...

    async def apply_delta(self, delta_id: str) -> StoryMemoryApplyReceipt: ...

    async def rollback_delta(self, delta_id: str) -> StoryMemoryApplyReceipt: ...

    async def get_record(
        self,
        book_id: str,
        memory_key: str,
        *,
        include_reverted: bool = False,
    ) -> StoryMemoryRecord | None: ...

    async def list_records(
        self,
        book_id: str,
        *,
        kinds: Sequence[str] = (),
        include_reverted: bool = False,
    ) -> tuple[StoryMemoryRecord, ...]: ...

    async def list_versions(
        self,
        book_id: str,
        memory_key: str,
    ) -> tuple[StoryMemoryVersion, ...]: ...

    async def invalidate_chapter(
        self,
        book_id: str,
        chapter_id: str,
        *,
        current_revision: str | None = None,
    ) -> ChapterMemoryInvalidationReceipt: ...


__all__ = ["StoryMemoryRepository"]
