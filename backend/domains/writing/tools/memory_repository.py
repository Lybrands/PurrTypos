"""Persistence port used by the instance-bound Writing memory tools."""

from __future__ import annotations

from typing import Any, Protocol


class WritingToolMemoryRepository(Protocol):
    """Book-scoped memory operations exposed to concrete tool handlers.

    Mutation methods combine the ownership check and write in one repository
    call.  Handlers therefore never need an unscoped ``get-by-id`` preflight.
    """

    async def add_spark_idea(
        self,
        book_id: str,
        layer: str,
        content: str,
        chapter_id: str | None = None,
        character_id: str | int | None = None,
    ) -> dict[str, Any]: ...

    async def update_spark_idea(
        self,
        book_id: str,
        id_: str,
        data: dict[str, Any],
    ) -> dict[str, Any] | None: ...

    async def delete_spark_idea(
        self,
        book_id: str,
        id_: str,
    ) -> dict[str, Any] | None: ...

    async def add_foreshadowing(
        self,
        book_id: str,
        chapter_id: str | None,
        content: str,
        type_: str | None = None,
        expected_chapter_id: str | None = None,
        status: str = "未回收",
        resolved_chapter_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def get_spark_ideas_for_prompt(
        self,
        book_id: str,
        query: str,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    async def get_foreshadowing_for_prompt(
        self,
        book_id: str,
        query: str,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    async def search_memory_items(
        self,
        book_id: str,
        query: str = "",
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    async def create_memory_item(
        self,
        *,
        book_id: str,
        kind: str,
        content: str,
        scope_type: str = "book",
        scope_id: str | None = None,
        summary: str = "",
        keywords: str = "",
        importance: int = 3,
        confidence: float = 1.0,
        status: str = "active",
        pinned: int | bool = 0,
        source_type: str = "manual",
        source_id: str | int | None = None,
        fingerprint: str | None = None,
    ) -> dict[str, Any]: ...

    async def update_memory_item(
        self,
        book_id: str,
        id_: str | int,
        data: dict[str, Any],
    ) -> dict[str, Any] | None: ...

    async def archive_memory_item(
        self,
        book_id: str,
        id_: str | int,
    ) -> dict[str, Any] | None: ...

    async def link_memory_items(
        self,
        *,
        book_id: str,
        from_memory_id: int | str,
        to_memory_id: int | str,
        relation: str,
        note: str = "",
    ) -> dict[str, Any]: ...

    async def update_foreshadowing(
        self,
        book_id: str,
        id_: str,
        data: dict[str, Any],
    ) -> dict[str, Any] | None: ...


__all__ = ["WritingToolMemoryRepository"]
