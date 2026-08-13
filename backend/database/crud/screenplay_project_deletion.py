"""Hard-delete one native screenplay project and its owned runtime state."""

from __future__ import annotations


async def delete_screenplay_project_data(db, project_id: str) -> bool:
    """Delete every project-owned row without deciding API authorization."""

    async with db.transaction():
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
