from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

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
    row = await characters_crud.create_character(db, bookId, body.data)
    if not row:
        return {"success": False, "error": "创建失败"}
    return {"success": True, "data": row}


@router.put("/characters/{id}")
async def update_character(id: str, body: UpdateCharacterRequest):
    db = get_db()
    try:
        cid = int(id)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的人物 ID"}
    row = await characters_crud.update_character(db, cid, body.data)
    if not row:
        return {"success": False, "error": "人物不存在"}
    return {"success": True, "data": row}


@router.delete("/characters/{id}")
async def delete_character(id: str):
    db = get_db()
    try:
        cid = int(id)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的人物 ID"}
    await characters_crud.delete_character(db, cid)
    return {"success": True}


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
