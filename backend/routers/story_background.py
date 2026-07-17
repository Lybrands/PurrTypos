from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from config import DATA_DIR
from database.crud import story_background as story_background_crud
from database.crud import story_background_history as bg_hist_crud
from dependencies import get_db
from schemas.story_background import SaveStoryBackgroundRequest
from utils.file_storage import safe_unlink_stored_file

router = APIRouter(tags=["story-background"])


class AttachmentInput(BaseModel):
    name: str
    storedPath: str = Field(alias="stored_path")

    model_config = {"populate_by_name": True}


class AddAttachmentsRequest(BaseModel):
    attachments: list[AttachmentInput] = Field(default_factory=list)


@router.get("/story-background/{bookId}")
async def get_story_background(bookId: str):
    db = get_db()
    row = await story_background_crud.get_story_background(db, bookId)
    return {"success": True, "data": row}


@router.put("/story-background/{bookId}")
async def save_story_background(bookId: str, body: SaveStoryBackgroundRequest):
    db = get_db()
    before_row = await story_background_crud.get_story_background(db, bookId)
    before_content = (before_row.get("content") if before_row else None) or ""
    await story_background_crud.save_story_background(db, bookId, body.content)
    await bg_hist_crud.insert_story_background_history(
        db,
        book_id=bookId,
        before_content=before_content,
        after_content=body.content,
        source="user",
    )
    try:
        from services import memory_deposition_service
        await memory_deposition_service.deposit_manual_background_memory(bookId, body.content)
    except Exception:
        pass
    return {"success": True}


@router.get("/story-background/{bookId}/attachments")
async def get_attachments(bookId: str):
    db = get_db()
    rows = await db.fetch_all(
        "SELECT * FROM story_background_attachments WHERE book_id = ? ORDER BY create_time ASC",
        [bookId],
    )
    return {"success": True, "data": rows}


@router.post("/story-background/{bookId}/attachments")
async def add_attachment(bookId: str, body: AddAttachmentsRequest):
    db = get_db()
    added = []
    async with db.transaction():
        for item in body.attachments:
            row = await story_background_crud.add_story_background_attachment(
                db,
                bookId,
                item.name,
                item.storedPath,
            )
            if row:
                added.append(row)
    rows = await story_background_crud.get_story_background_attachments(db, bookId)
    return {"success": True, "data": rows, "addedCount": len(added)}


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
    safe_unlink_stored_file(stored_path, DATA_DIR)
    return {"success": True, "data": {"storedPath": stored_path}}
