"""Delete screenplay conversation rows owned by one terminal session."""

from __future__ import annotations


async def delete_screenplay_session_rows(db, session_id: int) -> None:
    turns = await db.fetch_all(
        "SELECT id FROM screenplay_agent_turns WHERE session_id = ?",
        [int(session_id)],
    )
    operations = await db.fetch_all(
        "SELECT id FROM screenplay_agent_operations WHERE session_id = ?",
        [int(session_id)],
    )
    turn_ids = [str(row["id"]) for row in turns]
    operation_ids = [str(row["id"]) for row in operations]

    if turn_ids:
        marks = ",".join("?" for _ in turn_ids)
        await db.execute(
            f"DELETE FROM screenplay_agent_cancel_commands "
            f"WHERE turn_id IN ({marks})",
            turn_ids,
        )
    if operation_ids:
        marks = ",".join("?" for _ in operation_ids)
        await db.execute(
            f"DELETE FROM screenplay_checkpoint_plans "
            f"WHERE operation_id IN ({marks})",
            operation_ids,
        )
        for table in (
            "screenplay_agent_operation_usage",
            "screenplay_agent_operation_commands",
        ):
            await db.execute(
                f"DELETE FROM {table} WHERE operation_id IN ({marks})",
                operation_ids,
            )
        await db.execute(
            f"DELETE FROM screenplay_agent_operations WHERE id IN ({marks})",
            operation_ids,
        )
    if turn_ids:
        marks = ",".join("?" for _ in turn_ids)
        await db.execute(
            f"DELETE FROM screenplay_agent_turns WHERE id IN ({marks})",
            turn_ids,
        )


__all__ = ["delete_screenplay_session_rows"]
