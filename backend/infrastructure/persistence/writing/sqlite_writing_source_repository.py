"""SQLite source data used by Writing spark and foreshadowing tools."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


logger = logging.getLogger(__name__)


class SqliteWritingSourceRepository:
    """Persist specialized book-setting sources without semantic-memory mirrors."""

    def __init__(self, db: DatabaseConnection):
        self._db = db

    async def get_spark_ideas_by_ids(
        self,
        book_id: str,
        ids: list[str],
    ) -> list[dict[str, Any]]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = await self._db.fetch_all(
            f"SELECT * FROM ai_memories WHERE book_id = ? "
            f"AND id IN ({placeholders})",
            [book_id, *ids],
        )
        return [_spark_idea_row(row) for row in rows]

    async def get_foreshadowing_by_ids(
        self,
        book_id: str,
        ids: list[str],
    ) -> list[dict[str, Any]]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = await self._db.fetch_all(
            f"SELECT * FROM ai_foreshadowing WHERE book_id = ? "
            f"AND id IN ({placeholders})",
            [book_id, *ids],
        )
        return [_foreshadowing_row(row) for row in rows]

    async def add_spark_idea(
        self,
        book_id: str,
        layer: str,
        content: str,
        chapter_id: str | None = None,
        character_id: str | int | None = None,
    ) -> dict[str, Any]:
        row_id = await self._db.execute_and_get_id(
            "INSERT INTO ai_memories "
            "(book_id, layer, content, chapter_id, character_id) "
            "VALUES (?, ?, ?, ?, ?)",
            [book_id, layer, content.strip(), chapter_id, character_id],
        )
        row = await self._db.fetch_one(
            "SELECT * FROM ai_memories WHERE id = ? AND book_id = ?",
            [row_id, book_id],
        )
        return _spark_idea_row(row)

    async def update_spark_idea(
        self,
        book_id: str,
        id_: str,
        data: dict[str, Any],
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_memories WHERE id = ? AND book_id = ?",
            [id_, book_id],
        )
        if not row:
            return None
        await self._db.execute(
            "UPDATE ai_memories SET content = ?, layer = ?, "
            "chapter_id = ?, character_id = ? WHERE id = ? AND book_id = ?",
            [
                data.get("content", row["content"]),
                data.get("layer", row["layer"]),
                data.get("chapter_id", row["chapter_id"]),
                data.get("character_id", row["character_id"]),
                id_,
                book_id,
            ],
        )
        updated = await self._db.fetch_one(
            "SELECT * FROM ai_memories WHERE id = ? AND book_id = ?",
            [id_, book_id],
        )
        return _spark_idea_row(updated)

    async def delete_spark_idea(
        self,
        book_id: str,
        id_: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_memories WHERE id = ? AND book_id = ?",
            [id_, book_id],
        )
        if not row:
            return None
        await self._db.execute(
            "DELETE FROM ai_memories WHERE id = ? AND book_id = ?",
            [id_, book_id],
        )
        return _spark_idea_row(row)

    async def list_spark_ideas(
        self,
        book_id: str,
        *,
        layer: str | None = None,
    ) -> list[dict[str, Any]]:
        if layer:
            rows = await self._db.fetch_all(
                "SELECT * FROM ai_memories WHERE book_id = ? AND layer = ? "
                "ORDER BY create_time ASC",
                [book_id, layer],
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM ai_memories WHERE book_id = ? "
                "ORDER BY create_time ASC",
                [book_id],
            )
        return [_spark_idea_row(row) for row in rows]

    async def add_foreshadowing(
        self,
        book_id: str,
        chapter_id: str | None,
        content: str,
        type_: str | None = None,
        expected_chapter_id: str | None = None,
        status: str = "未回收",
        resolved_chapter_id: str | None = None,
    ) -> dict[str, Any]:
        row_id = await self._db.execute_and_get_id(
            "INSERT INTO ai_foreshadowing "
            "(book_id, chapter_id, content, type, expected_chapter_id, "
            "status, resolved_chapter_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                book_id,
                chapter_id,
                content.strip(),
                type_ or "悬念",
                expected_chapter_id,
                status or "未回收",
                resolved_chapter_id,
            ],
        )
        row = await self._db.fetch_one(
            "SELECT * FROM ai_foreshadowing WHERE id = ? AND book_id = ?",
            [row_id, book_id],
        )
        return _foreshadowing_row(row)

    async def update_foreshadowing(
        self,
        book_id: str,
        id_: str,
        data: dict[str, Any],
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_foreshadowing WHERE id = ? AND book_id = ?",
            [id_, book_id],
        )
        if not row:
            return None
        await self._db.execute(
            "UPDATE ai_foreshadowing SET content=?, type=?, status=?, "
            "expected_chapter_id=?, resolved_chapter_id=?, "
            "update_time=datetime('now') WHERE id=? AND book_id=?",
            [
                data.get("content", row["content"]),
                data.get("type", row["type"]),
                data.get("status", row["status"]),
                data.get("expected_chapter_id", row["expected_chapter_id"]),
                data.get("resolved_chapter_id", row["resolved_chapter_id"]),
                id_,
                book_id,
            ],
        )
        updated = await self._db.fetch_one(
            "SELECT * FROM ai_foreshadowing WHERE id = ? AND book_id = ?",
            [id_, book_id],
        )
        return _foreshadowing_row(updated)

    async def delete_foreshadowing(
        self,
        book_id: str,
        id_: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_foreshadowing WHERE id = ? AND book_id = ?",
            [id_, book_id],
        )
        if not row:
            return None
        await self._db.execute(
            "DELETE FROM ai_foreshadowing WHERE id = ? AND book_id = ?",
            [id_, book_id],
        )
        return _foreshadowing_row(row)

    async def list_foreshadowing(
        self,
        book_id: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if status:
            rows = await self._db.fetch_all(
                "SELECT * FROM ai_foreshadowing WHERE book_id = ? "
                "AND status = ? ORDER BY create_time ASC",
                [book_id, status],
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM ai_foreshadowing WHERE book_id = ? "
                "ORDER BY create_time ASC",
                [book_id],
            )
        return [_foreshadowing_row(row) for row in rows]

    async def get_spark_ideas_for_prompt(
        self,
        book_id: str,
        query: str,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        opts = options or {}
        layers: list[str] | None = opts.get("layers")
        limit = int(opts.get("limit", 20))
        limit_per_layer = int(opts.get("limitPerLayer", 5))
        chapter_id: str | None = opts.get("chapterId")
        text = str(query or "").strip()

        if len(text) >= 2:
            try:
                return await self._search_spark_ideas_fts(
                    book_id, text, layers, chapter_id, limit
                )
            except Exception:
                logger.warning("本书设定 FTS5 检索失败", exc_info=True)
                raise

        rows = await self._db.fetch_all(
            "SELECT * FROM ai_memories WHERE book_id = ? ORDER BY create_time ASC",
            [book_id],
        )
        by_layer: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            row_layer = row["layer"]
            if layers and row_layer not in layers:
                continue
            if (
                chapter_id is not None
                and row.get("chapter_id") is not None
                and str(row["chapter_id"]) != str(chapter_id)
                and row_layer == "章节"
            ):
                continue
            bucket = by_layer.setdefault(row_layer, [])
            if len(bucket) < limit_per_layer:
                bucket.append(_spark_idea_row(row))

        result: list[dict[str, Any]] = []
        for layer in layers or list(by_layer):
            for item in by_layer.get(layer, []):
                result.append(item)
                if len(result) >= limit:
                    return result
        return result

    async def get_foreshadowing_for_prompt(
        self,
        book_id: str,
        query: str,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        del query
        opts = options or {}
        limit = int(opts.get("limit", 10))
        status = opts.get("status")
        if status:
            rows = await self._db.fetch_all(
                "SELECT * FROM ai_foreshadowing WHERE book_id = ? AND status = ? "
                "ORDER BY create_time ASC LIMIT ?",
                [book_id, status, limit],
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM ai_foreshadowing WHERE book_id = ? "
                "ORDER BY create_time ASC LIMIT ?",
                [book_id, limit],
            )
        return [_foreshadowing_row(row) for row in rows]

    async def _search_spark_ideas_fts(
        self,
        book_id: str,
        query: str,
        layers: list[str] | None,
        chapter_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        layer_filter = ""
        layer_params: list[Any] = []
        if layers:
            placeholders = ",".join("?" * len(layers))
            layer_filter = f"AND m.layer IN ({placeholders})"
            layer_params = list(layers)
        chapter_filter = ""
        chapter_params: list[Any] = []
        if chapter_id is not None:
            chapter_filter = (
                "AND (m.layer != '章节' OR m.chapter_id IS NULL OR m.chapter_id = ?)"
            )
            chapter_params = [chapter_id]
        rows = await self._db.fetch_all(
            f"""
                SELECT m.*
                FROM (
                    SELECT rowid, rank FROM ai_memories_fts
                    WHERE ai_memories_fts MATCH ? ORDER BY rank LIMIT {limit * 4}
                ) AS fts
                JOIN ai_memories m ON m.id = fts.rowid
                WHERE m.book_id = ? {layer_filter} {chapter_filter}
                ORDER BY fts.rank LIMIT ?
            """,
            [query, book_id, *layer_params, *chapter_params, limit],
        )
        return [_spark_idea_row(row) for row in rows]


def _spark_idea_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": row["id"],
        "book_id": row["book_id"],
        "layer": row["layer"],
        "content": row["content"],
        "chapter_id": row["chapter_id"],
        "character_id": row["character_id"],
        "create_time": row["create_time"],
    }


def _foreshadowing_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": row["id"],
        "book_id": row["book_id"],
        "chapter_id": row["chapter_id"],
        "content": row["content"],
        "type": row["type"],
        "status": row["status"],
        "expected_chapter_id": row["expected_chapter_id"],
        "resolved_chapter_id": row["resolved_chapter_id"],
        "create_time": row["create_time"],
        "update_time": row.get("update_time"),
    }


__all__ = ["SqliteWritingSourceRepository"]
