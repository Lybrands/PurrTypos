"""
Tool executor — port of electron/toolExecutor.js.

执行所有 Agent 工具调用（books / outlines / chapters / characters / spark idea /
foreshadowing 等）。

历史上这里是一个 1000+ 行的 if/elif 大锅饭。现在改成两张注册表：

* ``TOOL_HANDLERS``     —— ``name -> async handler(ctx, args, send_chunk) -> ToolResult``
* ``CACHE_PREDICTORS``  —— ``name -> bool predictor(ctx, args)``，预测调用是否会命中
                            读缓存（用于前端在工具执行前就标灰"将走缓存"）

新增工具 = 新增一个 ``@tool("xxx")`` handler（如果会读缓存，再加一个
``@cache_predictor("xxx")``）。``run_tools`` 与 ``tool_will_hit_read_cache``
都是纯 dispatch，不再需要改动。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from constants import FORESHADOWING_TYPES, SPARK_IDEA_LAYERS, SPARK_IDEA_LAYER_ORDERED
from database.crud.articles import get_article
from database.crud.book_style import get_book_style
from database.crud.chapters import add_chapter, get_chapters
from database.crud.characters import get_characters
from database.crud.outlines import (
    get_chapter_outlines,
    get_global_outline,
    get_or_create_global_outline,
    get_or_create_writing_outline,
    get_volume_outlines,
    update_outline,
)
from database.crud.story_background import get_story_background
from dependencies import get_db
from utils.outline_text import collect_text_outline_entries
from utils.text import (
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


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """工具执行结果。``content`` 是要喂回给 LLM 的字符串；
    ``from_cache`` 仅用于前端流式反馈"该工具走了读缓存"。"""
    content: str
    from_cache: bool = False


ToolHandler = Callable[[dict, dict, Callable[[dict], None] | None], Awaitable[ToolResult]]
CachePredictor = Callable[[dict, dict], bool]

TOOL_HANDLERS: dict[str, ToolHandler] = {}
CACHE_PREDICTORS: dict[str, CachePredictor] = {}


def tool(name: str) -> Callable[[ToolHandler], ToolHandler]:
    def deco(fn: ToolHandler) -> ToolHandler:
        TOOL_HANDLERS[name] = fn
        return fn
    return deco


def cache_predictor(name: str) -> Callable[[CachePredictor], CachePredictor]:
    def deco(fn: CachePredictor) -> CachePredictor:
        CACHE_PREDICTORS[name] = fn
        return fn
    return deco


# ---------------------------------------------------------------------------
# Cache predictors —— 提前判定是否会命中读缓存（用于 UI 标灰）
#
# 这部分逻辑必须与对应 handler 的"是否回写缓存"严格一致，否则会出现
# UI 显示"将走缓存"但实际还要去查库的不一致。
# ---------------------------------------------------------------------------


@cache_predictor("getChapterContent")
def _predict_get_chapter_content(ctx: dict, args: dict) -> bool:
    writing_chapters = _runtime_writing_chapters(ctx)
    resolved = resolve_chapter_id_strict(args, writing_chapters, ctx.get("chapterId"))
    if not resolved["ok"] or not resolved.get("chapterId"):
        return False
    cid = resolved["chapterId"]
    if not chapter_allowed_by_writing_catalog(cid, writing_chapters)["ok"]:
        return False
    cache = _ensure_chapter_content_cache(ctx)
    return bool(cache and str(cid).strip() in cache)


@cache_predictor("listWritingChapters")
def _predict_list_writing_chapters(ctx: dict, args: dict) -> bool:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return False
    return _read_tool_cache_get(ctx, f"listWritingChapters:{bid}") is not None


@cache_predictor("batchGetChapterContents")
def _predict_batch_get_chapter_contents(ctx: dict, args: dict) -> bool:
    writing_chapters = _runtime_writing_chapters(ctx)
    nc = _reject_numeric_chapter_ids_for_batch(args.get("chapterIds", []))
    if not nc["ok"]:
        return False
    cids = nc.get("ids", [])
    if not chapters_allowed_by_writing_catalog(cids, writing_chapters)["ok"]:
        return False
    cache = _ensure_chapter_content_cache(ctx)
    return bool(cids and all(cache and str(c).strip() in cache for c in cids))


@cache_predictor("getBookCharacters")
def _predict_get_book_characters(ctx: dict, args: dict) -> bool:
    bid = resolve_book_id_for_tools(ctx, args)
    ids = args.get("characterIds")
    names_q = args.get("names")
    if (isinstance(ids, list) and ids) or (isinstance(names_q, list) and names_q):
        return False
    return _read_tool_cache_get(ctx, f"getBookCharacters:all:{bid}") is not None


@cache_predictor("listBookCharacters")
def _predict_list_book_characters(ctx: dict, args: dict) -> bool:
    bid = resolve_book_id_for_tools(ctx, args)
    return _read_tool_cache_get(ctx, f"listBookCharacters:{bid}") is not None


@cache_predictor("getStoryBackground")
def _predict_get_story_background(ctx: dict, args: dict) -> bool:
    bid = resolve_book_id_for_tools(ctx, args)
    return _read_tool_cache_get(ctx, f"getStoryBackground:{bid}") is not None


@cache_predictor("getBookStyle")
def _predict_get_book_style(ctx: dict, args: dict) -> bool:
    bid = resolve_book_id_for_tools(ctx, args)
    return _read_tool_cache_get(ctx, f"getBookStyle:{bid}") is not None


@cache_predictor("queryOutline")
def _predict_query_outline(ctx: dict, args: dict) -> bool:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return False
    oids = args.get("outlineIds")
    max_len = args.get("maxTextLength", 32000) if isinstance(args.get("maxTextLength"), (int, float)) else 32000
    oid_key = ",".join(sorted(str(x) for x in oids)) if isinstance(oids, list) else ""
    return _read_tool_cache_get(ctx, f"queryOutline:{bid}:{oid_key}:{max_len}") is not None


@cache_predictor("getGlobalOutline")
def _predict_get_global_outline(ctx: dict, args: dict) -> bool:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return False
    max_len = args.get("maxTextLength", 32000) if isinstance(args.get("maxTextLength"), (int, float)) else 32000
    return _read_tool_cache_get(ctx, f"getGlobalOutline:{bid}:{max_len}") is not None


@cache_predictor("listOutlines")
def _predict_list_outlines(ctx: dict, args: dict) -> bool:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return False
    return _read_tool_cache_get(ctx, f"listOutlines:{bid}") is not None


# ---------------------------------------------------------------------------
# Handlers —— 一个工具一个函数
# ---------------------------------------------------------------------------


def _err(payload: dict | str) -> ToolResult:
    """统一错误返回。传 dict 自动 JSON，传 str 直接当成 ``error`` 字段。"""
    body = payload if isinstance(payload, dict) else {"error": str(payload)}
    return ToolResult(json.dumps(body, ensure_ascii=False))


@tool("getChapterContent")
async def _tool_get_chapter_content(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    writing_chapters = _runtime_writing_chapters(ctx)
    resolved = resolve_chapter_id_strict(args, writing_chapters, _runtime_chapter_id(ctx))
    if not resolved["ok"] or not resolved.get("chapterId"):
        msg = "getChapterContent 仅支持 chapterId" if resolved.get("reason") == "non_id_locator_forbidden" else CATALOG_TOOL_FAIL_MSG
        return _err({"error": msg})

    cid = resolved["chapterId"]
    title = args.get("title")
    max_len = args.get("maxTextLength") or 12000
    gate = chapter_allowed_by_writing_catalog(cid, writing_chapters)
    if not gate["ok"]:
        return ToolResult(_json_catalog_reject(ctx, gate, cid))

    key = str(cid).strip()
    cache = _ensure_chapter_content_cache(ctx)
    if cache is not None and key in cache:
        entry = cache[key]
        plain_slice = str(entry.get("plainTextFull") or "")[:max_len]
        title_res = title or entry.get("titleResolved") or _title_from_writing_catalog(cid, writing_chapters) or ""
        payload = {"chapterId": key, "title": title_res, "plainText": plain_slice or "（本章暂无正文内容）"}
        return ToolResult(json.dumps(payload, ensure_ascii=False), from_cache=True)

    full = await _read_chapter_plain_full(cid)
    if not full:
        return _err({"error": CATALOG_TOOL_FAIL_MSG, "chapterId": cid})

    title_resolved = title or _title_from_writing_catalog(cid, writing_chapters) or ""
    if cache is not None:
        cache[key] = {"plainTextFull": full["plainTextFull"], "titleResolved": title_resolved}
    plain_text = str(full.get("plainTextFull") or "")[:max_len]
    payload = {"chapterId": key, "title": title_resolved, "plainText": plain_text or "（本章暂无正文内容）"}
    return ToolResult(json.dumps(payload, ensure_ascii=False))


@tool("listWritingChapters")
async def _tool_list_writing_chapters(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法获取写作目录章节列表"})

    ck = f"listWritingChapters:{bid}"
    hit = _read_tool_cache_get(ctx, ck)
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    result = await _list_writing_chapters(str(bid))
    payload = json.dumps(result, ensure_ascii=False)[:24000]
    _read_tool_cache_set(ctx, ck, payload)
    return ToolResult(payload)


@tool("createWritingChapter")
async def _tool_create_writing_chapter(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法创建章节"})

    writing_chapters = _runtime_writing_chapters(ctx)
    parent_id = _resolve_create_writing_parent_id(args.get("parentId"), writing_chapters, _runtime_chapter_id(ctx))
    if parent_id:
        gate = _chapter_node_allowed_by_writing_catalog(parent_id, writing_chapters)
        if not gate["ok"]:
            p = _catalog_reject_payload(ctx, gate, parent_id)
            extra = {"parentId": p["chapterId"]} if p.get("chapterId") else {}
            return _err({"success": False, "error": p["error"], **extra})

    try:
        db = get_db()
        writing = await get_or_create_writing_outline(db, str(bid))
        if not writing or not writing.get("id"):
            return _err({"success": False, "error": "未找到写作目录，无法创建章节"})

        effective_pid = parent_id or None
        max_num = 0
        for c in writing_chapters:
            pid = None if c.get("parent_id") in (None, "") else str(c["parent_id"])
            if pid != effective_pid:
                continue
            m = re.match(r"^第(\d+)章", str(c.get("title") or ""))
            if m:
                max_num = max(max_num, int(m.group(1)))
        new_title = f"第{max_num + 1}章"
        created = await add_chapter(db, str(writing["id"]), new_title, effective_pid)
        _invalidate_read_tool_cache(ctx)

        new_id = str(created["id"])
        new_title_resolved = str(created.get("title") or new_title)
        new_parent_id = None if created.get("parent_id") in (None, "") else str(created["parent_id"])

        # 同步运行态：让本批后续工具直接看到新章节
        new_writing_chapters = [
            c for c in writing_chapters if str(c.get("id", "")) != new_id
        ] + [{
            "id": new_id,
            "title": new_title_resolved,
            "parent_id": new_parent_id,
            "level": int(created.get("level") or 1),
            "sort": int(created.get("sort") or 0),
        }]
        ctx["chapterId"] = new_id
        ctx["currentChapterTitle"] = new_title_resolved
        ctx["writingChapters"] = new_writing_chapters

        if send_chunk:
            send_chunk({"chapterCreated": {"chapterId": new_id, "title": new_title_resolved, "parentId": new_parent_id}})
        return ToolResult(json.dumps({
            "success": True,
            "bookId": str(bid),
            "writingOutlineId": str(writing["id"]),
            "chapter": {
                "id": new_id,
                "title": new_title_resolved,
                "parentId": new_parent_id,
                "level": int(created.get("level") or 1),
                "sort": int(created.get("sort") or 0),
            },
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("batchGetChapterContents")
async def _tool_batch_get_chapter_contents(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    writing_chapters = _runtime_writing_chapters(ctx)
    cids_raw = args.get("chapterIds", [])
    numeric_check = _reject_numeric_chapter_ids_for_batch(cids_raw)
    if not numeric_check["ok"]:
        return _err({"error": "batchGetChapterContents 仅支持 chapterId 字符串数组（listWritingChapters.items[].id）"})

    cids = numeric_check.get("ids", [])
    max_len = args.get("maxTextLength") or 12000
    batch_gate = chapters_allowed_by_writing_catalog(cids, writing_chapters)
    if not batch_gate["ok"]:
        return ToolResult(_json_batch_catalog_reject(ctx, batch_gate))

    title_map = {str(c.get("id")): c.get("title") for c in writing_chapters}
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
                title_res = t or _title_from_writing_catalog(cid, writing_chapters) or ""
                if cache is not None:
                    cache[cid] = {"plainTextFull": full["plainTextFull"], "titleResolved": title_res}
                pt = str(full.get("plainTextFull") or "")[:max_len]
                entries.append({"chapterId": cid, "title": title_res, "plainText": pt})

    payload = json.dumps(entries, ensure_ascii=False)[:24000]
    return ToolResult(payload, from_cache=batch_all_cached)


@tool("getBookCharacters")
async def _tool_get_book_characters(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    char_ids = args.get("characterIds")
    name_queries = args.get("names")
    filtered = (isinstance(char_ids, list) and len(char_ids) > 0) or (isinstance(name_queries, list) and len(name_queries) > 0)

    if not filtered:
        ck = f"getBookCharacters:all:{bid}"
        hit = _read_tool_cache_get(ctx, ck)
        if hit is not None:
            return ToolResult(hit, from_cache=True)

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
    return ToolResult(content)


@tool("listBookCharacters")
async def _tool_list_book_characters(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    ck = f"listBookCharacters:{bid}"
    hit = _read_tool_cache_get(ctx, ck)
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    chars = await get_characters(get_db(), bid) or []
    lst = [{"id": c.get("id"), "name": re.sub(r"\r?\n", " ", str(c.get("name") or "未命名")).strip() or "未命名"} for c in chars]
    payload = json.dumps(lst, ensure_ascii=False)
    _read_tool_cache_set(ctx, ck, payload)
    return ToolResult(payload)


@tool("getStoryBackground")
async def _tool_get_story_background(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    ck = f"getStoryBackground:{bid}"
    hit = _read_tool_cache_get(ctx, ck)
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    row = await get_story_background(get_db(), bid)
    content = (row.get("content") if row else None) or "（暂无小说背景）"
    _read_tool_cache_set(ctx, ck, content)
    return ToolResult(content)


@tool("getBookStyle")
async def _tool_get_book_style(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    ck = f"getBookStyle:{bid}"
    hit = _read_tool_cache_get(ctx, ck)
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    row = await get_book_style(get_db(), bid)
    if not row:
        content = "（暂无风格基调）"
    else:
        content = json.dumps(
            {
                "pov": row.get("pov") or "",
                "tone": row.get("tone") or "",
                "pace": row.get("pace") or "",
                "banned_rules": row.get("banned_rules") or "",
                "reference_chapter_ids": row.get("reference_chapter_ids") or "",
                "free_notes": row.get("free_notes") or "",
            },
            ensure_ascii=False,
        )
    _read_tool_cache_set(ctx, ck, content)
    return ToolResult(content)


@tool("queryOutline")
async def _tool_query_outline(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法查询大纲"})
    if args.get("outlineIndex") is not None or args.get("outlineTitle") is not None:
        return _err({"success": False, "error": "queryOutline 仅支持 outlineId/outlineIds"})

    if isinstance(args.get("outlineIds"), list):
        oids = args["outlineIds"]
    elif args.get("outlineId") is not None and str(args["outlineId"]).strip():
        oids = [str(args["outlineId"]).strip()]
    else:
        oids = []
    if not oids:
        return _err({"success": False, "error": "缺少有效 outlineId/outlineIds"})

    max_len = args["maxTextLength"] if isinstance(args.get("maxTextLength"), (int, float)) else 32000
    oid_key = ",".join(sorted(str(x) for x in oids))
    ck = f"queryOutline:{bid}:{oid_key}:{max_len}"
    hit = _read_tool_cache_get(ctx, ck)
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    result = await _query_outline(bid, oids, max_len)
    payload = json.dumps(result, ensure_ascii=False)[:24000]
    _read_tool_cache_set(ctx, ck, payload)
    return ToolResult(payload)


@tool("getGlobalOutline")
async def _tool_get_global_outline(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法获取总纲"})

    max_len = args["maxTextLength"] if isinstance(args.get("maxTextLength"), (int, float)) else 32000
    ck = f"getGlobalOutline:{bid}:{max_len}"
    hit = _read_tool_cache_get(ctx, ck)
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    result = await _get_global_outline(bid, max_len)
    if not result:
        return _err({"success": False, "error": "获取总纲失败"})
    payload = json.dumps(result, ensure_ascii=False)
    _read_tool_cache_set(ctx, ck, payload)
    return ToolResult(payload)


@tool("editGlobalOutline")
async def _tool_edit_global_outline(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    if not isinstance(args.get("markdownContent"), str):
        return _err({"success": False, "error": "markdownContent 必须为字符串"})

    try:
        db = get_db()
        go = await get_or_create_global_outline(db, bid)
        if not go or not go.get("id"):
            return _err({"success": False, "error": "无法创建或获取总纲"})

        saved = await update_outline(
            db,
            {"outlineId": str(go["id"]), "markdown_content": args["markdownContent"]},
            history_source="ai_tool",
            history_note="editGlobalOutline",
        )
        _invalidate_read_tool_cache(ctx)
        return ToolResult(json.dumps({
            "success": True,
            "bookId": str(bid),
            "outlineId": str(go["id"]),
            "title": (saved or {}).get("title") or "总纲",
            "type": (saved or {}).get("type") or "global",
            "markdownLength": len(args["markdownContent"]),
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("listOutlines")
async def _tool_list_outlines(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法获取大纲列表"})

    ck = f"listOutlines:{bid}"
    hit = _read_tool_cache_get(ctx, ck)
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    ol = await _get_available_outlines(bid)
    lst = [{"id": str(o.get("id")), "title": o.get("title", ""), "type": o.get("type", "")} for o in ol]
    payload = json.dumps({"success": True, "bookId": str(bid), "total": len(lst), "outlines": lst}, ensure_ascii=False)
    _read_tool_cache_set(ctx, ck, payload)
    return ToolResult(payload)


@tool("updateOutline")
async def _tool_update_outline(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    oid = str(args.get("outlineId") or "").strip()
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    if not oid:
        return _err({"success": False, "error": "缺少有效 outlineId"})

    update_payload: dict[str, Any] = {"outlineId": oid}
    for fld in ("title", "xmind_data", "file_path", "markdown_content"):
        if args.get(fld) is not None:
            update_payload[fld] = args[fld]
    if len(update_payload) == 1:
        return _err({"success": False, "error": "缺少可更新字段（title/xmind_data/file_path/markdown_content）"})

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

    if not any(str(o.get("id")) == oid for o in candidates if o):
        return _err({"success": False, "error": "outlineId 不属于当前书籍，或该大纲不存在", "outlineId": oid})

    try:
        saved = await update_outline(
            get_db(),
            update_payload,
            history_source="ai_tool",
            history_note="updateOutline",
        )
        _invalidate_read_tool_cache(ctx)
        return ToolResult(json.dumps({
            "success": True,
            "outlineId": oid,
            "title": (saved or {}).get("title", ""),
            "type": (saved or {}).get("type", ""),
            "updatedFields": [k for k in update_payload if k != "outlineId"],
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("editChapterContent")
async def _tool_edit_chapter_content(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    """自 v3.1 起 editChapterContent 不再直接落库，改为提交 diff 给前端，
    由用户在 DiffOverlay 接受/拒绝后通过 commitChapterDiff 真正写库。

    返回给 LLM 的语义是"已提交差异，等待用户确认"，**不要**再自称"已保存"，
    否则模型会基于该假设继续动作。
    """
    writing_chapters = _runtime_writing_chapters(ctx)
    runtime_chapter_id = _runtime_chapter_id(ctx)
    resolved = resolve_chapter_id_strict(args, writing_chapters, runtime_chapter_id)
    cid = resolved.get("chapterId", "") if resolved["ok"] else ""
    new_content = args.get("content", "") if isinstance(args.get("content"), str) else ""
    if not cid:
        msg = (
            "editChapterContent 仅支持 chapterId（chapterTitle/chapterIndex 已禁用）"
            if resolved.get("reason") == "non_id_locator_forbidden"
            else "章节定位失败（仅支持 chapterId）"
        )
        return _err({"success": False, "error": msg})

    edit_gate = chapter_allowed_by_writing_catalog(cid, writing_chapters)
    if not edit_gate["ok"]:
        p = _catalog_reject_payload(ctx, edit_gate, cid)
        extra = {"chapterId": p["chapterId"]} if p.get("chapterId") else {}
        return _err({"success": False, "error": p["error"], **extra})

    try:
        old_plain = (await _read_chapter_plain_full(cid) or {}).get("plainTextFull") or ""
        old_trim = str(old_plain).strip()
        merged_content = new_content
        is_collab = ctx.get("collabWriting") is True
        if is_collab and new_content.strip():
            new_trim = new_content.strip()
            if old_trim and new_trim and not new_trim.startswith(old_trim):
                merged_content = f"{old_trim}\n\n{new_trim}"

        # 与现有正文完全一致时无 diff 可提，告诉 LLM "无需变更" 即可
        if (merged_content or "").strip() == old_trim and old_trim != "":
            return ToolResult(json.dumps({
                "success": True,
                "message": "新内容与现有正文一致，无需变更",
                "chapterId": cid,
                "noop": True,
            }, ensure_ascii=False))

        if send_chunk:
            send_chunk({
                "proposedChapterDiff": {
                    "chapterId": cid,
                    "beforeText": old_plain,
                    "proposedText": merged_content,
                    "source": "ai_tool_edit_collab" if is_collab else "ai_tool_edit",
                },
            })
        return ToolResult(json.dumps({
            "success": True,
            "message": "已向用户提交差异预览，需用户在编辑器接受/拒绝后才会写入正文",
            "chapterId": cid,
            "pendingUserApproval": True,
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("addSparkIdea")
async def _tool_add_spark_idea(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    mem_content = str(args.get("content") or "").strip() if isinstance(args.get("content"), str) else ""
    cid = args.get("chapterId") or _runtime_chapter_id(ctx) or None
    character_id = args.get("characterId")
    layer_num = args.get("layer") if isinstance(args.get("layer"), (int, float)) else None
    layer_valid = layer_num is not None and int(layer_num) in (0, 1, 2, 3)

    if not bid:
        # ai_memories.book_id NOT NULL，缺 bookId 必然 INSERT IntegrityError；前置拦下给 LLM 一个能看懂的错
        return _err({"success": False, "error": "缺少有效 bookId，无法写入本书设定"})
    if not layer_valid or not mem_content:
        msg = "设定内容不能为空" if not mem_content else f"layer 须为 0/1/2/3（{' / '.join(SPARK_IDEA_LAYER_ORDERED)}）"
        return _err({"success": False, "error": msg})

    layer_str = SPARK_IDEA_LAYERS[int(layer_num)]
    try:
        from services import memory_service
        row = await memory_service.add_spark_idea(bid, layer_str, mem_content, cid, character_id)
        return ToolResult(json.dumps({
            "success": True,
            "message": f"已添加【{layer_str}设定】",
            "id": row.get("id") if row else None,
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("updateSparkIdea")
async def _tool_update_spark_idea(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    """修改一条已有设定（content / layer / chapterId / characterId），id 必填。

    关联实体语义：显式传 None 视为清空；不传（key 不在 args 里）视为不变。
    """
    raw_id = args.get("id")
    sid = str(raw_id).strip() if raw_id is not None else ""
    new_content_raw = args.get("content")
    new_content = str(new_content_raw).strip() if isinstance(new_content_raw, str) else None
    new_layer_raw = args.get("layer")
    new_layer_num = int(new_layer_raw) if isinstance(new_layer_raw, (int, float)) else None
    new_layer_valid = new_layer_num is None or new_layer_num in (0, 1, 2, 3)
    has_chapter = "chapterId" in args
    has_character = "characterId" in args
    new_chapter_id = args.get("chapterId")
    new_character_id = args.get("characterId")

    if not sid:
        return _err({"success": False, "error": "缺少 id：updateSparkIdea 必须指定要更新的条目"})
    if not new_layer_valid:
        return _err({"success": False, "error": f"layer 须为 0/1/2/3（{' / '.join(SPARK_IDEA_LAYER_ORDERED)}）"})
    if (
        (new_content is None or new_content == "")
        and new_layer_num is None
        and not has_chapter
        and not has_character
    ):
        return _err({"success": False, "error": "noop：content / layer / chapterId / characterId 至少需要传其中一个", "noop": True})

    try:
        from services import memory_service
        update_payload: dict[str, Any] = {}
        if new_content is not None and new_content != "":
            update_payload["content"] = new_content
        if new_layer_num is not None:
            update_payload["layer"] = SPARK_IDEA_LAYERS[new_layer_num]
        if has_chapter:
            update_payload["chapter_id"] = str(new_chapter_id) if new_chapter_id is not None else None
        if has_character:
            update_payload["character_id"] = int(new_character_id) if new_character_id is not None else None

        updated = await memory_service.update_spark_idea(sid, update_payload)
        if not updated:
            return _err({"success": False, "error": f"未找到 id={sid} 的设定条目"})
        return ToolResult(json.dumps({
            "success": True,
            "message": f"已更新【{updated.get('layer')}设定】",
            "id": updated.get("id"),
            "layer": updated.get("layer"),
            "content": updated.get("content"),
            "chapter_id": updated.get("chapter_id"),
            "character_id": updated.get("character_id"),
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("deleteSparkIdea")
async def _tool_delete_spark_idea(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    """物理删除一条设定；删除前先取出 content/layer 用于回显，便于 LLM 在回复中复述。"""
    raw_id = args.get("id")
    sid = str(raw_id).strip() if raw_id is not None else ""
    if not sid:
        return _err({"success": False, "error": "缺少 id：deleteSparkIdea 必须指定要删除的条目"})

    try:
        from services import memory_service
        existed = await memory_service.get_spark_ideas_by_ids([sid])
        if not existed:
            return _err({"success": False, "error": f"未找到 id={sid} 的设定条目"})
        target = existed[0]
        await memory_service.delete_spark_idea(sid)
        return ToolResult(json.dumps({
            "success": True,
            "message": f"已删除【{target.get('layer')}设定】",
            "id": target.get("id"),
            "layer": target.get("layer"),
            "content": target.get("content"),
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("addForeshadowing")
async def _tool_add_foreshadowing(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    for_chapter_id = args.get("chapterId") or _runtime_chapter_id(ctx) or None
    for_content = str(args.get("content") or "").strip() if isinstance(args.get("content"), str) else ""
    for_type = args.get("type") or FORESHADOWING_TYPES[0]

    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法写入伏笔"})
    if not for_chapter_id or not for_content:
        msg = "缺少 chapterId（埋入章节）" if not for_chapter_id else "伏笔内容不能为空"
        return _err({"success": False, "error": msg})
    if for_type not in FORESHADOWING_TYPES:
        return _err({"success": False, "error": f"type 须为：{' / '.join(FORESHADOWING_TYPES)}"})

    try:
        from services import memory_service
        row = await memory_service.add_foreshadowing(bid, for_chapter_id, for_content, for_type, None)
        return ToolResult(json.dumps({
            "success": True,
            "message": f"已添加伏笔【{for_type}】",
            "id": row.get("id") if row else None,
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("searchSparkIdeas")
async def _tool_search_spark_ideas(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法检索本书设定"})

    query = args.get("query") or ""
    layer = args.get("layer")
    cid = args.get("chapterId") or _runtime_chapter_id(ctx) or None
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
                # 行首带 [id:N] 前缀，便于 LLM 后续调 updateSparkIdea / 引用
                mid = m.get("id")
                mtxt = str(m.get("content") or "").strip()
                line = f"[id:{mid}] {mtxt}" if mid is not None else mtxt
                by_layer.setdefault(m.get("layer", ""), []).append(line)
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

        return ToolResult("\n\n".join(parts) if parts else "（未找到与当前检索相关的本书设定）")
    except Exception as e:
        return _err({"error": str(e)})


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------


def tool_will_hit_read_cache(ctx: dict, tc: dict, writing_chapters: list[dict]) -> bool:
    """提前预测一个工具调用是否会走读缓存。

    ``writing_chapters`` 参数保留是为了向后兼容 —— 实际 predictor 都直接读 ctx
    里的 ``writingChapters``，因为这两者必须一致（``run_tools`` 入口已经保证）。
    """
    name = (tc.get("function") or {}).get("name")
    args = _parse_args((tc.get("function") or {}).get("arguments", "{}"))
    allow = ctx.get("subagentAllowedToolNames")
    if isinstance(allow, set) and name and name not in allow:
        return False
    predictor = CACHE_PREDICTORS.get(name or "")
    if predictor is None:
        return False
    try:
        return bool(predictor(ctx, args))
    except Exception:
        return False


async def run_tools(
    tool_calls: list[dict],
    ctx: dict,
    send_chunk: Callable[[dict], None] | None = None,
) -> list[dict]:
    """批量执行 tool_calls，返回 ``[{tool_call_id, content}, ...]``。

    工具间共享状态全部走 ``ctx`` —— ``createWritingChapter`` 等会原地更新
    ``ctx['chapterId'] / ctx['writingChapters']``，本批后续工具自然能看到。
    """
    results: list[dict] = []
    writing_chapters_snapshot = _runtime_writing_chapters(ctx)
    tool_read_cache_mask = [
        tool_will_hit_read_cache(ctx, tc, writing_chapters_snapshot) for tc in tool_calls
    ]
    if send_chunk and any(tool_read_cache_mask):
        send_chunk({"toolReadCacheMask": tool_read_cache_mask})

    for i, tc in enumerate(tool_calls):
        name = (tc.get("function") or {}).get("name")
        args = _parse_args((tc.get("function") or {}).get("arguments", "{}"))
        from_cache = False

        try:
            allow = ctx.get("subagentAllowedToolNames")
            if isinstance(allow, set) and name and name not in allow:
                content = json.dumps(
                    {"error": f"工具「{name}」不在当前 subagent 授权范围，已拒绝执行。"},
                    ensure_ascii=False,
                )
            else:
                handler = TOOL_HANDLERS.get(name or "")
                if handler is None:
                    content = json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
                else:
                    res = await handler(ctx, args, send_chunk)
                    content = res.content
                    from_cache = res.from_cache
        except Exception as err:
            content = json.dumps({"error": str(err)}, ensure_ascii=False)

        results.append({"tool_call_id": tc.get("id"), "content": content})
        if send_chunk:
            chunk: dict = {"toolIndexCompleted": i}
            if from_cache:
                chunk["toolFromCache"] = True
            send_chunk(chunk)

    return results
