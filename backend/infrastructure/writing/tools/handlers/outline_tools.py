"""Writing Agent 的大纲读写 handler。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Callable

from database.crud.outlines import save_outline, update_outline
from domains.writing.tools.cache import (
    build_read_cache_key,
    normalize_query_outline_ids,
)
from domains.writing.tools.contracts import ToolResult, _err
from domains.writing.tools.scope import resolve_book_id_for_tools
from domains.writing.tools.state import (
    _invalidate_read_tool_cache,
    _read_tool_cache_get,
    _read_tool_cache_set,
)
from infrastructure.writing.tools.data_loaders import (
    _get_available_outlines,
    _get_global_outline,
    _query_outline,
)

if TYPE_CHECKING:
    from infrastructure.writing.tools.runtime import WritingToolDependencies


async def _tool_query_outline(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    book_id = resolve_book_id_for_tools(ctx, args)
    if not book_id:
        return _err({"success": False, "error": "缺少有效 bookId，无法查询大纲"})
    if args.get("outlineIndex") is not None or args.get("outlineTitle") is not None:
        return _err({
            "success": False,
            "error": "queryOutline 仅支持 outlineId/outlineIds",
        })

    outline_ids = normalize_query_outline_ids(args)
    if not outline_ids:
        return _err({"success": False, "error": "缺少有效 outlineId/outlineIds"})

    max_length = (
        args["maxTextLength"]
        if isinstance(args.get("maxTextLength"), (int, float))
        else 32000
    )
    cache_key = build_read_cache_key("queryOutline", ctx, args)
    hit = _read_tool_cache_get(ctx, cache_key) if cache_key else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    result = await _query_outline(
        dependencies.db,
        book_id,
        outline_ids,
        max_length,
    )
    payload = _bounded_outline_payload(result)
    if cache_key:
        _read_tool_cache_set(ctx, cache_key, payload)
    return ToolResult(payload)


def _bounded_outline_payload(
    result: Mapping[str, Any],
    *,
    max_characters: int = 24_000,
) -> str:
    """Fit outline text structurally so the tool receipt is always valid JSON."""

    if isinstance(max_characters, bool) or not isinstance(max_characters, int):
        raise TypeError("outline payload maximum characters must be an integer")
    if max_characters < 1:
        raise ValueError("outline payload maximum characters must be positive")
    limit = max_characters
    # A JSON object needs at least two characters.  This edge is not used by
    # the production 24k cap, but still honours the helper's exact bound.
    if limit == 1:
        return "0"
    original = json.dumps(dict(result), ensure_ascii=False)
    if len(original) <= limit:
        return original

    raw_outlines = result.get("outlines")
    outlines = (
        [dict(value) for value in raw_outlines if isinstance(value, Mapping)]
        if isinstance(raw_outlines, list)
        else []
    )
    envelope = {
        key: value
        for key, value in result.items()
        if key != "outlines"
    }
    envelope["responseTruncated"] = True

    def _render(text_cap: int) -> str:
        fitted: list[dict[str, Any]] = []
        for source in outlines:
            value = dict(source)
            for key in ("markdown", "xmindData"):
                text = value.get(key)
                if isinstance(text, str) and len(text) > text_cap:
                    value[key] = _truncate_outline_text(text, text_cap)
            fitted.append(value)
        return json.dumps(
            {**envelope, "outlines": fitted},
            ensure_ascii=False,
        )

    minimum = _render(0)
    if len(minimum) > limit:
        # Do not echo unbounded source metadata (for example a malformed,
        # multi-kilobyte bookId) into the fallback.  Select the richest valid
        # JSON receipt that actually fits the caller's bound.
        fallbacks = (
            {
                "success": False,
                "error": "大纲结果元数据超过安全返回上限，请减少 outlineIds 后重试",
                "responseTruncated": True,
                "outlines": [],
            },
            {"success": False, "responseTruncated": True, "outlines": []},
            {"outlines": []},
            {},
        )
        for fallback in fallbacks:
            payload = json.dumps(fallback, ensure_ascii=False)
            if len(payload) <= limit:
                return payload
        # limit==1 was handled above, so this is unreachable for positive
        # limits.  Keep a total return for defensive type-checking.
        return "0"

    high = max(
        (
            len(value)
            for outline in outlines
            for key in ("markdown", "xmindData")
            if isinstance((value := outline.get(key)), str)
        ),
        default=0,
    )
    low = 0
    best = minimum
    while low <= high:
        middle = (low + high) // 2
        candidate = _render(middle)
        if len(candidate) <= limit:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def _truncate_outline_text(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    marker = "\n…（工具结果已截断）"
    if limit <= len(marker):
        return value[:limit]
    return value[:limit - len(marker)] + marker


async def _tool_get_global_outline(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    book_id = resolve_book_id_for_tools(ctx, args)
    if not book_id:
        return _err({"success": False, "error": "缺少有效 bookId，无法获取总纲"})

    max_length = (
        args["maxTextLength"]
        if isinstance(args.get("maxTextLength"), (int, float))
        else 32000
    )
    cache_key = build_read_cache_key("getGlobalOutline", ctx, args)
    hit = _read_tool_cache_get(ctx, cache_key) if cache_key else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    result = await _get_global_outline(dependencies.db, book_id, max_length)
    if not result:
        return _err({"success": False, "error": "获取总纲失败"})
    payload = json.dumps(result, ensure_ascii=False)
    if cache_key:
        _read_tool_cache_set(ctx, cache_key, payload)
    return ToolResult(payload)


async def _tool_edit_global_outline(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    book_id = resolve_book_id_for_tools(ctx, args)
    if not book_id:
        return _err({"success": False, "error": "缺少有效 bookId"})
    if not isinstance(args.get("markdownContent"), str):
        return _err({"success": False, "error": "markdownContent 必须为字符串"})

    try:
        db = dependencies.db
        # Never let the legacy ``book_id IS NULL`` fallback cross the write
        # boundary. A confirmed edit may initialize a new outline, but it must
        # be scoped to this book from its first persisted row.
        global_outline = await db.fetch_one(
            "SELECT * FROM outlines "
            "WHERE type = 'global' AND book_id = ? LIMIT 1",
            [book_id],
        )
        if global_outline:
            saved = await update_outline(
                db,
                {
                    "outlineId": str(global_outline["id"]),
                    "markdown_content": args["markdownContent"],
                },
                history_source="ai_tool",
                history_note="editGlobalOutline",
            )
        else:
            book = await db.fetch_one(
                "SELECT id FROM books WHERE id = ?",
                [book_id],
            )
            if book is None:
                return _err({"success": False, "error": "当前书籍不存在，无法创建总纲"})
            global_outline = await save_outline(db, {
                "title": "总纲",
                "type": "global",
                "book_id": book_id,
                "markdown_content": args["markdownContent"],
            })
            saved = global_outline

        if not global_outline or not global_outline.get("id"):
            return _err({"success": False, "error": "无法创建或获取总纲"})
        _invalidate_read_tool_cache(ctx)
        if send_chunk:
            send_chunk({
                "settingUpdated": {
                    "kind": "outline",
                    "action": "update",
                    "id": str(global_outline["id"]),
                },
            })
        return ToolResult(json.dumps({
            "success": True,
            "bookId": str(book_id),
            "outlineId": str(global_outline["id"]),
            "title": (saved or {}).get("title") or "总纲",
            "type": (saved or {}).get("type") or "global",
            "markdownLength": len(args["markdownContent"]),
        }, ensure_ascii=False))
    except Exception as exc:
        return _err({"success": False, "error": str(exc)})


async def _tool_list_outlines(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    book_id = resolve_book_id_for_tools(ctx, args)
    if not book_id:
        return _err({"success": False, "error": "缺少有效 bookId，无法获取大纲列表"})

    cache_key = build_read_cache_key("listOutlines", ctx, args)
    hit = _read_tool_cache_get(ctx, cache_key) if cache_key else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    outlines = await _get_available_outlines(dependencies.db, book_id)
    outline_list = [
        {
            "id": str(outline.get("id")),
            "title": outline.get("title", ""),
            "type": outline.get("type", ""),
        }
        for outline in outlines
    ]
    payload = json.dumps({
        "success": True,
        "bookId": str(book_id),
        "total": len(outline_list),
        "outlines": outline_list,
    }, ensure_ascii=False)
    if cache_key:
        _read_tool_cache_set(ctx, cache_key, payload)
    return ToolResult(payload)


async def _tool_update_outline(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    book_id = resolve_book_id_for_tools(ctx, args)
    outline_id = str(args.get("outlineId") or "").strip()
    if not book_id:
        return _err({"success": False, "error": "缺少有效 bookId"})
    if not outline_id:
        return _err({"success": False, "error": "缺少有效 outlineId"})

    update_payload: dict[str, Any] = {"outlineId": outline_id}
    for field in ("title", "xmind_data", "file_path", "markdown_content"):
        if args.get(field) is not None:
            update_payload[field] = args[field]
    if len(update_payload) == 1:
        return _err({
            "success": False,
            "error": "缺少可更新字段（title/xmind_data/file_path/markdown_content）",
        })

    # Ownership is checked from persisted state, not from a loader that may
    # expose the read-only legacy global fallback. In particular a NULL-book
    # global outline can never be updated through a book-scoped tool call.
    target = await dependencies.db.fetch_one(
        "SELECT id FROM outlines WHERE id = ? AND book_id = ? "
        "AND type IN ('global', 'volume', 'chapter', 'writing')",
        [outline_id, book_id],
    )
    if target is None:
        return _err({
            "success": False,
            "error": "outlineId 不属于当前书籍，或该大纲不存在",
            "outlineId": outline_id,
        })

    try:
        saved = await update_outline(
            dependencies.db,
            update_payload,
            history_source="ai_tool",
            history_note="updateOutline",
        )
        _invalidate_read_tool_cache(ctx)
        if send_chunk:
            send_chunk({
                "settingUpdated": {
                    "kind": "outline",
                    "action": "update",
                    "id": outline_id,
                },
            })
        return ToolResult(json.dumps({
            "success": True,
            "outlineId": outline_id,
            "title": (saved or {}).get("title", ""),
            "type": (saved or {}).get("type", ""),
            "updatedFields": [
                key for key in update_payload if key != "outlineId"
            ],
        }, ensure_ascii=False))
    except Exception as exc:
        return _err({"success": False, "error": str(exc)})
