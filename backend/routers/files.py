from __future__ import annotations

from fastapi import APIRouter

from dependencies import get_db

router = APIRouter(tags=["files"])


@router.get("/database/info")
async def get_database_info():
    db = get_db()
    db_path = str(db.get_db_path())

    books = await db.fetch_all("SELECT id FROM books")
    book_count = len(books)

    outline_chapter_count = 0
    outlines = await db.fetch_all("SELECT id FROM outlines")
    for ol in outlines:
        chapters = await db.fetch_all(
            "SELECT id FROM outline_chapters WHERE outline_id = ?", [ol["id"]]
        )
        outline_chapter_count += len(chapters)

    article_count = 0
    for b in books:
        writing = await db.fetch_one(
            "SELECT id FROM outlines WHERE book_id = ? AND type = 'writing' LIMIT 1",
            [b["id"]],
        )
        if not writing:
            continue
        chapters = await db.fetch_all(
            "SELECT id FROM outline_chapters WHERE outline_id = ?", [writing["id"]]
        )
        for ch in chapters:
            article = await db.fetch_one(
                "SELECT content FROM articles WHERE chapter_id = ?", [ch["id"]]
            )
            if article and article.get("content"):
                article_count += 1

    return {
        "success": True,
        "data": {
            "dbPath": db_path,
            "books": book_count,
            "outlineChapters": outline_chapter_count,
            "articles": article_count,
        },
    }


@router.post("/database/export")
async def export_database():
    db = get_db()
    buf = await db.export_to_buffer()
    if not buf:
        return {"success": False, "error": "数据库未就绪"}
    db_path = str(db.get_db_path())
    return {
        "success": True,
        "data": {"dbPath": db_path, "size": len(buf)},
    }


@router.post("/database/import")
async def import_database():
    return {
        "success": False,
        "error": "Database import requires file upload — use Electron dialog or a dedicated upload endpoint",
    }
