"""SQLite adapter for book-scoped associated writing context."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

from domains.writing.repositories import AssociatedChapter, AssociatedOutline
from utils.text import extract_text_from_lexical

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


class SqliteAssociatedContextRepository:
    def __init__(self, db: DatabaseConnection):
        self._db = db

    async def load_chapters(
        self,
        book_id: str,
        chapter_ids: Sequence[str],
    ) -> tuple[AssociatedChapter, ...]:
        ids = _unique_ids(chapter_ids)
        if not ids:
            return ()
        placeholders = ",".join("?" for _ in ids)
        rows = await self._db.fetch_all(
            "SELECT oc.id, oc.title, COALESCE(a.content, '') AS raw_content "
            "FROM outline_chapters oc "
            "JOIN outlines o ON o.id = oc.outline_id "
            "LEFT JOIN articles a ON a.chapter_id = oc.id "
            f"WHERE o.book_id = ? AND oc.id IN ({placeholders})",
            [book_id, *ids],
        )
        by_id = {
            str(row["id"]): AssociatedChapter(
                id=str(row["id"]),
                title=str(row.get("title") or ""),
                text=_plain_text(row.get("raw_content")),
            )
            for row in rows
        }
        return tuple(by_id[item_id] for item_id in ids if item_id in by_id)

    async def load_outlines(
        self,
        book_id: str,
        outline_ids: Sequence[str],
    ) -> tuple[AssociatedOutline, ...]:
        ids = _unique_ids(outline_ids)
        if not ids:
            return ()
        placeholders = ",".join("?" for _ in ids)
        rows = await self._db.fetch_all(
            "SELECT id, title, type, markdown_content FROM outlines "
            f"WHERE id IN ({placeholders}) "
            "AND (book_id = ? OR "
            "(type = 'global' AND (book_id IS NULL OR book_id = ''))) "
            "AND type IN ('global', 'volume', 'chapter', 'writing')",
            [*ids, book_id],
        )
        by_id = {
            str(row["id"]): AssociatedOutline(
                id=str(row["id"]),
                title=str(row.get("title") or ""),
                kind=str(row.get("type") or ""),
                markdown=str(row.get("markdown_content") or ""),
            )
            for row in rows
        }
        return tuple(by_id[item_id] for item_id in ids if item_id in by_id)


def _unique_ids(values: Sequence[Any]) -> list[str]:
    return list(dict.fromkeys(
        str(value).strip() for value in values if str(value).strip()
    ))


def _plain_text(raw: Any) -> str:
    if not raw:
        return ""
    try:
        return extract_text_from_lexical(str(raw))
    except Exception:
        return ""
