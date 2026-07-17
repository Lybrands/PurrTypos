"""Book/chapter scope rules used by Writing-domain tools."""

from __future__ import annotations

import re
from typing import Any


def get_writable_chapters_for_agent(
    writing_chapters: list[dict],
) -> list[dict]:
    """Return leaf chapters, excluding volume/group nodes."""

    chapters = writing_chapters or []
    if not chapters:
        return []
    parent_ids = {
        str(parent_id)
        for chapter in chapters
        if (parent_id := chapter.get("parent_id")) is not None
        and str(parent_id).strip()
    }
    return [
        chapter
        for chapter in chapters
        if str(chapter.get("id", "")) not in parent_ids
    ]


def resolve_book_id_for_tools(ctx: dict, args: dict) -> str | None:
    from_context = (
        str(ctx.get("bookId") or "").strip()
        if isinstance(ctx, dict)
        else ""
    )
    if from_context:
        return from_context
    from_arguments = (
        str(args.get("bookId") or "").strip()
        if isinstance(args, dict)
        else ""
    )
    return from_arguments or None


def resolve_chapter_id_strict(
    args: dict,
    writing_chapters: list[dict],
    current_chapter_id: str | None,
) -> dict:
    by_id = str(args.get("chapterId") or "").strip() if args else ""
    by_title = (
        str(args.get("chapterTitle") or "").strip()
        if isinstance(args.get("chapterTitle"), str)
        else ""
    )
    by_index = args.get("chapterIndex") if args else None

    if by_title or by_index is not None:
        return {"ok": False, "reason": "non_id_locator_forbidden"}

    resolved = by_id
    if not resolved:
        current = str(current_chapter_id or "").strip()
        if not current:
            return {"ok": False, "reason": "locator_required"}
        resolved = current

    gate = chapter_allowed_by_writing_catalog(resolved, writing_chapters)
    if not gate["ok"]:
        return {"ok": False, "reason": "chapter_not_in_catalog"}
    return {"ok": True, "chapterId": resolved}


def chapter_allowed_by_writing_catalog(
    chapter_id: str,
    writing_chapters: list[dict],
) -> dict:
    if not isinstance(writing_chapters, list) or not writing_chapters:
        return {"ok": True}
    key = str(chapter_id or "").strip()
    if not key:
        return {"ok": False}
    writable = get_writable_chapters_for_agent(writing_chapters)
    row = next(
        (chapter for chapter in writable if str(chapter.get("id")) == key),
        None,
    )
    if not row or not str(row.get("title") or "").strip():
        return {"ok": False, "chapterId": key}
    return {"ok": True}


def _chapter_node_allowed_by_writing_catalog(
    chapter_id: str,
    writing_chapters: list[dict],
) -> dict:
    if not isinstance(writing_chapters, list) or not writing_chapters:
        return {"ok": True}
    key = str(chapter_id or "").strip()
    if not key:
        return {"ok": False}
    row = next(
        (
            chapter
            for chapter in writing_chapters
            if str(chapter.get("id", "")).strip() == key
        ),
        None,
    )
    if not row or not str(row.get("title") or "").strip():
        return {"ok": False, "chapterId": key}
    return {"ok": True}


def chapters_allowed_by_writing_catalog(
    chapter_ids: list[str],
    writing_chapters: list[dict],
) -> dict:
    if not isinstance(writing_chapters, list) or not writing_chapters:
        return {"ok": True}
    ids = [
        str(chapter_id).strip()
        for chapter_id in (chapter_ids or [])
        if str(chapter_id).strip()
    ]
    if not ids:
        return {"ok": False, "badIds": []}
    bad_ids = [
        chapter_id
        for chapter_id in ids
        if not chapter_allowed_by_writing_catalog(
            chapter_id,
            writing_chapters,
        )["ok"]
    ]
    if bad_ids:
        return {"ok": False, "badIds": bad_ids}
    return {"ok": True}


def _reject_numeric_chapter_ids_for_batch(chapter_ids: Any) -> dict:
    ids = [
        str(chapter_id).strip()
        for chapter_id in (
            chapter_ids if isinstance(chapter_ids, list) else []
        )
        if str(chapter_id).strip()
    ]
    bad_ids = [chapter_id for chapter_id in ids if re.fullmatch(r"\d+", chapter_id)]
    if bad_ids:
        return {"ok": False, "bad": bad_ids}
    return {"ok": True, "ids": ids}


def _normalize_chapter_parent_id(row: dict | None) -> str | None:
    if not isinstance(row, dict):
        return None
    raw = row.get("parent_id") if row.get("parent_id") is not None else row.get("parentId")
    if raw is None:
        return None
    normalized = str(raw).strip()
    return normalized or None


def _resolve_create_writing_parent_id(
    parent_id_raw: Any,
    writing_chapters: list[dict],
    current_chapter_id: str | None,
) -> str | None:
    explicit = str(parent_id_raw or "").strip()
    if explicit:
        return explicit
    current_id = str(current_chapter_id or "").strip()
    if not current_id or not isinstance(writing_chapters, list) or not writing_chapters:
        return None
    current = next(
        (
            chapter
            for chapter in writing_chapters
            if str(chapter.get("id", "")).strip() == current_id
        ),
        None,
    )
    if not current:
        return None
    current_parent = _normalize_chapter_parent_id(current)
    if current_parent:
        return current_parent
    has_children = any(
        _normalize_chapter_parent_id(chapter) == current_id
        for chapter in writing_chapters
    )
    return current_id if has_children else None


__all__ = [
    "_chapter_node_allowed_by_writing_catalog",
    "_normalize_chapter_parent_id",
    "_reject_numeric_chapter_ids_for_batch",
    "_resolve_create_writing_parent_id",
    "chapter_allowed_by_writing_catalog",
    "chapters_allowed_by_writing_catalog",
    "get_writable_chapters_for_agent",
    "resolve_book_id_for_tools",
    "resolve_chapter_id_strict",
]
