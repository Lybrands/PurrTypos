"""Tool handlers for reading a book's style guide."""

from __future__ import annotations

import json
from typing import Callable

from database.crud.book_style import get_book_style
from dependencies import get_db
from services.tool_runtime import (
    ToolResult,
    _read_tool_cache_get,
    _read_tool_cache_set,
    build_read_cache_key,
    resolve_book_id_for_tools,
    tool,
)


@tool("getBookStyle")
async def _tool_get_book_style(
    ctx: dict, args: dict, send_chunk: Callable | None,
) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    cache_key = build_read_cache_key("getBookStyle", ctx, args)
    hit = _read_tool_cache_get(ctx, cache_key) if cache_key else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    row = await get_book_style(get_db(), bid)
    if not row:
        content = "（暂无风格基调）"
    else:
        content = json.dumps({
            "pov": row.get("pov") or "",
            "tone": row.get("tone") or "",
            "pace": row.get("pace") or "",
            "banned_rules": row.get("banned_rules") or "",
            "reference_chapter_ids": row.get("reference_chapter_ids") or "",
            "free_notes": row.get("free_notes") or "",
        }, ensure_ascii=False)
    if cache_key:
        _read_tool_cache_set(ctx, cache_key, content)
    return ToolResult(content)
