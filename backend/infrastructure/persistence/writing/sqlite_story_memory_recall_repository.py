"""SQLite adapter for bounded, evidence-valid Story Memory recall."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Sequence

from domains.writing.repositories import StoryMemoryRecallItem

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


class SqliteStoryMemoryRecallRepository:
    """Recall only confirmed, active records whose latest source is valid.

    Planner declarations are ranking hints, not hard authority.  The SQL
    candidate predicate ORs declared kinds/entities/chapters with semantic
    terms so an imperfect declaration cannot hide otherwise relevant state.
    """

    def __init__(self, db: DatabaseConnection):
        self._db = db

    async def search_current(
        self,
        book_id: str,
        query: str,
        *,
        kinds: Sequence[str] = (),
        planner_kinds: Sequence[str] = (),
        entity_refs: Sequence[str] = (),
        chapter_ids: Sequence[str] = (),
        limit: int = 24,
    ) -> tuple[StoryMemoryRecallItem, ...]:
        normalized_book_id = str(book_id or "").strip()
        maximum = max(1, min(100, int(limit)))
        if not normalized_book_id:
            return ()

        clean_kinds = _clean(kinds)
        clean_planner_kinds = _clean(planner_kinds)
        clean_entities = _clean(entity_refs)
        clean_chapters = _clean(chapter_ids)
        terms = _query_terms(query)
        relevance_clauses: list[str] = []
        relevance_params: list[Any] = []
        if clean_planner_kinds:
            marks = ",".join("?" for _ in clean_planner_kinds)
            relevance_clauses.append(f"r.kind IN ({marks})")
            relevance_params.extend(clean_planner_kinds)
        if clean_entities:
            marks = ",".join("?" for _ in clean_entities)
            relevance_clauses.append(
                f"(r.subject_id IN ({marks}) OR r.memory_key IN ({marks}))"
            )
            relevance_params.extend([*clean_entities, *clean_entities])
        if clean_chapters:
            marks = ",".join("?" for _ in clean_chapters)
            relevance_clauses.append(f"s.chapter_id IN ({marks})")
            relevance_params.extend(clean_chapters)
        for term in terms:
            relevance_clauses.append(
                "(r.memory_key LIKE ? OR r.subject_id LIKE ? OR "
                "r.payload_json LIKE ? OR s.excerpt LIKE ?)"
            )
            like = f"%{term}%"
            relevance_params.extend([like, like, like, like])
        if not relevance_clauses:
            return ()

        candidate_limit = min(400, max(maximum * 6, 48))
        rows = await self._db.fetch_all(
            "SELECT r.id AS record_id, r.book_id, r.memory_key, r.kind, "
            "r.subject_id, r.payload_json, r.version, r.last_source_id, "
            "r.update_time, s.chapter_id, s.excerpt AS source_excerpt "
            "FROM story_memory_records AS r "
            "JOIN story_memory_sources AS s ON s.id = r.last_source_id "
            "WHERE r.book_id = ? AND r.status = 'confirmed' "
            "AND r.lifecycle = 'active' AND r.provenance_status = 'valid' "
            "AND s.status = 'valid' AND ("
            + " OR ".join(relevance_clauses)
            + ") ORDER BY r.update_time DESC, r.memory_key ASC LIMIT ?",
            [normalized_book_id, *relevance_params, candidate_limit],
        )
        ranked = sorted(
            rows,
            key=lambda row: (
                -_score(
                    row,
                    kinds=clean_kinds,
                    entity_refs=clean_entities,
                    chapter_ids=clean_chapters,
                    terms=terms,
                ),
                str(row.get("memory_key") or ""),
            ),
        )
        return tuple(_item(row) for row in ranked[:maximum])


def _clean(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        str(value).strip() for value in values if str(value).strip()
    ))


def _query_terms(query: str) -> tuple[str, ...]:
    text = str(query or "").strip()
    if not text:
        return ()
    values: list[str] = []
    for chunk in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_.:-]{2,}", text):
        if re.search(r"[\u4e00-\u9fff]", chunk) and len(chunk) > 3:
            values.append(chunk)
            values.extend(chunk[index:index + 2] for index in range(len(chunk) - 1))
        elif len(chunk) <= 12:
            values.append(chunk)
        else:
            values.append(chunk[:32])
    return tuple(dict.fromkeys(values))[:16]


def _score(
    row: dict[str, Any],
    *,
    kinds: Sequence[str],
    entity_refs: Sequence[str],
    chapter_ids: Sequence[str],
    terms: Sequence[str],
) -> int:
    kind = str(row.get("kind") or "")
    subject_id = str(row.get("subject_id") or "")
    key = str(row.get("memory_key") or "")
    chapter_id = str(row.get("chapter_id") or "")
    haystack = " ".join((
        key,
        subject_id,
        str(row.get("payload_json") or ""),
        str(row.get("source_excerpt") or ""),
    )).casefold()
    score = 0
    if kind in kinds:
        score += 12
    if chapter_id in chapter_ids:
        score += 10
    if subject_id in entity_refs or key in entity_refs:
        score += 24
    score += min(20, sum(2 for term in terms if term.casefold() in haystack))
    return score


def _item(row: dict[str, Any]) -> StoryMemoryRecallItem:
    try:
        payload = json.loads(str(row.get("payload_json") or "{}"))
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {"value": payload}
    return StoryMemoryRecallItem(
        record_id=str(row.get("record_id") or ""),
        book_id=str(row.get("book_id") or ""),
        memory_key=str(row.get("memory_key") or ""),
        kind=str(row.get("kind") or ""),
        subject_id=(
            str(row["subject_id"]) if row.get("subject_id") is not None else None
        ),
        payload=payload,
        version=max(1, int(row.get("version") or 1)),
        source_id=str(row.get("last_source_id") or ""),
        chapter_id=str(row.get("chapter_id") or ""),
        source_excerpt=str(row.get("source_excerpt") or ""),
        update_time=(
            str(row["update_time"]) if row.get("update_time") is not None else None
        ),
    )


__all__ = ["SqliteStoryMemoryRecallRepository"]
