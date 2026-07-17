"""Unified long-term memory service backed by SQLite + FTS5.

This module is the canonical recall surface for AI context. Legacy spark ideas
and foreshadowing rows are mirrored here, but their old tables remain the source
for their specialized UI/API fields during the transition.
"""

from __future__ import annotations

import re
from typing import Any

from dependencies import get_db

DEFAULT_STATUSES = ["active"]
VALID_RELATIONS = {"supersedes", "contradicts", "supports", "relates_to"}


def normalize_memory_text(text: str) -> str:
    """Normalize text enough for deterministic local duplicate detection."""
    raw = str(text or "").strip().lower()
    raw = re.sub(r"[\s\r\n\t]+", "", raw)
    raw = re.sub(r"[，。！？、,.!?;；:：\"'“”‘’（）()\[\]【】《》<>]", "", raw)
    return raw


def build_fingerprint(
    book_id: str,
    kind: str,
    scope_type: str,
    scope_id: str | None,
    content: str,
) -> str:
    normalized = normalize_memory_text(content)
    return "|".join([
        str(book_id or "").strip(),
        str(kind or "").strip(),
        str(scope_type or "book").strip(),
        str(scope_id or "").strip(),
        normalized[:240],
    ])


async def create_memory_item(
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
) -> dict:
    db = get_db()
    clean_content = str(content or "").strip()
    if not str(book_id or "").strip():
        raise ValueError("book_id is required")
    if not str(kind or "").strip():
        raise ValueError("kind is required")
    if not clean_content:
        raise ValueError("content is required")

    fp = fingerprint or build_fingerprint(book_id, kind, scope_type, scope_id, clean_content)
    existing = await db.fetch_one(
        "SELECT * FROM memory_items WHERE book_id = ? AND fingerprint = ?",
        [book_id, fp],
    )
    if existing:
        await db.execute(
            "UPDATE memory_items SET update_time = datetime('now') WHERE id = ?",
            [existing["id"]],
        )
        row = await db.fetch_one("SELECT * FROM memory_items WHERE id = ?", [existing["id"]])
        result = _memory_item_row(row)
        result["deduped"] = True
        return result

    row_id = await db.execute_and_get_id(
        "INSERT INTO memory_items "
        "(book_id, kind, scope_type, scope_id, content, summary, keywords, "
        "importance, confidence, status, pinned, fingerprint, source_type, source_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            fp,
            source_type or "manual",
            str(source_id) if source_id is not None else None,
        ],
    )
    row = await db.fetch_one("SELECT * FROM memory_items WHERE id = ?", [row_id])
    result = _memory_item_row(row)
    result["deduped"] = False
    return result


async def update_memory_item(id_: str | int, data: dict) -> dict | None:
    db = get_db()
    row = await db.fetch_one("SELECT * FROM memory_items WHERE id = ?", [id_])
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
        row["book_id"],
        fields["kind"],
        fields["scope_type"],
        fields["scope_id"],
        fields["content"],
    )
    await db.execute(
        "UPDATE memory_items SET kind=?, scope_type=?, scope_id=?, content=?, "
        "summary=?, keywords=?, importance=?, confidence=?, status=?, pinned=?, "
        "fingerprint=?, update_time=datetime('now') WHERE id=?",
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
        ],
    )
    updated = await db.fetch_one("SELECT * FROM memory_items WHERE id = ?", [id_])
    return _memory_item_row(updated)


async def archive_memory_item(id_: str | int) -> dict | None:
    return await update_memory_item(id_, {"status": "archived"})


async def get_memory_items_by_ids(ids: list[Any]) -> list[dict]:
    clean_ids = [str(x).strip() for x in ids or [] if str(x).strip()]
    if not clean_ids:
        return []
    db = get_db()
    placeholders = ",".join("?" * len(clean_ids))
    rows = await db.fetch_all(
        f"SELECT * FROM memory_items WHERE id IN ({placeholders}) ORDER BY id ASC",
        clean_ids,
    )
    return [_memory_item_row(r) for r in rows]


async def get_memory_items_by_source_ids(source_type: str, source_ids: list[Any]) -> list[dict]:
    clean_ids = [str(x).strip() for x in source_ids or [] if str(x).strip()]
    if not clean_ids:
        return []
    db = get_db()
    placeholders = ",".join("?" * len(clean_ids))
    rows = await db.fetch_all(
        f"SELECT * FROM memory_items WHERE source_type = ? AND source_id IN ({placeholders}) ORDER BY id ASC",
        [source_type, *clean_ids],
    )
    return [_memory_item_row(r) for r in rows]


async def search_memory_items(
    book_id: str,
    query: str = "",
    options: dict | None = None,
) -> list[dict]:
    opts = options or {}
    limit = int(opts.get("limit") or 20)
    statuses = opts.get("statuses") or DEFAULT_STATUSES
    kinds = opts.get("kinds")
    scope_type = opts.get("scopeType") or opts.get("scope_type")
    scope_id = opts.get("scopeId") or opts.get("scope_id")
    q = str(query or "").strip()

    db = get_db()
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

    base_order = (
        "ORDER BY m.pinned DESC, m.importance DESC, "
        "COALESCE(m.last_used_at, m.update_time, m.create_time) DESC, m.id ASC "
        "LIMIT ?"
    )

    if len(q) >= 2:
        try:
            sql = f"""
                SELECT m.*, memory_items_fts.rank AS fts_rank
                FROM memory_items_fts
                JOIN memory_items m ON m.id = memory_items_fts.rowid
                WHERE memory_items_fts MATCH ? AND {" AND ".join(where)}
                ORDER BY m.pinned DESC, m.importance DESC,
                         memory_items_fts.rank, m.id ASC
                LIMIT ?
            """
            rows = await db.fetch_all(sql, [q, *params, limit])
            if rows:
                return [_memory_item_row(r) for r in rows]
        except Exception:
            pass

    if q:
        terms = _query_like_terms(q)
        if terms:
            term_clauses = []
            for term in terms:
                term_clauses.append("(m.content LIKE ? OR m.summary LIKE ? OR m.keywords LIKE ?)")
                like = f"%{term}%"
                params.extend([like, like, like])
            where.append("(" + " OR ".join(term_clauses) + ")")

    rows = await db.fetch_all(
        f"SELECT m.* FROM memory_items m WHERE {' AND '.join(where)} {base_order}",
        [*params, limit],
    )
    return [_memory_item_row(r) for r in rows]


def _query_like_terms(query: str) -> list[str]:
    q = str(query or "").strip()
    if not q:
        return []
    terms = [q]
    chunks = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", q)
    for chunk in chunks:
        if len(chunk) <= 6:
            terms.append(chunk)
        else:
            terms.extend(chunk[i:i + 2] for i in range(0, len(chunk) - 1))
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            result.append(term)
    return result[:12]


async def link_memory_items(
    *,
    book_id: str,
    from_memory_id: int | str,
    to_memory_id: int | str,
    relation: str,
    note: str = "",
) -> dict:
    if relation not in VALID_RELATIONS:
        raise ValueError(f"relation must be one of: {', '.join(sorted(VALID_RELATIONS))}")
    db = get_db()
    endpoints = await get_memory_items_by_ids([from_memory_id, to_memory_id])
    by_id = {str(item["id"]): item for item in endpoints}
    from_item = by_id.get(str(from_memory_id))
    to_item = by_id.get(str(to_memory_id))
    if not from_item or not to_item:
        raise ValueError("both memory items must exist")
    if str(from_item.get("book_id") or "") != str(book_id) or str(to_item.get("book_id") or "") != str(book_id):
        raise ValueError("memory links must stay within the same book")
    row_id = await db.execute_and_get_id(
        "INSERT INTO memory_links (book_id, from_memory_id, to_memory_id, relation, note) "
        "VALUES (?, ?, ?, ?, ?)",
        [book_id, from_memory_id, to_memory_id, relation, note],
    )
    row = await db.fetch_one("SELECT * FROM memory_links WHERE id = ?", [row_id])
    return dict(row or {})


async def get_memory_links_for_items(
    book_id: str,
    memory_ids: list[Any],
) -> list[dict]:
    """Return relations touching any candidate memory in the same book.

    Retrieval intentionally happens after candidate recall, so relation lookup
    stays bounded by the current context set rather than scanning a whole book.
    """
    clean_ids = [str(x).strip() for x in memory_ids or [] if str(x).strip()]
    if not clean_ids:
        return []
    placeholders = ",".join("?" * len(clean_ids))
    db = get_db()
    rows = await db.fetch_all(
        "SELECT * FROM memory_links WHERE book_id = ? AND "
        f"(from_memory_id IN ({placeholders}) OR to_memory_id IN ({placeholders})) "
        "ORDER BY id ASC",
        [book_id, *clean_ids, *clean_ids],
    )
    return [dict(row) for row in rows]


async def mark_memory_items_used(ids: list[Any]) -> None:
    clean_ids = [str(x).strip() for x in ids or [] if str(x).strip()]
    if not clean_ids:
        return
    db = get_db()
    placeholders = ",".join("?" * len(clean_ids))
    await db.execute(
        f"UPDATE memory_items SET last_used_at = datetime('now') WHERE id IN ({placeholders})",
        clean_ids,
    )


async def mirror_spark_idea(row: dict | None) -> dict | None:
    if not row:
        return None
    scope_type = "book"
    scope_id = None
    if row.get("character_id") is not None:
        scope_type = "character"
        scope_id = str(row.get("character_id"))
    elif row.get("chapter_id") is not None:
        scope_type = "chapter"
        scope_id = str(row.get("chapter_id"))
    return await create_memory_item(
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


async def update_spark_idea_mirror(row: dict | None) -> dict | None:
    if not row:
        return None
    existing = await _get_by_source("spark_idea", row.get("id"))
    if not existing:
        return await mirror_spark_idea(row)
    scope_type = "book"
    scope_id = None
    if row.get("character_id") is not None:
        scope_type = "character"
        scope_id = str(row.get("character_id"))
    elif row.get("chapter_id") is not None:
        scope_type = "chapter"
        scope_id = str(row.get("chapter_id"))
    return await update_memory_item(
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


async def archive_by_source(source_type: str, source_id: str | int | None) -> None:
    existing = await _get_by_source(source_type, source_id)
    if existing:
        await archive_memory_item(existing["id"])


async def mirror_foreshadowing(row: dict | None) -> dict | None:
    if not row:
        return None
    status = "archived" if row.get("status") == "已回收" else "active"
    return await create_memory_item(
        book_id=str(row["book_id"]),
        kind="foreshadowing",
        scope_type="chapter",
        scope_id=str(row.get("chapter_id")) if row.get("chapter_id") is not None else None,
        content=str(row.get("content") or ""),
        keywords=f"{row.get('type') or ''} {row.get('status') or ''}".strip(),
        importance=4,
        status=status,
        source_type="foreshadowing",
        source_id=row.get("id"),
        fingerprint=f"legacy:foreshadowing:{row.get('id')}",
    )


async def update_foreshadowing_mirror(row: dict | None) -> dict | None:
    if not row:
        return None
    existing = await _get_by_source("foreshadowing", row.get("id"))
    if not existing:
        return await mirror_foreshadowing(row)
    status = "archived" if row.get("status") == "已回收" else "active"
    return await update_memory_item(
        existing["id"],
        {
            "kind": "foreshadowing",
            "scope_type": "chapter",
            "scope_id": str(row.get("chapter_id")) if row.get("chapter_id") is not None else None,
            "content": row.get("content") or "",
            "keywords": f"{row.get('type') or ''} {row.get('status') or ''}".strip(),
            "importance": 4,
            "status": status,
        },
    )


async def _get_by_source(source_type: str, source_id: str | int | None) -> dict | None:
    if source_id is None:
        return None
    db = get_db()
    row = await db.fetch_one(
        "SELECT * FROM memory_items WHERE source_type = ? AND source_id = ?",
        [source_type, str(source_id)],
    )
    return _memory_item_row(row) if row else None


def _memory_item_row(r: Any) -> dict:
    if r is None:
        return {}
    return {
        "id": r["id"],
        "book_id": r["book_id"],
        "kind": r["kind"],
        "scope_type": r["scope_type"],
        "scope_id": r["scope_id"],
        "content": r["content"],
        "summary": r["summary"],
        "keywords": r["keywords"],
        "importance": r["importance"],
        "confidence": r["confidence"],
        "status": r["status"],
        "pinned": r["pinned"],
        "fingerprint": r["fingerprint"],
        "source_type": r["source_type"],
        "source_id": r["source_id"],
        "create_time": r["create_time"],
        "update_time": r["update_time"],
        "last_used_at": r.get("last_used_at"),
    }
