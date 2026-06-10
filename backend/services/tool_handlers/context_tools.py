"""Book context tool handlers: characters, background, and style."""

from __future__ import annotations

import json
import re
from typing import Callable

from database.crud.book_style import get_book_style
from database.crud.characters import (
    create_character,
    get_characters,
    update_character,
)
from database.crud.story_background import (
    get_story_background,
    save_story_background,
)
from dependencies import get_db
from services.tool_runtime import (
    ToolResult,
    _err,
    _invalidate_read_tool_cache,
    _read_tool_cache_get,
    _read_tool_cache_set,
    build_read_cache_key,
    resolve_book_id_for_tools,
    tool,
)
from utils.text import format_characters_as_text

# 人物可写字段：工具参数名 → characters 表列名。
# 人物档案已 Markdown 化：除 name / tags 外的设定统一放 profileMd（整篇覆盖）。
CHARACTER_ARG_COLUMNS = {
    "name": "name",
    "tags": "tags",
    "profileMd": "profile_md",
}


@tool("getBookCharacters")
async def _tool_get_book_characters(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    char_ids = args.get("characterIds")
    name_queries = args.get("names")
    # ck 为 None 表示带过滤条件（characterIds/names），不走读缓存
    ck = build_read_cache_key("getBookCharacters", ctx, args)

    if ck is not None:
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
    if ck is not None:
        _read_tool_cache_set(ctx, ck, content)
    return ToolResult(content)


@tool("listBookCharacters")
async def _tool_list_book_characters(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    ck = build_read_cache_key("listBookCharacters", ctx, args)
    hit = _read_tool_cache_get(ctx, ck) if ck else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    chars = await get_characters(get_db(), bid) or []
    lst = [{"id": c.get("id"), "name": re.sub(r"\r?\n", " ", str(c.get("name") or "未命名")).strip() or "未命名"} for c in chars]
    payload = json.dumps(lst, ensure_ascii=False)
    if ck:
        _read_tool_cache_set(ctx, ck, payload)
    return ToolResult(payload)


@tool("getStoryBackground")
async def _tool_get_story_background(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    ck = build_read_cache_key("getStoryBackground", ctx, args)
    hit = _read_tool_cache_get(ctx, ck) if ck else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    row = await get_story_background(get_db(), bid)
    content = (row.get("content") if row else None) or "（暂无小说背景）"
    if ck:
        _read_tool_cache_set(ctx, ck, content)
    return ToolResult(content)


def _character_fields_from_args(args: dict) -> dict:
    """从工具参数提取人物字段（只取显式传入的，支持部分更新）。"""
    return {
        col: str(args[arg])
        for arg, col in CHARACTER_ARG_COLUMNS.items()
        if args.get(arg) is not None
    }


def _send_setting_updated(send_chunk: Callable | None, kind: str, **extra) -> None:
    if send_chunk:
        send_chunk({"settingUpdated": {"kind": kind, **extra}})


@tool("createCharacter")
async def _tool_create_character(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法创建人物"})
    name = str(args.get("name") or "").strip()
    if not name:
        return _err({"success": False, "error": "缺少人物名称 name"})

    data = _character_fields_from_args(args)
    data["name"] = name
    try:
        row = await create_character(get_db(), str(bid), data)
        if not row:
            return _err({"success": False, "error": "创建人物失败"})
        _invalidate_read_tool_cache(ctx)
        _send_setting_updated(send_chunk, "character", action="create", id=row.get("id"), name=name)
        return ToolResult(json.dumps({
            "success": True,
            "characterId": row.get("id"),
            "name": name,
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("updateCharacter")
async def _tool_update_character(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    try:
        cid = int(args.get("characterId"))
    except (TypeError, ValueError):
        return _err({"success": False, "error": "缺少有效 characterId（来自 listBookCharacters / getBookCharacters）"})

    data = _character_fields_from_args(args)
    if not data:
        return _err({"success": False, "error": f"缺少可更新字段（{'/'.join(CHARACTER_ARG_COLUMNS)}）"})

    try:
        db = get_db()
        chars = await get_characters(db, str(bid)) or []
        if not any(c.get("id") == cid for c in chars):
            return _err({"success": False, "error": "characterId 不属于当前书籍，或该人物不存在", "characterId": cid})

        row = await update_character(db, cid, data)
        if not row:
            return _err({"success": False, "error": "更新人物失败"})
        _invalidate_read_tool_cache(ctx)
        _send_setting_updated(send_chunk, "character", action="update", id=cid, name=row.get("name"))
        return ToolResult(json.dumps({
            "success": True,
            "characterId": cid,
            "name": row.get("name"),
            "updatedFields": sorted(data.keys()),
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("editStoryBackground")
async def _tool_edit_story_background(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    content = args.get("content")
    if not isinstance(content, str):
        return _err({"success": False, "error": "content 必须为字符串（小说背景 Markdown 全文，覆盖写入）"})

    try:
        await save_story_background(get_db(), str(bid), content)
        _invalidate_read_tool_cache(ctx)
        _send_setting_updated(send_chunk, "background", action="update")
        return ToolResult(json.dumps({
            "success": True,
            "bookId": str(bid),
            "contentLength": len(content),
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("getBookStyle")
async def _tool_get_book_style(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    ck = build_read_cache_key("getBookStyle", ctx, args)
    hit = _read_tool_cache_get(ctx, ck) if ck else None
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
    if ck:
        _read_tool_cache_set(ctx, ck, content)
    return ToolResult(content)
