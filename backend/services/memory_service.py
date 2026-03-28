"""
长期记忆服务 — 使用 SQLite (ai_memories / ai_foreshadowing 表) 存储。

原先基于 mem0 向量库的实现在没有 Ollama/OpenAI Embedder 的环境下无法工作；
现改为直接操作数据库，与其余路由保持一致。
"""

from __future__ import annotations

import logging
from typing import Any

from dependencies import get_db
from utils.id_utils import short_id8

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Memory CRUD
# ---------------------------------------------------------------------------

async def add_memory(
    book_id: str,
    layer: str,
    content: str,
    chapter_id: str | None = None,
    character_id: str | None = None,
) -> dict:
    db = get_db()
    row_id = await db.execute_and_get_id(
        "INSERT INTO ai_memories (book_id, layer, content, chapter_id, character_id) "
        "VALUES (?, ?, ?, ?, ?)",
        [book_id, layer, content.strip(), chapter_id, character_id],
    )
    row = await db.fetch_one("SELECT * FROM ai_memories WHERE id = ?", [row_id])
    return _memory_row(row)


async def update_memory(id_: str, data: dict) -> dict | None:
    db = get_db()
    row = await db.fetch_one("SELECT * FROM ai_memories WHERE id = ?", [id_])
    if not row:
        return None
    content = data.get("content", row["content"])
    layer = data.get("layer", row["layer"])
    await db.execute(
        "UPDATE ai_memories SET content = ?, layer = ? WHERE id = ?",
        [content, layer, id_],
    )
    updated = await db.fetch_one("SELECT * FROM ai_memories WHERE id = ?", [id_])
    return _memory_row(updated)


async def delete_memory(id_: str) -> None:
    db = get_db()
    await db.execute("DELETE FROM ai_memories WHERE id = ?", [id_])


async def get_memories_by_book(book_id: str, layer: str | None = None) -> list[dict]:
    db = get_db()
    if layer:
        rows = await db.fetch_all(
            "SELECT * FROM ai_memories WHERE book_id = ? AND layer = ? "
            "ORDER BY create_time DESC",
            [book_id, layer],
        )
    else:
        rows = await db.fetch_all(
            "SELECT * FROM ai_memories WHERE book_id = ? ORDER BY create_time DESC",
            [book_id],
        )
    return [_memory_row(r) for r in rows]


async def get_memories_by_ids(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    db = get_db()
    placeholders = ",".join("?" * len(ids))
    rows = await db.fetch_all(
        f"SELECT * FROM ai_memories WHERE id IN ({placeholders})", ids
    )
    return [_memory_row(r) for r in rows]


async def get_memories_for_prompt(
    book_id: str,
    query: str,
    options: dict | None = None,
) -> list[dict]:
    """返回书籍记忆用于提示词注入。

    - query 非空（≥2字）：FTS5 trigram 全文检索，按相关度排序
    - query 为空或1字：按创建时间倒序返回，每层取 limitPerLayer 条
    """
    opts = options or {}
    layers: list[str] | None = opts.get("layers")
    limit: int = opts.get("limit", 20)
    limit_per_layer: int = opts.get("limitPerLayer", 5)
    chapter_id: str | None = opts.get("chapterId")

    db = get_db()
    q = (query or "").strip()

    if len(q) >= 2:
        # ── FTS5 全文检索 ──────────────────────────────────────────
        try:
            rows = await _fts_search(db, book_id, q, layers, chapter_id, limit)
            return rows
        except Exception:
            # FTS 索引尚未建立或查询语法错误时退回全量过滤
            logger.warning("FTS5 检索失败，退回全量过滤", exc_info=True)

    # ── 全量过滤（query 为空 / 短词 / FTS 失败兜底）────────────────
    rows = await db.fetch_all(
        "SELECT * FROM ai_memories WHERE book_id = ? ORDER BY create_time DESC",
        [book_id],
    )

    by_layer: dict[str, list[dict]] = {}
    for r in rows:
        rl = r["layer"]
        if layers and rl not in layers:
            continue
        if (
            chapter_id is not None
            and r.get("chapter_id") is not None
            and str(r["chapter_id"]) != str(chapter_id)
            and rl == "章节"
        ):
            continue
        bucket = by_layer.setdefault(rl, [])
        if len(bucket) < limit_per_layer:
            bucket.append(_memory_row(r))

    result: list[dict] = []
    order = layers or list(by_layer.keys())
    for lyr in order:
        for item in by_layer.get(lyr, []):
            result.append(item)
            if len(result) >= limit:
                return result
    return result


async def _fts_search(
    db: Any,
    book_id: str,
    query: str,
    layers: list[str] | None,
    chapter_id: str | None,
    limit: int,
) -> list[dict]:
    """FTS5 trigram 全文检索，结果按 BM25 相关度排序。

    使用子查询形式：先从 FTS 索引取匹配 rowid，再与主表 JOIN 过滤元数据。
    """
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

    # 子查询从 FTS 取命中行的 rowid（同时获得 rank 排序），主表再按元数据二次过滤
    sql = f"""
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
    """
    params: list[Any] = [query, book_id, *layer_params, *chapter_params, limit]
    rows = await db.fetch_all(sql, params)
    return [_memory_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Foreshadowing CRUD
# ---------------------------------------------------------------------------

async def add_foreshadowing(
    book_id: str,
    chapter_id: str | None,
    content: str,
    type_: str | None = None,
    expected_chapter_id: str | None = None,
    status: str = "未回收",
    resolved_chapter_id: str | None = None,
) -> dict:
    db = get_db()
    f_type = type_ or "悬念"
    f_status = status or "未回收"
    row_id = await db.execute_and_get_id(
        "INSERT INTO ai_foreshadowing "
        "(book_id, chapter_id, content, type, expected_chapter_id, status, resolved_chapter_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [book_id, chapter_id, content.strip(), f_type, expected_chapter_id, f_status, resolved_chapter_id],
    )
    row = await db.fetch_one("SELECT * FROM ai_foreshadowing WHERE id = ?", [row_id])
    return _foreshadowing_row(row)


async def update_foreshadowing(id_: str, data: dict) -> dict | None:
    db = get_db()
    row = await db.fetch_one("SELECT * FROM ai_foreshadowing WHERE id = ?", [id_])
    if not row:
        return None
    fields = {
        "content": data.get("content", row["content"]),
        "type": data.get("type", row["type"]),
        "status": data.get("status", row["status"]),
        "expected_chapter_id": data.get("expected_chapter_id", row["expected_chapter_id"]),
        "resolved_chapter_id": data.get("resolved_chapter_id", row["resolved_chapter_id"]),
    }
    await db.execute(
        "UPDATE ai_foreshadowing SET content=?, type=?, status=?, "
        "expected_chapter_id=?, resolved_chapter_id=?, update_time=datetime('now') "
        "WHERE id=?",
        [fields["content"], fields["type"], fields["status"],
         fields["expected_chapter_id"], fields["resolved_chapter_id"], id_],
    )
    updated = await db.fetch_one("SELECT * FROM ai_foreshadowing WHERE id = ?", [id_])
    return _foreshadowing_row(updated)


async def delete_foreshadowing(id_: str) -> None:
    db = get_db()
    await db.execute("DELETE FROM ai_foreshadowing WHERE id = ?", [id_])


async def get_foreshadowing_by_book(
    book_id: str, status_filter: str | None = None
) -> list[dict]:
    db = get_db()
    if status_filter:
        rows = await db.fetch_all(
            "SELECT * FROM ai_foreshadowing WHERE book_id = ? AND status = ? "
            "ORDER BY create_time DESC",
            [book_id, status_filter],
        )
    else:
        rows = await db.fetch_all(
            "SELECT * FROM ai_foreshadowing WHERE book_id = ? ORDER BY create_time DESC",
            [book_id],
        )
    return [_foreshadowing_row(r) for r in rows]


async def get_foreshadowing_by_ids(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    db = get_db()
    placeholders = ",".join("?" * len(ids))
    rows = await db.fetch_all(
        f"SELECT * FROM ai_foreshadowing WHERE id IN ({placeholders})", ids
    )
    return [_foreshadowing_row(r) for r in rows]


async def get_foreshadowing_for_prompt(
    book_id: str,
    query: str,
    options: dict | None = None,
) -> list[dict]:
    opts = options or {}
    limit: int = opts.get("limit", 10)
    status_filter: str | None = opts.get("status")

    db = get_db()
    if status_filter:
        rows = await db.fetch_all(
            "SELECT * FROM ai_foreshadowing WHERE book_id = ? AND status = ? "
            "ORDER BY create_time DESC LIMIT ?",
            [book_id, status_filter, limit],
        )
    else:
        rows = await db.fetch_all(
            "SELECT * FROM ai_foreshadowing WHERE book_id = ? "
            "ORDER BY create_time DESC LIMIT ?",
            [book_id, limit],
        )
    return [_foreshadowing_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Row serialisers
# ---------------------------------------------------------------------------

def _memory_row(r: Any) -> dict:
    if r is None:
        return {}
    return {
        "id": r["id"],
        "book_id": r["book_id"],
        "layer": r["layer"],
        "content": r["content"],
        "chapter_id": r["chapter_id"],
        "character_id": r["character_id"],
        "create_time": r["create_time"],
    }


def _foreshadowing_row(r: Any) -> dict:
    if r is None:
        return {}
    return {
        "id": r["id"],
        "book_id": r["book_id"],
        "chapter_id": r["chapter_id"],
        "content": r["content"],
        "type": r["type"],
        "status": r["status"],
        "expected_chapter_id": r["expected_chapter_id"],
        "resolved_chapter_id": r["resolved_chapter_id"],
        "create_time": r["create_time"],
        "update_time": r.get("update_time"),
    }
