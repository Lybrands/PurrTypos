"""Hard-delete one native screenplay project and its owned runtime state."""

from __future__ import annotations

from contextlib import asynccontextmanager

from application.product_owner_deletion import prepare_session_owner_deletion


@asynccontextmanager
async def _owner_write_transaction(db):
    if db.current_task_owns_transaction():
        yield db
        return
    async with db.transaction(cancellation_linearizable=True):
        yield db


async def delete_screenplay_project_data(db, project_id: str) -> bool:
    """Delete every project-owned row without deciding API authorization."""

    async with _owner_write_transaction(db):
        project = await db.fetch_one(
            "SELECT id FROM screenplay_projects WHERE id = ?",
            [project_id],
        )
        if project is None:
            return False
        sessions = await db.fetch_all(
            "SELECT id FROM ai_sessions WHERE screenplay_project_id = ?",
            [project_id],
        )
        session_ids = [int(row["id"]) for row in sessions]
        await prepare_session_owner_deletion(
            db,
            session_ids,
            screenplay_project_ids=[project_id],
        )
        if session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            await db.execute(
                f"DELETE FROM screenplay_agent_turns "
                f"WHERE session_id IN ({placeholders})",
                session_ids,
            )
            await db.execute(
                f"DELETE FROM ai_conversation_summaries "
                f"WHERE session_id IN ({placeholders})",
                session_ids,
            )
            await db.execute(
                f"DELETE FROM ai_local_conversation_turn_receipts "
                f"WHERE session_id IN ({placeholders})",
                session_ids,
            )
            await db.execute(
                f"DELETE FROM ai_conversations "
                f"WHERE session_id IN ({placeholders})",
                session_ids,
            )
            await db.execute(
                f"UPDATE ai_agent_runs SET session_id = NULL "
                f"WHERE session_id IN ({placeholders})",
                session_ids,
            )
            await db.execute(
                f"DELETE FROM ai_sessions WHERE id IN ({placeholders})",
                session_ids,
            )

        operation_rows = await db.fetch_all(
            "SELECT id, turn_id FROM screenplay_agent_operations "
            "WHERE project_id = ?",
            [project_id],
        )
        operation_ids = [str(row["id"]) for row in operation_rows]
        turn_ids = [str(row["turn_id"]) for row in operation_rows]
        if operation_ids:
            operation_marks = ",".join("?" for _ in operation_ids)
            await db.execute(
                f"DELETE FROM screenplay_agent_operation_usage "
                f"WHERE operation_id IN ({operation_marks})",
                operation_ids,
            )
            await db.execute(
                f"DELETE FROM screenplay_agent_operation_commands "
                f"WHERE operation_id IN ({operation_marks})",
                operation_ids,
            )
            await db.execute(
                f"DELETE FROM screenplay_agent_operations "
                f"WHERE id IN ({operation_marks})",
                operation_ids,
            )
        if turn_ids:
            turn_marks = ",".join("?" for _ in turn_ids)
            await db.execute(
                f"DELETE FROM screenplay_agent_cancel_commands "
                f"WHERE turn_id IN ({turn_marks})",
                turn_ids,
            )

        for table in (
            "screenplay_revision_inputs",
            "screenplay_revision_parts",
            "screenplay_revision_source_refs",
        ):
            await db.execute(
                f"DELETE FROM {table} WHERE revision_id IN ("
                "SELECT id FROM screenplay_revisions WHERE project_id = ?)",
                [project_id],
            )
        await db.execute(
            "DELETE FROM screenplay_outbox_events WHERE "
            "(aggregate_type = 'screenplayProject' AND aggregate_id = ?) "
            "OR aggregate_id IN (SELECT id FROM screenplay_revisions "
            "WHERE project_id = ?)",
            [project_id, project_id],
        )
        for table in (
            "screenplay_review_decision_events",
            "screenplay_review_decisions",
            "screenplay_finalization_events",
            "screenplay_acceptance_events",
            "screenplay_project_heads",
            "screenplay_working_copies",
            "screenplay_revisions",
            "screenplay_command_receipts",
            "screenplay_deliverables",
        ):
            await db.execute(
                f"DELETE FROM {table} WHERE project_id = ?",
                [project_id],
            )
        await db.execute(
            "DELETE FROM screenplay_source_receipts WHERE project_id = ?",
            [project_id],
        )
        await db.execute(
            "DELETE FROM ai_agent_artifact_projections WHERE artifact_id IN ("
            "SELECT id FROM ai_agent_artifacts "
            "WHERE namespace = 'purrtypos.screenplay' AND owner_id = ?)",
            [project_id],
        )
        await db.execute(
            "DELETE FROM ai_agent_artifact_claims WHERE artifact_id IN ("
            "SELECT id FROM ai_agent_artifacts "
            "WHERE namespace = 'purrtypos.screenplay' AND owner_id = ?)",
            [project_id],
        )
        await db.execute(
            "DELETE FROM ai_agent_artifact_batches WHERE artifact_id IN ("
            "SELECT id FROM ai_agent_artifacts "
            "WHERE namespace = 'purrtypos.screenplay' AND owner_id = ?)",
            [project_id],
        )
        await db.execute(
            "DELETE FROM ai_agent_artifacts "
            "WHERE namespace = 'purrtypos.screenplay' AND owner_id = ?",
            [project_id],
        )
        await db.execute(
            "DELETE FROM ai_agent_long_task_units WHERE task_id IN ("
            "SELECT id FROM ai_agent_long_tasks "
            "WHERE namespace = 'purrtypos.screenplay' AND owner_id = ?)",
            [project_id],
        )
        await db.execute(
            "DELETE FROM ai_agent_long_tasks "
            "WHERE namespace = 'purrtypos.screenplay' AND owner_id = ?",
            [project_id],
        )
        await db.execute(
            "DELETE FROM ai_agent_work_item_runs WHERE work_item_id IN ("
            "SELECT id FROM ai_agent_work_items "
            "WHERE namespace = 'purrtypos.screenplay' AND owner_id = ?)",
            [project_id],
        )
        await db.execute(
            "DELETE FROM ai_agent_work_items "
            "WHERE namespace = 'purrtypos.screenplay' AND owner_id = ?",
            [project_id],
        )
        await db.execute(
            "DELETE FROM screenplay_projects WHERE id = ?",
            [project_id],
        )
    return True
