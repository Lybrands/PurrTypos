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
        operation_rows = await db.fetch_all(
            "SELECT id FROM screenplay_agent_operations WHERE project_id = ?",
            [project_id],
        )
        turn_rows = await db.fetch_all(
            "SELECT id FROM screenplay_agent_turns WHERE project_id = ?",
            [project_id],
        )
        operation_ids = [str(row["id"]) for row in operation_rows]
        turn_ids = [str(row["id"]) for row in turn_rows]
        await prepare_session_owner_deletion(
            db,
            session_ids,
            screenplay_project_ids=[project_id],
        )
        if turn_ids:
            turn_marks = ",".join("?" for _ in turn_ids)
            await db.execute(
                f"DELETE FROM screenplay_agent_cancel_commands "
                f"WHERE turn_id IN ({turn_marks})",
                turn_ids,
            )
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


async def retire_legacy_screenplay_project_data(db, project_id: str) -> bool:
    """Migration-only retirement after runtime schema is initialized.

    Legacy NULL-snapshot projects cannot be resumed by the native product. This
    path intentionally does not call the interactive active-owner guard; it
    owns startup reconciliation and may retire incomplete runtime rows.
    """

    async with _owner_write_transaction(db):
        project = await db.fetch_one(
            "SELECT id FROM screenplay_projects "
            "WHERE id = ? AND source_snapshot_json IS NULL",
            [project_id],
        )
        if project is None:
            return False
        await _retire_screenplay_project_data(db, project_id)
    return True


async def _retire_screenplay_project_data(db, project_id: str) -> None:
    sessions = await db.fetch_all(
        "SELECT id FROM ai_sessions WHERE screenplay_project_id = ?",
        [project_id],
    )
    session_ids = [int(row["id"]) for row in sessions]
    conversation_rows = await db.fetch_all(
        "SELECT id FROM ai_conversations WHERE session_id IN ("
        + ",".join("?" for _ in session_ids)
        + ")",
        session_ids,
    ) if session_ids else []
    conversation_ids = [int(row["id"]) for row in conversation_rows]
    direct_clauses = [
        "(session_id IS NULL AND binding_namespace IN ('screenplay.agent.turn', "
        "'screenplay.agent.turn.response', 'screenplay.agent.task', "
        "'screenplay.operation') "
        "AND binding_aggregate_id = ?)"
    ]
    direct_params: list[object] = [project_id]
    if session_ids:
        session_marks = ",".join("?" for _ in session_ids)
        direct_clauses.insert(0, f"session_id IN ({session_marks})")
        direct_params = [*session_ids, *direct_params]
    run_rows = await db.fetch_all(
        "WITH RECURSIVE owned(id) AS ("
        "SELECT id FROM ai_agent_runs WHERE "
        + " OR ".join(direct_clauses)
        + " UNION SELECT child.id FROM ai_agent_runs AS child "
        "JOIN owned AS parent ON child.parent_run_id = parent.id"
        ") SELECT id FROM owned",
        direct_params,
    )
    run_ids = [str(row["id"]) for row in run_rows]
    operation_rows = await db.fetch_all(
        "SELECT id FROM screenplay_agent_operations WHERE project_id = ?",
        [project_id],
    )
    turn_rows = await db.fetch_all(
        "SELECT id FROM screenplay_agent_turns WHERE project_id = ?",
        [project_id],
    )
    operation_ids = [str(row["id"]) for row in operation_rows]
    turn_ids = [str(row["id"]) for row in turn_rows]
    task_rows = await db.fetch_all(
        "SELECT id FROM ai_agent_long_tasks "
        "WHERE namespace = 'purrtypos.screenplay' AND owner_id = ?",
        [project_id],
    )
    task_ids = [str(row["id"]) for row in task_rows]
    work_rows = await db.fetch_all(
        "SELECT id FROM ai_agent_work_items "
        "WHERE namespace = 'purrtypos.screenplay' AND owner_id = ?",
        [project_id],
    )
    work_ids = [str(row["id"]) for row in work_rows]

    if turn_ids:
        marks = ",".join("?" for _ in turn_ids)
        await db.execute(
            f"DELETE FROM screenplay_agent_cancel_commands "
            f"WHERE turn_id IN ({marks})",
            turn_ids,
        )
    if operation_ids:
        marks = ",".join("?" for _ in operation_ids)
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
    if task_ids:
        marks = ",".join("?" for _ in task_ids)
        for table in ("ai_agent_long_task_usage", "ai_agent_long_task_units"):
            await db.execute(
                f"DELETE FROM {table} WHERE task_id IN ({marks})",
                task_ids,
            )
        await db.execute(
            f"DELETE FROM ai_agent_long_tasks WHERE id IN ({marks})",
            task_ids,
        )
    if work_ids:
        marks = ",".join("?" for _ in work_ids)
        await db.execute(
            f"DELETE FROM ai_agent_work_item_runs WHERE work_item_id IN ({marks})",
            work_ids,
        )
        await db.execute(
            f"DELETE FROM ai_agent_work_items WHERE id IN ({marks})",
            work_ids,
        )
    report_clauses: list[str] = []
    report_params: list[object] = []
    if session_ids:
        marks = ",".join("?" for _ in session_ids)
        report_clauses.append(f"session_id IN ({marks})")
        report_params.extend(session_ids)
    if conversation_ids:
        marks = ",".join("?" for _ in conversation_ids)
        report_clauses.append(f"conversation_id IN ({marks})")
        report_params.extend(conversation_ids)
    if run_ids:
        marks = ",".join("?" for _ in run_ids)
        report_clauses.append(f"agent_run_id IN ({marks})")
        report_params.extend(run_ids)
    if report_clauses:
        await db.execute(
            "UPDATE ai_error_reports SET session_id = NULL, "
            "conversation_id = NULL WHERE " + " OR ".join(report_clauses),
            report_params,
        )
    if conversation_ids:
        marks = ",".join("?" for _ in conversation_ids)
        await db.execute(
            "UPDATE memory_items SET status = 'archived', "
            "source_type = 'conversation_truncated', "
            "update_time = CURRENT_TIMESTAMP "
            "WHERE source_type = 'conversation' "
            f"AND source_id IN ({marks}) AND status <> 'archived'",
            [str(value) for value in conversation_ids],
        )
    if session_ids:
        marks = ",".join("?" for _ in session_ids)
        await db.execute(
            f"DELETE FROM ai_favorites WHERE session_id IN ({marks})",
            session_ids,
        )
        await db.execute(
            "UPDATE ai_agent_long_tasks SET metadata_json = "
            "json_remove(metadata_json, '$.sessionId') WHERE "
            f"CAST(json_extract(metadata_json, '$.sessionId') AS INTEGER) "
            f"IN ({marks})",
            session_ids,
        )
        await db.execute(
            f"DELETE FROM ai_writing_chat_requests WHERE session_id IN ({marks})",
            session_ids,
        )
        await db.execute(
            f"DELETE FROM ai_local_conversation_turn_receipts "
            f"WHERE session_id IN ({marks})",
            session_ids,
        )
        await db.execute(
            f"DELETE FROM ai_conversation_summaries WHERE session_id IN ({marks})",
            session_ids,
        )
        await db.execute(
            f"DELETE FROM ai_conversations WHERE session_id IN ({marks})",
            session_ids,
        )
        await db.execute(
            f"DELETE FROM ai_sessions WHERE id IN ({marks})",
            session_ids,
        )
    if run_ids:
        marks = ",".join("?" for _ in run_ids)
        await db.execute(
            "UPDATE ai_agent_runs SET status = CASE "
            "WHEN status IN ('pending', 'queued', 'running', 'paused') "
            "THEN 'canceled' ELSE status END, session_id = NULL, "
            "conversation_id = NULL, update_time = CURRENT_TIMESTAMP "
            f"WHERE id IN ({marks})",
            run_ids,
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
        await db.execute(f"DELETE FROM {table} WHERE project_id = ?", [project_id])
    await db.execute(
        "DELETE FROM screenplay_source_receipts WHERE project_id = ?",
        [project_id],
    )
    for table in (
        "ai_agent_artifact_projections",
        "ai_agent_artifact_claims",
        "ai_agent_artifact_batches",
    ):
        await db.execute(
            f"DELETE FROM {table} WHERE artifact_id IN ("
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
        "DELETE FROM screenplay_projects "
        "WHERE id = ? AND source_snapshot_json IS NULL",
        [project_id],
    )


__all__ = [
    "delete_screenplay_project_data",
    "retire_legacy_screenplay_project_data",
]
