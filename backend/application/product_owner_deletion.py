"""Shared transaction rules for deleting product-owned Agent sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from exceptions import AppError


class ProductOwnerActiveError(AppError):
    def __init__(self) -> None:
        super().__init__(
            "运行中、排队中或已暂停的对话不能删除，请先完成或终止任务。",
            409,
        )


@dataclass(frozen=True)
class SessionOwnerRows:
    session_ids: tuple[int, ...]
    conversation_ids: tuple[int, ...]


async def prepare_session_owner_deletion(
    db,
    session_ids: Iterable[int],
    *,
    screenplay_project_ids: Iterable[str] = (),
    book_ids: Iterable[str] = (),
) -> SessionOwnerRows:
    """Reject active ownership, then unlink terminal audit rows.

    The caller owns the surrounding cancellation-linearizable transaction and
    deletes the product rows after this shared preparation succeeds.
    """

    sessions = tuple(sorted({int(value) for value in session_ids}))
    projects = tuple(sorted({str(value) for value in screenplay_project_ids if value}))
    books = tuple(sorted({str(value) for value in book_ids if value}))
    if not sessions and not projects and not books:
        return SessionOwnerRows((), ())
    session_marks = _marks(sessions)
    conversations = await db.fetch_all(
        f"SELECT id FROM ai_conversations WHERE session_id IN ({session_marks})",
        list(sessions),
    ) if sessions else []
    conversation_ids = tuple(sorted(int(row["id"]) for row in conversations))
    conversation_marks = _marks(conversation_ids)
    owned_run_ids = await _owned_run_ids(db, sessions, conversation_ids)
    run_marks = _marks(owned_run_ids)
    run_owner_sql = f"id IN ({run_marks})" if owned_run_ids else ""
    run_owner_params = list(owned_run_ids)

    active_run = await db.fetch_one(
        "SELECT id FROM ai_agent_runs WHERE " + run_owner_sql
        + " AND status IN ('pending', 'queued', 'running', 'paused') LIMIT 1",
        run_owner_params,
    ) if run_owner_sql else None
    active_request = await db.fetch_one(
        f"SELECT request_id FROM ai_writing_chat_requests "
        f"WHERE session_id IN ({session_marks}) "
        "AND status IN ('accepted', 'starting') LIMIT 1",
        list(sessions),
    ) if sessions else None
    operation_clauses: list[str] = []
    operation_params: list[object] = []
    if sessions:
        operation_clauses.append(f"session_id IN ({session_marks})")
        operation_params.extend(sessions)
    if projects:
        project_marks = _marks(projects)
        operation_clauses.append(f"project_id IN ({project_marks})")
        operation_params.extend(projects)
    active_operation = await db.fetch_one(
        "SELECT id FROM screenplay_agent_operations WHERE ("
        + " OR ".join(operation_clauses)
        + ") AND status IN ('queued', 'running', 'paused') LIMIT 1",
        operation_params,
    ) if operation_clauses else None
    active_long_task = await _active_long_task(
        db,
        sessions,
        owned_run_ids,
        projects,
        books,
    )
    if active_run or active_request or active_operation or active_long_task:
        raise ProductOwnerActiveError()

    if sessions:
        report_clauses = [f"session_id IN ({session_marks})"]
        report_params: list[object] = [*sessions]
        if conversation_ids:
            report_clauses.append(f"conversation_id IN ({conversation_marks})")
            report_params.extend(conversation_ids)
        if owned_run_ids:
            report_clauses.append(f"agent_run_id IN ({run_marks})")
            report_params.extend(owned_run_ids)
        await db.execute(
            "UPDATE ai_error_reports SET session_id = NULL, conversation_id = NULL "
            "WHERE " + " OR ".join(report_clauses),
            report_params,
        )
        if run_owner_sql:
            assignments = [
                "session_id = CASE WHEN session_id IN (" + session_marks
                + ") THEN NULL ELSE session_id END"
            ]
            assignment_params: list[object] = [*sessions]
            if conversation_ids:
                assignments.append(
                    f"conversation_id = CASE WHEN conversation_id IN "
                    f"({conversation_marks}) THEN NULL ELSE conversation_id END"
                )
                assignment_params.extend(conversation_ids)
            await db.execute(
                "UPDATE ai_agent_runs SET " + ", ".join(assignments)
                + " WHERE " + run_owner_sql,
                [*assignment_params, *run_owner_params],
            )
        await _clear_long_task_session_metadata(
            db,
            sessions,
            owned_run_ids,
        )

    if sessions:
        for table in (
            "ai_favorites",
            "ai_conversation_summaries",
            "ai_writing_chat_requests",
            "ai_local_conversation_turn_receipts",
        ):
            await db.execute(
                f"DELETE FROM {table} WHERE session_id IN ({session_marks})",
                list(sessions),
            )
    if conversation_ids:
        await db.execute(
            "UPDATE memory_items SET status = 'archived', "
            "source_type = 'conversation_truncated', "
            "update_time = CURRENT_TIMESTAMP "
            "WHERE source_type = 'conversation' "
            f"AND source_id IN ({conversation_marks}) AND status <> 'archived'",
            [str(value) for value in conversation_ids],
        )
    if books:
        book_marks = _marks(books)
        namespaces = ["purrtypos.writing", "writing.book"]
        namespace_marks = _marks(namespaces)
        artifacts = await db.fetch_all(
            "SELECT id FROM ai_agent_artifacts "
            f"WHERE namespace IN ({namespace_marks}) "
            f"AND owner_id IN ({book_marks})",
            [*namespaces, *books],
        )
        artifact_ids = tuple(str(row["id"]) for row in artifacts)
        if artifact_ids:
            artifact_marks = _marks(artifact_ids)
            for table in (
                "ai_agent_artifact_projections",
                "ai_agent_artifact_claims",
                "ai_agent_artifact_batches",
            ):
                await db.execute(
                    f"DELETE FROM {table} "
                    f"WHERE artifact_id IN ({artifact_marks})",
                    list(artifact_ids),
                )
            await db.execute(
                f"DELETE FROM ai_agent_artifacts "
                f"WHERE id IN ({artifact_marks})",
                list(artifact_ids),
            )
        work_items = await db.fetch_all(
            "SELECT id FROM ai_agent_work_items "
            f"WHERE namespace IN ({namespace_marks}) "
            f"AND owner_id IN ({book_marks})",
            [*namespaces, *books],
        )
        work_item_ids = tuple(str(row["id"]) for row in work_items)
        if work_item_ids:
            work_marks = _marks(work_item_ids)
            await db.execute(
                f"DELETE FROM ai_agent_long_task_units WHERE task_id IN ("
                "SELECT id FROM ai_agent_long_tasks "
                f"WHERE work_item_id IN ({work_marks}))",
                list(work_item_ids),
            )
            await db.execute(
                f"DELETE FROM ai_agent_long_tasks "
                f"WHERE work_item_id IN ({work_marks})",
                list(work_item_ids),
            )
            await db.execute(
                f"DELETE FROM ai_agent_work_item_runs "
                f"WHERE work_item_id IN ({work_marks})",
                list(work_item_ids),
            )
            await db.execute(
                f"DELETE FROM ai_agent_work_items WHERE id IN ({work_marks})",
                list(work_item_ids),
            )
        # Some legacy LongTasks predate a Work Item row.
        await db.execute(
            f"DELETE FROM ai_agent_long_task_units WHERE task_id IN ("
            "SELECT id FROM ai_agent_long_tasks "
            f"WHERE namespace IN ({namespace_marks}) "
            f"AND owner_id IN ({book_marks}))",
            [*namespaces, *books],
        )
        await db.execute(
            f"DELETE FROM ai_agent_long_tasks "
            f"WHERE namespace IN ({namespace_marks}) "
            f"AND owner_id IN ({book_marks})",
            [*namespaces, *books],
        )
    return SessionOwnerRows(sessions, conversation_ids)


async def _active_long_task(db, sessions, owned_run_ids, projects, books):
    clauses: list[str] = []
    params: list[object] = []
    if sessions:
        marks = _marks(sessions)
        clauses.append(
            f"CAST(json_extract(lt.metadata_json, '$.sessionId') AS INTEGER) "
            f"IN ({marks})"
        )
        params.extend(sessions)
    if owned_run_ids:
        run_marks = _marks(owned_run_ids)
        clauses.append(
            f"lt.created_by_run_id IN ({run_marks})"
        )
        params.extend(owned_run_ids)
        clauses.append(
            "EXISTS (SELECT 1 FROM ai_agent_work_item_runs AS wir "
            "WHERE wir.work_item_id = lt.work_item_id "
            f"AND wir.run_id IN ({run_marks}))"
        )
        params.extend(owned_run_ids)
    if projects:
        marks = _marks(projects)
        clauses.append(
            f"(lt.namespace = 'purrtypos.screenplay' "
            f"AND lt.owner_id IN ({marks}))"
        )
        params.extend(projects)
    if books:
        marks = _marks(books)
        clauses.append(
            f"(lt.namespace IN ('purrtypos.writing', 'writing.book') "
            f"AND lt.owner_id IN ({marks}))"
        )
        params.extend(books)
    if not clauses:
        return None
    return await db.fetch_one(
        "SELECT lt.id FROM ai_agent_long_tasks AS lt "
        "WHERE lt.status IN ('pending', 'queued', 'running', 'paused') AND ("
        + " OR ".join(clauses) + ") LIMIT 1",
        params,
    )


async def _clear_long_task_session_metadata(db, sessions, owned_run_ids) -> None:
    if not sessions:
        return
    session_marks = _marks(sessions)
    clauses = [
        f"CAST(json_extract(metadata_json, '$.sessionId') AS INTEGER) "
        f"IN ({session_marks})"
    ]
    params: list[object] = [*sessions]
    if owned_run_ids:
        run_marks = _marks(owned_run_ids)
        clauses.extend([
            f"created_by_run_id IN ({run_marks})",
            "EXISTS (SELECT 1 FROM ai_agent_work_item_runs AS wir "
            "WHERE wir.work_item_id = ai_agent_long_tasks.work_item_id AND "
            f"wir.run_id IN ({run_marks}))",
        ])
        params.extend(owned_run_ids)
        params.extend(owned_run_ids)
    await db.execute(
        "UPDATE ai_agent_long_tasks SET metadata_json = "
        "json_remove(metadata_json, '$.sessionId') WHERE "
        + " OR ".join(clauses),
        params,
    )


def _run_owner_predicate(sessions, conversations, alias: str = ""):
    prefix = f"{alias}." if alias else ""
    clauses: list[str] = []
    params: list[object] = []
    if sessions:
        clauses.append(f"{prefix}session_id IN ({_marks(sessions)})")
        params.extend(sessions)
    if conversations:
        clauses.append(f"{prefix}conversation_id IN ({_marks(conversations)})")
        params.extend(conversations)
    return "(" + " OR ".join(clauses) + ")" if clauses else "", params


async def _owned_run_ids(db, sessions, conversations) -> tuple[str, ...]:
    direct_sql, direct_params = _run_owner_predicate(sessions, conversations)
    if not direct_sql:
        return ()
    rows = await db.fetch_all(
        "WITH RECURSIVE owned(id) AS ("
        " SELECT id FROM ai_agent_runs WHERE " + direct_sql
        + " UNION SELECT child.id FROM ai_agent_runs AS child "
        " JOIN owned AS parent ON child.parent_run_id = parent.id"
        ") SELECT id FROM owned ORDER BY id",
        direct_params,
    )
    return tuple(str(row["id"]) for row in rows)


def _marks(values) -> str:
    return ",".join("?" for _ in values)


__all__ = [
    "ProductOwnerActiveError",
    "SessionOwnerRows",
    "prepare_session_owner_deletion",
]
