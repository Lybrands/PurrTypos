from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from database.crud import character_history as char_hist_crud
from database.crud import characters as characters_crud
from dependencies import get_db
from schemas.characters import (
    CharacterOptionRequest,
    CreateCharacterRequest,
    UpdateCharacterOptionRequest,
    UpdateCharacterRequest,
)

router = APIRouter(tags=["characters"])


@router.get("/books/{bookId}/characters")
async def get_characters(bookId: str):
    db = get_db()
    rows = await db.fetch_all(
        "SELECT * FROM characters WHERE book_id = ? ORDER BY create_time ASC",
        [bookId],
    )
    return {"success": True, "data": rows}


@router.post("/books/{bookId}/characters")
async def create_character(bookId: str, body: CreateCharacterRequest):
    db = get_db()
    from services import memory_deposition_service

    async with db.transaction(cancellation_linearizable=True):
        row = await characters_crud.create_character(db, bookId, body.data)
        if not row:
            return {"success": False, "error": "创建失败"}
        delivery_keys = await memory_deposition_service.record_manual_character(db, row)
    deliveries = await memory_deposition_service.deliver_recorded(db, delivery_keys)
    return {
        "success": True,
        "data": row,
        "memoryDelivery": [item.to_dict() for item in deliveries],
    }


@router.put("/characters/{id}")
async def update_character(id: str, body: UpdateCharacterRequest):
    db = get_db()
    try:
        cid = int(id)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的人物 ID"}
    before = await db.fetch_one("SELECT * FROM characters WHERE id = ?", [cid])
    if not before:
        return {"success": False, "error": "人物不存在"}
    from services import memory_deposition_service

    async with db.transaction(cancellation_linearizable=True):
        row = await characters_crud.update_character(db, cid, body.data)
        if not row:
            return {"success": False, "error": "人物不存在"}
        await char_hist_crud.insert_character_history(
            db,
            character_id=cid,
            before_name=before.get("name") or "",
            before_tags=before.get("tags") or "",
            before_profile_md=before.get("profile_md") or "",
            after_name=row.get("name") or "",
            after_tags=row.get("tags") or "",
            after_profile_md=row.get("profile_md") or "",
            source="user",
        )
        delivery_keys = await memory_deposition_service.record_manual_character(db, row)
    deliveries = await memory_deposition_service.deliver_recorded(db, delivery_keys)
    return {
        "success": True,
        "data": row,
        "memoryDelivery": [item.to_dict() for item in deliveries],
    }


@router.delete("/characters/{id}")
async def delete_character(id: str):
    db = get_db()
    try:
        cid = int(id)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的人物 ID"}
    from services import memory_deposition_service

    row = await db.fetch_one("SELECT book_id FROM characters WHERE id = ?", [cid])
    if row is None:
        return {"success": False, "error": "人物不存在"}
    async with db.transaction(cancellation_linearizable=True):
        await characters_crud.delete_character(db, cid)
        delivery_keys = await memory_deposition_service.record_deleted_source(
            db,
            book_id=str(row["book_id"]),
            source_base=f"character:{cid}",
        )
    deliveries = await memory_deposition_service.deliver_recorded(db, delivery_keys)
    return {
        "success": True,
        "memoryDelivery": [item.to_dict() for item in deliveries],
    }


# --- Character Options ---

@router.get("/character-options")
async def get_character_options(category: Optional[str] = Query(None)):
    db = get_db()
    if category:
        rows = await db.fetch_all(
            "SELECT * FROM character_options WHERE category = ? ORDER BY id ASC",
            [category],
        )
    else:
        rows = await db.fetch_all(
            "SELECT * FROM character_options ORDER BY id ASC"
        )
    return {"success": True, "data": rows}


@router.post("/character-options")
async def add_character_option(body: CharacterOptionRequest):
    db = get_db()
    row_id = await db.execute_and_get_id(
        "INSERT INTO character_options (category, value) VALUES (?, ?)",
        [body.category, body.value],
    )
    row = await db.fetch_one("SELECT * FROM character_options WHERE id = ?", [row_id])
    return {"success": True, "data": row}


@router.put("/character-options/{id}")
async def update_character_option(id: int, body: UpdateCharacterOptionRequest):
    db = get_db()
    await db.execute(
        "UPDATE character_options SET value = ? WHERE id = ?",
        [body.value, id],
    )
    row = await db.fetch_one("SELECT * FROM character_options WHERE id = ?", [id])
    return {"success": True, "data": row}


@router.delete("/character-options/{id}")
async def delete_character_option(id: int):
    db = get_db()
    await db.execute("DELETE FROM character_options WHERE id = ?", [id])
    return {"success": True}
