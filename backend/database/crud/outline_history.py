"""
Outline modification history CRUD.

Each row is a *before* snapshot captured immediately before an update is
applied to the ``outlines`` table.  The intent mirrors
``chapter_diff_history``: keep the user safe from destructive edits — in
particular AI tool calls that overwrite ``markdown_content`` /
``xmind_data`` — by allowing one-click rollback to the previous state.

Snapshot triggering lives in ``crud.outlines.update_outline``; this module
is the storage layer only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


SOURCE_USER = "user"
SOURCE_AI_TOOL = "ai_tool"
SOURCE_ROLLBACK_PREFIX = "rollback_of:"


async def insert_outline_history(
    db: "DatabaseConnection",
    *,
    outline_id: str,
    before_title: str | None,
    before_type: str | None,
    before_markdown_content: str | None,
    before_xmind_data: str | None,
    source: str = SOURCE_USER,
    note: str | None = None,
) -> int | None:
    return await db.execute_and_get_id(
        "INSERT INTO outline_history "
        "(outline_id, before_title, before_type, before_markdown_content, "
        "before_xmind_data, source, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            outline_id,
            before_title,
            before_type,
            before_markdown_content,
            before_xmind_data,
            source,
            note,
        ],
    )


async def list_outline_history(
    db: "DatabaseConnection", outline_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    """列表场景：不返回完整 markdown / xmind 全文，避免一次拉太大。
    长字段用 SUBSTR 截前 200 字做预览，详情走 get_outline_history。"""
    return await db.fetch_all(
        "SELECT id, outline_id, before_title, before_type, "
        "SUBSTR(COALESCE(before_markdown_content, ''), 1, 200) AS markdown_preview, "
        "LENGTH(COALESCE(before_markdown_content, '')) AS markdown_length, "
        "LENGTH(COALESCE(before_xmind_data, '')) AS xmind_length, "
        "source, note, create_time "
        "FROM outline_history WHERE outline_id = ? "
        "ORDER BY create_time DESC, id DESC LIMIT ?",
        [outline_id, int(limit)],
    )


async def get_outline_history(
    db: "DatabaseConnection", history_id: int
) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT id, outline_id, before_title, before_type, "
        "before_markdown_content, before_xmind_data, "
        "source, note, create_time "
        "FROM outline_history WHERE id = ?",
        [int(history_id)],
    )
