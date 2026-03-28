"""
Outline CRUD – port of all Database.*Outline* methods from database.js.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection

from utils.id_utils import short_id8


async def save_outline(
    db: DatabaseConnection, data: dict[str, Any] | str
) -> dict[str, Any]:
    if isinstance(data, str):
        title = data
        outline_type = "other"
        xmind_data = None
        file_path = None
        book_id = None
        writing_chapter_id = None
        parent_outline_id = None
    else:
        title = data.get("title", "")
        outline_type = data.get("type", "other")
        xmind_data = data.get("xmind_data")
        file_path = data.get("file_path")
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
        "book_id, writing_chapter_id, parent_outline_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            new_id, title, outline_type, sort, xmind_data, file_path,
            book_id, writing_chapter_id, parent_outline_id,
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
    db: DatabaseConnection, data: dict[str, Any]
) -> dict[str, Any] | None:
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
    vals.append(outline_id)
    await db.execute(
        f"UPDATE outlines SET {', '.join(parts)} WHERE id = ?", vals
    )
    return await db.fetch_one("SELECT * FROM outlines WHERE id = ?", [outline_id])


async def delete_outline(db: DatabaseConnection, outline_id: str) -> None:
    await db.execute("DELETE FROM outline_chapters WHERE outline_id = ?", [outline_id])
    await db.execute("DELETE FROM outlines WHERE id = ?", [outline_id])


async def get_outlines(
    db: DatabaseConnection, type_filter: str | None = None
) -> list[dict[str, Any]]:
    if type_filter in ("global", "chapter", "other"):
        return await db.fetch_all(
            "SELECT * FROM outlines WHERE type = ? ORDER BY sort ASC, create_time ASC",
            [type_filter],
        )
    return await db.fetch_all(
        "SELECT * FROM outlines ORDER BY type ASC, sort ASC, create_time ASC"
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
    if book_id:
        return await db.fetch_all(
            "SELECT * FROM outlines WHERE type = ? AND book_id = ? "
            "ORDER BY sort ASC, create_time ASC",
            ["chapter", book_id],
        )
    return await db.fetch_all(
        "SELECT * FROM outlines WHERE type = ? ORDER BY sort ASC, create_time ASC",
        ["chapter"],
    )


async def get_other_outlines(
    db: DatabaseConnection, book_id: str | None = None
) -> list[dict[str, Any]]:
    if book_id:
        return await db.fetch_all(
            "SELECT * FROM outlines WHERE type = ? AND book_id = ? "
            "ORDER BY sort ASC, create_time ASC",
            ["other", book_id],
        )
    return await db.fetch_all(
        "SELECT * FROM outlines WHERE type = ? ORDER BY sort ASC, create_time ASC",
        ["other"],
    )


async def get_volume_outlines(
    db: DatabaseConnection, book_id: str | None = None
) -> list[dict[str, Any]]:
    if book_id:
        vols = await db.fetch_all(
            "SELECT * FROM outlines WHERE type = ? AND book_id = ? "
            "ORDER BY sort ASC, create_time ASC",
            ["volume", book_id],
        )
    else:
        vols = await db.fetch_all(
            "SELECT * FROM outlines WHERE type = ? ORDER BY sort ASC, create_time ASC",
            ["volume"],
        )
    result = []
    for vol in vols:
        chapters = await db.fetch_all(
            "SELECT * FROM outlines WHERE type = ? AND parent_outline_id = ? "
            "ORDER BY sort ASC, create_time ASC",
            ["chapter", vol["id"]],
        )
        result.append({**vol, "chapters": chapters})
    return result


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
