"""
AI conversation CRUD – port from database.js.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


async def save_conversation(
    db: DatabaseConnection,
    session_id: int,
    chapter_id: str | None,
    prompt: str,
    response: str,
    model: str | None = None,
    thinking: str | None = None,
    tool_call_segments_json: str | None = None,
    thinking_blocks_json: str | None = None,
    thinking_durations_ms_json: str | None = None,
    task_plan_json: str | None = None,
    subagent_result_json: str | None = None,
) -> None:
    await db.execute(
        "INSERT INTO ai_conversations "
        "(session_id, chapter_id, prompt, response, model, thinking, "
        "tool_call_segments, thinking_blocks, thinking_durations_ms, task_plan, subagent_result) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            session_id, chapter_id, prompt, response,
            model, thinking, tool_call_segments_json, thinking_blocks_json,
            thinking_durations_ms_json,
            task_plan_json,
            subagent_result_json,
        ],
    )


async def get_conversations(
    db: DatabaseConnection, session_id: int
) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT * FROM ai_conversations WHERE session_id = ? ORDER BY create_time ASC",
        [session_id],
    )


async def delete_conversations_after_turn(
    db: DatabaseConnection, session_id: int, keep_turn_count: int
) -> None:
    if keep_turn_count <= 0:
        await db.execute(
            "DELETE FROM ai_conversations WHERE session_id = ?", [session_id]
        )
        return
    to_keep = await db.fetch_all(
        "SELECT id FROM ai_conversations WHERE session_id = ? "
        "ORDER BY create_time ASC LIMIT ?",
        [session_id, keep_turn_count],
    )
    if not to_keep:
        return
    placeholders = ",".join("?" for _ in to_keep)
    ids = [r["id"] for r in to_keep]
    await db.execute(
        f"DELETE FROM ai_conversations WHERE session_id = ? AND id NOT IN ({placeholders})",
        [session_id, *ids],
    )
