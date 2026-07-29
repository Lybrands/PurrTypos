from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response

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
    filename = db_path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] or "purrtypos.db"
    return Response(
        content=buf,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-PurrTypos-Db-Path": db_path,
            "X-PurrTypos-Db-Size": str(len(buf)),
        },
    )


async def _database_stats() -> dict[str, int]:
    db = get_db()
    books = await db.fetch_one("SELECT COUNT(*) AS count FROM books")
    outline_chapters = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM outline_chapters"
    )
    articles = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM articles WHERE content IS NOT NULL AND content != ''"
    )
    return {
        "books": int((books or {}).get("count") or 0),
        "outlineChapters": int((outline_chapters or {}).get("count") or 0),
        "articles": int((articles or {}).get("count") or 0),
    }


@router.post("/database/import")
async def import_database(
    request: Request,
    fileName: str = Query(default="backup.db"),
):
    if not fileName.lower().endswith(".db"):
        return {"success": False, "error": "请选择 .db 数据库备份文件"}
    payload = await request.body()
    if len(payload) > 512 * 1024 * 1024:
        return {"success": False, "error": "数据库备份不能超过 512 MB"}
    db = get_db()
    before_stats = await _database_stats()
    try:
        await db.import_from_buffer(payload)
    except (ValueError, RuntimeError) as error:
        return {"success": False, "error": str(error)}
    after_stats = await _database_stats()
    return {
        "success": True,
        "data": {
            "beforeStats": before_stats,
            "afterStats": after_stats,
        },
    }
