from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from database.crud.outlines import get_associable_outlines
from dependencies import get_db
from schemas.outlines import SaveOutlineRequest, UpdateOutlineRequest
from utils.id_utils import short_id8

router = APIRouter(tags=["outlines"])


@router.get("/outlines")
async def get_outlines(type: Optional[str] = Query(None)):
    db = get_db()
    if type:
        rows = await db.fetch_all(
            "SELECT * FROM outlines WHERE type = ? ORDER BY create_time ASC",
            [type],
        )
    else:
        rows = await db.fetch_all(
            "SELECT * FROM outlines ORDER BY create_time ASC"
        )
    return {"success": True, "data": rows}


@router.post("/outlines")
async def save_outline(body: SaveOutlineRequest):
    db = get_db()
    outline_id = short_id8()
    await db.execute(
        """INSERT INTO outlines
           (id, title, type, book_id, xmind_data, file_path,
            markdown_content, writing_chapter_id, parent_outline_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            outline_id, body.title, body.type or "chapter", body.book_id,
            body.xmind_data, body.file_path, body.markdown_content,
            body.writing_chapter_id, body.parent_outline_id,
        ],
    )
    row = await db.fetch_one("SELECT * FROM outlines WHERE id = ?", [outline_id])
    return {"success": True, "data": row}


@router.put("/outlines/{outlineId}")
async def update_outline(outlineId: str, body: UpdateOutlineRequest):
    db = get_db()
    fields = []
    values = []
    for field_name in ["title", "xmind_data", "file_path", "markdown_content", "book_id", "type"]:
        val = getattr(body, field_name, None)
        if val is not None:
            fields.append(f"{field_name} = ?")
            values.append(val)
    if fields:
        values.append(outlineId)
        await db.execute(
            f"UPDATE outlines SET {', '.join(fields)} WHERE id = ?",
            values,
        )
    row = await db.fetch_one("SELECT * FROM outlines WHERE id = ?", [outlineId])
    return {"success": True, "data": row}


@router.delete("/outlines/{outlineId}")
async def delete_outline(outlineId: str):
    db = get_db()
    await db.execute("DELETE FROM outline_chapters WHERE outline_id = ?", [outlineId])
    await db.execute("DELETE FROM outlines WHERE id = ?", [outlineId])
    return {"success": True}


@router.get("/outlines/volume/{bookId}")
async def get_volume_outlines(bookId: str):
    db = get_db()
    volumes = await db.fetch_all(
        "SELECT * FROM outlines WHERE book_id = ? AND type = 'volume' ORDER BY create_time ASC",
        [bookId],
    )
    for vol in volumes:
        chapters = await db.fetch_all(
            "SELECT * FROM outlines WHERE parent_outline_id = ? AND type = 'chapter' ORDER BY create_time ASC",
            [vol["id"]],
        )
        vol["chapters"] = chapters
    return {"success": True, "data": volumes}


@router.get("/outlines/global/{bookId}")
async def get_global_outline(bookId: str):
    db = get_db()
    row = await db.fetch_one(
        "SELECT * FROM outlines WHERE book_id = ? AND type = 'global' LIMIT 1",
        [bookId],
    )
    return {"success": True, "data": row}


@router.post("/outlines/global/{bookId}/ensure")
async def ensure_global_outline(bookId: str):
    db = get_db()
    row = await db.fetch_one(
        "SELECT * FROM outlines WHERE book_id = ? AND type = 'global' LIMIT 1",
        [bookId],
    )
    if row is None:
        outline_id = short_id8()
        await db.execute(
            "INSERT INTO outlines (id, title, type, book_id) VALUES (?, ?, 'global', ?)",
            [outline_id, "总纲", bookId],
        )
        row = await db.fetch_one("SELECT * FROM outlines WHERE id = ?", [outline_id])
    return {"success": True, "data": row}


@router.get("/outlines/writing/{bookId}")
async def get_writing_outline(bookId: str):
    db = get_db()
    row = await db.fetch_one(
        "SELECT * FROM outlines WHERE book_id = ? AND type = 'writing' LIMIT 1",
        [bookId],
    )
    if row is None:
        outline_id = short_id8()
        await db.execute(
            "INSERT INTO outlines (id, title, type, book_id) VALUES (?, ?, 'writing', ?)",
            [outline_id, "写作章节", bookId],
        )
        row = await db.fetch_one("SELECT * FROM outlines WHERE id = ?", [outline_id])
    return {"success": True, "data": row}


@router.get("/outlines/chapter/{bookId}")
async def get_chapter_outlines(bookId: str):
    db = get_db()
    rows = await db.fetch_all(
        "SELECT * FROM outlines WHERE book_id = ? AND type = 'chapter' ORDER BY create_time ASC",
        [bookId],
    )
    return {"success": True, "data": rows}


@router.get("/outlines/associable/{bookId}")
async def list_associable_outlines(bookId: str):
    """AI 关联章节大纲列表；顺序与左侧大纲面板章节区一致。"""
    db = get_db()
    rows = await get_associable_outlines(db, bookId)
    return {"success": True, "data": rows}


@router.get("/outlines/by-writing-chapter/{writingChapterId}")
async def get_outline_by_writing_chapter(writingChapterId: str):
    db = get_db()
    chapter = await db.fetch_one(
        "SELECT * FROM outline_chapters WHERE id = ?", [writingChapterId]
    )
    if chapter is None:
        return {"success": True, "data": None}
    outline = await db.fetch_one(
        "SELECT * FROM outlines WHERE id = ?", [chapter["outline_id"]]
    )
    return {"success": True, "data": outline}
