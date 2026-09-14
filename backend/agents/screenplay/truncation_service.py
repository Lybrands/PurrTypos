"""Cancellation-first history truncation for replacement Screenplay Turns."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from exceptions import AppError, NotFoundError


_ACTIVE_TURN_STATUSES = {"queued", "planning", "running", "paused"}


class ScreenplayReplacementTruncationService:
    def __init__(self, db) -> None:
        self._db = db

    async def tail(self, turn_id: str) -> tuple[dict[str, Any], ...]:
        identity = str(turn_id or "").strip()
        anchor = await self._db.fetch_one(
            "SELECT id, project_id, session_id, rowid AS turn_rowid "
            "FROM screenplay_agent_turns WHERE id = ?",
            [identity],
        )
        if anchor is None:
            raise NotFoundError("剧本 Agent Turn 不存在")
        rows = await self._db.fetch_all(
            "SELECT t.id AS turn_id, t.status AS turn_status, "
            "t.planner_run_id AS root_run_id, t.project_id, t.session_id, "
            "o.id AS operation_id, o.long_task_id AS task_id "
            "FROM screenplay_agent_turns AS t "
            "LEFT JOIN screenplay_agent_operations AS o ON o.turn_id = t.id "
            "WHERE t.project_id = ? AND t.session_id = ? AND t.rowid >= ? "
            "ORDER BY t.rowid",
            [anchor["project_id"], anchor["session_id"], anchor["turn_rowid"]],
        )
        return tuple(dict(row) for row in rows)

    async def delete_terminal_tail(self, turn_id: str) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            rows = await self.tail(turn_id)
            turn_ids = _values(rows, "turn_id")
            operation_ids = _values(rows, "operation_id")
            task_ids = _values(rows, "task_id")
            event_runs = await self._db.fetch_all(
                "SELECT DISTINCT run_id, root_run_id FROM ai_agent_run_events "
                f"WHERE turn_id IN ({_marks(turn_ids)})",
                list(turn_ids),
            )
            root_ids = tuple(dict.fromkeys((
                *_values(rows, "root_run_id"),
                *_values(event_runs, "run_id"),
                *_values(event_runs, "root_run_id"),
            )))
            await self._require_terminal_barrier(
                rows=rows, task_ids=task_ids, root_ids=root_ids
            )
            await self._delete_task_outputs(task_ids)
            if task_ids:
                task_marks = _marks(task_ids)
                for table, column in (
                    ("screenplay_checkpoint_plans", "task_id"),
                    ("screenplay_operation_access_receipts", "task_id"),
                    ("screenplay_operation_usage_receipts", "task_id"),
                    ("screenplay_replacement_projection_receipts", "task_id"),
                    ("ai_agent_long_task_events", "task_id"),
                    ("ai_agent_long_task_usage", "task_id"),
                    ("ai_agent_long_task_units", "task_id"),
                    ("ai_agent_long_task_runs", "task_id"),
                    ("ai_agent_long_tasks", "id"),
                ):
                    await self._db.execute(
                        f"DELETE FROM {table} WHERE {column} IN ({task_marks})",
                        list(task_ids),
                    )
            if root_ids:
                root_marks = _marks(root_ids)
                await self._db.execute(
                    "UPDATE ai_agent_runs SET session_id = NULL, "
                    "conversation_id = NULL, update_time = CURRENT_TIMESTAMP "
                    f"WHERE id IN ({root_marks}) OR root_run_id IN ({root_marks})",
                    [*root_ids, *root_ids],
                )
            if operation_ids:
                operation_marks = _marks(operation_ids)
                await self._db.execute(
                    "DELETE FROM screenplay_agent_cancel_commands WHERE "
                    f"operation_id IN ({operation_marks})",
                    list(operation_ids),
                )
                for table in (
                    "screenplay_agent_operation_usage",
                    "screenplay_agent_operation_commands",
                    "screenplay_agent_operations",
                ):
                    await self._db.execute(
                        f"DELETE FROM {table} WHERE operation_id IN "
                        f"({operation_marks})" if table != "screenplay_agent_operations"
                        else f"DELETE FROM {table} WHERE id IN ({operation_marks})",
                        list(operation_ids),
                    )
            turn_marks = _marks(turn_ids)
            await self._db.execute(
                f"DELETE FROM screenplay_agent_cancel_commands "
                f"WHERE turn_id IN ({turn_marks})",
                list(turn_ids),
            )
            await self._db.execute(
                f"DELETE FROM screenplay_agent_turns WHERE id IN ({turn_marks})",
                list(turn_ids),
            )
            first = rows[0]
            return {
                "projectId": str(first["project_id"]),
                "sessionId": int(first["session_id"]),
                "deletedTurnIds": list(turn_ids),
                "deletedOperationIds": list(operation_ids),
                "deletedTaskIds": list(task_ids),
            }

    async def _require_terminal_barrier(
        self,
        *,
        rows: Sequence[dict[str, Any]],
        task_ids: Sequence[str],
        root_ids: Sequence[str],
    ) -> None:
        if any(str(row.get("turn_status") or "") in _ACTIVE_TURN_STATUSES for row in rows):
            raise AppError("screenplay truncate Turn is still active", 409)
        if root_ids:
            active = await self._db.fetch_one(
                "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE "
                f"(id IN ({_marks(root_ids)}) OR root_run_id IN ({_marks(root_ids)})) "
                "AND (status = 'running' OR execution_owner_id IS NOT NULL "
                "OR lease_expires_at_ms IS NOT NULL)",
                [*root_ids, *root_ids],
            )
            if int((active or {}).get("count") or 0):
                raise AppError("screenplay truncate runtime is still active", 409)
        if task_ids:
            task_marks = _marks(task_ids)
            active_tasks = await self._db.fetch_one(
                "SELECT COUNT(*) AS count FROM ai_agent_long_tasks WHERE "
                f"id IN ({task_marks}) AND status IN ('pending','running','paused')",
                list(task_ids),
            )
            active_units = await self._db.fetch_one(
                "SELECT COUNT(*) AS count FROM ai_agent_long_task_units WHERE "
                f"task_id IN ({task_marks}) AND (status IN ('claimed','running') "
                "OR worker_id IS NOT NULL OR lease_expires_at_ms IS NOT NULL)",
                list(task_ids),
            )
            if int((active_tasks or {}).get("count") or 0) or int(
                (active_units or {}).get("count") or 0
            ):
                raise AppError("screenplay truncate task is still active", 409)

    async def _delete_task_outputs(self, task_ids: Sequence[str]) -> None:
        if not task_ids:
            return
        task_marks = _marks(task_ids)
        revisions = await self._db.fetch_all(
            "SELECT r.id FROM screenplay_revisions AS r "
            "LEFT JOIN screenplay_project_heads AS h ON h.revision_id = r.id "
            "LEFT JOIN screenplay_acceptance_events AS a ON a.revision_id = r.id "
            f"WHERE r.agent_task_id IN ({task_marks}) "
            "AND h.revision_id IS NULL AND a.revision_id IS NULL",
            list(task_ids),
        )
        revision_ids = tuple(str(row["id"]) for row in revisions)
        if revision_ids:
            revision_marks = _marks(revision_ids)
            for table in (
                "screenplay_revision_inputs",
                "screenplay_revision_parts",
                "screenplay_revision_source_refs",
            ):
                await self._db.execute(
                    f"DELETE FROM {table} WHERE revision_id IN ({revision_marks})",
                    list(revision_ids),
                )
            await self._db.execute(
                f"DELETE FROM screenplay_outbox_events WHERE aggregate_id IN "
                f"({revision_marks})",
                list(revision_ids),
            )
            await self._db.execute(
                f"DELETE FROM screenplay_revisions WHERE id IN ({revision_marks})",
                list(revision_ids),
            )
        await self._db.execute(
            f"UPDATE screenplay_revisions SET agent_task_id = NULL "
            f"WHERE agent_task_id IN ({task_marks})",
            list(task_ids),
        )
        artifacts = await self._db.fetch_all(
            "SELECT id FROM ai_agent_artifacts WHERE namespace = 'purrtypos.screenplay' "
            f"AND json_extract(metadata_json, '$.taskId') IN ({task_marks})",
            list(task_ids),
        )
        artifact_ids = tuple(str(row["id"]) for row in artifacts)
        if artifact_ids:
            artifact_marks = _marks(artifact_ids)
            for table in (
                "ai_agent_artifact_claims",
                "ai_agent_artifact_projections",
                "ai_agent_artifact_batches",
                "ai_agent_artifacts",
            ):
                await self._db.execute(
                    f"DELETE FROM {table} WHERE "
                    + ("artifact_id" if table != "ai_agent_artifacts" else "id")
                    + f" IN ({artifact_marks})",
                    list(artifact_ids),
                )


def _values(rows: Sequence[dict[str, Any]], key: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        str(row.get(key) or "").strip()
        for row in rows
        if str(row.get(key) or "").strip()
    ))


def _marks(values: Sequence[str]) -> str:
    if not values:
        raise ValueError("SQL placeholder collection must not be empty")
    return ",".join("?" for _ in values)


__all__ = ["ScreenplayReplacementTruncationService"]
