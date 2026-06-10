"""
Character & character_options CRUD – port from database.js.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


# ── characters ───────────────────────────────────────────────────

async def get_characters(
    db: DatabaseConnection, book_id: str
) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT * FROM characters WHERE book_id = ? ORDER BY create_time ASC",
        [book_id],
    )


async def create_character(
    db: DatabaseConnection, book_id: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    """人物档案已 Markdown 化：除 name / tags 外的内容统一存 profile_md。

    旧的固定字段列（gender/age/…/remark）仅供历史数据读取，不再写入。
    """
    await db.execute(
        "INSERT INTO characters (book_id, name, tags, profile_md) "
        "VALUES (?, ?, ?, ?)",
        [
            book_id,
            data.get("name", ""),
            data.get("tags", ""),
            data.get("profile_md", ""),
        ],
    )
    return await db.fetch_one(
        "SELECT * FROM characters ORDER BY id DESC LIMIT 1"
    )


async def update_character(
    db: DatabaseConnection, character_id: int, data: dict[str, Any]
) -> dict[str, Any] | None:
    """部分更新：只覆盖显式传入的 name / tags / profile_md。"""
    row = await db.fetch_one(
        "SELECT * FROM characters WHERE id = ?", [character_id]
    )
    if not row:
        return None
    name = data.get("name", row["name"])
    tags = data.get("tags", row.get("tags", ""))
    profile_md = data.get("profile_md", row.get("profile_md") or "")
    await db.execute(
        "UPDATE characters SET name = ?, tags = ?, profile_md = ? WHERE id = ?",
        [name, tags, profile_md, character_id],
    )
    return await db.fetch_one(
        "SELECT * FROM characters WHERE id = ?", [character_id]
    )


async def delete_character(db: DatabaseConnection, character_id: int) -> None:
    await db.execute("DELETE FROM characters WHERE id = ?", [character_id])


# ── character_options ────────────────────────────────────────────

async def get_character_options(
    db: DatabaseConnection, category: str
) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT * FROM character_options WHERE category = ? ORDER BY sort ASC, id ASC",
        [category],
    )


async def add_character_option(
    db: DatabaseConnection, category: str, value: str
) -> dict[str, Any] | None:
    existing = await db.fetch_one(
        "SELECT id FROM character_options WHERE category = ? AND value = ?",
        [category, value],
    )
    if existing:
        return existing
    max_sort = await db.fetch_one(
        "SELECT MAX(sort) as m FROM character_options WHERE category = ?",
        [category],
    )
    sort = (int(max_sort["m"]) if max_sort and max_sort["m"] is not None else -1) + 1
    await db.execute(
        "INSERT INTO character_options (category, value, sort) VALUES (?, ?, ?)",
        [category, value, sort],
    )
    return await db.fetch_one(
        "SELECT * FROM character_options ORDER BY id DESC LIMIT 1"
    )


async def update_character_option(
    db: DatabaseConnection, option_id: int, value: str
) -> dict[str, Any] | None:
    await db.execute(
        "UPDATE character_options SET value = ? WHERE id = ?", [value, option_id]
    )
    return await db.fetch_one(
        "SELECT * FROM character_options WHERE id = ?", [option_id]
    )


async def delete_character_option(
    db: DatabaseConnection, option_id: int
) -> None:
    await db.execute("DELETE FROM character_options WHERE id = ?", [option_id])
