"""Book context tool handlers: characters, background, and style."""

from __future__ import annotations

import json
import re
from typing import Callable

from database.crud.book_style import get_book_style
from database.crud.characters import (
    create_character,
    delete_character,
    get_characters,
)
from database.crud.setting_entities import (
    ENTITY_TYPE_LABELS,
    create_setting_entity,
    delete_setting_entity,
    get_setting_entities,
    normalize_entity_type,
)
from database.crud.story_background import (
    get_story_background,
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
from utils.text import format_characters_as_text, format_setting_entities_as_text

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


def _character_snapshot(row: dict) -> dict:
    return {
        "name": str(row.get("name") or ""),
        "tags": str(row.get("tags") or ""),
        "profileMd": str(row.get("profile_md") or ""),
    }


def _merge_character_proposed(current: dict, updates: dict) -> dict:
    snap = _character_snapshot(current)
    if "name" in updates:
        snap["name"] = str(updates["name"])
    if "tags" in updates:
        snap["tags"] = str(updates["tags"])
    if "profile_md" in updates:
        snap["profileMd"] = str(updates["profile_md"])
    return snap


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
    """自 v3.2 起 updateCharacter 不再直接落库，改为提交设定 diff 给前端审阅。"""
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
        current = next((c for c in chars if c.get("id") == cid), None)
        if not current:
            return _err({"success": False, "error": "characterId 不属于当前书籍，或该人物不存在", "characterId": cid})

        before = _character_snapshot(current)
        proposed = _merge_character_proposed(current, data)
        if before == proposed:
            return ToolResult(json.dumps({
                "success": True,
                "message": "新设定与现有一致，无需变更",
                "characterId": cid,
                "noop": True,
            }, ensure_ascii=False))

        _invalidate_read_tool_cache(ctx)
        if send_chunk:
            send_chunk({
                "proposedSettingDiff": {
                    "kind": "character",
                    "bookId": str(bid),
                    "characterId": cid,
                    "characterName": proposed.get("name") or before.get("name") or "",
                    "before": before,
                    "proposed": proposed,
                    "source": "ai_tool_edit",
                },
            })
        return ToolResult(json.dumps({
            "success": True,
            "message": "已向用户提交人物设定差异预览，需用户在设定面板接受/拒绝后才会写入",
            "characterId": cid,
            "name": proposed.get("name") or before.get("name"),
            "pendingUserApproval": True,
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("deleteCharacter")
async def _tool_delete_character(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    """物理删除人物（不可恢复）。SKILL.md 约束模型：先向用户复述并获明确同意再调用。"""
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    try:
        cid = int(args.get("characterId"))
    except (TypeError, ValueError):
        return _err({"success": False, "error": "缺少有效 characterId（来自 listBookCharacters / getBookCharacters）"})

    try:
        db = get_db()
        chars = await get_characters(db, str(bid)) or []
        target = next((c for c in chars if c.get("id") == cid), None)
        if not target:
            return _err({"success": False, "error": "characterId 不属于当前书籍，或该人物不存在", "characterId": cid})

        name = str(target.get("name") or "")
        await delete_character(db, cid)
        _invalidate_read_tool_cache(ctx)
        _send_setting_updated(send_chunk, "character", action="delete", id=cid, name=name)
        return ToolResult(json.dumps({
            "success": True,
            "message": f"已删除人物「{name}」（不可恢复）",
            "characterId": cid,
            "name": name,
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("editStoryBackground")
async def _tool_edit_story_background(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    """自 v3.2 起 editStoryBackground 不再直接落库，改为提交设定 diff 给前端审阅。"""
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    content = args.get("content")
    if not isinstance(content, str):
        return _err({"success": False, "error": "content 必须为字符串（小说背景 Markdown 全文，覆盖写入）"})

    try:
        db = get_db()
        row = await get_story_background(db, str(bid))
        before_content = (row.get("content") if row else None) or ""
        if (content or "").strip() == (before_content or "").strip() and before_content.strip():
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
    except Exception as e:
        return _err({"success": False, "error": str(e)})


# ── 世界设定实体（地点 / 势力 / 物品 / 其他）─────────────────────────

ENTITY_ARG_COLUMNS = {
    "name": "name",
    "tags": "tags",
    "profileMd": "profile_md",
}


def _entity_fields_from_args(args: dict) -> dict:
    return {
        col: str(args[arg])
        for arg, col in ENTITY_ARG_COLUMNS.items()
        if args.get(arg) is not None
    }


def _entity_snapshot(row: dict) -> dict:
    return {
        "name": str(row.get("name") or ""),
        "tags": str(row.get("tags") or ""),
        "profileMd": str(row.get("profile_md") or ""),
    }


def _merge_entity_proposed(current: dict, updates: dict) -> dict:
    snap = _entity_snapshot(current)
    if "name" in updates:
        snap["name"] = str(updates["name"])
    if "tags" in updates:
        snap["tags"] = str(updates["tags"])
    if "profile_md" in updates:
        snap["profileMd"] = str(updates["profile_md"])
    return snap


@tool("listSettingEntities")
async def _tool_list_setting_entities(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    ck = build_read_cache_key("listSettingEntities", ctx, args)
    hit = _read_tool_cache_get(ctx, ck) if ck else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    rows = await get_setting_entities(get_db(), str(bid)) or []
    lst = [{
        "id": e.get("id"),
        "type": e.get("entity_type"),
        "typeLabel": ENTITY_TYPE_LABELS.get(str(e.get("entity_type") or ""), "其他"),
        "name": re.sub(r"\r?\n", " ", str(e.get("name") or "未命名")).strip() or "未命名",
    } for e in rows]
    payload = json.dumps(lst, ensure_ascii=False)
    if ck:
        _read_tool_cache_set(ctx, ck, payload)
    return ToolResult(payload)


@tool("getSettingEntities")
async def _tool_get_setting_entities(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    ent_ids = args.get("entityIds")
    name_queries = args.get("names")
    type_filter = str(args.get("entityType") or "").strip()
    ck = build_read_cache_key("getSettingEntities", ctx, args)

    if ck is not None:
        hit = _read_tool_cache_get(ctx, ck)
        if hit is not None:
            return ToolResult(hit, from_cache=True)

    rows = await get_setting_entities(get_db(), str(bid)) or []
    if type_filter:
        tf = normalize_entity_type(type_filter)
        rows = [e for e in rows if str(e.get("entity_type")) == tf]
    if isinstance(ent_ids, list) and ent_ids:
        id_set = set()
        for x in ent_ids:
            try:
                id_set.add(int(x))
            except (ValueError, TypeError):
                pass
        rows = [e for e in rows if e.get("id") in id_set]
    elif isinstance(name_queries, list) and name_queries:
        needles = [str(n).strip().lower() for n in name_queries if str(n).strip()]
        if needles:
            rows = [e for e in rows if any(q in str(e.get("name") or "").lower() for q in needles)]

    content = format_setting_entities_as_text(rows, ENTITY_TYPE_LABELS) or "（暂无世界设定条目）"
    if ck is not None:
        _read_tool_cache_set(ctx, ck, content)
    return ToolResult(content)


@tool("createSettingEntity")
async def _tool_create_setting_entity(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法创建设定条目"})
    name = str(args.get("name") or "").strip()
    if not name:
        return _err({"success": False, "error": "缺少条目名称 name"})
    entity_type = normalize_entity_type(args.get("entityType"))

    data = _entity_fields_from_args(args)
    data["name"] = name
    data["entity_type"] = entity_type
    try:
        row = await create_setting_entity(get_db(), str(bid), data)
        if not row:
            return _err({"success": False, "error": "创建设定条目失败"})
        _invalidate_read_tool_cache(ctx)
        _send_setting_updated(
            send_chunk, "entity", action="create",
            id=row.get("id"), name=name, entityType=entity_type,
        )
        return ToolResult(json.dumps({
            "success": True,
            "entityId": row.get("id"),
            "entityType": entity_type,
            "name": name,
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("updateSettingEntity")
async def _tool_update_setting_entity(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    """与 updateCharacter 一致：提议制，提交设定 diff 给前端审阅后才落库。"""
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    try:
        eid = int(args.get("entityId"))
    except (TypeError, ValueError):
        return _err({"success": False, "error": "缺少有效 entityId（来自 listSettingEntities / getSettingEntities）"})

    data = _entity_fields_from_args(args)
    if not data:
        return _err({"success": False, "error": f"缺少可更新字段（{'/'.join(ENTITY_ARG_COLUMNS)}）"})

    try:
        db = get_db()
        rows = await get_setting_entities(db, str(bid)) or []
        current = next((e for e in rows if e.get("id") == eid), None)
        if not current:
            return _err({"success": False, "error": "entityId 不属于当前书籍，或该条目不存在", "entityId": eid})

        before = _entity_snapshot(current)
        proposed = _merge_entity_proposed(current, data)
        if before == proposed:
            return ToolResult(json.dumps({
                "success": True,
                "message": "新设定与现有一致，无需变更",
                "entityId": eid,
                "noop": True,
            }, ensure_ascii=False))

        _invalidate_read_tool_cache(ctx)
        if send_chunk:
            send_chunk({
                "proposedSettingDiff": {
                    "kind": "entity",
                    "bookId": str(bid),
                    "entityId": eid,
                    "entityType": str(current.get("entity_type") or "other"),
                    "entityName": proposed.get("name") or before.get("name") or "",
                    "before": before,
                    "proposed": proposed,
                    "source": "ai_tool_edit",
                },
            })
        return ToolResult(json.dumps({
            "success": True,
            "message": "已向用户提交设定条目差异预览，需用户在设定面板接受/拒绝后才会写入",
            "entityId": eid,
            "name": proposed.get("name") or before.get("name"),
            "pendingUserApproval": True,
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("deleteSettingEntity")
async def _tool_delete_setting_entity(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    """物理删除设定条目及其历史（不可恢复）。SKILL.md 约束模型：先复述并获用户明确同意。"""
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    try:
        eid = int(args.get("entityId"))
    except (TypeError, ValueError):
        return _err({"success": False, "error": "缺少有效 entityId（来自 listSettingEntities / getSettingEntities）"})

    try:
        db = get_db()
        rows = await get_setting_entities(db, str(bid)) or []
        target = next((e for e in rows if e.get("id") == eid), None)
        if not target:
            return _err({"success": False, "error": "entityId 不属于当前书籍，或该条目不存在", "entityId": eid})

        name = str(target.get("name") or "")
        type_label = ENTITY_TYPE_LABELS.get(str(target.get("entity_type") or ""), "其他")
        await delete_setting_entity(db, eid)
        _invalidate_read_tool_cache(ctx)
        _send_setting_updated(send_chunk, "entity", action="delete", id=eid, name=name)
        return ToolResult(json.dumps({
            "success": True,
            "message": f"已删除{type_label}设定「{name}」（不可恢复）",
            "entityId": eid,
            "name": name,
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
