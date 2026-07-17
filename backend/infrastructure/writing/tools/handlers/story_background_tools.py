"""Tool handlers for reading and proposing changes to the story background."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable

from database.crud.story_background import get_story_background
from domains.writing.tools.cache import build_read_cache_key
from domains.writing.tools.contracts import ToolResult, _err
from domains.writing.tools.scope import resolve_book_id_for_tools
from domains.writing.tools.state import (
    _invalidate_read_tool_cache,
    _read_tool_cache_get,
    _read_tool_cache_set,
)

if TYPE_CHECKING:
    from infrastructure.writing.tools.runtime import WritingToolDependencies


async def _tool_get_story_background(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    cache_key = build_read_cache_key("getStoryBackground", ctx, args)
    hit = _read_tool_cache_get(ctx, cache_key) if cache_key else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    row = await get_story_background(dependencies.db, bid)
    content = (row.get("content") if row else None) or "（暂无小说背景）"
    if cache_key:
        _read_tool_cache_set(ctx, cache_key, content)
    return ToolResult(content)


async def _tool_edit_story_background(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    """Submit a background diff for review instead of writing immediately."""
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    content = args.get("content")
    if not isinstance(content, str):
        return _err({
            "success": False,
            "error": "content 必须为字符串（小说背景 Markdown 全文，覆盖写入）",
        })

    try:
        row = await get_story_background(dependencies.db, str(bid))
        before_content = (row.get("content") if row else None) or ""
        if content.strip() == before_content.strip() and before_content.strip():
            return ToolResult(json.dumps({
                "success": True,
                "message": "新内容与现有背景一致，无需变更",
                "bookId": str(bid),
                "noop": True,
            }, ensure_ascii=False))

        _invalidate_read_tool_cache(ctx)
        if send_chunk:
            send_chunk({
                "proposedSettingDiff": {
                    "kind": "background",
                    "bookId": str(bid),
                    "before": {"content": before_content},
                    "proposed": {"content": content},
                    "source": "ai_tool_edit",
                },
            })
        return ToolResult(json.dumps({
            "success": True,
            "message": "已向用户提交故事背景差异预览，需用户在设定面板接受/拒绝后才会写入",
            "bookId": str(bid),
            "pendingUserApproval": True,
        }, ensure_ascii=False))
    except Exception as error:
        return _err({"success": False, "error": str(error)})
