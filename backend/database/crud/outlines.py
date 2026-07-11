"""
Outline CRUD – port of all Database.*Outline* methods from database.js.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection

from utils.id_utils import short_id8


async def _get_outlines_by_type(
    db: DatabaseConnection,
    outline_type: str,
    book_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return one outline type in its stable display order."""
    if book_id:
        return await db.fetch_all(
            "SELECT * FROM outlines WHERE type = ? AND book_id = ? "
            "ORDER BY create_time ASC",
            [outline_type, book_id],
        )
    return await db.fetch_all(
        "SELECT * FROM outlines WHERE type = ? ORDER BY create_time ASC",
        [outline_type],
    )


async def _get_child_chapters_by_parent(
    db: DatabaseConnection,
    parent_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Batch-load child outlines and group them by parent outline ID."""
    if not parent_ids:
        return {}

    placeholders = ", ".join("?" for _ in parent_ids)
    rows = await db.fetch_all(
        "SELECT * FROM outlines WHERE type = ? "
        f"AND parent_outline_id IN ({placeholders}) ORDER BY create_time ASC",
        ["chapter", *parent_ids],
    )
    grouped: dict[str, list[dict[str, Any]]] = {
        parent_id: [] for parent_id in parent_ids
    }
    for row in rows:
        parent_id = str(row.get("parent_outline_id") or "")
        if parent_id in grouped:
            grouped[parent_id].append(row)
    return grouped


async def save_outline(
    db: DatabaseConnection, data: dict[str, Any] | str
) -> dict[str, Any]:
    if isinstance(data, str):
        title = data
        outline_type = "chapter"
        xmind_data = None
        file_path = None
        markdown_content = None
        book_id = None
        writing_chapter_id = None
        parent_outline_id = None
    else:
        title = data.get("title", "")
        outline_type = data.get("type") or "chapter"
        xmind_data = data.get("xmind_data")
        file_path = data.get("file_path")
        markdown_content = data.get("markdown_content")
        book_id = data.get("book_id")
        writing_chapter_id = data.get("writing_chapter_id")
        parent_outline_id = data.get("parent_outline_id")

    if outline_type == "global":
        if book_id:
            all_global = await db.fetch_all(
                "SELECT id FROM outlines WHERE type = ? AND book_id = ?",
                ["global", book_id],
            )
        else:
            all_global = await db.fetch_all(
                "SELECT id FROM outlines WHERE type = ? AND book_id IS NULL",
                ["global"],
            )
        if all_global:
            keep_id = all_global[0]["id"]
            await db.execute(
                "UPDATE outlines SET title = ?, xmind_data = ?, file_path = ? WHERE id = ?",
                [title, xmind_data, file_path, keep_id],
            )
            for dup in all_global[1:]:
                await db.execute(
                    "DELETE FROM outline_chapters WHERE outline_id = ?", [dup["id"]]
                )
                await db.execute("DELETE FROM outlines WHERE id = ?", [dup["id"]])
            return {
                "id": keep_id,
                "title": title,
                "type": "global",
                "book_id": book_id,
            }

    if book_id:
        max_sort = await db.fetch_one(
            "SELECT COALESCE(MAX(sort), 0) as m FROM outlines WHERE type = ? AND book_id = ?",
            [outline_type, book_id],
        )
    else:
        max_sort = await db.fetch_one(
            "SELECT COALESCE(MAX(sort), 0) as m FROM outlines WHERE type = ?",
            [outline_type],
        )
    sort = (int(max_sort["m"]) if max_sort else 0) + 1
    new_id = short_id8()
    await db.execute(
        "INSERT INTO outlines (id, title, type, sort, xmind_data, file_path, "
        "markdown_content, book_id, writing_chapter_id, parent_outline_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            new_id, title, outline_type, sort, xmind_data, file_path,
            markdown_content, book_id, writing_chapter_id, parent_outline_id,
        ],
    )
    return {
        "id": new_id,
        "title": title,
        "type": outline_type,
        "book_id": book_id,
        "writing_chapter_id": writing_chapter_id,
        "parent_outline_id": parent_outline_id,
    }


async def update_outline(
    db: DatabaseConnection,
    data: dict[str, Any],
    *,
    history_source: str | None = "user",
    history_note: str | None = None,
) -> dict[str, Any] | None:
    """更新大纲。

    在写入新值之前会调用 ``insert_outline_history`` 把旧值整行快照写进
    ``outline_history``，便于用户回退（尤其是 LLM 改坏总纲的场景）。
    若调用方明确不想入历史（例如 restore 流程自己已经处理了快照），
    可以传 ``history_source=None`` 关闭。
    """
    outline_id = data.get("outlineId")
    if not outline_id:
        raise ValueError("outlineId required")
    parts: list[str] = []
    vals: list[Any] = []
    if "title" in data:
        parts.append("title = ?")
        vals.append(data["title"])
    if "xmind_data" in data:
        parts.append("xmind_data = ?")
        vals.append(data["xmind_data"])
        parts.append("file_path = ?")
        vals.append(data.get("file_path"))
    if "markdown_content" in data:
        parts.append("markdown_content = ?")
        vals.append(data["markdown_content"])
    if not parts:
        return await db.fetch_one("SELECT * FROM outlines WHERE id = ?", [outline_id])

    if history_source:
        prev = await db.fetch_one(
            "SELECT title, type, markdown_content, xmind_data "
            "FROM outlines WHERE id = ?",
            [outline_id],
        )
        if prev is not None:
            from database.crud.outline_history import insert_outline_history
            try:
                await insert_outline_history(
                    db,
                    outline_id=str(outline_id),
                    before_title=prev.get("title"),
                    before_type=prev.get("type"),
                    before_markdown_content=prev.get("markdown_content"),
                    before_xmind_data=prev.get("xmind_data"),
                    source=history_source,
                    note=history_note,
                )
            except Exception:
                # 快照失败不应阻塞主写入；最坏情况是这一笔无回退点。
                pass

    vals.append(outline_id)
    await db.execute(
        f"UPDATE outlines SET {', '.join(parts)} WHERE id = ?", vals
    )
    return await db.fetch_one("SELECT * FROM outlines WHERE id = ?", [outline_id])


async def restore_outline_from_history(
    db: DatabaseConnection,
    history_id: int,
) -> dict[str, Any] | None:
    """把指定历史快照写回 outlines；同时为本次回退再创建一条 history，
    这样回退本身也能再次回退（相当于 redo）。"""
    from database.crud.outline_history import (
        get_outline_history,
        insert_outline_history,
    )

    target = await get_outline_history(db, history_id)
    if not target:
        raise ValueError(f"outline_history id={history_id} not found")

    outline_id = str(target["outline_id"])
    current = await db.fetch_one(
        "SELECT title, type, markdown_content, xmind_data "
        "FROM outlines WHERE id = ?",
        [outline_id],
    )
    if current is None:
        raise ValueError(f"outline id={outline_id} not found")

    await insert_outline_history(
        db,
        outline_id=outline_id,
        before_title=current.get("title"),
        before_type=current.get("type"),
        before_markdown_content=current.get("markdown_content"),
        before_xmind_data=current.get("xmind_data"),
        source=f"rollback_of:{history_id}",
        note=f"恢复至 #{history_id}",
    )

    parts = ["title = ?", "markdown_content = ?", "xmind_data = ?"]
    vals: list[Any] = [
        target.get("before_title"),
        target.get("before_markdown_content"),
        target.get("before_xmind_data"),
    ]
    vals.append(outline_id)
    await db.execute(
        f"UPDATE outlines SET {', '.join(parts)} WHERE id = ?", vals
    )
    return await db.fetch_one("SELECT * FROM outlines WHERE id = ?", [outline_id])


async def delete_outline(db: DatabaseConnection, outline_id: str) -> None:
    async with db.transaction():
        await db.execute(
            "DELETE FROM outline_chapters WHERE outline_id = ?", [outline_id]
        )
        await db.execute("DELETE FROM outlines WHERE id = ?", [outline_id])


async def get_outlines(
    db: DatabaseConnection, type_filter: str | None = None
) -> list[dict[str, Any]]:
    if type_filter:
        return await db.fetch_all(
            "SELECT * FROM outlines WHERE type = ? ORDER BY create_time ASC",
            [type_filter],
        )
    return await db.fetch_all(
        "SELECT * FROM outlines ORDER BY create_time ASC"
    )


async def get_global_outline(
    db: DatabaseConnection, book_id: str | None = None
) -> dict[str, Any] | None:
    if book_id:
        scoped = await db.fetch_one(
            "SELECT * FROM outlines WHERE type = ? AND book_id = ? LIMIT 1",
            ["global", book_id],
        )
        if scoped:
            return scoped
        return await db.fetch_one(
            "SELECT * FROM outlines WHERE type = ? AND book_id IS NULL LIMIT 1",
            ["global"],
        )
    return await db.fetch_one(
        "SELECT * FROM outlines WHERE type = ? AND book_id IS NULL LIMIT 1",
        ["global"],
    )


async def get_or_create_global_outline(
    db: DatabaseConnection, book_id: str | None = None
) -> dict[str, Any] | None:
    existing = await get_global_outline(db, book_id)
    if existing:
        return existing
    await save_outline(db, {
        "title": "总纲",
        "type": "global",
        "book_id": book_id,
        "xmind_data": None,
        "file_path": None,
    })
    return await get_global_outline(db, book_id)


async def get_writing_outline(
    db: DatabaseConnection, book_id: str | None = None
) -> dict[str, Any] | None:
    if book_id:
        return await db.fetch_one(
            "SELECT * FROM outlines WHERE type = ? AND book_id = ? LIMIT 1",
            ["writing", book_id],
        )
    return await db.fetch_one(
        "SELECT * FROM outlines WHERE type = ? LIMIT 1", ["writing"]
    )


async def get_or_create_writing_outline(
    db: DatabaseConnection, book_id: str | None = None
) -> dict[str, Any] | None:
    if book_id:
        row = await db.fetch_one(
            "SELECT * FROM outlines WHERE type = ? AND book_id = ? LIMIT 1",
            ["writing", book_id],
        )
        if not row:
            book = await db.fetch_one(
                "SELECT title FROM books WHERE id = ?", [book_id]
            )
            wid = short_id8()
            await db.execute(
                "INSERT INTO outlines (id, title, type, sort, book_id) "
                "VALUES (?, ?, ?, ?, ?)",
                [wid, (book["title"] if book else "我的作品"), "writing", 0, book_id],
            )
            row = await db.fetch_one("SELECT * FROM outlines WHERE id = ?", [wid])
        return row
    row = await db.fetch_one(
        "SELECT * FROM outlines WHERE type = ? LIMIT 1", ["writing"]
    )
    if not row:
        wid = short_id8()
        await db.execute(
            "INSERT INTO outlines (id, title, type, sort) VALUES (?, ?, ?, ?)",
            [wid, "我的作品", "writing", 0],
        )
        row = await db.fetch_one("SELECT * FROM outlines WHERE id = ?", [wid])
    return row


async def get_chapter_outlines(
    db: DatabaseConnection, book_id: str | None = None
) -> list[dict[str, Any]]:
    return await _get_outlines_by_type(db, "chapter", book_id)


async def get_volume_outlines(
    db: DatabaseConnection, book_id: str | None = None
) -> list[dict[str, Any]]:
    volumes = await _get_outlines_by_type(db, "volume", book_id)
    chapters_by_parent = await _get_child_chapters_by_parent(
        db, [str(volume["id"]) for volume in volumes]
    )
    return [
        {
            **volume,
            "chapters": chapters_by_parent.get(str(volume["id"]), []),
        }
        for volume in volumes
    ]


async def get_associable_outlines(
    db: DatabaseConnection, book_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    AI 关联大纲列表，按创建时间升序排列（先创建的在前）。
    - 分卷：卷大纲（按创建时间）-> 卷下章节大纲（按创建时间）
    - 非分卷：章节大纲按创建时间升序
    不包含总纲/写作大纲。
    """
    if not book_id or not str(book_id).strip():
        return []
    bid = str(book_id).strip()
    book = await db.fetch_one(
        "SELECT enable_volume FROM books WHERE id = ? LIMIT 1",
        [bid],
    )
    enable_volume = bool(int(book["enable_volume"])) if book and book.get("enable_volume") is not None else False
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_row(row: dict[str, Any] | None) -> None:
        if row is None:
            return
        oid = row.get("id")
        if oid is None or str(oid) in seen:
            return
        seen.add(str(oid))
        result.append(dict(row))

    if enable_volume:
        # 分卷模式：批量读取所有卷内章节，避免每卷一次查询。
        for volume_group in await get_volume_outlines(db, bid):
            chapters = volume_group.get("chapters") or []
            volume = dict(volume_group)
            volume.pop("chapters", None)
            add_row(volume)
            for chapter in chapters:
                add_row(dict(chapter))
        return result

    # 非分卷模式：章节大纲按创建时间升序
    return await _get_outlines_by_type(db, "chapter", bid)


async def get_outline_by_writing_chapter_id(
    db: DatabaseConnection, writing_chapter_id: str
) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM outlines WHERE writing_chapter_id = ? LIMIT 1",
        [writing_chapter_id],
    )


async def update_outline_xmind(
    db: DatabaseConnection,
    outline_id: str,
    title: str,
    xmind_data: str | None,
    file_path: str | None = None,
) -> dict[str, Any] | None:
    await db.execute(
        "UPDATE outlines SET title = ?, xmind_data = ?, file_path = ? WHERE id = ?",
        [title, xmind_data, file_path, outline_id],
    )
    return await db.fetch_one("SELECT * FROM outlines WHERE id = ?", [outline_id])
