"""Request-scoped mutable state helpers for Writing tools."""

from __future__ import annotations

import json
from typing import Any


def _ensure_chapter_content_cache(ctx: dict) -> dict | None:
    if not isinstance(ctx, dict):
        return None
    if "chapterContentCache" not in ctx:
        ctx["chapterContentCache"] = {}
    return ctx["chapterContentCache"]


def _title_from_writing_catalog(
    chapter_id: str,
    writing_chapters: list[dict],
) -> str:
    key = str(chapter_id or "").strip()
    chapter = next(
        (row for row in (writing_chapters or []) if str(row.get("id")) == key),
        None,
    )
    return str(chapter.get("title") or "").strip() if chapter else ""


def _ensure_read_tool_cache(ctx: dict) -> dict | None:
    if not isinstance(ctx, dict):
        return None
    if "readToolCache" not in ctx:
        ctx["readToolCache"] = {}
    return ctx["readToolCache"]


def _read_tool_cache_get(ctx: dict, key: str) -> str | None:
    cache = _ensure_read_tool_cache(ctx)
    return cache.get(key) if cache else None


def _read_tool_cache_set(ctx: dict, key: str, value: str) -> None:
    cache = _ensure_read_tool_cache(ctx)
    if cache is not None:
        cache[key] = value


def _invalidate_read_tool_cache(ctx: dict) -> None:
    cache = _ensure_read_tool_cache(ctx)
    if cache is not None:
        cache.clear()


CATALOG_TOOL_FAIL_MSG = "失败"


def _ensure_catalog_reject_set(ctx: dict) -> set:
    if "_catalogRejectedChapterIds" not in ctx:
        ctx["_catalogRejectedChapterIds"] = set()
    return ctx["_catalogRejectedChapterIds"]


def _catalog_reject_payload(ctx: dict, gate: dict, cid_raw: Any) -> dict:
    rejected = _ensure_catalog_reject_set(ctx)
    key = ""
    if gate.get("chapterId") and str(gate["chapterId"]).strip():
        key = str(gate["chapterId"]).strip()
    elif cid_raw is not None and str(cid_raw).strip():
        key = str(cid_raw).strip()
    if key:
        rejected.add(key)
    result: dict = {"error": CATALOG_TOOL_FAIL_MSG}
    if key:
        result["chapterId"] = key
    return result


def _json_catalog_reject(ctx: dict, gate: dict, cid_raw: Any) -> str:
    return json.dumps(
        _catalog_reject_payload(ctx, gate, cid_raw),
        ensure_ascii=False,
    )


def _json_batch_catalog_reject(ctx: dict, batch_gate: dict) -> str:
    rejected = _ensure_catalog_reject_set(ctx)
    bad_ids = [
        str(value).strip()
        for value in (batch_gate.get("badIds") or [])
        if str(value).strip()
    ]
    all_duplicates = bool(bad_ids) and all(value in rejected for value in bad_ids)
    if not all_duplicates:
        rejected.update(bad_ids)
    return json.dumps({"error": CATALOG_TOOL_FAIL_MSG}, ensure_ascii=False)


def _parse_args(args_str: str) -> dict:
    try:
        return json.loads(args_str or "{}")
    except Exception:
        return {}


def _runtime_chapter_id(ctx: dict) -> str:
    chapter_id = ctx.get("chapterId")
    return str(chapter_id).strip() if chapter_id is not None else ""


def _runtime_writing_chapters(ctx: dict) -> list[dict]:
    return list(ctx.get("writingChapters") or [])


__all__ = [
    "CATALOG_TOOL_FAIL_MSG",
    "_catalog_reject_payload",
    "_ensure_catalog_reject_set",
    "_ensure_chapter_content_cache",
    "_ensure_read_tool_cache",
    "_invalidate_read_tool_cache",
    "_json_batch_catalog_reject",
    "_json_catalog_reject",
    "_parse_args",
    "_read_tool_cache_get",
    "_read_tool_cache_set",
    "_runtime_chapter_id",
    "_runtime_writing_chapters",
    "_title_from_writing_catalog",
]
