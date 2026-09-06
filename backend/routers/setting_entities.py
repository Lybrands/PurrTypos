from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from database.crud import setting_entities as entities_crud
from database.crud import setting_entity_history as ent_hist_crud
from dependencies import get_db
from schemas.setting_entities import (
    CreateSettingEntityRequest,
    UpdateSettingEntityRequest,
)

router = APIRouter(tags=["setting-entities"])


@router.get("/books/{bookId}/setting-entities")
async def get_setting_entities(bookId: str, type: Optional[str] = Query(None)):
    db = get_db()
    rows = await entities_crud.get_setting_entities(db, bookId, entity_type=type)
    return {"success": True, "data": rows}


@router.post("/books/{bookId}/setting-entities")
async def create_setting_entity(bookId: str, body: CreateSettingEntityRequest):
    db = get_db()
    name = str(body.name or "").strip()
    if not name:
        return {"success": False, "error": "名称不能为空"}
    from services import memory_deposition_service

    async with db.transaction(cancellation_linearizable=True):
        row = await entities_crud.create_setting_entity(db, bookId, {
            "entity_type": body.entityType,
            "name": name,
            "tags": body.tags or "",
            "profile_md": body.profileMd or "",
        })
        if not row:
            return {"success": False, "error": "创建失败"}
        delivery_keys = await memory_deposition_service.record_manual_entity(db, row)
    deliveries = await memory_deposition_service.deliver_recorded(db, delivery_keys)
    return {"success": True, "data": row, "memoryDelivery": [item.to_dict() for item in deliveries]}


@router.put("/setting-entities/{id}")
async def update_setting_entity(id: str, body: UpdateSettingEntityRequest):
    db = get_db()
    try:
        eid = int(id)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的实体 ID"}
    before = await entities_crud.get_setting_entity(db, eid)
    if not before:
        return {"success": False, "error": "实体不存在"}

    data: dict = {}
    if body.entityType is not None:
        data["entity_type"] = body.entityType
    if body.name is not None:
        data["name"] = body.name
    if body.tags is not None:
        data["tags"] = body.tags
    if body.profileMd is not None:
        data["profile_md"] = body.profileMd

    from services import memory_deposition_service

    async with db.transaction(cancellation_linearizable=True):
        row = await entities_crud.update_setting_entity(db, eid, data)
        if not row:
            return {"success": False, "error": "实体不存在"}
        await ent_hist_crud.insert_entity_history(
            db,
            entity_id=eid,
            before_name=before.get("name") or "",
            before_tags=before.get("tags") or "",
            before_profile_md=before.get("profile_md") or "",
            after_name=row.get("name") or "",
            after_tags=row.get("tags") or "",
            after_profile_md=row.get("profile_md") or "",
            source="user",
        )
        delivery_keys = await memory_deposition_service.record_manual_entity(db, row)
    deliveries = await memory_deposition_service.deliver_recorded(db, delivery_keys)
    return {"success": True, "data": row, "memoryDelivery": [item.to_dict() for item in deliveries]}


@router.delete("/setting-entities/{id}")
async def delete_setting_entity(id: str):
    db = get_db()
    try:
        eid = int(id)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的实体 ID"}
    from services import memory_deposition_service

    row = await entities_crud.get_setting_entity(db, eid)
    if row is None:
        return {"success": False, "error": "实体不存在"}
    async with db.transaction(cancellation_linearizable=True):
        await entities_crud.delete_setting_entity(db, eid)
        delivery_keys = await memory_deposition_service.record_deleted_source(
            db,
            book_id=str(row["book_id"]),
            source_base=f"setting-entity:{eid}",
        )
    deliveries = await memory_deposition_service.deliver_recorded(db, delivery_keys)
    return {"success": True, "memoryDelivery": [item.to_dict() for item in deliveries]}
