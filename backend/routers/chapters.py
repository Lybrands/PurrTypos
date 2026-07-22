from __future__ import annotations

from fastapi import APIRouter

from dependencies import get_db
from schemas.chapters import (
    AddChapterRequest,
    RenameChapterRequest,
    SaveChaptersRequest,
    UpdateChapterProgressRequest,
)
from utils.id_utils import short_id8

router = APIRouter(tags=["chapters"])


@router.get("/chapters/{outlineId}")
async def get_chapters(outlineId: str):
    db = get_db()
    rows = await db.fetch_all(
        "SELECT * FROM outline_chapters WHERE outline_id = ? ORDER BY sort ASC",
        [outlineId],
    )
    return {"success": True, "data": rows}


@router.post("/chapters/{outlineId}")
async def save_chapters(outlineId: str, body: SaveChaptersRequest):
    db = get_db()
    await db.execute("DELETE FROM outline_chapters WHERE outline_id = ?", [outlineId])
    for i, ch in enumerate(body.chapters):
        ch_id = ch.id or short_id8()
        await db.execute(
            "INSERT INTO outline_chapters (id, outline_id, title, level, sort, parent_id) VALUES (?, ?, ?, ?, ?, ?)",
            [ch_id, outlineId, ch.title, 1, i, ch.parent_id],
        )
    return {"success": True}


@router.post("/chapters/{outlineId}/add")
async def add_chapter(outlineId: str, body: AddChapterRequest):
    db = get_db()
    chapter_id = short_id8()
    max_row = await db.fetch_one(
        "SELECT COALESCE(MAX(sort), -1) AS max_sort FROM outline_chapters WHERE outline_id = ?",
        [outlineId],
    )
    next_order = (max_row["max_sort"] if max_row else 0) + 1
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title, level, sort, parent_id) VALUES (?, ?, ?, ?, ?, ?)",
        [chapter_id, outlineId, body.title, 1, next_order, body.parentId],
    )
    row = await db.fetch_one("SELECT * FROM outline_chapters WHERE id = ?", [chapter_id])

    # 同步创建对应大纲记录（upsert by writing_chapter_id）
    await _upsert_chapter_outline(db, outlineId, chapter_id, body.title, body.parentId, body.isVolume)

    return {"success": True, "data": row}


@router.delete("/chapters/{chapterId}")
async def delete_chapter(chapterId: str):
    db = get_db()
    owner = await db.fetch_one(
        "SELECT o.book_id FROM outline_chapters AS c "
        "JOIN outlines AS o ON o.id = c.outline_id WHERE c.id = ?",
        [chapterId],
    )
    if owner and owner.get("book_id"):
        try:
            from domains.writing.story_memory_ledger import StoryMemoryLedger
            from infrastructure.persistence.writing.sqlite_story_memory_repository import (
                SqliteStoryMemoryRepository,
            )

            await StoryMemoryLedger(
                SqliteStoryMemoryRepository(db)
            ).chapter_changed(str(owner["book_id"]), chapterId)
        except Exception:
            pass
    await db.execute("DELETE FROM outline_chapters WHERE id = ?", [chapterId])
    return {"success": True}


@router.put("/chapters/{chapterId}/rename")
async def rename_chapter(chapterId: str, body: RenameChapterRequest):
    db = get_db()
    await db.execute(
        "UPDATE outline_chapters SET title = ? WHERE id = ?",
        [body.title, chapterId],
    )
    # 同步更新对应大纲的标题
    await db.execute(
        "UPDATE outlines SET title = ? WHERE writing_chapter_id = ?",
        [body.title, chapterId],
    )
    return {"success": True}


@router.put("/chapters/{chapterId}/progress")
async def update_chapter_progress(chapterId: str, body: UpdateChapterProgressRequest):
    db = get_db()
    await db.execute(
        "UPDATE outline_chapters SET progress = ? WHERE id = ?",
        [body.progress, chapterId],
    )
    return {"success": True}


async def _upsert_chapter_outline(
    db,
    writing_outline_id: str,
    chapter_id: str,
    title: str,
    parent_chapter_id: str | None,
    is_volume: bool,
) -> None:
    """章节创建时同步创建（或更新）对应的 outlines 记录。"""
    # 已存在则直接更新标题，无需重复插入
    existing = await db.fetch_one(
        "SELECT id FROM outlines WHERE writing_chapter_id = ?", [chapter_id]
    )
    if existing:
        await db.execute(
            "UPDATE outlines SET title = ? WHERE writing_chapter_id = ?",
            [title, chapter_id],
        )
        return

    # 从写作大纲取 book_id
    parent_outline = await db.fetch_one(
        "SELECT book_id FROM outlines WHERE id = ?", [writing_outline_id]
    )
    book_id = parent_outline["book_id"] if parent_outline else None

    # 分卷模式下，章节的 parent_outline_id = 所在卷对应的 outline.id
    parent_outline_id: str | None = None
    if parent_chapter_id:
        vol_outline = await db.fetch_one(
            "SELECT id FROM outlines WHERE writing_chapter_id = ?", [parent_chapter_id]
        )
        if vol_outline:
            parent_outline_id = vol_outline["id"]

    outline_type = "volume" if is_volume else "chapter"

    max_sort = await db.fetch_one(
        "SELECT COALESCE(MAX(sort), 0) AS m FROM outlines WHERE type = ? AND book_id IS ?",
        [outline_type, book_id],
    )
    sort = (int(max_sort["m"]) if max_sort else 0) + 1

    new_id = short_id8()
    await db.execute(
        "INSERT INTO outlines (id, title, type, sort, book_id, writing_chapter_id, parent_outline_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [new_id, title, outline_type, sort, book_id, chapter_id, parent_outline_id],
    )
