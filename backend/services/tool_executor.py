"""
Tool executor — port of electron/toolExecutor.js.

Executes all Agent tool calls (books, outlines, chapters, characters,
spark ideas, foreshadowing, etc.).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from constants import FORESHADOWING_TYPES, SPARK_IDEA_LAYERS, SPARK_IDEA_LAYER_ORDERED
from database.crud.articles import get_article, save_article
from database.crud.chapters import add_chapter, get_chapters
from database.crud.characters import get_characters
from database.crud.outlines import (
    get_chapter_outlines,
    get_global_outline,
    get_or_create_global_outline,
    get_or_create_writing_outline,
    get_volume_outlines,
    get_writing_outline,
    update_outline,
)
from database.crud.story_background import get_story_background
from dependencies import get_db
from utils.outline_text import collect_text_outline_entries
from utils.text import (
    extract_latest_paragraph,
    extract_text_from_lexical,
    format_characters_as_text,
    format_chapters_as_text,
)
from utils.writing_chapters import get_writable_chapters_for_agent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outline helpers
# ---------------------------------------------------------------------------

async def _load_outline_with_chapters(outline: dict) -> dict:
    chapters = await get_chapters(get_db(), outline["id"])
    return {"outline": outline, "chapters": chapters, "chaptersText": format_chapters_as_text(chapters)}


async def _load_all_outlines_for_book(book_id: str) -> dict:
    db = get_db()
    global_res = await get_global_outline(db, book_id)
    volume_res = await get_volume_outlines(db, book_id)
    chapter_res = await get_chapter_outlines(db, book_id)
    writing_res = await get_or_create_writing_outline(db, book_id)

    global_outline = await _load_outline_with_chapters(global_res) if global_res else None
    volume_outlines = []
    for vol in (volume_res or []):
        chapters_detail = []
        for ch in (vol.get("chapters") or []):
            chapters_detail.append(await _load_outline_with_chapters(ch))
        volume_outlines.append({**vol, "chapters_detail": chapters_detail})
    chapter_outlines = []
    for o in (chapter_res or []):
        chapter_outlines.append(await _load_outline_with_chapters(o))
    writing_outline = await _load_outline_with_chapters(writing_res) if writing_res else None
    return {
        "globalOutline": global_outline,
        "volumeOutlines": volume_outlines,
        "chapterOutlines": chapter_outlines,
        "writingOutline": writing_outline,
    }


async def _get_available_outlines(book_id: str) -> list[dict]:
    db = get_db()
    result: list[dict] = []
    seen: set[str] = set()

    def add_row(row: dict | None) -> None:
        if row is None:
            return
        oid = row.get("id")
        if oid is None or str(oid) in seen:
            return
        seen.add(str(oid))
        result.append(dict(row))

    add_row(await get_global_outline(db, book_id))
    for vol in await get_volume_outlines(db, book_id) or []:
        add_row({k: v for k, v in vol.items() if k != "chapters"})
        for ch in vol.get("chapters") or []:
            add_row(ch)
    for ch in await get_chapter_outlines(db, book_id) or []:
        add_row(ch)
    return result


async def _get_global_outline(book_id: str, max_text_length: int = 32000) -> dict | None:
    row = await get_or_create_global_outline(get_db(), book_id)
    if not row:
        return None
    md_raw = row.get("markdown_content") or ""
    md = md_raw[:max_text_length] + "\n…（已截断）" if isinstance(md_raw, str) and len(md_raw) > max_text_length else md_raw
    return {
        "success": True,
        "bookId": str(book_id),
        "outlineId": str(row["id"]),
        "title": row.get("title") or "总纲",
        "type": row.get("type") or "global",
        "markdown": md or "",
        "hasMarkdown": bool(md_raw and str(md_raw).strip()),
    }


async def _query_outline(book_id: str, outline_ids: list[str], max_text_length: int = 32000) -> dict:
    all_outlines = await _get_available_outlines(book_id)
    filter_set: set[str] | None = set(str(x) for x in outline_ids) if outline_ids else None
    targets = [o for o in all_outlines if filter_set is None or str(o.get("id")) in filter_set]
    text_entries = await collect_text_outline_entries(
        get_db(), book_id, list(filter_set) if filter_set else None,
    )
    by_id_text = {str(e["id"]): e["markdown"] for e in text_entries}
    outlines = []
    for o in targets:
        oid = str(o.get("id"))
        md = by_id_text.get(oid)
        trimmed = md[:max_text_length] + "\n…（已截断）" if isinstance(md, str) and len(md) > max_text_length else md
        outlines.append({
            "id": oid,
            "title": o.get("title", ""),
            "type": o.get("type", ""),
            "xmindData": o.get("xmind_data", "") if isinstance(o.get("xmind_data"), str) else "",
            "markdown": trimmed or "",
            "hasMarkdown": bool(md and str(md).strip()),
        })
    return {"success": True, "bookId": str(book_id), "total": len(outlines), "outlines": outlines}


# ---------------------------------------------------------------------------
# Chapter helpers
# ---------------------------------------------------------------------------

async def _read_chapter_plain_full(chapter_id: str) -> dict | None:
    row = await get_article(get_db(), chapter_id)
    if not row:
        return None
    raw = row.get("content") or ""
    return {"plainTextFull": extract_text_from_lexical(raw) if raw else ""}


async def _list_writing_chapters(book_id: str) -> dict:
    db = get_db()
    writing = await get_or_create_writing_outline(db, book_id)
    if not writing or not writing.get("id"):
        return {"success": False, "error": "未找到写作目录"}
    rows = await get_chapters(db, writing["id"]) or []
    parent_set: set[str] = set()
    for r in rows:
        pid = r.get("parent_id")
        if pid is not None and pid != "":
            parent_set.add(str(pid))
    items = []
    for r in rows:
        rid = str(r.get("id"))
        has_children = rid in parent_set
        items.append({
            "id": rid,
            "title": str(r.get("title") or ""),
            "parentId": None if r.get("parent_id") in (None, "") else str(r["parent_id"]),
            "level": int(r.get("level") or 1),
            "sort": int(r.get("sort") or 0),
            "nodeType": "volume" if has_children else "chapter",
            "hasChildren": has_children,
        })
    return {
        "success": True,
        "bookId": str(book_id),
        "writingOutlineId": str(writing["id"]),
        "total": len(items),
        "items": items,
    }


# ---------------------------------------------------------------------------
# Context / cache helpers
# ---------------------------------------------------------------------------

def _ensure_chapter_content_cache(ctx: dict) -> dict | None:
    if not isinstance(ctx, dict):
        return None
    if "chapterContentCache" not in ctx:
        ctx["chapterContentCache"] = {}
    return ctx["chapterContentCache"]


def _invalidate_chapter_content_cache(ctx: dict, chapter_id: str) -> None:
    cache = _ensure_chapter_content_cache(ctx)
    if cache is not None and chapter_id:
        cache.pop(str(chapter_id).strip(), None)


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
# Book / chapter ID resolution
# ---------------------------------------------------------------------------

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
# Read-cache hit prediction
# ---------------------------------------------------------------------------

def _parse_args(args_str: str) -> dict:
    try:
        return json.loads(args_str or "{}")
    except Exception:
        return {}


def tool_will_hit_read_cache(ctx: dict, tc: dict, writing_chapters: list[dict]) -> bool:
    name = (tc.get("function") or {}).get("name")
    args = _parse_args((tc.get("function") or {}).get("arguments", "{}"))
    allow = ctx.get("subagentAllowedToolNames")
    if isinstance(allow, set) and name and name not in allow:
        return False
    try:
        if name == "getChapterContent":
            resolved = resolve_chapter_id_strict(args, writing_chapters, ctx.get("chapterId"))
            if not resolved["ok"] or not resolved.get("chapterId"):
                return False
            cid = resolved["chapterId"]
            if not chapter_allowed_by_writing_catalog(cid, writing_chapters)["ok"]:
                return False
            cache = _ensure_chapter_content_cache(ctx)
            return bool(cache and str(cid).strip() in cache)
        elif name == "listWritingChapters":
            bid = resolve_book_id_for_tools(ctx, args)
            if not bid:
                return False
            return _read_tool_cache_get(ctx, f"listWritingChapters:{bid}") is not None
        elif name == "batchGetChapterContents":
            nc = _reject_numeric_chapter_ids_for_batch(args.get("chapterIds", []))
            if not nc["ok"]:
                return False
            cids = nc.get("ids", [])
            if not chapters_allowed_by_writing_catalog(cids, writing_chapters)["ok"]:
                return False
            cache = _ensure_chapter_content_cache(ctx)
            return bool(cids and all(cache and str(c).strip() in cache for c in cids))
        elif name == "getBookCharacters":
            bid = resolve_book_id_for_tools(ctx, args)
            ids = args.get("characterIds")
            names_q = args.get("names")
            if (isinstance(ids, list) and ids) or (isinstance(names_q, list) and names_q):
                return False
            return _read_tool_cache_get(ctx, f"getBookCharacters:all:{bid}") is not None
        elif name == "listBookCharacters":
            bid = resolve_book_id_for_tools(ctx, args)
            return _read_tool_cache_get(ctx, f"listBookCharacters:{bid}") is not None
        elif name == "getStoryBackground":
            bid = resolve_book_id_for_tools(ctx, args)
            return _read_tool_cache_get(ctx, f"getStoryBackground:{bid}") is not None
        elif name == "queryOutline":
            bid = resolve_book_id_for_tools(ctx, args)
            if not bid:
                return False
            oids = args.get("outlineIds")
            max_len = args.get("maxTextLength", 32000) if isinstance(args.get("maxTextLength"), (int, float)) else 32000
            oid_key = ",".join(sorted(str(x) for x in oids)) if isinstance(oids, list) else ""
            return _read_tool_cache_get(ctx, f"queryOutline:{bid}:{oid_key}:{max_len}") is not None
        elif name == "getGlobalOutline":
            bid = resolve_book_id_for_tools(ctx, args)
            if not bid:
                return False
            max_len = args.get("maxTextLength", 32000) if isinstance(args.get("maxTextLength"), (int, float)) else 32000
            return _read_tool_cache_get(ctx, f"getGlobalOutline:{bid}:{max_len}") is not None
        elif name == "listOutlines":
            bid = resolve_book_id_for_tools(ctx, args)
            if not bid:
                return False
            return _read_tool_cache_get(ctx, f"listOutlines:{bid}") is not None
        else:
            return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main run_tools
# ---------------------------------------------------------------------------

async def run_tools(
    tool_calls: list[dict],
    ctx: dict,
    send_chunk: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Execute a batch of tool calls. Returns list of ``{tool_call_id, content}``."""
    results: list[dict] = []
    book_id = ctx.get("bookId")
    chapter_id = ctx.get("chapterId")
    runtime_chapter_id = str(chapter_id).strip() if chapter_id is not None else ""
    runtime_chapter_title = str(ctx.get("currentChapterTitle") or "")
    writing_chapters = list(ctx.get("writingChapters") or [])
    runtime_writing_chapters = list(writing_chapters)

    tool_read_cache_mask = [tool_will_hit_read_cache(ctx, tc, writing_chapters) for tc in tool_calls]
    if send_chunk and any(tool_read_cache_mask):
        send_chunk({"toolReadCacheMask": tool_read_cache_mask})

    for i, tc in enumerate(tool_calls):
        name = (tc.get("function") or {}).get("name")
        args = _parse_args((tc.get("function") or {}).get("arguments", "{}"))
        content: str = ""
        tool_from_cache = False

        try:
            allow = ctx.get("subagentAllowedToolNames")
            if isinstance(allow, set) and name and name not in allow:
                content = json.dumps({"error": f"工具「{name}」不在当前 subagent 授权范围，已拒绝执行。"}, ensure_ascii=False)
                results.append({"tool_call_id": tc.get("id"), "content": content})
                if send_chunk:
                    send_chunk({"toolIndexCompleted": i})
                continue

            if name == "getChapterContent":
                resolved = resolve_chapter_id_strict(args, runtime_writing_chapters, runtime_chapter_id)
                if not resolved["ok"] or not resolved.get("chapterId"):
                    content = json.dumps({
                        "error": "getChapterContent 仅支持 chapterId" if resolved.get("reason") == "non_id_locator_forbidden" else CATALOG_TOOL_FAIL_MSG
                    }, ensure_ascii=False)
                else:
                    cid = resolved["chapterId"]
                    title = args.get("title")
                    max_len = args.get("maxTextLength") or 12000
                    gate = chapter_allowed_by_writing_catalog(cid, runtime_writing_chapters)
                    if not gate["ok"]:
                        content = _json_catalog_reject(ctx, gate, cid)
                    else:
                        key = str(cid).strip()
                        cache = _ensure_chapter_content_cache(ctx)
                        if cache is not None and key in cache:
                            entry = cache[key]
                            plain_slice = str(entry.get("plainTextFull") or "")[:max_len]
                            title_res = title or entry.get("titleResolved") or _title_from_writing_catalog(cid, runtime_writing_chapters) or ""
                            content = json.dumps({"chapterId": key, "title": title_res, "plainText": plain_slice or "（本章暂无正文内容）"}, ensure_ascii=False)
                            tool_from_cache = True
                        else:
                            full = await _read_chapter_plain_full(cid)
                            if not full:
                                content = json.dumps({"error": CATALOG_TOOL_FAIL_MSG, "chapterId": cid}, ensure_ascii=False)
                            else:
                                title_resolved = title or _title_from_writing_catalog(cid, runtime_writing_chapters) or ""
                                if cache is not None:
                                    cache[key] = {"plainTextFull": full["plainTextFull"], "titleResolved": title_resolved}
                                plain_text = str(full.get("plainTextFull") or "")[:max_len]
                                content = json.dumps({"chapterId": key, "title": title_resolved, "plainText": plain_text or "（本章暂无正文内容）"}, ensure_ascii=False)

            elif name == "listWritingChapters":
                bid = resolve_book_id_for_tools(ctx, args)
                if not bid:
                    content = json.dumps({"success": False, "error": "缺少有效 bookId，无法获取写作目录章节列表"}, ensure_ascii=False)
                else:
                    ck = f"listWritingChapters:{bid}"
                    hit = _read_tool_cache_get(ctx, ck)
                    if hit is not None:
                        content = hit
                        tool_from_cache = True
                    else:
                        result = await _list_writing_chapters(str(bid))
                        payload = json.dumps(result, ensure_ascii=False)[:24000]
                        _read_tool_cache_set(ctx, ck, payload)
                        content = payload

            elif name == "createWritingChapter":
                bid = resolve_book_id_for_tools(ctx, args)
                if not bid:
                    content = json.dumps({"success": False, "error": "缺少有效 bookId，无法创建章节"}, ensure_ascii=False)
                else:
                    parent_id_raw = args.get("parentId")
                    parent_id = _resolve_create_writing_parent_id(parent_id_raw, runtime_writing_chapters, runtime_chapter_id)
                    if parent_id:
                        gate = _chapter_node_allowed_by_writing_catalog(parent_id, runtime_writing_chapters)
                        if not gate["ok"]:
                            p = _catalog_reject_payload(ctx, gate, parent_id)
                            content = json.dumps({"success": False, "error": p["error"], **({"parentId": p["chapterId"]} if p.get("chapterId") else {})}, ensure_ascii=False)
                            results.append({"tool_call_id": tc.get("id"), "content": content})
                            if send_chunk:
                                send_chunk({"toolIndexCompleted": i})
                            continue
                    try:
                        db = get_db()
                        writing = await get_or_create_writing_outline(db, str(bid))
                        if not writing or not writing.get("id"):
                            content = json.dumps({"success": False, "error": "未找到写作目录，无法创建章节"}, ensure_ascii=False)
                        else:
                            effective_pid = None if not parent_id else parent_id
                            max_num = 0
                            for c in runtime_writing_chapters:
                                pid = None if c.get("parent_id") in (None, "") else str(c["parent_id"])
                                if pid != effective_pid:
                                    continue
                                m = re.match(r"^第(\d+)章", str(c.get("title") or ""))
                                if m:
                                    max_num = max(max_num, int(m.group(1)))
                            new_title = f"第{max_num + 1}章"
                            created = await add_chapter(db, str(writing["id"]), new_title, effective_pid)
                            _invalidate_read_tool_cache(ctx)
                            runtime_chapter_id = str(created["id"])
                            runtime_chapter_title = str(created.get("title") or new_title)
                            runtime_writing_chapters = [
                                c for c in runtime_writing_chapters if str(c.get("id", "")) != runtime_chapter_id
                            ] + [{
                                "id": runtime_chapter_id,
                                "title": runtime_chapter_title,
                                "parent_id": None if created.get("parent_id") in (None, "") else str(created["parent_id"]),
                                "level": int(created.get("level") or 1),
                                "sort": int(created.get("sort") or 0),
                            }]
                            ctx["chapterId"] = runtime_chapter_id
                            ctx["currentChapterTitle"] = runtime_chapter_title
                            ctx["writingChapters"] = runtime_writing_chapters
                            if send_chunk:
                                send_chunk({"chapterCreated": {
                                    "chapterId": runtime_chapter_id,
                                    "title": runtime_chapter_title,
                                    "parentId": None if created.get("parent_id") in (None, "") else str(created["parent_id"]),
                                }})
                            content = json.dumps({
                                "success": True,
                                "bookId": str(bid),
                                "writingOutlineId": str(writing["id"]),
                                "chapter": {
                                    "id": str(created["id"]),
                                    "title": str(created.get("title") or new_title),
                                    "parentId": None if created.get("parent_id") in (None, "") else str(created["parent_id"]),
                                    "level": int(created.get("level") or 1),
                                    "sort": int(created.get("sort") or 0),
                                },
                            }, ensure_ascii=False)
                    except Exception as e:
                        content = json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

            elif name == "batchGetChapterContents":
                cids_raw = args.get("chapterIds", [])
                numeric_check = _reject_numeric_chapter_ids_for_batch(cids_raw)
                if not numeric_check["ok"]:
                    content = json.dumps({"error": "batchGetChapterContents 仅支持 chapterId 字符串数组（listWritingChapters.items[].id）"}, ensure_ascii=False)
                else:
                    cids = numeric_check.get("ids", [])
                    max_len = args.get("maxTextLength") or 12000
                    batch_gate = chapters_allowed_by_writing_catalog(cids, runtime_writing_chapters)
                    if not batch_gate["ok"]:
                        content = _json_batch_catalog_reject(ctx, batch_gate)
                    else:
                        title_map = {str(c.get("id")): c.get("title") for c in runtime_writing_chapters}
                        cache = _ensure_chapter_content_cache(ctx)
                        batch_all_cached = bool(cids)
                        entries = []
                        for cid_raw in cids:
                            cid = str(cid_raw).strip()
                            t = title_map.get(cid)
                            if cache is not None and cid in cache:
                                entry = cache[cid]
                                pt = str(entry.get("plainTextFull") or "")[:max_len]
                                entries.append({"chapterId": cid, "title": t or entry.get("titleResolved") or "", "plainText": pt})
                            else:
                                batch_all_cached = False
                                full = await _read_chapter_plain_full(cid)
                                if not full:
                                    entries.append({"chapterId": cid, "title": t or "", "plainText": ""})
                                else:
                                    title_res = t or _title_from_writing_catalog(cid, runtime_writing_chapters) or ""
                                    if cache is not None:
                                        cache[cid] = {"plainTextFull": full["plainTextFull"], "titleResolved": title_res}
                                    pt = str(full.get("plainTextFull") or "")[:max_len]
                                    entries.append({"chapterId": cid, "title": title_res, "plainText": pt})
                        content = json.dumps([{"chapterId": e["chapterId"], "title": e["title"], "plainText": e["plainText"]} for e in entries], ensure_ascii=False)[:24000]
                        tool_from_cache = batch_all_cached

            elif name == "getBookCharacters":
                bid = resolve_book_id_for_tools(ctx, args)
                char_ids = args.get("characterIds")
                name_queries = args.get("names")
                filtered = (isinstance(char_ids, list) and len(char_ids) > 0) or (isinstance(name_queries, list) and len(name_queries) > 0)
                if not filtered:
                    ck = f"getBookCharacters:all:{bid}"
                    hit = _read_tool_cache_get(ctx, ck)
                    if hit is not None:
                        content = hit
                        tool_from_cache = True
                if not content:
                    chars = await get_characters(get_db(), bid) or []
                    if isinstance(char_ids, list) and char_ids:
                        id_set = set()
                        for x in char_ids:
                            try:
                                id_set.add(int(x))
                            except (ValueError, TypeError):
                                pass
                        chars = [c for c in chars if c.get("id") in id_set]
                    elif isinstance(name_queries, list) and name_queries:
                        needles = [str(n).strip().lower() for n in name_queries if str(n).strip()]
                        if needles:
                            chars = [c for c in chars if any(q in str(c.get("name") or "").lower() for q in needles)]
                    content = format_characters_as_text(chars) or "（暂无人物）"
                    if not filtered:
                        _read_tool_cache_set(ctx, f"getBookCharacters:all:{bid}", content)

            elif name == "listBookCharacters":
                bid = resolve_book_id_for_tools(ctx, args)
                ck = f"listBookCharacters:{bid}"
                hit = _read_tool_cache_get(ctx, ck)
                if hit is not None:
                    content = hit
                    tool_from_cache = True
                else:
                    chars = await get_characters(get_db(), bid) or []
                    lst = [{"id": c.get("id"), "name": re.sub(r"\r?\n", " ", str(c.get("name") or "未命名")).strip() or "未命名"} for c in chars]
                    content = json.dumps(lst, ensure_ascii=False)
                    _read_tool_cache_set(ctx, ck, content)

            elif name == "getStoryBackground":
                bid = resolve_book_id_for_tools(ctx, args)
                ck = f"getStoryBackground:{bid}"
                hit = _read_tool_cache_get(ctx, ck)
                if hit is not None:
                    content = hit
                    tool_from_cache = True
                else:
                    row = await get_story_background(get_db(), bid)
                    content = (row.get("content") if row else None) or "（暂无小说背景）"
                    _read_tool_cache_set(ctx, ck, content)

            elif name == "queryOutline":
                bid = resolve_book_id_for_tools(ctx, args)
                if not bid:
                    content = json.dumps({"success": False, "error": "缺少有效 bookId，无法查询大纲"}, ensure_ascii=False)
                elif args.get("outlineIndex") is not None or args.get("outlineTitle") is not None:
                    content = json.dumps({"success": False, "error": "queryOutline 仅支持 outlineId/outlineIds"}, ensure_ascii=False)
                else:
                    oids = args.get("outlineIds") if isinstance(args.get("outlineIds"), list) else (
                        [str(args["outlineId"]).strip()] if args.get("outlineId") is not None and str(args["outlineId"]).strip() else []
                    )
                    if not oids:
                        content = json.dumps({"success": False, "error": "缺少有效 outlineId/outlineIds"}, ensure_ascii=False)
                    else:
                        max_len = args["maxTextLength"] if isinstance(args.get("maxTextLength"), (int, float)) else 32000
                        oid_key = ",".join(sorted(str(x) for x in oids))
                        ck = f"queryOutline:{bid}:{oid_key}:{max_len}"
                        hit = _read_tool_cache_get(ctx, ck)
                        if hit is not None:
                            content = hit
                            tool_from_cache = True
                        else:
                            result = await _query_outline(bid, oids, max_len)
                            payload = json.dumps(result, ensure_ascii=False)[:24000]
                            _read_tool_cache_set(ctx, ck, payload)
                            content = payload

            elif name == "getGlobalOutline":
                bid = resolve_book_id_for_tools(ctx, args)
                if not bid:
                    content = json.dumps({"success": False, "error": "缺少有效 bookId，无法获取总纲"}, ensure_ascii=False)
                else:
                    max_len = args["maxTextLength"] if isinstance(args.get("maxTextLength"), (int, float)) else 32000
                    ck = f"getGlobalOutline:{bid}:{max_len}"
                    hit = _read_tool_cache_get(ctx, ck)
                    if hit is not None:
                        content = hit
                        tool_from_cache = True
                    else:
                        result = await _get_global_outline(bid, max_len)
                        if not result:
                            content = json.dumps({"success": False, "error": "获取总纲失败"}, ensure_ascii=False)
                        else:
                            content = json.dumps(result, ensure_ascii=False)
                            _read_tool_cache_set(ctx, ck, content)

            elif name == "editGlobalOutline":
                bid = resolve_book_id_for_tools(ctx, args)
                if not bid:
                    content = json.dumps({"success": False, "error": "缺少有效 bookId"}, ensure_ascii=False)
                elif not isinstance(args.get("markdownContent"), str):
                    content = json.dumps({"success": False, "error": "markdownContent 必须为字符串"}, ensure_ascii=False)
                else:
                    try:
                        db = get_db()
                        go = await get_or_create_global_outline(db, bid)
                        if not go or not go.get("id"):
                            content = json.dumps({"success": False, "error": "无法创建或获取总纲"}, ensure_ascii=False)
                        else:
                            saved = await update_outline(db, {"outlineId": str(go["id"]), "markdown_content": args["markdownContent"]})
                            _invalidate_read_tool_cache(ctx)
                            content = json.dumps({
                                "success": True,
                                "bookId": str(bid),
                                "outlineId": str(go["id"]),
                                "title": (saved or {}).get("title") or "总纲",
                                "type": (saved or {}).get("type") or "global",
                                "markdownLength": len(args["markdownContent"]),
                            }, ensure_ascii=False)
                    except Exception as e:
                        content = json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

            elif name == "listOutlines":
                bid = resolve_book_id_for_tools(ctx, args)
                if not bid:
                    content = json.dumps({"success": False, "error": "缺少有效 bookId，无法获取大纲列表"}, ensure_ascii=False)
                else:
                    ck = f"listOutlines:{bid}"
                    hit = _read_tool_cache_get(ctx, ck)
                    if hit is not None:
                        content = hit
                        tool_from_cache = True
                    else:
                        ol = await _get_available_outlines(bid)
                        lst = [{"id": str(o.get("id")), "title": o.get("title", ""), "type": o.get("type", "")} for o in ol]
                        payload = json.dumps({"success": True, "bookId": str(bid), "total": len(lst), "outlines": lst}, ensure_ascii=False)
                        _read_tool_cache_set(ctx, ck, payload)
                        content = payload

            elif name == "updateOutline":
                bid = resolve_book_id_for_tools(ctx, args)
                oid = str(args.get("outlineId") or "").strip()
                if not bid:
                    content = json.dumps({"success": False, "error": "缺少有效 bookId"}, ensure_ascii=False)
                elif not oid:
                    content = json.dumps({"success": False, "error": "缺少有效 outlineId"}, ensure_ascii=False)
                else:
                    update_payload: dict[str, Any] = {"outlineId": oid}
                    for fld in ("title", "xmind_data", "file_path", "markdown_content"):
                        if args.get(fld) is not None:
                            update_payload[fld] = args[fld]
                    if len(update_payload) == 1:
                        content = json.dumps({"success": False, "error": "缺少可更新字段（title/xmind_data/file_path/markdown_content）"}, ensure_ascii=False)
                    else:
                        all_outlines = await _load_all_outlines_for_book(bid)
                        candidates: list[dict] = []
                        if all_outlines.get("globalOutline"):
                            candidates.append(all_outlines["globalOutline"]["outline"])
                        for v in (all_outlines.get("volumeOutlines") or []):
                            candidates.append(v)
                            for cd in (v.get("chapters_detail") or []):
                                candidates.append(cd["outline"])
                        for o in (all_outlines.get("chapterOutlines") or []):
                            candidates.append(o["outline"])
                        if all_outlines.get("writingOutline"):
                            candidates.append(all_outlines["writingOutline"]["outline"])
                        exists = any(str(o.get("id")) == oid for o in candidates if o)
                        if not exists:
                            content = json.dumps({"success": False, "error": "outlineId 不属于当前书籍，或该大纲不存在", "outlineId": oid}, ensure_ascii=False)
                        else:
                            try:
                                saved = await update_outline(get_db(), update_payload)
                                _invalidate_read_tool_cache(ctx)
                                content = json.dumps({
                                    "success": True,
                                    "outlineId": oid,
                                    "title": (saved or {}).get("title", ""),
                                    "type": (saved or {}).get("type", ""),
                                    "updatedFields": [k for k in update_payload if k != "outlineId"],
                                }, ensure_ascii=False)
                            except Exception as e:
                                content = json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

            elif name == "editChapterContent":
                resolved = resolve_chapter_id_strict(args, runtime_writing_chapters, runtime_chapter_id)
                cid = resolved.get("chapterId", "") if resolved["ok"] else ""
                new_content = args.get("content", "") if isinstance(args.get("content"), str) else ""
                if not cid:
                    content = json.dumps({
                        "success": False,
                        "error": "editChapterContent 仅支持 chapterId（chapterTitle/chapterIndex 已禁用）" if resolved.get("reason") == "non_id_locator_forbidden" else "章节定位失败（仅支持 chapterId）",
                    }, ensure_ascii=False)
                else:
                    edit_gate = chapter_allowed_by_writing_catalog(cid, runtime_writing_chapters)
                    if not edit_gate["ok"]:
                        p = _catalog_reject_payload(ctx, edit_gate, cid)
                        content = json.dumps({"success": False, "error": p["error"], **({"chapterId": p["chapterId"]} if p.get("chapterId") else {})}, ensure_ascii=False)
                    else:
                        try:
                            merged_content = new_content
                            if ctx.get("collabWriting") is True and new_content.strip():
                                old_plain = (await _read_chapter_plain_full(cid) or {}).get("plainTextFull") or ""
                                old_trim = str(old_plain).strip()
                                new_trim = new_content.strip()
                                if old_trim and new_trim and not new_trim.startswith(old_trim):
                                    merged_content = f"{old_trim}\n\n{new_trim}"
                            await save_article(get_db(), cid, merged_content)
                            _invalidate_chapter_content_cache(ctx, cid)
                            if send_chunk:
                                latest_paragraph = extract_latest_paragraph(new_content)
                                send_chunk({
                                    "chapterContentUpdated": cid,
                                    **({"collabLatestParagraph": latest_paragraph} if latest_paragraph else {}),
                                })
                            content = json.dumps({"success": True, "message": "章节正文已保存", "chapterId": cid}, ensure_ascii=False)
                        except Exception as e:
                            content = json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

            elif name == "addSparkIdea":
                bid = resolve_book_id_for_tools(ctx, args)
                mem_content = str(args.get("content") or "").strip() if isinstance(args.get("content"), str) else ""
                cid = args.get("chapterId") or runtime_chapter_id or None
                character_id = args.get("characterId")
                layer_num = args.get("layer") if isinstance(args.get("layer"), (int, float)) else None
                layer_valid = layer_num is not None and int(layer_num) in (0, 1, 2, 3)
                if not layer_valid or not mem_content:
                    content = json.dumps({
                        "success": False,
                        "error": "设定内容不能为空" if not mem_content else f"layer 须为 0/1/2/3（{' / '.join(SPARK_IDEA_LAYER_ORDERED)}）",
                    }, ensure_ascii=False)
                else:
                    layer_str = SPARK_IDEA_LAYERS[int(layer_num)]
                    try:
                        from services import memory_service
                        row = await memory_service.add_spark_idea(bid, layer_str, mem_content, cid, character_id)
                        content = json.dumps({"success": True, "message": f"已添加【{layer_str}设定】", "id": row.get("id") if row else None}, ensure_ascii=False)
                    except Exception as e:
                        content = json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

            elif name == "addForeshadowing":
                bid = resolve_book_id_for_tools(ctx, args)
                for_chapter_id = args.get("chapterId")
                for_content = str(args.get("content") or "").strip() if isinstance(args.get("content"), str) else ""
                for_type = args.get("type") or FORESHADOWING_TYPES[0]
                if not for_chapter_id or not for_content:
                    content = json.dumps({
                        "success": False,
                        "error": "缺少 chapterId（埋入章节）" if not for_chapter_id else "伏笔内容不能为空",
                    }, ensure_ascii=False)
                elif for_type not in FORESHADOWING_TYPES:
                    content = json.dumps({"success": False, "error": f"type 须为：{' / '.join(FORESHADOWING_TYPES)}"}, ensure_ascii=False)
                else:
                    try:
                        from services import memory_service
                        row = await memory_service.add_foreshadowing(bid, for_chapter_id, for_content, for_type, None)
                        content = json.dumps({"success": True, "message": f"已添加伏笔【{for_type}】", "id": row.get("id") if row else None}, ensure_ascii=False)
                    except Exception as e:
                        content = json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

            elif name == "searchSparkIdeas":
                bid = resolve_book_id_for_tools(ctx, args)
                query = args.get("query") or ""
                layer = args.get("layer")
                cid = args.get("chapterId") or runtime_chapter_id or None
                limit = args.get("limit") or 15
                try:
                    from services import memory_service
                    parts: list[str] = []
                    want_foreshadowing = not layer or layer == "伏笔"
                    want_layers = None if (not layer or layer == "伏笔") else [layer]
                    mem_limit = max(1, limit - 5) if want_foreshadowing else limit
                    mem_res = await memory_service.get_spark_ideas_for_prompt(
                        bid, query,
                        options={"layers": want_layers, "chapterId": cid, "limit": mem_limit},
                    )
                    if mem_res:
                        by_layer: dict[str, list[str]] = {}
                        for m in mem_res:
                            by_layer.setdefault(m.get("layer", ""), []).append(str(m.get("content") or "").strip())
                        for layer_name in SPARK_IDEA_LAYER_ORDERED:
                            arr = by_layer.get(layer_name)
                            if arr:
                                parts.append(f"【{layer_name}设定】\n" + "\n".join(arr))
                    if want_foreshadowing:
                        for_res = await memory_service.get_foreshadowing_for_prompt(
                            bid, query, options={"limit": 5},
                        )
                        if for_res:
                            lines = [f"- {f.get('content')}（类型：{f.get('type')}，状态：{f.get('status')}）" for f in for_res]
                            parts.append("【伏笔】\n" + "\n".join(lines))
                    content = "\n\n".join(parts) if parts else "（未找到与当前检索相关的本书设定）"
                except Exception as e:
                    content = json.dumps({"error": str(e)}, ensure_ascii=False)

            else:
                content = json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)

        except Exception as err:
            content = json.dumps({"error": str(err)}, ensure_ascii=False)

        results.append({"tool_call_id": tc.get("id"), "content": content})
        if send_chunk:
            chunk: dict = {"toolIndexCompleted": i}
            if tool_from_cache:
                chunk["toolFromCache"] = True
            send_chunk(chunk)

    return results
