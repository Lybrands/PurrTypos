"""Persistence ports and records used by writing context assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class AssociatedChapter:
    id: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class AssociatedOutline:
    id: str
    title: str
    kind: str
    markdown: str


@dataclass(frozen=True, slots=True)
class StoryMemoryRecallItem:
    """One authoritative current-story fact with valid source evidence."""

    record_id: str
    book_id: str
    memory_key: str
    kind: str
    subject_id: str | None
    payload: Mapping[str, Any]
    version: int
    source_id: str
    chapter_id: str
    source_excerpt: str
    update_time: str | None = None
    candidate_channels: tuple[str, ...] = ()
    retrieval_score: float = 0.0


class AssociatedContextRepository(Protocol):
    """Read associated prose while enforcing a book boundary."""

    async def load_chapters(
        self,
        book_id: str,
        chapter_ids: Sequence[str],
    ) -> tuple[AssociatedChapter, ...]: ...

    async def load_outlines(
        self,
        book_id: str,
        outline_ids: Sequence[str],
    ) -> tuple[AssociatedOutline, ...]: ...


class StoryMemoryRecallRepository(Protocol):
    """Bounded retrieval over authoritative current Story Memory state."""

    async def search_current(
        self,
        book_id: str,
        query: str,
        *,
        kinds: Sequence[str] = (),
        planner_kinds: Sequence[str] = (),
        entity_refs: Sequence[str] = (),
        chapter_ids: Sequence[str] = (),
        limit: int = 24,
    ) -> tuple[StoryMemoryRecallItem, ...]: ...

    async def get_current_by_ids(
        self,
        book_id: str,
        record_ids: Sequence[str],
    ) -> tuple[StoryMemoryRecallItem, ...]: ...
