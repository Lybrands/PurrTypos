from __future__ import annotations

from fastapi import APIRouter

from database.crud import story_background as story_background_crud
from dependencies import get_db
from schemas.story_background import SaveStoryBackgroundRequest

router = APIRouter(tags=["story-background"])


@router.get("/story-background/{bookId}")
async def get_story_background(bookId: str):
    db = get_db()
    row = await story_background_crud.get_story_background(db, bookId)
    return {"success": True, "data": row}


@router.put("/story-background/{bookId}")
async def save_story_background(bookId: str, body: SaveStoryBackgroundRequest):
    db = get_db()
    await story_background_crud.save_story_background(db, bookId, body.content)
    return {"success": True}


@router.get("/story-background/{bookId}/attachments")
async def get_attachments(bookId: str):
    db = get_db()
    rows = await db.fetch_all(
        "SELECT * FROM story_background_attachments WHERE book_id = ? ORDER BY create_time DESC",
        [bookId],
    )
    return {"success": True, "data": rows}


@router.post("/story-background/{bookId}/attachments")
async def add_attachment(bookId: str):
    return {"success": False, "error": "File upload via API not supported — use Electron dialog"}


@router.delete("/story-background/attachments/{id}")
async def delete_attachment(id: int):
    db = get_db()
    row = await db.fetch_one(
        "SELECT stored_path FROM story_background_attachments WHERE id = ?", [id]
    )
    stored_path = row["stored_path"] if row else None
    await db.execute(
        "DELETE FROM story_background_attachments WHERE id = ?", [id]
    )
    return {"success": True, "data": {"storedPath": stored_path}}
