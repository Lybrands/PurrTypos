"""Tool handlers for world-setting entities such as places and factions."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Callable

from database.crud.setting_entities import (
    ENTITY_TYPE_LABELS,
    create_setting_entity,
    delete_setting_entity,
    get_setting_entities,
    normalize_entity_type,
)
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
from utils.text import format_setting_entities_as_text

if TYPE_CHECKING:
    from infrastructure.writing.tools.runtime import WritingToolDependencies


async def _tool_list_setting_entities(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    from application.creation_material_service import materials
    from application.creation_material_service import materials
    cache_key = None if await materials(dependencies.db).binding(str(bid)) else build_read_cache_key("getSettingEntities", ctx, args) if await materials(dependencies.db).binding(str(bid)) else build_read_cache_key("listSettingEntities", ctx, args)
    hit = _read_tool_cache_get(ctx, cache_key) if cache_key else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    rows = await get_setting_entities(dependencies.db, str(bid)) or []
    summary = [
        {
            "id": entity.get("id"),
            **({"materialLink": entity["materialLink"]} if entity.get("materialLink") else {}),
            "type": entity.get("entity_type"),
            "typeLabel": ENTITY_TYPE_LABELS.get(
                str(entity.get("entity_type") or ""), "其他",
            ),
            "name": re.sub(
                r"\r?\n", " ", str(entity.get("name") or "未命名"),
            ).strip() or "未命名",
        }
        for entity in rows
    ]
    payload = json.dumps(summary, ensure_ascii=False)
    if cache_key:
        _read_tool_cache_set(ctx, cache_key, payload)
    return ToolResult(payload)


async def _tool_get_setting_entities(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    entity_ids = args.get("entityIds")
    name_queries = args.get("names")
    type_filter = str(args.get("entityType") or "").strip()
    cache_key = None

    if cache_key is not None:
        hit = _read_tool_cache_get(ctx, cache_key)
        if hit is not None:
            return ToolResult(hit, from_cache=True)

    rows = await get_setting_entities(dependencies.db, str(bid)) or []
    if type_filter:
        normalized_type = normalize_entity_type(type_filter)
        rows = [
            entity for entity in rows
            if str(entity.get("entity_type")) == normalized_type
        ]
    rows = filter_setting_rows(rows, ids=entity_ids, names=name_queries)

    content = (
        format_setting_entities_as_text(rows, ENTITY_TYPE_LABELS)
        or "（暂无世界设定条目）"
    )
    if cache_key is not None:
        _read_tool_cache_set(ctx, cache_key, content)
    return ToolResult(content)


async def _tool_create_setting_entity(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法创建设定条目"})
    name = str(args.get("name") or "").strip()
    if not name:
        return _err({"success": False, "error": "缺少条目名称 name"})
    entity_type = normalize_entity_type(args.get("entityType"))

    data = fields_from_args(args)
    data["name"] = name
    data["entity_type"] = entity_type
    try:
        row = await create_setting_entity(dependencies.db, str(bid), data)
        if not row:
            return _err({"success": False, "error": "创建设定条目失败"})
        _invalidate_read_tool_cache(ctx)
        send_setting_updated(
            send_chunk,
            "entity",
            action="create",
            id=row.get("id"),
            name=name,
            entityType=entity_type,
        )
        return ToolResult(json.dumps({
            "success": True,
            "entityId": row.get("id"),
            "entityType": entity_type,
            "name": name,
        }, ensure_ascii=False))
    except Exception as error:
        return _err({"success": False, "error": str(error)})


async def _tool_update_setting_entity(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    """Submit an entity diff for review instead of writing immediately."""
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    try:
        entity_id = int(args.get("entityId"))
    except (TypeError, ValueError):
        return _err({
            "success": False,
            "error": "缺少有效 entityId（来自 listSettingEntities / getSettingEntities）",
        })

    data = fields_from_args(args)
    if not data:
        fields = "/".join(SETTING_ARG_COLUMNS)
        return _err({"success": False, "error": f"缺少可更新字段（{fields}）"})

    try:
        rows = await get_setting_entities(dependencies.db, str(bid)) or []
        current = next(
            (entity for entity in rows if entity.get("id") == entity_id),
            None,
        )
        if not current:
            return _err({
                "success": False,
                "error": "entityId 不属于当前书籍，或该条目不存在",
                "entityId": entity_id,
            })

        from application.creation_material_service import materials
        data = await materials(dependencies.db).normalize_links(str(bid), data)
        before = setting_snapshot(current)
        proposed = merge_setting_proposal(current, data)
        if before == proposed:
            return ToolResult(json.dumps({
                "success": True,
                "message": "新设定与现有一致，无需变更",
                "entityId": entity_id,
                "noop": True,
            }, ensure_ascii=False))

        _invalidate_read_tool_cache(ctx)
        if send_chunk:
            send_chunk({
                "proposedSettingDiff": {
                    "kind": "entity",
                    "bookId": str(bid),
                    "entityId": entity_id,
                    "entityType": str(current.get("entity_type") or "other"),
                    "entityName": proposed.get("name") or before.get("name") or "",
                    "before": before,
                    "proposed": proposed,
                    "source": "ai_tool_edit",
                    "baseRevision": current.get("baseRevision"),
                },
            })
        return ToolResult(json.dumps({
            "success": True,
            "message": "已向用户提交设定条目差异预览，需用户在设定面板接受/拒绝后才会写入",
            "entityId": entity_id,
            "name": proposed.get("name") or before.get("name"),
            "pendingUserApproval": True,
        }, ensure_ascii=False))
    except Exception as error:
        return _err({"success": False, "error": str(error)})


async def _tool_delete_setting_entity(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    """Physically delete an entity after the caller has confirmed intent."""
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    try:
        entity_id = int(args.get("entityId"))
    except (TypeError, ValueError):
        return _err({
            "success": False,
            "error": "缺少有效 entityId（来自 listSettingEntities / getSettingEntities）",
        })

    try:
        db = dependencies.db
        rows = await get_setting_entities(db, str(bid)) or []
        target = next(
            (entity for entity in rows if entity.get("id") == entity_id),
            None,
        )
        if not target:
            return _err({
                "success": False,
                "error": "entityId 不属于当前书籍，或该条目不存在",
                "entityId": entity_id,
            })

        name = str(target.get("name") or "")
        type_label = ENTITY_TYPE_LABELS.get(
            str(target.get("entity_type") or ""), "其他",
        )
        await delete_setting_entity(db, entity_id, base_revision=target.get("baseRevision"))
        _invalidate_read_tool_cache(ctx)
        send_setting_updated(
            send_chunk, "entity", action="delete", id=entity_id, name=name,
        )
        return ToolResult(json.dumps({
            "success": True,
            "message": f"已删除{type_label}设定「{name}」",
            "entityId": entity_id,
            "name": name,
        }, ensure_ascii=False))
    except Exception as error:
        return _err({"success": False, "error": str(error)})
