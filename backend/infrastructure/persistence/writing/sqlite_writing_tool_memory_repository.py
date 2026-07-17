"""SQLite implementation of the Writing tool memory persistence port."""

from __future__ import annotations

import logging
import re
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


logger = logging.getLogger(__name__)

DEFAULT_STATUSES = ("active",)
VALID_RELATIONS = {"supersedes", "contradicts", "supports", "relates_to"}


def normalize_memory_text(text: str) -> str:
    """Normalize text for deterministic local duplicate detection."""

    raw = str(text or "").strip().lower()
    raw = re.sub(r"[\s\r\n\t]+", "", raw)
    return re.sub(r"[，。！？、,.!?;；:：\"'“”‘’（）()\[\]【】《》<>]", "", raw)


def build_fingerprint(
    book_id: str,
    kind: str,
    scope_type: str,
    scope_id: str | None,
    content: str,
) -> str:
    return "|".join([
        str(book_id or "").strip(),
        str(kind or "").strip(),
        str(scope_type or "book").strip(),
        str(scope_id or "").strip(),
        normalize_memory_text(content)[:240],
    ])


class SqliteWritingToolMemoryRepository:
    """Own all SQLite state needed by the eleven Writing memory tools.

    Every mutation is scoped by ``book_id`` at the SQL boundary.  Legacy
    spark/foreshadowing rows remain the specialized source of truth and are
    mirrored to ``memory_items`` on a best-effort basis, matching the existing
    service behavior.
    """

    def __init__(self, db: DatabaseConnection):
        self._db = db

    async def add_spark_idea(
        self,
        book_id: str,
        layer: str,
        content: str,
        chapter_id: str | None = None,
        character_id: str | int | None = None,
    ) -> dict[str, Any]:
        async with self._db.transaction():
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
        result = _spark_idea_row(row)
        try:
            await self._mirror_spark_idea(result)
        except Exception:
            logger.warning("镜像本书设定到长期记忆失败", exc_info=True)
        return result

    async def update_spark_idea(
        self,
        book_id: str,
        id_: str,
        data: dict[str, Any],
    ) -> dict[str, Any] | None:
        async with self._db.transaction():
            row = await self._db.fetch_one(
                "SELECT * FROM ai_memories WHERE id = ? AND book_id = ?",
                [id_, book_id],
            )
            if not row:
                return None
            content = data.get("content", row["content"])
            layer = data.get("layer", row["layer"])
            chapter_id = data.get("chapter_id", row["chapter_id"])
            character_id = data.get("character_id", row["character_id"])
            await self._db.execute(
                "UPDATE ai_memories SET content = ?, layer = ?, "
                "chapter_id = ?, character_id = ? "
                "WHERE id = ? AND book_id = ?",
                [content, layer, chapter_id, character_id, id_, book_id],
            )
            updated = await self._db.fetch_one(
                "SELECT * FROM ai_memories WHERE id = ? AND book_id = ?",
                [id_, book_id],
            )
        result = _spark_idea_row(updated)
        try:
            await self._update_spark_idea_mirror(result)
        except Exception:
            logger.warning("同步本书设定长期记忆失败", exc_info=True)
        return result

    async def delete_spark_idea(
        self,
        book_id: str,
        id_: str,
    ) -> dict[str, Any] | None:
        async with self._db.transaction():
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
        result = _spark_idea_row(row)
        try:
            await self._archive_by_source(book_id, "spark_idea", id_)
        except Exception:
            logger.warning("归档本书设定长期记忆失败", exc_info=True)
        return result

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
        f_type = type_ or "悬念"
        f_status = status or "未回收"
        async with self._db.transaction():
            row_id = await self._db.execute_and_get_id(
                "INSERT INTO ai_foreshadowing "
                "(book_id, chapter_id, content, type, expected_chapter_id, "
                "status, resolved_chapter_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    book_id,
                    chapter_id,
                    content.strip(),
                    f_type,
                    expected_chapter_id,
                    f_status,
                    resolved_chapter_id,
                ],
            )
            row = await self._db.fetch_one(
                "SELECT * FROM ai_foreshadowing WHERE id = ? AND book_id = ?",
                [row_id, book_id],
            )
        result = _foreshadowing_row(row)
        try:
            await self._mirror_foreshadowing(result)
        except Exception:
            logger.warning("镜像伏笔到长期记忆失败", exc_info=True)
        return result

    async def get_spark_ideas_for_prompt(
        self,
        book_id: str,
        query: str,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        opts = options or {}
        layers: list[str] | None = opts.get("layers")
        limit: int = opts.get("limit", 20)
        limit_per_layer: int = opts.get("limitPerLayer", 5)
        chapter_id: str | None = opts.get("chapterId")
        text = (query or "").strip()

        if len(text) >= 2:
            try:
                return await self._search_spark_ideas_fts(
                    book_id,
                    text,
                    layers,
                    chapter_id,
                    limit,
                )
            except Exception:
                logger.warning("FTS5 检索失败，退回全量过滤", exc_info=True)

        rows = await self._db.fetch_all(
            "SELECT * FROM ai_memories WHERE book_id = ? "
            "ORDER BY create_time ASC",
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
        order = layers or list(by_layer.keys())
        for layer in order:
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
        # The compatibility search never filtered foreshadowing by query.
        del query
        opts = options or {}
        limit: int = opts.get("limit", 10)
        status_filter: str | None = opts.get("status")
        if status_filter:
            rows = await self._db.fetch_all(
                "SELECT * FROM ai_foreshadowing "
                "WHERE book_id = ? AND status = ? "
                "ORDER BY create_time ASC LIMIT ?",
                [book_id, status_filter, limit],
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM ai_foreshadowing WHERE book_id = ? "
                "ORDER BY create_time ASC LIMIT ?",
                [book_id, limit],
            )
        return [_foreshadowing_row(row) for row in rows]

    async def search_memory_items(
        self,
        book_id: str,
        query: str = "",
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        opts = options or {}
        limit = int(opts.get("limit") or 20)
        statuses = opts.get("statuses") or DEFAULT_STATUSES
        kinds = opts.get("kinds")
        scope_type = opts.get("scopeType") or opts.get("scope_type")
        scope_id = opts.get("scopeId") or opts.get("scope_id")
        text = str(query or "").strip()

        params: list[Any] = [book_id]
        where = ["m.book_id = ?"]
        if statuses:
            placeholders = ",".join("?" * len(statuses))
            where.append(f"m.status IN ({placeholders})")
            params.extend(statuses)
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            where.append(f"m.kind IN ({placeholders})")
            params.extend(kinds)
        if scope_type:
            where.append("m.scope_type = ?")
            params.append(scope_type)
        if scope_id is not None:
            where.append("m.scope_id = ?")
            params.append(str(scope_id))

        if len(text) >= 2:
            try:
                rows = await self._db.fetch_all(
                    "SELECT m.*, memory_items_fts.rank AS fts_rank "
                    "FROM memory_items_fts "
                    "JOIN memory_items m ON m.id = memory_items_fts.rowid "
                    f"WHERE memory_items_fts MATCH ? AND {' AND '.join(where)} "
                    "ORDER BY m.pinned DESC, m.importance DESC, "
                    "memory_items_fts.rank, m.id ASC LIMIT ?",
                    [text, *params, limit],
                )
                if rows:
                    return [_memory_item_row(row) for row in rows]
            except Exception:
                pass

        if text:
            term_clauses: list[str] = []
            for term in _query_like_terms(text):
                term_clauses.append(
                    "(m.content LIKE ? OR m.summary LIKE ? OR m.keywords LIKE ?)"
                )
                like = f"%{term}%"
                params.extend([like, like, like])
            if term_clauses:
                where.append("(" + " OR ".join(term_clauses) + ")")

        rows = await self._db.fetch_all(
            f"SELECT m.* FROM memory_items m WHERE {' AND '.join(where)} "
            "ORDER BY m.pinned DESC, m.importance DESC, "
            "COALESCE(m.last_used_at, m.update_time, m.create_time) DESC, "
            "m.id ASC LIMIT ?",
            [*params, limit],
        )
        return [_memory_item_row(row) for row in rows]

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
    ) -> dict[str, Any]:
        clean_content = str(content or "").strip()
        if not str(book_id or "").strip():
            raise ValueError("book_id is required")
        if not str(kind or "").strip():
            raise ValueError("kind is required")
        if not clean_content:
            raise ValueError("content is required")

        resolved_fingerprint = fingerprint or build_fingerprint(
            book_id,
            kind,
            scope_type,
            scope_id,
            clean_content,
        )
        async with self._db.transaction():
            existing = await self._db.fetch_one(
                "SELECT * FROM memory_items "
                "WHERE book_id = ? AND fingerprint = ?",
                [book_id, resolved_fingerprint],
            )
            if existing:
                await self._db.execute(
                    "UPDATE memory_items SET update_time = datetime('now') "
                    "WHERE id = ? AND book_id = ?",
                    [existing["id"], book_id],
                )
                row = await self._db.fetch_one(
                    "SELECT * FROM memory_items WHERE id = ? AND book_id = ?",
                    [existing["id"], book_id],
                )
                result = _memory_item_row(row)
                result["deduped"] = True
                return result

            row_id = await self._db.execute_and_get_id(
                "INSERT INTO memory_items "
                "(book_id, kind, scope_type, scope_id, content, summary, "
                "keywords, importance, confidence, status, pinned, fingerprint, "
                "source_type, source_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    book_id,
                    kind,
                    scope_type or "book",
                    str(scope_id) if scope_id is not None else None,
                    clean_content,
                    str(summary or "").strip(),
                    str(keywords or "").strip(),
                    int(importance),
                    float(confidence),
                    status or "active",
                    1 if pinned else 0,
                    resolved_fingerprint,
                    source_type or "manual",
                    str(source_id) if source_id is not None else None,
                ],
            )
            row = await self._db.fetch_one(
                "SELECT * FROM memory_items WHERE id = ? AND book_id = ?",
                [row_id, book_id],
            )
        result = _memory_item_row(row)
        result["deduped"] = False
        return result

    async def update_memory_item(
        self,
        book_id: str,
        id_: str | int,
        data: dict[str, Any],
    ) -> dict[str, Any] | None:
        async with self._db.transaction():
            row = await self._db.fetch_one(
                "SELECT * FROM memory_items WHERE id = ? AND book_id = ?",
                [id_, book_id],
            )
            if not row:
                return None
            fields = {
                "kind": data.get("kind", row["kind"]),
                "scope_type": data.get("scope_type", row["scope_type"]),
                "scope_id": data.get("scope_id", row["scope_id"]),
                "content": data.get("content", row["content"]),
                "summary": data.get("summary", row["summary"]),
                "keywords": data.get("keywords", row["keywords"]),
                "importance": data.get("importance", row["importance"]),
                "confidence": data.get("confidence", row["confidence"]),
                "status": data.get("status", row["status"]),
                "pinned": data.get("pinned", row["pinned"]),
            }
            fields["fingerprint"] = build_fingerprint(
                book_id,
                fields["kind"],
                fields["scope_type"],
                fields["scope_id"],
                fields["content"],
            )
            await self._db.execute(
                "UPDATE memory_items SET kind=?, scope_type=?, scope_id=?, "
                "content=?, summary=?, keywords=?, importance=?, confidence=?, "
                "status=?, pinned=?, fingerprint=?, update_time=datetime('now') "
                "WHERE id=? AND book_id=?",
                [
                    fields["kind"],
                    fields["scope_type"],
                    fields["scope_id"],
                    str(fields["content"] or "").strip(),
                    str(fields["summary"] or "").strip(),
                    str(fields["keywords"] or "").strip(),
                    int(fields["importance"]),
                    float(fields["confidence"]),
                    fields["status"],
                    1 if fields["pinned"] else 0,
                    fields["fingerprint"],
                    id_,
                    book_id,
                ],
            )
            updated = await self._db.fetch_one(
                "SELECT * FROM memory_items WHERE id = ? AND book_id = ?",
                [id_, book_id],
            )
        return _memory_item_row(updated)

    async def archive_memory_item(
        self,
        book_id: str,
        id_: str | int,
    ) -> dict[str, Any] | None:
        return await self.update_memory_item(book_id, id_, {"status": "archived"})

    async def link_memory_items(
        self,
        *,
        book_id: str,
        from_memory_id: int | str,
        to_memory_id: int | str,
        relation: str,
        note: str = "",
    ) -> dict[str, Any]:
        if relation not in VALID_RELATIONS:
            raise ValueError(
                f"relation must be one of: {', '.join(sorted(VALID_RELATIONS))}"
            )
        async with self._db.transaction():
            endpoints = await self._db.fetch_all(
                "SELECT id FROM memory_items "
                "WHERE book_id = ? AND id IN (?, ?)",
                [book_id, from_memory_id, to_memory_id],
            )
            endpoint_ids = {str(row["id"]) for row in endpoints}
            if (
                str(from_memory_id) not in endpoint_ids
                or str(to_memory_id) not in endpoint_ids
            ):
                raise ValueError(
                    "both memory items must exist in the current book"
                )
            row_id = await self._db.execute_and_get_id(
                "INSERT INTO memory_links "
                "(book_id, from_memory_id, to_memory_id, relation, note) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    book_id,
                    from_memory_id,
                    to_memory_id,
                    relation,
                    note,
                ],
            )
            row = await self._db.fetch_one(
                "SELECT * FROM memory_links WHERE id = ? AND book_id = ?",
                [row_id, book_id],
            )
        return dict(row or {})

    async def update_foreshadowing(
        self,
        book_id: str,
        id_: str,
        data: dict[str, Any],
    ) -> dict[str, Any] | None:
        async with self._db.transaction():
            row = await self._db.fetch_one(
                "SELECT * FROM ai_foreshadowing "
                "WHERE id = ? AND book_id = ?",
                [id_, book_id],
            )
            if not row:
                return None
            fields = {
                "content": data.get("content", row["content"]),
                "type": data.get("type", row["type"]),
                "status": data.get("status", row["status"]),
                "expected_chapter_id": data.get(
                    "expected_chapter_id",
                    row["expected_chapter_id"],
                ),
                "resolved_chapter_id": data.get(
                    "resolved_chapter_id",
                    row["resolved_chapter_id"],
                ),
            }
            await self._db.execute(
                "UPDATE ai_foreshadowing SET content=?, type=?, status=?, "
                "expected_chapter_id=?, resolved_chapter_id=?, "
                "update_time=datetime('now') WHERE id=? AND book_id=?",
                [
                    fields["content"],
                    fields["type"],
                    fields["status"],
                    fields["expected_chapter_id"],
                    fields["resolved_chapter_id"],
                    id_,
                    book_id,
                ],
            )
            updated = await self._db.fetch_one(
                "SELECT * FROM ai_foreshadowing "
                "WHERE id = ? AND book_id = ?",
                [id_, book_id],
            )
        result = _foreshadowing_row(updated)
        try:
            await self._update_foreshadowing_mirror(result)
        except Exception:
            logger.warning("同步伏笔长期记忆失败", exc_info=True)
        return result

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
                "AND (m.layer != '章节' OR m.chapter_id IS NULL "
                "OR m.chapter_id = ?)"
            )
            chapter_params = [chapter_id]

        rows = await self._db.fetch_all(
            f"""
                SELECT m.*
                FROM (
                    SELECT rowid, rank
                    FROM ai_memories_fts
                    WHERE ai_memories_fts MATCH ?
                    ORDER BY rank
                    LIMIT {limit * 4}
                ) AS fts
                JOIN ai_memories m ON m.id = fts.rowid
                WHERE m.book_id = ?
                  {layer_filter}
                  {chapter_filter}
                ORDER BY fts.rank
                LIMIT ?
            """,
            [query, book_id, *layer_params, *chapter_params, limit],
        )
        return [_spark_idea_row(row) for row in rows]

    async def _get_memory_item_by_source(
        self,
        book_id: str,
        source_type: str,
        source_id: str | int | None,
    ) -> dict[str, Any] | None:
        if source_id is None:
            return None
        row = await self._db.fetch_one(
            "SELECT * FROM memory_items WHERE book_id = ? "
            "AND source_type = ? AND source_id = ?",
            [book_id, source_type, str(source_id)],
        )
        return _memory_item_row(row) if row else None

    async def _mirror_spark_idea(
        self,
        row: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not row:
            return None
        scope_type, scope_id = _spark_scope(row)
        return await self.create_memory_item(
            book_id=str(row["book_id"]),
            kind="canon",
            scope_type=scope_type,
            scope_id=scope_id,
            content=str(row.get("content") or ""),
            importance=4,
            status="active",
            source_type="spark_idea",
            source_id=row.get("id"),
            fingerprint=f"legacy:spark:{row.get('id')}",
        )

    async def _update_spark_idea_mirror(
        self,
        row: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not row:
            return None
        book_id = str(row["book_id"])
        existing = await self._get_memory_item_by_source(
            book_id,
            "spark_idea",
            row.get("id"),
        )
        if not existing:
            return await self._mirror_spark_idea(row)
        scope_type, scope_id = _spark_scope(row)
        return await self.update_memory_item(
            book_id,
            existing["id"],
            {
                "kind": "canon",
                "scope_type": scope_type,
                "scope_id": scope_id,
                "content": row.get("content") or "",
                "importance": 4,
                "status": "active",
                "pinned": existing.get("pinned", 0),
            },
        )

    async def _mirror_foreshadowing(
        self,
        row: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not row:
            return None
        return await self.create_memory_item(
            book_id=str(row["book_id"]),
            kind="foreshadowing",
            scope_type="chapter",
            scope_id=(
                str(row.get("chapter_id"))
                if row.get("chapter_id") is not None
                else None
            ),
            content=str(row.get("content") or ""),
            keywords=f"{row.get('type') or ''} {row.get('status') or ''}".strip(),
            importance=4,
            status=("archived" if row.get("status") == "已回收" else "active"),
            source_type="foreshadowing",
            source_id=row.get("id"),
            fingerprint=f"legacy:foreshadowing:{row.get('id')}",
        )

    async def _update_foreshadowing_mirror(
        self,
        row: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not row:
            return None
        book_id = str(row["book_id"])
        existing = await self._get_memory_item_by_source(
            book_id,
            "foreshadowing",
            row.get("id"),
        )
        if not existing:
            return await self._mirror_foreshadowing(row)
        return await self.update_memory_item(
            book_id,
            existing["id"],
            {
                "kind": "foreshadowing",
                "scope_type": "chapter",
                "scope_id": (
                    str(row.get("chapter_id"))
                    if row.get("chapter_id") is not None
                    else None
                ),
                "content": row.get("content") or "",
                "keywords": (
                    f"{row.get('type') or ''} {row.get('status') or ''}".strip()
                ),
                "importance": 4,
                "status": (
                    "archived" if row.get("status") == "已回收" else "active"
                ),
            },
        )

    async def _archive_by_source(
        self,
        book_id: str,
        source_type: str,
        source_id: str | int | None,
    ) -> None:
        existing = await self._get_memory_item_by_source(
            book_id,
            source_type,
            source_id,
        )
        if existing:
            await self.archive_memory_item(book_id, existing["id"])


def _query_like_terms(query: str) -> list[str]:
    text = str(query or "").strip()
    if not text:
        return []
    terms = [text]
    chunks = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", text)
    for chunk in chunks:
        if len(chunk) <= 6:
            terms.append(chunk)
        else:
            terms.extend(chunk[index:index + 2] for index in range(len(chunk) - 1))
    return list(dict.fromkeys(terms))[:12]


def _spark_scope(row: dict[str, Any]) -> tuple[str, str | None]:
    if row.get("character_id") is not None:
        return "character", str(row["character_id"])
    if row.get("chapter_id") is not None:
        return "chapter", str(row["chapter_id"])
    return "book", None


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


def _memory_item_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": row["id"],
        "book_id": row["book_id"],
        "kind": row["kind"],
        "scope_type": row["scope_type"],
        "scope_id": row["scope_id"],
        "content": row["content"],
        "summary": row["summary"],
        "keywords": row["keywords"],
        "importance": row["importance"],
        "confidence": row["confidence"],
        "status": row["status"],
        "pinned": row["pinned"],
        "fingerprint": row["fingerprint"],
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "create_time": row["create_time"],
        "update_time": row["update_time"],
        "last_used_at": row.get("last_used_at"),
    }


__all__ = [
    "SqliteWritingToolMemoryRepository",
    "build_fingerprint",
    "normalize_memory_text",
]
