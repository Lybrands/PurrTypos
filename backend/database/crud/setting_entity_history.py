"""Setting entity revision history CRUD（与 character_history 同构）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


async def insert_entity_history(
    db: DatabaseConnection,
    *,
    entity_id: int,
    before_name: str = "",
    before_tags: str = "",
    before_profile_md: str = "",
    after_name: str = "",
    after_tags: str = "",
    after_profile_md: str = "",
    source: str = "user",
    accepted_segments: int = 0,
    rejected_segments: int = 0,
) -> int | None:
    return await db.execute_and_get_id(
        "INSERT INTO setting_entity_history "
        "(entity_id, before_name, before_tags, before_profile_md, "
        "after_name, after_tags, after_profile_md, source, "
        "accepted_segments, rejected_segments) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            int(entity_id),
            before_name,
            before_tags,
            before_profile_md,
            after_name,
            after_tags,
            after_profile_md,
            source,
            int(accepted_segments),
            int(rejected_segments),
        ],
    )


async def list_entity_history(
    db: DatabaseConnection, entity_id: int, limit: int = 50
) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT id, entity_id, before_name, before_tags, "
        "before_profile_md, after_name, after_tags, after_profile_md, "
        "source, accepted_segments, rejected_segments, create_time "
        "FROM setting_entity_history WHERE entity_id = ? "
        "ORDER BY create_time DESC LIMIT ?",
        [int(entity_id), int(limit)],
    )


async def get_entity_history(
    db: DatabaseConnection, history_id: int
) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT id, entity_id, before_name, before_tags, "
        "before_profile_md, after_name, after_tags, after_profile_md, "
        "source, accepted_segments, rejected_segments, create_time "
        "FROM setting_entity_history WHERE id = ?",
        [int(history_id)],
    )
