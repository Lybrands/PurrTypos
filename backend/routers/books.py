from __future__ import annotations

import re

from fastapi import APIRouter

from constants import BOOK_COLORS
from database.crud.articles import get_article
from database.crud.chapters import get_chapters
from database.crud.outlines import get_or_create_writing_outline
from dependencies import get_db
from schemas.books import CreateBookRequest, RenameBookRequest
from utils.id_utils import short_id8
from utils.text import extract_text_from_lexical

router = APIRouter(tags=["books"])


@router.get("/books/{bookId}/word-count")
async def get_book_word_count(bookId: str):
    """本书写作目录下全部章节正文有效字数（与编辑器一致：Lexical 抽纯文本后去掉空白）。"""
    db = get_db()
    writing = await get_or_create_writing_outline(db, bookId)
    if not writing or not writing.get("id"):
        return {"success": True, "data": {"count": 0}}
    chapters = await get_chapters(db, writing["id"]) or []
    total = 0
    for ch in chapters:
        art = await get_article(db, str(ch["id"]))
        raw = (art or {}).get("content") if art else None
        if not raw:
            continue
        plain = extract_text_from_lexical(raw)
        total += len(re.sub(r"\s", "", plain, flags=re.UNICODE))
    return {"success": True, "data": {"count": total}}


@router.get("/books")
async def get_books():
    db = get_db()
    rows = await db.fetch_all(
        "SELECT * FROM books ORDER BY create_time ASC"
    )
    return {"success": True, "data": rows}


@router.post("/books")
async def create_book(body: CreateBookRequest):
    db = get_db()
    book_id = short_id8()
    cnt = await db.fetch_one("SELECT COUNT(*) as c FROM books")
    color = BOOK_COLORS[int(cnt["c"] if cnt else 0) % len(BOOK_COLORS)]
    enable_volume = 1 if body.enableVolume else 0
    await db.execute(
        "INSERT INTO books (id, title, cover_color, enable_volume) VALUES (?, ?, ?, ?)",
        [book_id, body.title, color, enable_volume],
    )
    book = await db.fetch_one("SELECT * FROM books WHERE id = ?", [book_id])
    wid = short_id8()
    await db.execute(
        "INSERT INTO outlines (id, title, type, sort, book_id) VALUES (?, ?, ?, ?, ?)",
        [wid, body.title, "writing", 0, book_id],
    )
    return {"success": True, "data": book}


@router.delete("/books/{bookId}")
async def delete_book(bookId: str):
    db = get_db()
    outlines = await db.fetch_all(
        "SELECT id FROM outlines WHERE book_id = ?", [bookId]
    )
    for o in outlines:
        chapters = await db.fetch_all(
            "SELECT id FROM outline_chapters WHERE outline_id = ?", [o["id"]]
        )
        for ch in chapters:
            await db.execute("DELETE FROM articles WHERE chapter_id = ?", [ch["id"]])
            await db.execute(
                "DELETE FROM ai_conversations WHERE chapter_id = ?", [ch["id"]]
            )
        await db.execute(
            "DELETE FROM outline_chapters WHERE outline_id = ?", [o["id"]]
        )
        await db.execute("DELETE FROM outlines WHERE id = ?", [o["id"]])
    await db.execute("DELETE FROM characters WHERE book_id = ?", [bookId])
    await db.execute("DELETE FROM story_background WHERE book_id = ?", [bookId])
    attachments = await db.fetch_all(
        "SELECT stored_path FROM story_background_attachments WHERE book_id = ?",
        [bookId],
    )
    await db.execute(
        "DELETE FROM story_background_attachments WHERE book_id = ?", [bookId]
    )
    await db.execute("DELETE FROM books WHERE id = ?", [bookId])
    attachment_paths = [a["stored_path"] for a in (attachments or []) if a.get("stored_path")]
    return {"success": True, "data": {"attachmentPaths": attachment_paths}}


@router.put("/books/{bookId}/rename")
async def rename_book(bookId: str, body: RenameBookRequest):
    db = get_db()
    await db.execute(
        "UPDATE books SET title = ? WHERE id = ?",
        [body.title, bookId],
    )
    return {"success": True}
