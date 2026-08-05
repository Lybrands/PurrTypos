"""Authoritative, book-scoped catalogs used by Writing Agent runs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from database.crud.outlines import get_associable_outlines

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


class SqliteWritingCatalogRepository:
    """Load slim planning catalogs without trusting renderer snapshots."""

    def __init__(self, db: DatabaseConnection):
        self._db = db

    async def load_writing_chapters(
        self,
        book_id: str,
    ) -> tuple[dict[str, Any], ...]:
        rows = await self._db.fetch_all(
            "SELECT c.id, c.title, c.parent_id, c.level, c.sort "
            "FROM outline_chapters AS c "
            "JOIN outlines AS o ON o.id = c.outline_id "
            "WHERE o.book_id = ? AND o.type = 'writing' "
            "ORDER BY c.sort ASC, c.id ASC",
            [book_id],
        )
        return tuple({
            "id": str(row["id"]),
            "title": str(row.get("title") or ""),
            "parent_id": _optional_text(row.get("parent_id")),
            "level": int(row.get("level") or 1),
            "sort": int(row.get("sort") or 0),
        } for row in rows)

    async def load_available_outlines(
        self,
        book_id: str,
    ) -> tuple[dict[str, Any], ...]:
        rows = await get_associable_outlines(self._db, book_id)
        return tuple({
            "id": str(row["id"]),
            "title": str(row.get("title") or ""),
            "type": str(row.get("type") or ""),
            "writing_chapter_id": _optional_text(
                row.get("writing_chapter_id")
            ),
            "parent_outline_id": _optional_text(
                row.get("parent_outline_id")
            ),
        } for row in rows)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
