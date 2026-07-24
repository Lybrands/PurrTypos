"""SQLite adapter for book-scoped long-term memory recall."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Sequence

from domains.writing.repositories import MemoryItem, MemoryLink

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


_FIELD_PREFIX = re.compile(
    r"^(?:任务目标|操作|目标|具体要求|约束|必须保留|交付物|所需证据类型)\s*[:：]\s*"
)
_CLAUSE_SPLIT = re.compile(r"[\n\r。！？!?；;，,、]+")
_TOKEN_CHUNK = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_.:-]{2,}")


class SqliteMemoryRecallRepository:
    def __init__(self, db: DatabaseConnection):
        self._db = db

    async def get_by_ids(
        self,
        book_id: str,
        ids: Sequence[Any],
        *,
        statuses: Sequence[str],
    ) -> tuple[MemoryItem, ...]:
        clean_ids = _clean_values(ids)
        if not clean_ids:
            return ()
        id_marks = ",".join("?" for _ in clean_ids)
        status_clause, status_params = _status_clause(statuses)
        rows = await self._db.fetch_all(
            "SELECT * FROM memory_items WHERE book_id = ? "
            f"AND id IN ({id_marks}){status_clause} ORDER BY id ASC",
            [book_id, *clean_ids, *status_params],
        )
        return tuple(_memory_item(row) for row in rows)

    async def get_by_source_ids(
        self,
        book_id: str,
        source_type: str,
        source_ids: Sequence[Any],
        *,
        statuses: Sequence[str],
    ) -> tuple[MemoryItem, ...]:
        clean_ids = _clean_values(source_ids)
        if not clean_ids:
            return ()
        id_marks = ",".join("?" for _ in clean_ids)
        status_clause, status_params = _status_clause(statuses)
        rows = await self._db.fetch_all(
            "SELECT * FROM memory_items WHERE book_id = ? AND source_type = ? "
            f"AND source_id IN ({id_marks}){status_clause} ORDER BY id ASC",
            [book_id, source_type, *clean_ids, *status_params],
        )
        return tuple(_memory_item(row) for row in rows)

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
    ) -> tuple[MemoryItem, ...]:
        maximum = max(1, int(limit))
        where, params = _search_filters(
            book_id,
            statuses=statuses,
            kinds=kinds,
            scope_type=scope_type,
            scope_id=scope_id,
        )
        text = str(query or "").strip()
        terms = _query_like_terms(text)
        searchable = tuple(value for value in terms if len(value) >= 3)
        fts_rows: list[dict[str, Any]] = []
        if searchable:
            expression = " OR ".join(
                '"' + value.replace('"', '""') + '"' for value in searchable
            )
            try:
                fts_rows = await self._db.fetch_all(
                    "SELECT m.*, memory_items_fts.rank AS fts_rank "
                    "FROM memory_items_fts "
                    "JOIN memory_items m ON m.id = memory_items_fts.rowid "
                    f"WHERE memory_items_fts MATCH ? AND {' AND '.join(where)} "
                    "ORDER BY m.pinned DESC, m.importance DESC, "
                    "memory_items_fts.rank, m.id ASC LIMIT ?",
                    [expression, *params, maximum],
                )
            except Exception:
                fts_rows = []

        fallback_where = list(where)
        fallback_params = list(params)
        if terms:
            clauses: list[str] = []
            for term in _evenly_sample(terms, min(32, len(terms))):
                clauses.append(
                    "(m.content LIKE ? OR m.summary LIKE ? OR m.keywords LIKE ?)"
                )
                like = f"%{term}%"
                fallback_params.extend([like, like, like])
            if clauses:
                fallback_where.append("(" + " OR ".join(clauses) + ")")
        fallback_rows = await self._db.fetch_all(
            f"SELECT m.* FROM memory_items m WHERE {' AND '.join(fallback_where)} "
            "ORDER BY m.pinned DESC, m.importance DESC, "
            "COALESCE(m.last_used_at, m.update_time, m.create_time) DESC, "
            "m.id ASC LIMIT ?",
            [*fallback_params, maximum],
        )
        merged: list[dict[str, Any]] = []
        seen: set[int] = set()
        for row in (*fts_rows, *fallback_rows):
            item_id = int(row["id"])
            if item_id in seen:
                continue
            seen.add(item_id)
            merged.append(row)
            if len(merged) >= maximum:
                break
        return tuple(_memory_item(row) for row in merged)

    async def get_links(
        self,
        book_id: str,
        memory_ids: Sequence[int],
    ) -> tuple[MemoryLink, ...]:
        ids = _clean_values(memory_ids)
        if not ids:
            return ()
        placeholders = ",".join("?" for _ in ids)
        rows = await self._db.fetch_all(
            "SELECT * FROM memory_links WHERE book_id = ? AND "
            f"(from_memory_id IN ({placeholders}) "
            f"OR to_memory_id IN ({placeholders})) ORDER BY id ASC",
            [book_id, *ids, *ids],
        )
        return tuple(MemoryLink(
            from_memory_id=int(row["from_memory_id"]),
            to_memory_id=int(row["to_memory_id"]),
            relation=str(row.get("relation") or ""),
            note=str(row.get("note") or ""),
        ) for row in rows)

    async def mark_used(
        self,
        book_id: str,
        memory_ids: Sequence[int],
    ) -> None:
        ids = _clean_values(memory_ids)
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        await self._db.execute(
            "UPDATE memory_items SET last_used_at = datetime('now') "
            f"WHERE book_id = ? AND id IN ({placeholders})",
            [book_id, *ids],
        )


def _search_filters(
    book_id: str,
    *,
    statuses: Sequence[str],
    kinds: Sequence[str],
    scope_type: str | None,
    scope_id: str | None,
) -> tuple[list[str], list[Any]]:
    where = ["m.book_id = ?"]
    params: list[Any] = [book_id]
    if statuses:
        marks = ",".join("?" for _ in statuses)
        where.append(f"m.status IN ({marks})")
        params.extend(statuses)
    if kinds:
        marks = ",".join("?" for _ in kinds)
        where.append(f"m.kind IN ({marks})")
        params.extend(kinds)
    if scope_type:
        where.append("m.scope_type = ?")
        params.append(scope_type)
    if scope_id is not None:
        where.append("m.scope_id = ?")
        params.append(str(scope_id))
    return where, params


def _status_clause(statuses: Sequence[str]) -> tuple[str, list[str]]:
    if not statuses:
        return "", []
    marks = ",".join("?" for _ in statuses)
    return f" AND status IN ({marks})", [str(value) for value in statuses]


def _clean_values(values: Sequence[Any]) -> list[str]:
    return list(dict.fromkeys(
        str(value).strip() for value in values if str(value).strip()
    ))


def _query_like_terms(query: str) -> list[str]:
    text = str(query or "").strip()
    if not text:
        return []
    groups: list[tuple[str, ...]] = []
    for line in text.splitlines() or (text,):
        body = _FIELD_PREFIX.sub("", line.strip())
        for clause in _CLAUSE_SPLIT.split(body):
            values: list[str] = []
            for chunk in _TOKEN_CHUNK.findall(clause):
                if re.search(r"[\u4e00-\u9fff]", chunk):
                    if len(chunk) <= 3:
                        values.append(chunk)
                    else:
                        if len(chunk) <= 12:
                            values.append(chunk)
                        values.extend(
                            chunk[index:index + 2]
                            for index in range(len(chunk) - 1)
                        )
                        values.extend(
                            chunk[index:index + 3]
                            for index in range(len(chunk) - 2)
                        )
                else:
                    values.append(chunk[:64])
            cleaned = tuple(dict.fromkeys(value for value in values if value))
            if cleaned:
                groups.append(cleaned)
    maximum = 96
    if not groups:
        return []
    quota = max(1, maximum // len(groups))
    selected: list[str] = []
    leftovers: list[tuple[str, ...]] = []
    for group in groups:
        sampled = _evenly_sample(group, min(len(group), quota))
        selected.extend(sampled)
        sampled_set = set(sampled)
        leftovers.append(tuple(value for value in group if value not in sampled_set))
    cursor = 0
    while len(selected) < maximum and any(leftovers):
        index = cursor % len(leftovers)
        if leftovers[index]:
            selected.append(leftovers[index][0])
            leftovers[index] = leftovers[index][1:]
        cursor += 1
    return list(dict.fromkeys(selected))[:maximum]


def _evenly_sample(values: Sequence[str], maximum: int) -> tuple[str, ...]:
    rows = tuple(values)
    if maximum <= 0 or not rows:
        return ()
    if len(rows) <= maximum:
        return rows
    if maximum == 1:
        return (rows[len(rows) // 2],)
    indexes = {
        round(index * (len(rows) - 1) / (maximum - 1))
        for index in range(maximum)
    }
    return tuple(rows[index] for index in sorted(indexes))


def _memory_item(row: dict[str, Any]) -> MemoryItem:
    return MemoryItem(
        id=int(row["id"]),
        book_id=str(row.get("book_id") or ""),
        kind=str(row.get("kind") or ""),
        scope_type=str(row.get("scope_type") or "book"),
        scope_id=(str(row["scope_id"]) if row.get("scope_id") is not None else None),
        content=str(row.get("content") or ""),
        summary=str(row.get("summary") or ""),
        importance=int(row.get("importance") or 0),
        status=str(row.get("status") or ""),
        pinned=int(row.get("pinned") or 0),
    )
