"""
Book CRUD – port of Database.getBooks / createBook / deleteBook / renameBook.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection

from constants import BOOK_COLORS
from utils.id_utils import short_id8


async def get_books(db: DatabaseConnection) -> list[dict[str, Any]]:
    return await db.fetch_all("SELECT * FROM books ORDER BY create_time ASC")


async def create_book(
    db: DatabaseConnection, title: str, enable_volume: bool = False
) -> dict[str, Any] | None:
    bid = short_id8()
    cnt = await db.fetch_one("SELECT COUNT(*) as c FROM books")
    color = BOOK_COLORS[int(cnt["c"] if cnt else 0) % len(BOOK_COLORS)]
    vol = 1 if enable_volume else 0
    await db.execute(
        "INSERT INTO books (id, title, cover_color, enable_volume) VALUES (?, ?, ?, ?)",
        [bid, title, color, vol],
    )
    row = await db.fetch_one("SELECT * FROM books WHERE id = ?", [bid])
    wid = short_id8()
    await db.execute(
        "INSERT INTO outlines (id, title, type, sort, book_id) VALUES (?, ?, ?, ?, ?)",
        [wid, title, "writing", 0, bid],
    )
    return row


async def delete_book(db: DatabaseConnection, book_id: str) -> dict[str, Any]:
    outlines = await db.fetch_all(
        "SELECT id FROM outlines WHERE book_id = ?", [book_id]
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
    await db.execute("DELETE FROM characters WHERE book_id = ?", [book_id])
    await db.execute("DELETE FROM story_background WHERE book_id = ?", [book_id])
    attachments = await db.fetch_all(
        "SELECT stored_path FROM story_background_attachments WHERE book_id = ?",
        [book_id],
    )
    await db.execute(
        "DELETE FROM story_background_attachments WHERE book_id = ?", [book_id]
    )
    await db.execute("DELETE FROM books WHERE id = ?", [book_id])
    return {
        "attachmentPaths": [a["stored_path"] for a in (attachments or [])]
    }


async def rename_book(db: DatabaseConnection, book_id: str, title: str) -> None:
    await db.execute("UPDATE books SET title = ? WHERE id = ?", [title, book_id])
