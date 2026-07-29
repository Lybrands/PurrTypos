"""Resolve and enforce the immutable narrative scope of a book adaptation."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from exceptions import AppError
from utils.book_structure import get_ordered_leaf_chapters


WHOLE_BOOK = "whole_book"
RESTRICTED_MODES = frozenset({
    "first_chapters",
    "first_volumes",
    "selected_chapters",
    "selected_volumes",
})
VALID_MODES = frozenset({WHOLE_BOOK, *RESTRICTED_MODES})


def parse_source_scope(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        parsed = dict(value)
    else:
        try:
            parsed = json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = {}
    mode = str(parsed.get("mode") or WHOLE_BOOK)
    if mode not in VALID_MODES:
        mode = WHOLE_BOOK
    return {
        "schemaVersion": 1,
        "mode": mode,
        "requestedCount": _positive_int(parsed.get("requestedCount")),
        "chapterIds": _string_list(parsed.get("chapterIds")),
        "volumeIds": _string_list(parsed.get("volumeIds")),
        "chapters": (
            list(parsed.get("chapters") or [])
            if isinstance(parsed.get("chapters"), list)
            else []
        ),
    }


def is_restricted_source_scope(project_or_scope: Mapping[str, Any]) -> bool:
    scope = (
        parse_source_scope(project_or_scope.get("source_scope"))
        if "source_scope" in project_or_scope
        else parse_source_scope(project_or_scope.get("source_scope_json"))
        if "source_scope_json" in project_or_scope
        else parse_source_scope(project_or_scope)
    )
    return scope["mode"] in RESTRICTED_MODES


async def resolve_source_scope(
    db,
    book_id: str,
    requested: Mapping[str, Any] | None,
) -> dict[str, Any]:
    raw = dict(requested or {})
    mode = str(raw.get("mode") or WHOLE_BOOK).strip()
    if mode not in VALID_MODES:
        raise AppError("不支持的原作改编范围", 422)
    chapters = await get_ordered_leaf_chapters(db, book_id)
    if not chapters:
        raise AppError("来源作品还没有可用于改编的正文章节", 409)
    if mode == WHOLE_BOOK:
        return parse_source_scope({"mode": WHOLE_BOOK})

    by_id = {str(item["id"]): item for item in chapters}
    ordered_volume_ids = list(dict.fromkeys(
        str(item.get("volume_id") or "")
        for item in chapters
        if str(item.get("volume_id") or "")
    ))
    count = _positive_int(raw.get("count"))
    requested_chapter_ids = _string_list(raw.get("chapterIds"))
    requested_volume_ids = _string_list(raw.get("volumeIds"))

    selected: list[dict[str, Any]]
    selected_volume_ids: list[str] = []
    if mode == "first_chapters":
        if count is None:
            raise AppError("按前几章改编时必须提供正整数 count", 422)
        if count > len(chapters):
            raise AppError(f"来源作品只有 {len(chapters)} 个正文章节", 422)
        selected = chapters[:count]
    elif mode == "first_volumes":
        if count is None:
            raise AppError("按前几卷改编时必须提供正整数 count", 422)
        if not ordered_volume_ids:
            raise AppError("来源作品没有可用于按卷选择的分卷结构", 422)
        if count > len(ordered_volume_ids):
            raise AppError(f"来源作品只有 {len(ordered_volume_ids)} 个非空卷", 422)
        selected_volume_ids = ordered_volume_ids[:count]
        selected = [
            item for item in chapters
            if str(item.get("volume_id") or "") in selected_volume_ids
        ]
    elif mode == "selected_chapters":
        if not requested_chapter_ids:
            raise AppError("指定章节改编时必须选择至少一个章节", 422)
        missing = set(requested_chapter_ids).difference(by_id)
        if missing:
            raise AppError("选择的章节不属于来源作品", 422)
        selected_set = set(requested_chapter_ids)
        selected = [item for item in chapters if str(item["id"]) in selected_set]
    else:
        if not requested_volume_ids:
            raise AppError("指定卷改编时必须选择至少一个卷", 422)
        missing = set(requested_volume_ids).difference(ordered_volume_ids)
        if missing:
            raise AppError("选择的卷不属于来源作品或卷内没有章节", 422)
        selected_volume_ids = [
            item for item in ordered_volume_ids
            if item in set(requested_volume_ids)
        ]
        selected = [
            item for item in chapters
            if str(item.get("volume_id") or "") in selected_volume_ids
        ]

    snapshot = [{
        "id": str(item["id"]),
        "title": str(item.get("title") or ""),
        "index": int(item.get("index") or 0),
        "volumeId": str(item.get("volume_id") or "") or None,
        "volumeTitle": str(item.get("volume_title") or "") or None,
    } for item in selected]
    return {
        "schemaVersion": 1,
        "mode": mode,
        "requestedCount": count,
        "chapterIds": [item["id"] for item in snapshot],
        "volumeIds": selected_volume_ids,
        "chapters": snapshot,
    }


async def scoped_chapters(
    db,
    project: Mapping[str, Any],
) -> list[dict[str, Any]]:
    book_id = str(project.get("source_book_id") or "").strip()
    if not book_id:
        return []
    scope = parse_source_scope(
        project.get("source_scope")
        if project.get("source_scope") is not None
        else project.get("source_scope_json")
    )
    chapters = await get_ordered_leaf_chapters(db, book_id)
    if scope["mode"] == WHOLE_BOOK:
        return chapters
    allowed = set(scope["chapterIds"])
    return [item for item in chapters if str(item["id"]) in allowed]


async def scoped_outline_ids(
    db,
    project: Mapping[str, Any],
) -> set[str]:
    if not is_restricted_source_scope(project):
        return set()
    scope = parse_source_scope(
        project.get("source_scope")
        if project.get("source_scope") is not None
        else project.get("source_scope_json")
    )
    writing_ids = [*scope["chapterIds"], *scope["volumeIds"]]
    if not writing_ids:
        return set()
    result: set[str] = set()
    for offset in range(0, len(writing_ids), 500):
        batch = writing_ids[offset:offset + 500]
        placeholders = ",".join("?" for _ in batch)
        rows = await db.fetch_all(
            "SELECT id FROM outlines WHERE book_id = ? "
            f"AND writing_chapter_id IN ({placeholders})",
            [project["source_book_id"], *batch],
        )
        result.update(str(row["id"]) for row in rows)
    return result


def source_scope_summary(scope_value: object) -> dict[str, Any]:
    scope = parse_source_scope(scope_value)
    chapters = scope["chapters"]
    whole_book = scope["mode"] == WHOLE_BOOK
    return {
        "mode": scope["mode"],
        "requestedCount": scope["requestedCount"],
        "chapterCount": None if whole_book else len(scope["chapterIds"]),
        "volumeCount": None if whole_book else len(scope["volumeIds"]),
        "firstChapter": chapters[0] if chapters else None,
        "lastChapter": chapters[-1] if chapters else None,
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item).strip()
    ))


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
