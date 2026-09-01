"""Persistence port for specialized Writing source tools."""

from __future__ import annotations

from typing import Any, Protocol


class WritingSourceRepository(Protocol):
    async def get_spark_ideas_by_ids(
        self, book_id: str, ids: list[str],
    ) -> list[dict[str, Any]]: ...

    async def get_foreshadowing_by_ids(
        self, book_id: str, ids: list[str],
    ) -> list[dict[str, Any]]: ...

    async def add_spark_idea(
        self, book_id: str, layer: str, content: str,
        chapter_id: str | None = None,
        character_id: str | int | None = None,
    ) -> dict[str, Any]: ...

    async def update_spark_idea(
        self, book_id: str, id_: str, data: dict[str, Any],
    ) -> dict[str, Any] | None: ...

    async def delete_spark_idea(
        self, book_id: str, id_: str,
    ) -> dict[str, Any] | None: ...

    async def add_foreshadowing(
        self, book_id: str, chapter_id: str | None, content: str,
        type_: str | None = None,
        expected_chapter_id: str | None = None,
        status: str = "未回收",
        resolved_chapter_id: str | None = None,
    ) -> dict[str, Any]: ...

    async def update_foreshadowing(
        self, book_id: str, id_: str, data: dict[str, Any],
    ) -> dict[str, Any] | None: ...

    async def get_spark_ideas_for_prompt(
        self, book_id: str, query: str,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    async def get_foreshadowing_for_prompt(
        self, book_id: str, query: str,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...


__all__ = ["WritingSourceRepository"]
