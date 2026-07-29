from __future__ import annotations

import re
import secrets
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
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


@router.post("/story-background/{bookId}/attachments/upload")
async def upload_attachments(
    bookId: str,
    request: Request,
    fileName: str = Query(default="attachment"),
):
    base = DATA_DIR if DATA_DIR and DATA_DIR != Path("") else Path(".")
    attachment_dir = base / "story-background-attachments" / bookId
    attachment_dir.mkdir(parents=True, exist_ok=True)
    original_name = Path(fileName or "attachment").name
    safe_name = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", original_name)
    stored_name = f"{secrets.token_hex(8)}-{safe_name or 'attachment'}"
    target = attachment_dir / stored_name
    total = 0
    with target.open("wb") as output:
        async for chunk in request.stream():
            total += len(chunk)
            if total > 100 * 1024 * 1024:
                output.close()
                target.unlink(missing_ok=True)
                return {"success": False, "error": "单个附件不能超过 100 MB"}
            output.write(chunk)
    relative = target.relative_to(base).as_posix()

    db = get_db()
    await story_background_crud.add_story_background_attachment(
        db, bookId, original_name, relative
    )
    rows = await story_background_crud.get_story_background_attachments(db, bookId)
    return {"success": True, "data": rows, "addedCount": 1}


@router.get("/story-background/attachments/content")
async def open_attachment(storedPath: str = Query(...)):
    base = DATA_DIR if DATA_DIR and DATA_DIR != Path("") else Path(".")
    root = base.resolve()
    target = (root / storedPath).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return {"success": False, "error": "非法附件路径"}
    if not target.is_file():
        return {"success": False, "error": "附件不存在"}
    return FileResponse(target, filename=target.name.split("-", 1)[-1])


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
