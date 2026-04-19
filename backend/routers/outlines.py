from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from database.crud.outline_history import (
    get_outline_history,
    list_outline_history,
)
from database.crud.outlines import (
    get_associable_outlines,
    get_outline_by_writing_chapter_id,
    restore_outline_from_history,
    update_outline as crud_update_outline,
)
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
    """更新大纲。

    走 ``crud.outlines.update_outline`` 以便统一在写入前快照旧值到
    ``outline_history``；这条接口由用户 UI 触发，因此 source='user'。

    book_id / type 这两个字段不属于"内容修改"且历史里无快照需求，
    所以单独在外层 SQL 里更新（不走 history）。
    """
    db = get_db()
    payload: dict = {"outlineId": outlineId}
    for field_name in ["title", "xmind_data", "file_path", "markdown_content"]:
        val = getattr(body, field_name, None)
        if val is not None:
            payload[field_name] = val

    if len(payload) > 1:
        await crud_update_outline(db, payload, history_source="user")

    side_fields = []
    side_values = []
    for field_name in ["book_id", "type"]:
        val = getattr(body, field_name, None)
        if val is not None:
            side_fields.append(f"{field_name} = ?")
            side_values.append(val)
    if side_fields:
        side_values.append(outlineId)
        await db.execute(
            f"UPDATE outlines SET {', '.join(side_fields)} WHERE id = ?",
            side_values,
        )

    row = await db.fetch_one("SELECT * FROM outlines WHERE id = ?", [outlineId])
    return {"success": True, "data": row}


@router.get("/outlines/{outlineId}/history")
async def get_outline_history_list(outlineId: str, limit: int = 50):
    db = get_db()
    rows = await list_outline_history(db, outlineId, limit=limit)
    return {"success": True, "data": rows}


@router.get("/outlines/history/{historyId}")
async def get_outline_history_detail(historyId: int):
    db = get_db()
    row = await get_outline_history(db, historyId)
    return {"success": True, "data": row}


@router.post("/outlines/history/{historyId}/restore")
async def restore_outline_history(historyId: int):
    db = get_db()
    try:
        row = await restore_outline_from_history(db, historyId)
    except ValueError as e:
        return {"success": False, "error": str(e)}
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
    """旧语义：返回 writing chapter 所属的【外层 outline】（写作大纲整体）。
    保留以兼容历史调用。**注意**这通常不是用户在 OutlinePanel 看到的那条
    "本章大纲"——本章自己的大纲请用下面的 /outlines/for-chapter/{id}。
    """
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


@router.get("/outlines/for-chapter/{writingChapterId}")
async def get_outline_for_chapter(writingChapterId: str):
    """新语义：返回与该 writing chapter 直接绑定的 outline 行
    （即 outlines.writing_chapter_id == writingChapterId 的那条，
    通常是 type='chapter' 或 type='volume'）。
    用于 AI 提示词模板里的 {本章大纲} 等场景。
    """
    db = get_db()
    outline = await get_outline_by_writing_chapter_id(db, writingChapterId)
    return {"success": True, "data": outline}
