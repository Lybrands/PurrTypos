"""
请求级工具执行上下文（ctx dict）的操作 helper。

ctx 是单次 ``/ai/chat/stream`` 请求内贯穿所有工具调用的可变 dict，这里集中
所有直接读写 ctx 的小函数：章节内容缓存、读工具缓存、目录拒绝集合、运行时
章节信息读取与参数解析。仅依赖标准库。
"""

from __future__ import annotations

import json
from typing import Any


# ---------------------------------------------------------------------------
# Chapter-content / read-tool caches
# ---------------------------------------------------------------------------

def _ensure_chapter_content_cache(ctx: dict) -> dict | None:
    if not isinstance(ctx, dict):
        return None
    if "chapterContentCache" not in ctx:
        ctx["chapterContentCache"] = {}
    return ctx["chapterContentCache"]


def _title_from_writing_catalog(chapter_id: str, writing_chapters: list[dict]) -> str:
    key = str(chapter_id or "").strip()
    c = next((x for x in (writing_chapters or []) if str(x.get("id")) == key), None)
    return str(c.get("title") or "").strip() if c else ""


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


# ---------------------------------------------------------------------------
# Catalog reject helpers
# ---------------------------------------------------------------------------

CATALOG_TOOL_FAIL_MSG = "失败"


def _ensure_catalog_reject_set(ctx: dict) -> set:
    if "_catalogRejectedChapterIds" not in ctx:
        ctx["_catalogRejectedChapterIds"] = set()
    return ctx["_catalogRejectedChapterIds"]


def _catalog_reject_payload(ctx: dict, gate: dict, cid_raw: Any) -> dict:
    s = _ensure_catalog_reject_set(ctx)
    key = ""
    if gate.get("chapterId") and str(gate["chapterId"]).strip():
        key = str(gate["chapterId"]).strip()
    elif cid_raw is not None and str(cid_raw).strip():
        key = str(cid_raw).strip()
    if key:
        s.add(key)
    result: dict = {"error": CATALOG_TOOL_FAIL_MSG}
    if key:
        result["chapterId"] = key
    return result


def _json_catalog_reject(ctx: dict, gate: dict, cid_raw: Any) -> str:
    p = _catalog_reject_payload(ctx, gate, cid_raw)
    return json.dumps(p, ensure_ascii=False)


def _json_batch_catalog_reject(ctx: dict, batch_gate: dict) -> str:
    s = _ensure_catalog_reject_set(ctx)
    bad_ids = [str(x).strip() for x in (batch_gate.get("badIds") or []) if str(x).strip()]
    all_dup = bool(bad_ids) and all(i in s for i in bad_ids)
    if not all_dup:
        for i in bad_ids:
            s.add(i)
    return json.dumps({"error": CATALOG_TOOL_FAIL_MSG}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Runtime info / args
# ---------------------------------------------------------------------------

def _parse_args(args_str: str) -> dict:
    try:
        return json.loads(args_str or "{}")
    except Exception:
        return {}


def _runtime_chapter_id(ctx: dict) -> str:
    cid = ctx.get("chapterId")
    return str(cid).strip() if cid is not None else ""


def _runtime_writing_chapters(ctx: dict) -> list[dict]:
    return list(ctx.get("writingChapters") or [])
