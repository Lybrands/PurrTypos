from __future__ import annotations

import re

from fastapi import APIRouter

from config import DATA_DIR
from constants import BOOK_COLORS
from database.crud.articles import get_article
from database.crud.chapters import get_chapters
from database.crud.outlines import get_or_create_writing_outline
from dependencies import get_db
from application.product_owner_deletion import prepare_session_owner_deletion
from schemas.books import CreateBookRequest, RenameBookRequest
from utils.file_storage import safe_unlink_stored_file
from utils.id_utils import short_id8
from utils.text import extract_text_from_lexical

router = APIRouter(tags=["books"])


def _placeholders(values: list[object]) -> str:
    return ",".join("?" for _ in values)


async def _delete_where_in(db, table: str, column: str, values: list[object]) -> None:
    if not values:
        return
    await db.execute(
        f"DELETE FROM {table} WHERE {column} IN ({_placeholders(values)})",
        values,
    )


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
        "SELECT b.*, COALESCE(sw.title, CASE WHEN cb.id IS NOT NULL "
        "THEN '已删除来源' END) AS continuation_source_title, "
        "COALESCE(ss.title, CASE WHEN cb.id IS NOT NULL "
        "THEN '原分叉章节已删除' END) AS continuation_fork_section_title, "
        "cb.fork_ordinal AS continuation_fork_ordinal, "
        "cb.source_revision_id AS continuation_source_revision_id, "
        "cb.canon_snapshot_id AS continuation_canon_snapshot_id "
        "FROM books AS b LEFT JOIN continuation_bindings AS cb "
        "ON cb.target_book_id = b.id LEFT JOIN novel_source_works AS sw "
        "ON sw.id = cb.source_work_id LEFT JOIN novel_source_sections AS ss "
        "ON ss.id = cb.fork_section_id ORDER BY b.create_time ASC"
    )
    return {"success": True, "data": rows}


@router.post("/books")
async def create_book(body: CreateBookRequest):
    db = get_db()
    book_id = short_id8()
    cnt = await db.fetch_one("SELECT COUNT(*) as c FROM books")
    color = BOOK_COLORS[int(cnt["c"] if cnt else 0) % len(BOOK_COLORS)]
    enable_volume = 1 if body.enableVolume else 0
    async with db.transaction():
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
        from application.creation_material_service import materials
        material_service = materials(db)
        initial = await material_service.preview(book_id)
        await material_service.migrate(book_id, initial['sourceRevision'])
    return {"success": True, "data": book}


@router.delete("/books/{bookId}")
async def delete_book(bookId: str):
    db = get_db()
    attachment_paths: list[str] = []
    from application.memory_delivery import (
        MemoryDeliveryService,
        record_book_deletion,
    )
    from application.memory_operations import MemoryApplicationService
    from application.agent_composition import get_agent_composition

    async with db.transaction(cancellation_linearizable=True):
        book = await db.fetch_one("SELECT id FROM books WHERE id = ?", [bookId])
        if book is None:
            return {"success": False, "error": "书籍不存在"}
        memory_operation_key = await record_book_deletion(db, book_id=bookId)
        continuation = await db.fetch_one(
            "SELECT canon_snapshot_id FROM continuation_bindings "
            "WHERE target_book_id = ?",
            [bookId],
        )
        outlines = await db.fetch_all(
            "SELECT id FROM outlines WHERE book_id = ?", [bookId]
        )
        outline_ids = [str(o["id"]) for o in outlines if o.get("id") is not None]

        chapters = []
        if outline_ids:
            chapters = await db.fetch_all(
                f"SELECT id FROM outline_chapters WHERE outline_id IN ({_placeholders(outline_ids)})",
                outline_ids,
            )
        chapter_ids = [str(ch["id"]) for ch in chapters if ch.get("id") is not None]

        session_rows = await db.fetch_all(
            "SELECT id FROM ai_sessions WHERE book_id = ? "
            "AND (scope IS NULL OR scope != 'screenplay')",
            [bookId],
        )
        if chapter_ids:
            session_rows.extend(await db.fetch_all(
                f"SELECT id FROM ai_sessions WHERE chapter_id IN ({_placeholders(chapter_ids)})",
                chapter_ids,
            ))
        session_ids = sorted({
            int(row["id"])
            for row in session_rows
            if row.get("id") is not None
        })
        await prepare_session_owner_deletion(
            db,
            session_ids,
            book_ids=[bookId],
        )

        attachments = await db.fetch_all(
            "SELECT stored_path FROM story_background_attachments WHERE book_id = ?",
            [bookId],
        )
        attachment_paths = [
            a["stored_path"]
            for a in (attachments or [])
            if a.get("stored_path")
        ]

        await _delete_where_in(db, "ai_favorites", "session_id", session_ids)
        await _delete_where_in(
            db,
            "ai_conversation_summaries",
            "session_id",
            session_ids,
        )
        await _delete_where_in(
            db,
            "ai_local_conversation_turn_receipts",
            "session_id",
            session_ids,
        )
        await _delete_where_in(db, "ai_conversations", "session_id", session_ids)
        await _delete_where_in(db, "ai_conversations", "chapter_id", chapter_ids)
        for session_id in session_ids:
            await db.execute("DELETE FROM writing_technique_selections WHERE scope_kind='session' AND scope_id=?", [str(session_id)])
        await _delete_where_in(db, "ai_sessions", "id", session_ids)
        await db.execute(
            "UPDATE ai_sessions SET book_id = NULL "
            "WHERE book_id = ? AND scope = 'screenplay'",
            [bookId],
        )

        await db.execute("DELETE FROM ai_memories WHERE book_id = ?", [bookId])
        await db.execute("DELETE FROM ai_foreshadowing WHERE book_id = ?", [bookId])
        await db.execute(
            "DELETE FROM chapter_annotations WHERE book_id = ?", [bookId]
        )
        await db.execute(
            "DELETE FROM story_memory_versions WHERE book_id = ?",
            [bookId],
        )
        await db.execute(
            "DELETE FROM story_memory_analysis_runs WHERE book_id = ?",
            [bookId],
        )
        await db.execute(
            "DELETE FROM story_memory_evolution_reviews WHERE book_id = ?",
            [bookId],
        )
        await db.execute(
            "DELETE FROM story_memory_delta_operations WHERE delta_id IN "
            "(SELECT id FROM story_memory_deltas WHERE book_id = ?)",
            [bookId],
        )
        await db.execute(
            "DELETE FROM story_memory_sources WHERE book_id = ?",
            [bookId],
        )
        await db.execute(
            "DELETE FROM story_memory_deltas WHERE book_id = ?",
            [bookId],
        )
        await db.execute(
            "DELETE FROM story_memory_records WHERE book_id = ?",
            [bookId],
        )

        await _delete_where_in(db, "articles", "chapter_id", chapter_ids)
        await _delete_where_in(db, "chapter_canvas", "chapter_id", chapter_ids)
        await _delete_where_in(db, "chapter_diff_history", "chapter_id", chapter_ids)
        await _delete_where_in(db, "outline_history", "outline_id", outline_ids)
        await _delete_where_in(db, "outline_chapters", "outline_id", outline_ids)
        await _delete_where_in(db, "outlines", "id", outline_ids)

        await db.execute("DELETE FROM characters WHERE book_id = ?", [bookId])
        await db.execute("DELETE FROM story_background WHERE book_id = ?", [bookId])
        await db.execute("DELETE FROM story_background_attachments WHERE book_id = ?", [bookId])
        await db.execute(
            "UPDATE writing_technique_grants SET active=0,generation=generation+1 WHERE book_id = ?", [bookId]
        )
        for table in ("continuation_source_sections", "continuation_operations", "continuation_material_baselines"):
            await db.execute(f"DELETE FROM {table} WHERE book_id=?", [bookId])
        await db.execute("DELETE FROM writing_technique_selections WHERE scope_kind='book' AND scope_id=?", [bookId])
        if continuation is not None:
            snapshot_id = str(continuation["canon_snapshot_id"])
            await db.execute(
                "DELETE FROM continuation_bindings WHERE target_book_id = ?",
                [bookId],
            )
            await db.execute(
                "DELETE FROM continuation_canon_records WHERE snapshot_id = ?",
                [snapshot_id],
            )
            await db.execute(
                "DELETE FROM continuation_canon_snapshots WHERE id = ?",
                [snapshot_id],
            )
        await db.execute(
            "DELETE FROM setting_entity_history WHERE entity_id IN "
            "(SELECT id FROM setting_entities WHERE book_id = ?)",
            [bookId],
        )
        await db.execute("DELETE FROM setting_entities WHERE book_id = ?", [bookId])
        await db.execute("DELETE FROM book_word_stats WHERE book_id = ?", [bookId])
        # 剧本项目拥有独立生命周期。删除来源书籍时仅解除引用，
        # 保留已产生的剧本文档与项目历史。
        await db.execute(
            "UPDATE screenplay_projects SET source_book_id = NULL, "
            "update_time = CURRENT_TIMESTAMP WHERE source_book_id = ?",
            [bookId],
        )
        await db.execute("UPDATE novel_knowledge_bindings SET state='unbound',generation=generation+1,version=version+1 WHERE book_id=?", [bookId])
        await db.execute("DELETE FROM creation_material_trash WHERE book_id=?", [bookId])
        await db.execute("DELETE FROM creation_material_files WHERE book_id=?", [bookId])
        await db.execute("DELETE FROM creation_material_books WHERE book_id=?", [bookId])
        await db.execute("DELETE FROM books WHERE id = ?", [bookId])

    composition = get_agent_composition()
    memory_delivery = await MemoryDeliveryService(
        db,
        MemoryApplicationService(db, composition.memory_resource),
    ).deliver_book_deletion(memory_operation_key)
    for stored_path in attachment_paths:
        safe_unlink_stored_file(stored_path, DATA_DIR)
    return {
        "success": True,
        "data": {"attachmentPaths": attachment_paths},
        "memoryDelivery": memory_delivery.to_dict(),
    }


@router.put("/books/{bookId}/rename")
async def rename_book(bookId: str, body: RenameBookRequest):
    db = get_db()
    await db.execute(
        "UPDATE books SET title = ? WHERE id = ?",
        [body.title, bookId],
    )
    return {"success": True}
