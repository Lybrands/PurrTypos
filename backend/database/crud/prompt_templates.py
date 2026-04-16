"""AI prompt templates CRUD — user-defined prompt snippets for the chat composer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


async def list_prompt_templates(db: DatabaseConnection) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT * FROM ai_prompt_templates ORDER BY sort ASC, id ASC"
    )


async def create_prompt_template(
    db: DatabaseConnection,
    title: str,
    content: str,
    sort: int | None = None,
) -> dict[str, Any] | None:
    if sort is None:
        row = await db.fetch_one(
            "SELECT COALESCE(MAX(sort), -1) + 1 AS next_sort FROM ai_prompt_templates"
        )
        sort = int(row["next_sort"]) if row else 0
    tpl_id = await db.execute_and_get_id(
        "INSERT INTO ai_prompt_templates (title, content, sort) VALUES (?, ?, ?)",
        [title, content, sort],
    )
    return await db.fetch_one(
        "SELECT * FROM ai_prompt_templates WHERE id = ?", [tpl_id]
    )


async def update_prompt_template(
    db: DatabaseConnection,
    tpl_id: int,
    title: str | None = None,
    content: str | None = None,
    sort: int | None = None,
) -> dict[str, Any] | None:
    fields: list[str] = []
    params: list[Any] = []
    if title is not None:
        fields.append("title = ?")
        params.append(title)
    if content is not None:
        fields.append("content = ?")
        params.append(content)
    if sort is not None:
        fields.append("sort = ?")
        params.append(sort)
    if not fields:
        return await db.fetch_one(
            "SELECT * FROM ai_prompt_templates WHERE id = ?", [tpl_id]
        )
    fields.append("update_time = CURRENT_TIMESTAMP")
    params.append(tpl_id)
    await db.execute(
        f"UPDATE ai_prompt_templates SET {', '.join(fields)} WHERE id = ?",
        params,
    )
    return await db.fetch_one(
        "SELECT * FROM ai_prompt_templates WHERE id = ?", [tpl_id]
    )


async def delete_prompt_template(db: DatabaseConnection, tpl_id: int) -> None:
    await db.execute("DELETE FROM ai_prompt_templates WHERE id = ?", [tpl_id])


async def reorder_prompt_templates(
    db: DatabaseConnection, ordered_ids: list[int]
) -> None:
    for idx, tpl_id in enumerate(ordered_ids):
        await db.execute(
            "UPDATE ai_prompt_templates SET sort = ?, update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [idx, tpl_id],
        )
