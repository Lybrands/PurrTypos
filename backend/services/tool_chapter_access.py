"""
Book / chapter ID 解析与写作目录鉴权。

Agent 工具只允许操作"写作目录"里存在且有标题的章节；这里集中所有
ID 归一化、目录白名单 gate 与 parent_id 推断逻辑。
"""

from __future__ import annotations

import re
from typing import Any

from utils.writing_chapters import get_writable_chapters_for_agent


def resolve_book_id_for_tools(ctx: dict, args: dict) -> str | None:
    from_ctx = str(ctx.get("bookId") or "").strip() if isinstance(ctx, dict) else ""
    if from_ctx:
        return from_ctx
    from_args = str(args.get("bookId") or "").strip() if isinstance(args, dict) else ""
    return from_args or None


def resolve_chapter_id_strict(
    args: dict, writing_chapters: list[dict], current_chapter_id: str | None,
) -> dict:
    by_id = str(args.get("chapterId") or "").strip() if args else ""
    by_title = str(args.get("chapterTitle") or "").strip() if isinstance(args.get("chapterTitle"), str) else ""
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


def chapter_allowed_by_writing_catalog(chapter_id: str, writing_chapters: list[dict]) -> dict:
    if not isinstance(writing_chapters, list) or not writing_chapters:
        return {"ok": True}
    key = str(chapter_id or "").strip()
    if not key:
        return {"ok": False}
    writable = get_writable_chapters_for_agent(writing_chapters)
    row = next((c for c in writable if str(c.get("id")) == key), None)
    if not row:
        return {"ok": False, "chapterId": key}
    if not str(row.get("title") or "").strip():
        return {"ok": False, "chapterId": key}
    return {"ok": True}


def _chapter_node_allowed_by_writing_catalog(chapter_id: str, writing_chapters: list[dict]) -> dict:
    if not isinstance(writing_chapters, list) or not writing_chapters:
        return {"ok": True}
    key = str(chapter_id or "").strip()
    if not key:
        return {"ok": False}
    row = next((c for c in writing_chapters if str(c.get("id", "")).strip() == key), None)
    if not row:
        return {"ok": False, "chapterId": key}
    if not str(row.get("title") or "").strip():
        return {"ok": False, "chapterId": key}
    return {"ok": True}


def chapters_allowed_by_writing_catalog(chapter_ids: list[str], writing_chapters: list[dict]) -> dict:
    if not isinstance(writing_chapters, list) or not writing_chapters:
        return {"ok": True}
    ids = [str(x).strip() for x in (chapter_ids or []) if str(x).strip()]
    if not ids:
        return {"ok": False, "badIds": []}
    bad = [i for i in ids if not chapter_allowed_by_writing_catalog(i, writing_chapters)["ok"]]
    if bad:
        return {"ok": False, "badIds": bad}
    return {"ok": True}


def _reject_numeric_chapter_ids_for_batch(cids: Any) -> dict:
    ids = [str(x).strip() for x in (cids if isinstance(cids, list) else []) if str(x).strip()]
    bad = [s for s in ids if re.fullmatch(r"\d+", s)]
    if bad:
        return {"ok": False, "bad": bad}
    return {"ok": True, "ids": ids}


def _normalize_chapter_parent_id(row: dict | None) -> str | None:
    if not isinstance(row, dict):
        return None
    raw = row.get("parent_id") if row.get("parent_id") is not None else row.get("parentId")
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _resolve_create_writing_parent_id(
    parent_id_raw: Any, writing_chapters: list[dict], current_chapter_id: str | None,
) -> str | None:
    explicit = str(parent_id_raw or "").strip()
    if explicit:
        return explicit
    current_id = str(current_chapter_id or "").strip()
    if not current_id or not isinstance(writing_chapters, list) or not writing_chapters:
        return None
    current = next((c for c in writing_chapters if str(c.get("id", "")).strip() == current_id), None)
    if not current:
        return None
    cp = _normalize_chapter_parent_id(current)
    if cp:
        return cp
    has_children = any(_normalize_chapter_parent_id(c) == current_id for c in writing_chapters)
    if has_children:
        return current_id
    return None
