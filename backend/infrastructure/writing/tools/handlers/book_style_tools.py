"""Writing Agent 的书籍风格读取 handler。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable

from database.crud.book_style import get_book_style
from domains.writing.tools.cache import build_read_cache_key
from domains.writing.tools.contracts import ToolResult
from domains.writing.tools.scope import resolve_book_id_for_tools
from domains.writing.tools.state import _read_tool_cache_get, _read_tool_cache_set

if TYPE_CHECKING:
    from infrastructure.writing.tools.runtime import WritingToolDependencies


async def _tool_get_book_style(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    cache_key = build_read_cache_key("getBookStyle", ctx, args)
    hit = _read_tool_cache_get(ctx, cache_key) if cache_key else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    row = await get_book_style(dependencies.db, bid)
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
