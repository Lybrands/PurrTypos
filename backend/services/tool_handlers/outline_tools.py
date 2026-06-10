"""Outline tool handlers."""

from __future__ import annotations

import json
from typing import Any, Callable

from database.crud.outlines import get_or_create_global_outline, update_outline
from dependencies import get_db
from services.tool_runtime import (
    ToolResult,
    _err,
    _get_available_outlines,
    _get_global_outline,
    _invalidate_read_tool_cache,
    _load_all_outlines_for_book,
    _query_outline,
    _read_tool_cache_get,
    _read_tool_cache_set,
    build_read_cache_key,
    normalize_query_outline_ids,
    resolve_book_id_for_tools,
    tool,
)


@tool("queryOutline")
async def _tool_query_outline(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法查询大纲"})
    if args.get("outlineIndex") is not None or args.get("outlineTitle") is not None:
        return _err({"success": False, "error": "queryOutline 仅支持 outlineId/outlineIds"})

    oids = normalize_query_outline_ids(args)
    if not oids:
        return _err({"success": False, "error": "缺少有效 outlineId/outlineIds"})

    max_len = args["maxTextLength"] if isinstance(args.get("maxTextLength"), (int, float)) else 32000
    ck = build_read_cache_key("queryOutline", ctx, args)
    hit = _read_tool_cache_get(ctx, ck) if ck else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    result = await _query_outline(bid, oids, max_len)
    payload = json.dumps(result, ensure_ascii=False)[:24000]
    if ck:
        _read_tool_cache_set(ctx, ck, payload)
    return ToolResult(payload)


@tool("getGlobalOutline")
async def _tool_get_global_outline(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法获取总纲"})

    max_len = args["maxTextLength"] if isinstance(args.get("maxTextLength"), (int, float)) else 32000
    ck = build_read_cache_key("getGlobalOutline", ctx, args)
    hit = _read_tool_cache_get(ctx, ck) if ck else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    result = await _get_global_outline(bid, max_len)
    if not result:
        return _err({"success": False, "error": "获取总纲失败"})
    payload = json.dumps(result, ensure_ascii=False)
    if ck:
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
        if send_chunk:
            send_chunk({"settingUpdated": {"kind": "outline", "action": "update", "id": str(go["id"])}})
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

    ck = build_read_cache_key("listOutlines", ctx, args)
    hit = _read_tool_cache_get(ctx, ck) if ck else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    ol = await _get_available_outlines(bid)
    lst = [{"id": str(o.get("id")), "title": o.get("title", ""), "type": o.get("type", "")} for o in ol]
    payload = json.dumps({"success": True, "bookId": str(bid), "total": len(lst), "outlines": lst}, ensure_ascii=False)
    if ck:
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
        if send_chunk:
            send_chunk({"settingUpdated": {"kind": "outline", "action": "update", "id": oid}})
        return ToolResult(json.dumps({
            "success": True,
            "outlineId": oid,
            "title": (saved or {}).get("title", ""),
            "type": (saved or {}).get("type", ""),
            "updatedFields": [k for k in update_payload if k != "outlineId"],
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})
