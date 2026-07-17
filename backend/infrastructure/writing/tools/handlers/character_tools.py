"""Tool handlers for reading and changing book characters."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Callable

from database.crud.characters import create_character, delete_character, get_characters
from domains.writing.tools.setting_helpers import (
    SETTING_ARG_COLUMNS,
    fields_from_args,
    filter_setting_rows,
    merge_setting_proposal,
    send_setting_updated,
    setting_snapshot,
)
from domains.writing.tools.cache import build_read_cache_key
from domains.writing.tools.contracts import ToolResult, _err
from domains.writing.tools.scope import resolve_book_id_for_tools
from domains.writing.tools.state import (
    _invalidate_read_tool_cache,
    _read_tool_cache_get,
    _read_tool_cache_set,
)
from utils.text import format_characters_as_text

if TYPE_CHECKING:
    from infrastructure.writing.tools.runtime import WritingToolDependencies


async def _tool_get_book_characters(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    char_ids = args.get("characterIds")
    name_queries = args.get("names")
    # None means this is a filtered read, which intentionally bypasses cache.
    cache_key = build_read_cache_key("getBookCharacters", ctx, args)

    if cache_key is not None:
        hit = _read_tool_cache_get(ctx, cache_key)
        if hit is not None:
            return ToolResult(hit, from_cache=True)

    characters = await get_characters(dependencies.db, bid) or []
    characters = filter_setting_rows(
        characters, ids=char_ids, names=name_queries,
    )
    content = format_characters_as_text(characters) or "（暂无人物）"
    if cache_key is not None:
        _read_tool_cache_set(ctx, cache_key, content)
    return ToolResult(content)


async def _tool_list_book_characters(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    cache_key = build_read_cache_key("listBookCharacters", ctx, args)
    hit = _read_tool_cache_get(ctx, cache_key) if cache_key else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    characters = await get_characters(dependencies.db, bid) or []
    summary = [
        {
            "id": character.get("id"),
            "name": re.sub(
                r"\r?\n", " ", str(character.get("name") or "未命名"),
            ).strip() or "未命名",
        }
        for character in characters
    ]
    payload = json.dumps(summary, ensure_ascii=False)
    if cache_key:
        _read_tool_cache_set(ctx, cache_key, payload)
    return ToolResult(payload)


async def _tool_create_character(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法创建人物"})
    name = str(args.get("name") or "").strip()
    if not name:
        return _err({"success": False, "error": "缺少人物名称 name"})

    data = fields_from_args(args)
    data["name"] = name
    try:
        row = await create_character(dependencies.db, str(bid), data)
        if not row:
            return _err({"success": False, "error": "创建人物失败"})
        _invalidate_read_tool_cache(ctx)
        send_setting_updated(
            send_chunk, "character", action="create", id=row.get("id"), name=name,
        )
        return ToolResult(json.dumps({
            "success": True,
            "characterId": row.get("id"),
            "name": name,
        }, ensure_ascii=False))
    except Exception as error:
        return _err({"success": False, "error": str(error)})


async def _tool_update_character(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    """Submit a character diff for review instead of writing immediately."""
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    try:
        character_id = int(args.get("characterId"))
    except (TypeError, ValueError):
        return _err({
            "success": False,
            "error": "缺少有效 characterId（来自 listBookCharacters / getBookCharacters）",
        })

    data = fields_from_args(args)
    if not data:
        fields = "/".join(SETTING_ARG_COLUMNS)
        return _err({"success": False, "error": f"缺少可更新字段（{fields}）"})

    try:
        characters = await get_characters(dependencies.db, str(bid)) or []
        current = next(
            (character for character in characters if character.get("id") == character_id),
            None,
        )
        if not current:
            return _err({
                "success": False,
                "error": "characterId 不属于当前书籍，或该人物不存在",
                "characterId": character_id,
            })

        before = setting_snapshot(current)
        proposed = merge_setting_proposal(current, data)
        if before == proposed:
            return ToolResult(json.dumps({
                "success": True,
                "message": "新设定与现有一致，无需变更",
                "characterId": character_id,
                "noop": True,
            }, ensure_ascii=False))

        _invalidate_read_tool_cache(ctx)
        if send_chunk:
            send_chunk({
                "proposedSettingDiff": {
                    "kind": "character",
                    "bookId": str(bid),
                    "characterId": character_id,
                    "characterName": proposed.get("name") or before.get("name") or "",
                    "before": before,
                    "proposed": proposed,
                    "source": "ai_tool_edit",
                },
            })
        return ToolResult(json.dumps({
            "success": True,
            "message": "已向用户提交人物设定差异预览，需用户在设定面板接受/拒绝后才会写入",
            "characterId": character_id,
            "name": proposed.get("name") or before.get("name"),
            "pendingUserApproval": True,
        }, ensure_ascii=False))
    except Exception as error:
        return _err({"success": False, "error": str(error)})


async def _tool_delete_character(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    """Physically delete a character after the caller has confirmed intent."""
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    try:
        character_id = int(args.get("characterId"))
    except (TypeError, ValueError):
        return _err({
            "success": False,
            "error": "缺少有效 characterId（来自 listBookCharacters / getBookCharacters）",
        })

    try:
        db = dependencies.db
        characters = await get_characters(db, str(bid)) or []
        target = next(
            (character for character in characters if character.get("id") == character_id),
            None,
        )
        if not target:
            return _err({
                "success": False,
                "error": "characterId 不属于当前书籍，或该人物不存在",
                "characterId": character_id,
            })

        name = str(target.get("name") or "")
        await delete_character(db, character_id)
        _invalidate_read_tool_cache(ctx)
        send_setting_updated(
            send_chunk, "character", action="delete", id=character_id, name=name,
        )
        return ToolResult(json.dumps({
            "success": True,
            "message": f"已删除人物「{name}」（不可恢复）",
            "characterId": character_id,
            "name": name,
        }, ensure_ascii=False))
    except Exception as error:
        return _err({"success": False, "error": str(error)})
