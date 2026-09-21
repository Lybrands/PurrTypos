"""
Chapter annotation (批注) CRUD.

批注锚定在章节纯文本的扁平偏移上（与前端 editorStateToText 同一套
`\n` 段落拼接规则）。quoted_text / context_before / context_after 是
创建时刻的文本快照，用于原文变更后按引文重定位；锚定解析在前端完成。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


_ANNOTATION_FIELDS = (
    "id, book_id, chapter_id, start_offset, end_offset, quoted_text, "
    "context_before, context_after, note, status, source, "
    "create_time, update_time"
)


def _annotation_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": row["id"],
        "book_id": row["book_id"],
        "chapter_id": row["chapter_id"],
        "start_offset": row["start_offset"],
        "end_offset": row["end_offset"],
        "quoted_text": row["quoted_text"],
        "context_before": row["context_before"],
        "context_after": row["context_after"],
        "note": row["note"],
        "status": row["status"],
        "source": row["source"],
        "create_time": row["create_time"],
        "update_time": row["update_time"],
    }


async def add_annotation(
    db: DatabaseConnection,
    *,
    book_id: str,
    chapter_id: str,
    start_offset: int,
    end_offset: int,
    quoted_text: str,
    context_before: str = "",
    context_after: str = "",
    note: str,
    source: str = "manual",
) -> dict[str, Any]:
    row_id = await db.execute_and_get_id(
        "INSERT INTO chapter_annotations "
        "(book_id, chapter_id, start_offset, end_offset, quoted_text, "
        "context_before, context_after, note, status, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
        [
            book_id,
            chapter_id,
            int(start_offset),
            int(end_offset),
            quoted_text,
            context_before,
            context_after,
            note.strip(),
            source,
        ],
    )
    row = await db.fetch_one(
        f"SELECT {_ANNOTATION_FIELDS} FROM chapter_annotations "
        "WHERE id = ? AND book_id = ?",
        [row_id, book_id],
    )
    return _annotation_row(row)


async def update_annotation(
    db: DatabaseConnection,
    *,
    book_id: str,
    annotation_id: int,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    if not data:
        return None
    assignments = ", ".join(f"{key} = ?" for key in data)
    params = [*data.values(), annotation_id, book_id]
    await db.execute(
        f"UPDATE chapter_annotations SET {assignments}, "
        "update_time = CURRENT_TIMESTAMP "
        "WHERE id = ? AND book_id = ?",
        params,
    )
    row = await db.fetch_one(
        f"SELECT {_ANNOTATION_FIELDS} FROM chapter_annotations "
        "WHERE id = ? AND book_id = ?",
        [annotation_id, book_id],
    )
    if row is None:
        return None
    return _annotation_row(row)


async def delete_annotation(
    db: DatabaseConnection,
    *,
    book_id: str,
    annotation_id: int,
) -> dict[str, Any] | None:
    row = await db.fetch_one(
        f"SELECT {_ANNOTATION_FIELDS} FROM chapter_annotations "
        "WHERE id = ? AND book_id = ?",
        [annotation_id, book_id],
    )
    if not row:
        return None
    await db.execute(
        "DELETE FROM chapter_annotations WHERE id = ? AND book_id = ?",
        [annotation_id, book_id],
    )
    return _annotation_row(row)


async def list_annotations(
    db: DatabaseConnection,
    *,
    book_id: str,
    chapter_id: str | None = None,
) -> list[dict[str, Any]]:
    chapter_filter = "AND chapter_id = ?" if chapter_id else ""
    params: list[Any] = [book_id] + ([chapter_id] if chapter_id else [])
    rows = await db.fetch_all(
        f"SELECT {_ANNOTATION_FIELDS} FROM chapter_annotations "
        f"WHERE book_id = ? {chapter_filter} "
        "ORDER BY start_offset ASC, id ASC",
        params,
    )
    return [_annotation_row(row) for row in rows]
