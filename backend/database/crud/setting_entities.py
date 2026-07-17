"""
世界设定实体（地点 / 势力 / 物品 / 其他）CRUD，结构与人物卡一致。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection

ENTITY_TYPES = ("location", "faction", "item", "other")

ENTITY_TYPE_LABELS = {
    "location": "地点",
    "faction": "势力",
    "item": "物品",
    "other": "其他",
}


def normalize_entity_type(value: Any) -> str:
    v = str(value or "").strip().lower()
    return v if v in ENTITY_TYPES else "other"


async def get_setting_entities(
    db: DatabaseConnection, book_id: str, entity_type: str | None = None
) -> list[dict[str, Any]]:
    if entity_type:
        return await db.fetch_all(
            "SELECT * FROM setting_entities WHERE book_id = ? AND entity_type = ? "
            "ORDER BY create_time ASC",
            [book_id, normalize_entity_type(entity_type)],
        )
    return await db.fetch_all(
        "SELECT * FROM setting_entities WHERE book_id = ? ORDER BY create_time ASC",
        [book_id],
    )


async def get_setting_entity(
    db: DatabaseConnection, entity_id: int
) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM setting_entities WHERE id = ?", [int(entity_id)]
    )


async def create_setting_entity(
    db: DatabaseConnection, book_id: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    entity_id = await db.execute_and_get_id(
        "INSERT INTO setting_entities (book_id, entity_type, name, tags, profile_md) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            book_id,
            normalize_entity_type(data.get("entity_type")),
            str(data.get("name") or ""),
            str(data.get("tags") or ""),
            str(data.get("profile_md") or ""),
        ],
    )
    return await get_setting_entity(db, int(entity_id))


async def update_setting_entity(
    db: DatabaseConnection, entity_id: int, data: dict[str, Any]
) -> dict[str, Any] | None:
    """部分更新：只覆盖显式传入的 entity_type / name / tags / profile_md。"""
    row = await get_setting_entity(db, entity_id)
    if not row:
        return None
    entity_type = (
        normalize_entity_type(data["entity_type"])
        if data.get("entity_type") is not None
        else row["entity_type"]
    )
    name = data.get("name", row["name"])
    tags = data.get("tags", row.get("tags") or "")
    profile_md = data.get("profile_md", row.get("profile_md") or "")
    await db.execute(
        "UPDATE setting_entities SET entity_type = ?, name = ?, tags = ?, profile_md = ? "
        "WHERE id = ?",
        [entity_type, name, tags, profile_md, int(entity_id)],
    )
    return await get_setting_entity(db, entity_id)


async def delete_setting_entity(db: DatabaseConnection, entity_id: int) -> None:
    async with db.transaction():
        await db.execute(
            "DELETE FROM setting_entity_history WHERE entity_id = ?", [int(entity_id)]
        )
        await db.execute(
            "DELETE FROM setting_entities WHERE id = ?", [int(entity_id)]
        )
