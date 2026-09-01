"""Aggregate semantic memories and versioned story state for one UI surface."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any, Mapping, Sequence

from domains.writing.unified_memory import (
    UnifiedMemoryItem,
    UnifiedMemoryPage,
    UnifiedMemorySource,
    UnifiedMemoryStatus,
)


_STATUS_ORDER = {
    UnifiedMemoryStatus.CONFLICT: 0,
    UnifiedMemoryStatus.PENDING: 1,
    UnifiedMemoryStatus.STALE: 2,
    UnifiedMemoryStatus.ACTIVE: 3,
    UnifiedMemoryStatus.REJECTED: 4,
    UnifiedMemoryStatus.ARCHIVED: 5,
}


def normalize_memory_text(text: str) -> str:
    raw = str(text or "").strip().lower()
    raw = re.sub(r"[\s\r\n\t]+", "", raw)
    return re.sub(r"[，。！？、,.!?;；:：\"'“”‘’（）()\[\]【】《》<>]", "", raw)


class UnifiedMemoryQueryService:
    def __init__(self, db: Any, memory):
        self._db = db
        self._memory = memory

    async def list_items(
        self,
        book_id: str,
        *,
        query: str = "",
        statuses: Sequence[str] = (),
        kinds: Sequence[str] = (),
        sources: Sequence[str] = (),
        limit: int = 120,
    ) -> UnifiedMemoryPage:
        clean_book_id = str(book_id or "").strip()
        if not clean_book_id:
            raise ValueError("book_id is required")
        semantic_states = tuple(dict.fromkeys(
            "disabled" if value in {"archived", "rejected", "conflict", "stale"}
            else value
            for value in statuses
            if value in {"active", "pending", "archived", "rejected", "conflict", "stale"}
        ))
        semantic_rows = await self._memory.list_records(
            book_id=clean_book_id,
            states=semantic_states,
            kinds=kinds,
            query=query,
            limit=500,
        )
        story_rows, review_rows, character_rows = await _gather(
            self._db,
            clean_book_id,
        )
        names = {
            str(row["id"]): str(row.get("name") or row["id"])
            for row in character_rows
        }
        story_items = tuple(
            _story_item(row, names)
            for row in story_rows
        )
        review_group_statuses = _review_group_statuses(review_rows)
        review_items = tuple(
            item
            for row in review_rows
            if (
                item := _review_item(
                    row,
                    names,
                    review_group_statuses.get(str(row.get("delta_id") or "")),
                )
            ) is not None
        )
        authority_keys = {
            str(item.memory_key or "").strip()
            for item in story_items
            if item.memory_key
        }
        authority_text = {
            normalize_memory_text(value)
            for item in story_items
            for value in (item.content, item.summary)
            if normalize_memory_text(value)
        }
        semantic_items: list[UnifiedMemoryItem] = []
        suppressed = 0
        for row in semantic_rows:
            item = _semantic_item(row, clean_book_id)
            normalized = normalize_memory_text(item.content)
            source_id = str(item.source_id or "").strip()
            source_is_story = item.source_type in {
                "story_memory",
                "story_memory_record",
            }
            if (
                (source_is_story and source_id in authority_keys)
                or (normalized and normalized in authority_text)
            ):
                suppressed += 1
                continue
            semantic_items.append(item)

        clean_statuses = {
            UnifiedMemoryStatus(str(value).strip())
            for value in statuses
            if str(value).strip()
        }
        clean_kinds = {str(value).strip() for value in kinds if str(value).strip()}
        clean_sources = {
            UnifiedMemorySource(str(value).strip())
            for value in sources
            if str(value).strip()
        }
        needle = normalize_memory_text(query)
        ordinary = [*story_items, *semantic_items]
        filtered = [
            item
            for item in ordinary
            if (not clean_statuses or item.status in clean_statuses)
            and (not clean_kinds or item.kind in clean_kinds)
            and (not clean_sources or item.source in clean_sources)
            and _matches(item, needle)
        ]
        review_groups: dict[str, list[UnifiedMemoryItem]] = {}
        for item in review_items:
            review_groups.setdefault(str(item.delta_id or item.id), []).append(item)
        for group in review_groups.values():
            # A review delta is one atomic decision. Filters may select the
            # group, but never return only part of it and accidentally submit
            # an incomplete resolution.
            if clean_statuses and not any(
                item.status in clean_statuses for item in group
            ):
                continue
            if clean_sources and not any(
                item.source in clean_sources for item in group
            ):
                continue
            if clean_kinds and not any(item.kind in clean_kinds for item in group):
                continue
            if needle and not any(_matches(item, needle) for item in group):
                continue
            filtered.extend(group)
        filtered.sort(
            key=lambda item: str(item.update_time or item.create_time or ""),
            reverse=True,
        )
        filtered.sort(key=lambda item: (
            _STATUS_ORDER[item.status],
            0 if item.pinned else 1,
        ))
        maximum = max(1, min(500, int(limit)))
        selected = list(filtered[:maximum])
        selected_delta_ids = {
            item.delta_id
            for item in selected
            if item.source is UnifiedMemorySource.STORY_CANDIDATE
            and item.delta_id
        }
        selected_ids = {item.id for item in selected}
        selected.extend(
            item
            for item in filtered[maximum:]
            if item.id not in selected_ids
            and item.delta_id in selected_delta_ids
        )
        return UnifiedMemoryPage(
            items=tuple(selected),
            total=len(filtered),
            suppressed_duplicates=suppressed,
        )


async def _gather(db: Any, book_id: str):
    story_rows = await db.fetch_all(
        "SELECT r.*, s.chapter_id AS source_chapter_id, "
        "s.excerpt AS source_excerpt, s.status AS source_status, "
        "c.title AS chapter_title FROM story_memory_records AS r "
        "LEFT JOIN story_memory_sources AS s ON s.id = r.last_source_id "
        "LEFT JOIN outline_chapters AS c ON c.id = s.chapter_id "
        "WHERE r.book_id = ? ORDER BY r.update_time DESC, r.memory_key ASC",
        [book_id],
    )
    review_rows = await db.fetch_all(
        "SELECT e.*, c.title AS chapter_title "
        "FROM story_memory_evolution_reviews AS e "
        "LEFT JOIN outline_chapters AS c ON c.id = e.chapter_id "
        "WHERE e.book_id = ? ORDER BY e.update_time DESC, e.id DESC",
        [book_id],
    )
    character_rows = await db.fetch_all(
        "SELECT id, name FROM characters WHERE book_id = ?",
        [book_id],
    )
    return story_rows, review_rows, character_rows


def _semantic_item(row: Mapping[str, Any], book_id: str) -> UnifiedMemoryItem:
    state = str(row.get("state") or "active")
    reason = str(row.get("reason") or "")
    mapped_status = {
        "active": UnifiedMemoryStatus.ACTIVE,
        "pending": UnifiedMemoryStatus.PENDING,
        "disabled": {
            "rejected": UnifiedMemoryStatus.REJECTED,
            "conflict": UnifiedMemoryStatus.CONFLICT,
            "stale": UnifiedMemoryStatus.STALE,
        }.get(reason, UnifiedMemoryStatus.ARCHIVED),
    }.get(state, UnifiedMemoryStatus.ARCHIVED)
    memory_id = str(row["id"])
    metadata = dict(row.get("metadata") or {})
    source = dict(row.get("source") or {})
    return UnifiedMemoryItem(
        id=f"semantic:{memory_id}",
        book_id=book_id,
        source=UnifiedMemorySource.SEMANTIC,
        kind=str(metadata.get("kind") or "summary"),
        status=mapped_status,
        content=str(row.get("text") or ""),
        summary=str(metadata.get("summary") or ""),
        structured_data={
            "keywords": str(metadata.get("keywords") or ""),
            "importance": int(metadata.get("importance") or 0),
            "reason": reason or None,
        },
        scope_type=str(metadata.get("scopeType") or "book"),
        scope_id=_optional(metadata.get("scopeId")),
        confidence=float(metadata.get("confidence") or 0.0),
        version=int(row.get("version") or 1),
        pinned=bool(metadata.get("pinned")),
        source_type="inferred" if row.get("inferred") else "direct",
        source_id=_optional(source.get("id")),
        actions=(
            ("activate", "edit", "pin", "archive", "review", "delete", "view_history", "view_links")
            if mapped_status is UnifiedMemoryStatus.PENDING
            else ("edit", "pin", "archive", "delete", "view_history", "view_links")
            if mapped_status is UnifiedMemoryStatus.ACTIVE
            else ("edit", "activate", "delete", "view_history", "view_links")
        ),
        create_time=_optional(row.get("createdAt")),
        update_time=_optional(row.get("updatedAt")),
    )


def _story_item(
    row: Mapping[str, Any],
    character_names: Mapping[str, str],
) -> UnifiedMemoryItem:
    payload = _json_object(row.get("payload_json"))
    kind = str(row.get("kind") or "")
    lifecycle = str(row.get("lifecycle") or "active")
    provenance = str(row.get("provenance_status") or "valid")
    story_status = str(row.get("status") or "confirmed")
    if provenance != "valid":
        status = UnifiedMemoryStatus.STALE
    elif lifecycle != "active" or story_status == "deprecated":
        status = UnifiedMemoryStatus.ARCHIVED
    elif story_status != "confirmed":
        status = UnifiedMemoryStatus.PENDING
    else:
        status = UnifiedMemoryStatus.ACTIVE
    memory_key = str(row.get("memory_key") or "")
    return UnifiedMemoryItem(
        id=f"story:{memory_key}",
        book_id=str(row["book_id"]),
        source=UnifiedMemorySource.STORY_STATE,
        kind=kind,
        status=status,
        content=_story_content(kind, payload, character_names),
        summary=_story_summary(kind, payload),
        structured_data=payload,
        scope_type="story_entity",
        scope_id=_optional(row.get("subject_id")),
        chapter_id=_optional(row.get("source_chapter_id")),
        chapter_title=_optional(row.get("chapter_title")),
        evidence_excerpt=str(row.get("source_excerpt") or ""),
        confidence=1.0,
        version=int(row.get("version") or 1),
        source_type="story_memory",
        source_id=_optional(row.get("last_source_id")),
        memory_key=memory_key,
        actions=("view_history",),
        create_time=_optional(row.get("create_time")),
        update_time=_optional(row.get("update_time")),
    )


def _review_item(
    row: Mapping[str, Any],
    character_names: Mapping[str, str],
    group_status: UnifiedMemoryStatus | None,
) -> UnifiedMemoryItem | None:
    review_status = str(row.get("review_status") or "open")
    resolution = str(row.get("resolution") or "pending")
    if review_status == "resolved" and resolution == "accepted":
        return None
    classification = str(row.get("classification") or "addition")
    status = group_status or UnifiedMemoryStatus.PENDING
    payload = _json_object(row.get("candidate_payload_json"))
    kind = str(row.get("kind") or "")
    delta_id = str(row.get("delta_id") or "")
    target_key = str(row.get("target_key") or "")
    return UnifiedMemoryItem(
        id=f"candidate:{delta_id}:{target_key}",
        book_id=str(row["book_id"]),
        source=UnifiedMemorySource.STORY_CANDIDATE,
        kind=kind,
        status=status,
        content=_story_content(kind, payload, character_names),
        summary=str(row.get("rationale") or ""),
        structured_data={
            **payload,
            "fieldChanges": _json_list(row.get("field_changes_json")),
        },
        scope_type="chapter",
        scope_id=_optional(row.get("chapter_id")),
        chapter_id=_optional(row.get("chapter_id")),
        chapter_title=_optional(row.get("chapter_title")),
        evidence_excerpt=str(row.get("source_excerpt") or ""),
        confidence=float(row.get("candidate_confidence") or 0.0),
        source_type="story_memory_candidate",
        source_id=delta_id,
        delta_id=delta_id,
        target_key=target_key,
        classification=classification,
        recommendation=_optional(row.get("recommendation")),
        risk=_optional(row.get("risk")),
        resolution=resolution,
        actions=("accept", "reject") if review_status == "open" else (),
        create_time=_optional(row.get("create_time")),
        update_time=_optional(row.get("update_time")),
    )


def _story_content(
    kind: str,
    payload: Mapping[str, Any],
    names: Mapping[str, str],
) -> str:
    def character(value: Any) -> str:
        key = str(value or "").strip()
        return names.get(key, key)

    if kind == "character_state":
        return (
            f"{character(payload.get('characterId'))}的"
            f"{payload.get('attribute') or '状态'}：{_display(payload.get('value'))}"
        )
    if kind == "relationship_state":
        return (
            f"{character(payload.get('sourceCharacterId'))}与"
            f"{character(payload.get('targetCharacterId'))}的"
            f"{payload.get('relationType') or '关系'}："
            f"{_display(payload.get('state'))}"
        )
    if kind == "world_fact":
        return str(payload.get("statement") or "世界事实")
    if kind == "timeline_event":
        title = str(payload.get("title") or "时间线事件")
        summary = str(payload.get("summary") or "")
        return f"{title}：{summary}" if summary else title
    if kind == "plot_thread":
        title = str(payload.get("title") or "剧情线")
        summary = str(payload.get("summary") or "")
        return f"{title}：{summary}" if summary else title
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _review_group_statuses(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, UnifiedMemoryStatus]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("delta_id") or ""), []).append(row)
    result: dict[str, UnifiedMemoryStatus] = {}
    for delta_id, decisions in grouped.items():
        statuses = {str(item.get("review_status") or "open") for item in decisions}
        resolutions = {str(item.get("resolution") or "pending") for item in decisions}
        classifications = {
            str(item.get("classification") or "addition") for item in decisions
        }
        if "stale" in statuses:
            result[delta_id] = UnifiedMemoryStatus.STALE
        elif statuses == {"resolved"}:
            result[delta_id] = (
                UnifiedMemoryStatus.REJECTED
                if "rejected" in resolutions
                else UnifiedMemoryStatus.ARCHIVED
            )
        elif classifications.intersection({"conflict", "supersession"}):
            result[delta_id] = UnifiedMemoryStatus.CONFLICT
        else:
            result[delta_id] = UnifiedMemoryStatus.PENDING
    return result


def _story_summary(kind: str, payload: Mapping[str, Any]) -> str:
    if kind == "plot_thread" and payload.get("state"):
        return f"剧情线状态：{payload['state']}"
    if kind == "world_fact" and payload.get("truthMode"):
        return f"事实模式：{payload['truthMode']}"
    if kind == "timeline_event" and payload.get("storyTime"):
        return f"故事时间：{payload['storyTime']}"
    return str(payload.get("note") or payload.get("description") or "")


def _matches(item: UnifiedMemoryItem, needle: str) -> bool:
    if not needle:
        return True
    haystack = normalize_memory_text(" ".join([
        item.content,
        item.summary,
        item.kind,
        item.evidence_excerpt,
        item.chapter_title or "",
        json.dumps(item.structured_data, ensure_ascii=False),
    ]))
    return needle in haystack


def page_to_response(page: UnifiedMemoryPage) -> dict[str, Any]:
    return {
        "items": [asdict(item) for item in page.items],
        "total": page.total,
        "suppressedDuplicates": page.suppressed_duplicates,
    }


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
        return parsed if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


def _display(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


__all__ = ["UnifiedMemoryQueryService", "page_to_response"]
