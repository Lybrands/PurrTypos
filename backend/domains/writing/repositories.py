"""Persistence ports and records used by writing context assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence


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
class MemoryItem:
    id: int
    book_id: str
    kind: str
    scope_type: str
    scope_id: str | None
    content: str
    summary: str
    importance: int
    status: str
    pinned: int


@dataclass(frozen=True, slots=True)
class MemoryLink:
    from_memory_id: int
    to_memory_id: int
    relation: str
    note: str = ""


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


class MemoryRecallRepository(Protocol):
    """Book-scoped memory retrieval needed by the writing domain."""

    async def get_by_ids(
        self,
        book_id: str,
        ids: Sequence[Any],
        *,
        statuses: Sequence[str],
    ) -> tuple[MemoryItem, ...]: ...

    async def get_by_source_ids(
        self,
        book_id: str,
        source_type: str,
        source_ids: Sequence[Any],
        *,
        statuses: Sequence[str],
    ) -> tuple[MemoryItem, ...]: ...

    async def search(
        self,
        book_id: str,
        query: str,
        *,
        limit: int,
        statuses: Sequence[str] = ("active",),
        kinds: Sequence[str] = (),
        scope_type: str | None = None,
        scope_id: str | None = None,
    ) -> tuple[MemoryItem, ...]: ...

    async def get_links(
        self,
        book_id: str,
        memory_ids: Sequence[int],
    ) -> tuple[MemoryLink, ...]: ...

    async def mark_used(
        self,
        book_id: str,
        memory_ids: Sequence[int],
    ) -> None: ...
