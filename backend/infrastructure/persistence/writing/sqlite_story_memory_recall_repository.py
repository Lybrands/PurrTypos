"""SQLite adapter for broad, evidence-valid Story Memory candidate recall."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

from domains.writing.repositories import StoryMemoryRecallItem

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


_BASE_SELECT = (
    "SELECT r.id AS record_id, r.book_id, r.memory_key, r.kind, "
    "r.subject_id, r.payload_json, r.version, r.last_source_id, "
    "r.update_time, s.chapter_id, s.excerpt AS source_excerpt "
    "FROM story_memory_records AS r "
    "JOIN story_memory_sources AS s ON s.id = r.last_source_id "
)
_BASE_WHERE = (
    "r.book_id = ? AND r.status = 'confirmed' "
    "AND r.lifecycle = 'active' AND r.provenance_status = 'valid' "
    "AND s.status = 'valid'"
)
_RRF_CONSTANT = 60
_FIELD_PREFIX = re.compile(
    r"^(?:任务目标|操作|目标|具体要求|约束|必须保留|交付物|所需证据类型)\s*[:：]\s*"
)
_CLAUSE_SPLIT = re.compile(r"[\n\r。！？!?；;，,、]+")
_TOKEN_CHUNK = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_.:-]{2,}")


@dataclass(frozen=True, slots=True)
class _ResolvedEntity:
    entity_type: str
    entity_id: str


class SqliteStoryMemoryRecallRepository:
    """Generate a high-recall candidate set through independent channels.

    Authority and evidence validity remain hard filters. Type, entity, chapter,
    FTS and lexical signals each produce their own bounded ranking; RRF merges
    those rankings without pretending their raw scores are comparable.
    """

    def __init__(self, db: DatabaseConnection):
        self._db = db

    async def get_current_by_ids(
        self,
        book_id: str,
        record_ids: Sequence[str],
    ) -> tuple[StoryMemoryRecallItem, ...]:
        normalized_book_id = str(book_id or "").strip()
        clean_ids = _clean(record_ids)
        if not normalized_book_id or not clean_ids:
            return ()
        marks = ",".join("?" for _ in clean_ids)
        rows = await self._db.fetch_all(
            _BASE_SELECT
            + f"WHERE {_BASE_WHERE} AND r.id IN ({marks}) "
            + "ORDER BY r.memory_key ASC",
            [normalized_book_id, *clean_ids],
        )
        return tuple(
            _item(row, candidate_channels=(), retrieval_score=0.0)
            for row in rows
        )

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
        maximum = max(1, min(160, int(limit)))
        if not normalized_book_id:
            return ()

        clean_kinds = _clean(kinds)
        clean_planner_kinds = _clean(planner_kinds)
        contract_kinds = tuple(
            value for value in clean_kinds if value not in clean_planner_kinds
        )
        clean_entities = _clean(entity_refs)
        clean_chapters = _clean(chapter_ids)
        terms = _query_terms(query, maximum=96)
        resolved_entities = await self._resolve_entities(
            normalized_book_id,
            clean_entities,
            query,
        )
        channel_limit = min(120, max(24, maximum))
        rankings: list[tuple[str, list[dict[str, Any]]]] = []

        if clean_planner_kinds:
            rankings.append((
                "planner_kind",
                await self._fetch_kinds(
                    normalized_book_id,
                    clean_planner_kinds,
                    channel_limit,
                ),
            ))
        if contract_kinds:
            rankings.append((
                "contract_kind",
                await self._fetch_kinds(
                    normalized_book_id,
                    contract_kinds,
                    channel_limit,
                ),
            ))
        elif clean_kinds and not clean_planner_kinds:
            rankings.append((
                "contract_kind",
                await self._fetch_kinds(
                    normalized_book_id,
                    clean_kinds,
                    channel_limit,
                ),
            ))
        if clean_entities or resolved_entities:
            rankings.append((
                "entity",
                await self._fetch_entities(
                    normalized_book_id,
                    clean_entities,
                    resolved_entities,
                    channel_limit,
                ),
            ))
        if clean_chapters:
            rankings.append((
                "chapter",
                await self._fetch_chapters(
                    normalized_book_id,
                    clean_chapters,
                    channel_limit,
                ),
            ))
        if terms:
            fts_rows = await self._fetch_fts(
                normalized_book_id,
                terms,
                channel_limit,
            )
            if fts_rows:
                rankings.append(("fts", fts_rows))
            like_rows = await self._fetch_like(
                normalized_book_id,
                _evenly_sample(terms, min(32, len(terms))),
                channel_limit,
            )
            if like_rows:
                rankings.append(("lexical", like_rows))
        if not rankings:
            return ()

        row_by_id: dict[str, dict[str, Any]] = {}
        channels_by_id: dict[str, list[str]] = {}
        fused_score: dict[str, float] = {}
        for channel, rows in rankings:
            seen_in_channel: set[str] = set()
            for rank, row in enumerate(rows, start=1):
                record_id = str(row.get("record_id") or "")
                if not record_id or record_id in seen_in_channel:
                    continue
                seen_in_channel.add(record_id)
                row_by_id.setdefault(record_id, row)
                channels_by_id.setdefault(record_id, []).append(channel)
                fused_score[record_id] = (
                    fused_score.get(record_id, 0.0)
                    + 1.0 / (_RRF_CONSTANT + rank)
                )

        ranked_ids = sorted(
            row_by_id,
            key=lambda record_id: (
                -fused_score.get(record_id, 0.0),
                -_legacy_tiebreak_score(
                    row_by_id[record_id],
                    kinds=clean_kinds,
                    entity_refs=clean_entities,
                    chapter_ids=clean_chapters,
                    terms=terms,
                ),
                str(row_by_id[record_id].get("memory_key") or ""),
            ),
        )
        return tuple(
            _item(
                row_by_id[record_id],
                candidate_channels=tuple(channels_by_id.get(record_id, ())),
                retrieval_score=fused_score.get(record_id, 0.0),
            )
            for record_id in ranked_ids[:maximum]
        )

    async def _fetch_kinds(
        self,
        book_id: str,
        kinds: Sequence[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in kinds)
        return await self._db.fetch_all(
            _BASE_SELECT
            + f"WHERE {_BASE_WHERE} AND r.kind IN ({marks}) "
            + "ORDER BY r.update_time DESC, r.memory_key ASC LIMIT ?",
            [book_id, *kinds, limit],
        )

    async def _fetch_entities(
        self,
        book_id: str,
        raw_refs: Sequence[str],
        resolved: Sequence[_ResolvedEntity],
        limit: int,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if raw_refs:
            marks = ",".join("?" for _ in raw_refs)
            clauses.append(
                f"(r.subject_id IN ({marks}) OR r.memory_key IN ({marks}))"
            )
            params.extend([*raw_refs, *raw_refs])
        if resolved:
            link_clauses: list[str] = []
            for entity in resolved:
                link_clauses.append(
                    "(l.entity_id = ? AND "
                    "(l.entity_type = ? OR l.entity_type = 'generic'))"
                )
                params.extend([entity.entity_id, entity.entity_type])
            clauses.append(
                "EXISTS (SELECT 1 FROM story_memory_entity_links AS l "
                "WHERE l.record_id = r.id AND ("
                + " OR ".join(link_clauses)
                + "))"
            )
        return await self._db.fetch_all(
            _BASE_SELECT
            + f"WHERE {_BASE_WHERE} AND ("
            + " OR ".join(clauses)
            + ") ORDER BY r.update_time DESC, r.memory_key ASC LIMIT ?",
            [book_id, *params, limit],
        )

    async def _fetch_chapters(
        self,
        book_id: str,
        chapter_ids: Sequence[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in chapter_ids)
        return await self._db.fetch_all(
            _BASE_SELECT
            + f"WHERE {_BASE_WHERE} AND s.chapter_id IN ({marks}) "
            + "ORDER BY r.update_time DESC, r.memory_key ASC LIMIT ?",
            [book_id, *chapter_ids, limit],
        )

    async def _fetch_fts(
        self,
        book_id: str,
        terms: Sequence[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        searchable = tuple(value for value in terms if len(value) >= 3)
        if not searchable:
            return []
        expression = " OR ".join(
            '"' + value.replace('"', '""') + '"' for value in searchable
        )
        try:
            return await self._db.fetch_all(
                "SELECT r.id AS record_id, r.book_id, r.memory_key, r.kind, "
                "r.subject_id, r.payload_json, r.version, r.last_source_id, "
                "r.update_time, s.chapter_id, s.excerpt AS source_excerpt, "
                "bm25(story_memory_fts) AS fts_rank "
                "FROM story_memory_fts "
                "JOIN story_memory_records AS r "
                "ON r.id = story_memory_fts.record_id "
                "JOIN story_memory_sources AS s ON s.id = r.last_source_id "
                "WHERE story_memory_fts MATCH ? AND "
                + _BASE_WHERE
                + " ORDER BY fts_rank ASC, r.update_time DESC LIMIT ?",
                [expression, book_id, limit],
            )
        except Exception:
            # FTS5 can be unavailable in a custom SQLite build. The lexical
            # channel below remains a deterministic compatibility fallback.
            return []

    async def _fetch_like(
        self,
        book_id: str,
        terms: Sequence[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for term in terms:
            clauses.append(
                "(r.memory_key LIKE ? OR r.subject_id LIKE ? OR "
                "r.payload_json LIKE ? OR s.excerpt LIKE ?)"
            )
            like = f"%{term}%"
            params.extend([like, like, like, like])
        if not clauses:
            return []
        return await self._db.fetch_all(
            _BASE_SELECT
            + f"WHERE {_BASE_WHERE} AND ("
            + " OR ".join(clauses)
            + ") ORDER BY r.update_time DESC, r.memory_key ASC LIMIT ?",
            [book_id, *params, limit],
        )

    async def _resolve_entities(
        self,
        book_id: str,
        explicit_refs: Sequence[str],
        query: str,
    ) -> tuple[_ResolvedEntity, ...]:
        rows = await self._db.fetch_all(
            "SELECT CAST(id AS TEXT) AS id, 'character' AS entity_type, "
            "name, tags FROM characters WHERE book_id = ? "
            "UNION ALL "
            "SELECT CAST(id AS TEXT) AS id, 'setting' AS entity_type, "
            "name, tags FROM setting_entities WHERE book_id = ?",
            [book_id, book_id],
        )
        normalized_refs = {
            _normalize_entity_text(value) for value in explicit_refs if value
        }
        normalized_query = _normalize_entity_text(query)
        resolved: list[_ResolvedEntity] = []
        for row in rows:
            entity_id = str(row.get("id") or "").strip()
            entity_type = str(row.get("entity_type") or "").strip()
            names = _entity_names(row)
            direct_id = (
                _normalize_entity_text(entity_id) in normalized_refs
                or _normalize_entity_text(
                    f"{entity_type}:{entity_id}"
                ) in normalized_refs
            )
            explicit_name = bool(normalized_refs.intersection(names))
            mentioned = any(
                len(value) >= 2 and value in normalized_query for value in names
            )
            if direct_id or explicit_name or mentioned:
                resolved.append(_ResolvedEntity(entity_type, entity_id))
        return tuple(dict.fromkeys(resolved))


def _clean(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        str(value).strip() for value in values if str(value).strip()
    ))


def _query_terms(query: str, *, maximum: int = 96) -> tuple[str, ...]:
    """Create bounded terms while preserving coverage across all task fields."""

    text = str(query or "").strip()
    if not text or maximum <= 0:
        return ()
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
    if not groups:
        return ()

    quota = max(1, maximum // len(groups))
    selected: list[str] = []
    leftovers: list[tuple[str, ...]] = []
    for group in groups:
        sampled = _evenly_sample(group, min(len(group), quota))
        selected.extend(sampled)
        sampled_set = set(sampled)
        leftovers.append(tuple(value for value in group if value not in sampled_set))
    if len(selected) < maximum:
        cursor = 0
        while len(selected) < maximum and any(leftovers):
            index = cursor % len(leftovers)
            if leftovers[index]:
                selected.append(leftovers[index][0])
                leftovers[index] = leftovers[index][1:]
            cursor += 1
    return tuple(dict.fromkeys(selected))[:maximum]


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


def _normalize_entity_text(value: object) -> str:
    return "".join(str(value or "").casefold().split())


def _entity_names(row: dict[str, Any]) -> frozenset[str]:
    # Tags describe traits/categories and are not guaranteed aliases. Treating
    # them as names would resolve broad words such as "警察" to a specific
    # character. Alias support should use an explicit alias registry.
    raw_values = [str(row.get("name") or "")]
    return frozenset(
        normalized
        for value in raw_values
        if (normalized := _normalize_entity_text(value))
    )


def _legacy_tiebreak_score(
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


def _item(
    row: dict[str, Any],
    *,
    candidate_channels: tuple[str, ...],
    retrieval_score: float,
) -> StoryMemoryRecallItem:
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
        candidate_channels=candidate_channels,
        retrieval_score=(
            0.0 if not math.isfinite(retrieval_score) else retrieval_score
        ),
    )


__all__ = ["SqliteStoryMemoryRecallRepository"]
