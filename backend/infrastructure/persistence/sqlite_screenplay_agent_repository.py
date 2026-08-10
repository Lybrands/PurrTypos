"""SQLite Turn messages and UI projections over screenplay Operations."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from domains.screenplay_agent import ScreenplayIntent
from exceptions import AppError, NotFoundError
from infrastructure.persistence.run_execution_store import now_ms


LEASE_MS = 30_000


class SqliteScreenplayAgentRepository:
    """Own Turns/events while Operation and PurrA repositories own execution."""

    def __init__(self, db, *, owner_id: str) -> None:
        self._db = db
        self._owner_id = _required(owner_id, "screenplay Agent owner id")

    async def begin_turn(
        self,
        *,
        command_id: str,
        project_id: str,
        session_id: int,
        content: str,
        runtime_profile: Mapping[str, Any],
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            existing = await self._db.fetch_one(
                _TURN_WITH_OPERATION_SQL
                + " WHERE t.project_id = ? AND t.command_id = ?",
                [project_id, command_id],
            )
            if existing is not None:
                if (
                    int(existing["session_id"]) != int(session_id)
                    or str(existing["user_content"]) != content
                ):
                    raise AppError("同一个对话命令对应了不同请求", 409)
                return _turn_view(existing)
            await self._require_session(project_id, session_id)
            active_operation = await self._db.fetch_one(
                "SELECT id FROM screenplay_agent_operations WHERE project_id = ? "
                "AND session_id = ? AND status IN ('queued', 'running', 'paused') "
                "LIMIT 1",
                [project_id, int(session_id)],
            )
            if active_operation is not None:
                raise AppError("当前对话仍有未结束的剧本任务", 409)
            active = await self._db.fetch_one(
                "SELECT id FROM screenplay_agent_turns WHERE project_id = ? "
                "AND status IN ('queued', 'planning', 'running') LIMIT 1",
                [project_id],
            )
            if active is not None:
                raise AppError("当前剧本项目仍在处理上一条消息", 409)
            turn_id = f"spaturn_{uuid.uuid4().hex}"
            await self._db.execute(
                "INSERT INTO screenplay_agent_turns "
                "(id, project_id, session_id, command_id, user_content, "
                "runtime_profile_json) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    turn_id,
                    project_id,
                    int(session_id),
                    command_id,
                    content,
                    _dump(runtime_profile),
                ],
            )
            await self._event(
                project_id=project_id,
                session_id=session_id,
                turn_id=turn_id,
                event_type="screenplay.agent.turn.queued",
                payload={},
            )
            return _turn_view(await self._require_turn(turn_id))

    async def recover_after_restart(self) -> tuple[str, ...]:
        """Close credential-bound Turns while preserving PurrA checkpoints."""

        error = {
            "code": "screenplay_agent_restarted",
            "message": "应用重启中断了本轮执行，请编辑消息后重新发送。",
        }
        async with self._db.transaction(cancellation_linearizable=True):
            turns = await self._db.fetch_all(
                "SELECT * FROM screenplay_agent_turns "
                "WHERE status IN ('queued', 'planning', 'running')"
            )
            for turn in turns:
                operation = await self._db.fetch_one(
                    "SELECT * FROM screenplay_agent_operations WHERE turn_id = ?",
                    [turn["id"]],
                )
                turn_status = "failed"
                assistant_content = error["message"]
                if operation is not None and str(operation["status"]) in {
                    "queued", "running", "paused",
                }:
                    turn_status = "paused"
                    assistant_content = ""
                    await self._db.execute(
                        "UPDATE screenplay_agent_operations SET status = 'paused', "
                        "error_json = ?, update_time = CURRENT_TIMESTAMP WHERE id = ?",
                        [_dump(error), operation["id"]],
                    )
                await self._db.execute(
                    "UPDATE screenplay_agent_turns SET status = ?, "
                    "assistant_content = ?, execution_owner_id = NULL, "
                    "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                    [turn_status, assistant_content, turn["id"]],
                )
                await self._event_for_turn(
                    turn,
                    (
                        "screenplay.agent.task.paused"
                        if turn_status == "paused"
                        else "screenplay.agent.turn.failed"
                    ),
                    {"error": error},
                )
            return tuple(str(turn["id"]) for turn in turns)

    async def claim_turn(self, turn_id: str) -> bool:
        current = now_ms()
        async with self._db.transaction(cancellation_linearizable=True):
            turn = await self._require_turn(turn_id)
            if str(turn["status"]) not in {"queued", "planning"}:
                return False
            owner = str(turn.get("execution_owner_id") or "")
            if owner and int(turn.get("lease_expires_at_ms") or 0) > current:
                return False
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = 'planning', "
                "execution_owner_id = ?, lease_expires_at_ms = ?, "
                "heartbeat_at_ms = ?, attempt = attempt + 1, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [self._owner_id, current + LEASE_MS, current, turn_id],
            )
            await self._event_for_turn(
                turn,
                "screenplay.agent.turn.planning",
                {"attempt": int(turn.get("attempt") or 0) + 1},
            )
            return True

    async def record_intent(
        self,
        turn_id: str,
        *,
        intent: ScreenplayIntent,
        planner_run_id: str | None,
    ) -> None:
        async with self._db.transaction():
            turn = await self._require_owned_turn(turn_id)
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET intent_json = ?, "
                "planner_run_id = ?, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                [
                    _dump(intent.to_mapping()),
                    str(planner_run_id or "").strip() or None,
                    turn_id,
                ],
            )
            await self._event_for_turn(
                turn,
                "screenplay.agent.intent.resolved",
                intent.to_mapping(),
            )

    async def complete_answer(self, turn_id: str, reply: str) -> dict[str, Any]:
        return await self._finish_turn(
            turn_id,
            status="completed",
            assistant_content=reply,
            event_type="screenplay.agent.turn.completed",
            event_payload={"kind": "answer"},
            require_planning_owner=True,
        )

    async def attach_operation(
        self,
        turn_id: str,
        *,
        operation_id: str,
        task_id: str,
        target_role: str,
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            turn = await self._require_owned_turn(turn_id)
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = 'running', "
                "operation_id = ?, execution_owner_id = NULL, "
                "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [operation_id, turn_id],
            )
            await self._event_for_turn(
                turn,
                "screenplay.agent.task.attached",
                {"taskId": task_id, "targetRole": target_role},
                task_id=task_id,
            )
            return _turn_view(await self._require_turn(turn_id))

    async def complete_operation(
        self,
        turn_id: str,
        *,
        task_id: str,
        revision_id: str,
        assistant_content: str,
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            turn = await self._require_turn(turn_id)
            if str(turn["status"]) == "completed":
                return _turn_view(turn)
            if str(turn["status"]) != "running":
                raise AppError("剧本任务已不在运行状态", 409)
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = 'completed', "
                "assistant_content = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [assistant_content, turn_id],
            )
            await self._event_for_turn(
                turn,
                "screenplay.agent.task.completed",
                {"taskId": task_id, "revisionId": revision_id},
                task_id=task_id,
            )
            return _turn_view(await self._require_turn(turn_id))

    async def fail_turn(
        self,
        turn_id: str,
        *,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        return await self._fail(turn_id, code=code, message=message)

    async def fail_task(
        self,
        turn_id: str,
        *,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        return await self._fail(turn_id, code=code, message=message)

    async def pause_task(
        self,
        turn_id: str,
        *,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            turn = await self._require_turn(turn_id)
            operation = await self._operation_for_turn(turn_id)
            if str(turn["status"]) == "paused":
                return _turn_view(turn)
            if str(turn["status"]) in {"completed", "failed", "canceled"}:
                return _turn_view(turn)
            error = {"code": code, "message": message}
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = 'paused', "
                "assistant_content = '', "
                "execution_owner_id = NULL, lease_expires_at_ms = NULL, "
                "heartbeat_at_ms = NULL, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                [turn_id],
            )
            task_id = str((operation or {}).get("long_task_id") or "") or None
            await self._event_for_turn(
                turn,
                "screenplay.agent.task.paused",
                {"taskId": task_id, "error": error},
                task_id=task_id,
            )
            return _turn_view(await self._require_turn(turn_id))

    async def _fail(
        self,
        turn_id: str,
        *,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            turn = await self._require_turn(turn_id)
            operation = await self._operation_for_turn(turn_id)
            if str(turn["status"]) in {
                "completed", "paused", "failed", "canceled",
            }:
                return _turn_view(turn)
            error = {"code": code, "message": message}
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = 'failed', "
                "assistant_content = ?, "
                "execution_owner_id = NULL, lease_expires_at_ms = NULL, "
                "heartbeat_at_ms = NULL, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                [message, turn_id],
            )
            task_id = str((operation or {}).get("long_task_id") or "") or None
            await self._event_for_turn(
                turn,
                "screenplay.agent.task.failed" if task_id
                else "screenplay.agent.turn.failed",
                {"taskId": task_id, "error": error},
                task_id=task_id,
            )
            return _turn_view(await self._require_turn(turn_id))

    async def cancel_turn(self, turn_id: str) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            turn = await self._require_turn(turn_id)
            operation = await self._operation_for_turn(turn_id)
            if str(turn["status"]) not in {
                "queued", "planning", "running", "paused",
            }:
                return _turn_view(turn)
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = 'canceled', "
                "assistant_content = ?, execution_owner_id = NULL, "
                "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                ["剧本任务已终止。", turn_id],
            )
            await self._event_for_turn(
                turn,
                "screenplay.agent.task.canceled" if operation
                else "screenplay.agent.turn.canceled",
                {"taskId": (operation or {}).get("long_task_id")},
                task_id=str((operation or {}).get("long_task_id") or "") or None,
            )
            return _turn_view(await self._require_turn(turn_id))

    async def truncate_from_turn(self, turn_id: str) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            turn = await self._require_turn(turn_id)
            rows = await self._db.fetch_all(
                "SELECT t.*, o.id AS authoritative_operation_id, "
                "o.long_task_id AS authoritative_task_id "
                "FROM screenplay_agent_turns AS t "
                "LEFT JOIN screenplay_agent_operations AS o ON o.turn_id = t.id "
                "WHERE t.project_id = ? "
                "AND t.session_id = ? AND t.rowid >= ("
                "SELECT rowid FROM screenplay_agent_turns WHERE id = ?) "
                "ORDER BY t.rowid",
                [turn["project_id"], turn["session_id"], turn_id],
            )
            turn_ids = [str(row["id"]) for row in rows]
            task_ids = [
                str(row["authoritative_task_id"])
                for row in rows
                if row.get("authoritative_task_id")
            ]
            operation_ids = [
                str(row["authoritative_operation_id"])
                for row in rows
                if row.get("authoritative_operation_id")
            ]
            turn_marks = _marks(turn_ids)
            await self._db.execute(
                f"DELETE FROM screenplay_agent_events "
                f"WHERE turn_id IN ({turn_marks})",
                turn_ids,
            )
            await self._db.execute(
                f"DELETE FROM screenplay_agent_chunks "
                f"WHERE turn_id IN ({turn_marks})",
                turn_ids,
            )
            if task_ids:
                await self._delete_tasks(task_ids)
            if operation_ids:
                operation_marks = _marks(operation_ids)
                await self._db.execute(
                    f"DELETE FROM screenplay_agent_operation_commands "
                    f"WHERE operation_id IN ({operation_marks})",
                    operation_ids,
                )
                await self._db.execute(
                    f"DELETE FROM screenplay_agent_operations "
                    f"WHERE id IN ({operation_marks})",
                    operation_ids,
                )
            await self._db.execute(
                f"DELETE FROM screenplay_agent_turns WHERE id IN ({turn_marks})",
                turn_ids,
            )
            return {
                "projectId": str(turn["project_id"]),
                "sessionId": int(turn["session_id"]),
                "deletedTurnIds": turn_ids,
                "deletedTaskIds": task_ids,
            }

    async def _delete_tasks(self, task_ids: Sequence[str]) -> None:
        marks = _marks(task_ids)
        await self._remove_task_candidates(task_ids)
        work_items = await self._db.fetch_all(
            f"SELECT work_item_id FROM ai_agent_long_tasks "
            f"WHERE id IN ({marks})",
            list(task_ids),
        )
        work_item_ids = [str(row["work_item_id"]) for row in work_items]
        for table, column in (
            ("screenplay_agent_task_outputs", "task_id"),
            ("ai_agent_long_task_units", "task_id"),
            ("ai_agent_long_tasks", "id"),
        ):
            await self._db.execute(
                f"DELETE FROM {table} WHERE {column} IN ({marks})",
                list(task_ids),
            )
        if work_item_ids:
            item_marks = _marks(work_item_ids)
            await self._db.execute(
                f"DELETE FROM ai_agent_work_item_runs "
                f"WHERE work_item_id IN ({item_marks})",
                work_item_ids,
            )
            await self._db.execute(
                f"DELETE FROM ai_agent_work_items WHERE id IN ({item_marks})",
                work_item_ids,
            )

    async def _remove_task_candidates(self, task_ids: Sequence[str]) -> None:
        marks = _marks(task_ids)
        rows = await self._db.fetch_all(
            "SELECT r.id FROM screenplay_revisions AS r "
            "LEFT JOIN screenplay_project_heads AS h ON h.revision_id = r.id "
            "LEFT JOIN screenplay_acceptance_events AS a ON a.revision_id = r.id "
            f"WHERE r.agent_task_id IN ({marks}) "
            "AND h.revision_id IS NULL AND a.revision_id IS NULL",
            list(task_ids),
        )
        revision_ids = [str(row["id"]) for row in rows]
        if revision_ids:
            revision_marks = _marks(revision_ids)
            for table in (
                "screenplay_revision_inputs",
                "screenplay_revision_parts",
                "screenplay_revision_source_refs",
            ):
                await self._db.execute(
                    f"DELETE FROM {table} WHERE revision_id IN ({revision_marks})",
                    revision_ids,
                )
            await self._db.execute(
                f"DELETE FROM screenplay_outbox_events "
                f"WHERE aggregate_id IN ({revision_marks})",
                revision_ids,
            )
            await self._db.execute(
                f"DELETE FROM screenplay_revisions WHERE id IN ({revision_marks})",
                revision_ids,
            )
        await self._db.execute(
            f"UPDATE screenplay_revisions SET agent_task_id = NULL "
            f"WHERE agent_task_id IN ({marks})",
            list(task_ids),
        )

    async def get_snapshot(
        self,
        *,
        project_id: str,
        session_id: int,
    ) -> dict[str, Any]:
        await self._require_session(project_id, session_id, writable=False)
        turns = await self._db.fetch_all(
            "SELECT t.*, o.id AS authoritative_operation_id, "
            "o.status AS operation_status, "
            "o.long_task_id AS authoritative_task_id, "
            "o.target_role AS operation_target_role, "
            "o.requirements_json AS operation_requirements_json, "
            "o.result_revision_id AS operation_result_revision_id, "
            "COALESCE(o.error_json, (SELECT json_extract(e.payload_json, '$.error') "
            "FROM screenplay_agent_events AS e WHERE e.turn_id = t.id "
            "AND e.event_type IN ('screenplay.agent.turn.failed', "
            "'screenplay.agent.task.failed', 'screenplay.agent.task.paused') "
            "ORDER BY e.id DESC LIMIT 1)) AS operation_error_json, "
            "o.create_time AS operation_create_time, "
            "o.update_time AS operation_update_time "
            "FROM screenplay_agent_turns AS t "
            "LEFT JOIN screenplay_agent_operations AS o ON o.turn_id = t.id "
            "WHERE t.project_id = ? AND t.session_id = ? ORDER BY t.rowid",
            [project_id, int(session_id)],
        )
        cursor = await self._db.fetch_one(
            "SELECT COALESCE(MAX(id), 0) AS cursor FROM screenplay_agent_events "
            "WHERE project_id = ? AND session_id = ?",
            [project_id, int(session_id)],
        )
        return {
            "projectId": project_id,
            "sessionId": int(session_id),
            "cursor": int((cursor or {}).get("cursor") or 0),
            "turns": [_turn_view(row) for row in turns],
            "tasks": [
                await self._task_view(turn)
                for turn in turns
                if turn.get("authoritative_task_id")
            ],
        }

    async def _task_view(self, turn: Mapping[str, Any]) -> dict[str, Any]:
        task_id = str(turn["authoritative_task_id"])
        task = await self._db.fetch_one(
            "SELECT * FROM ai_agent_long_tasks WHERE id = ?",
            [task_id],
        )
        units = (
            await self._db.fetch_all(
                "SELECT * FROM ai_agent_long_task_units "
                "WHERE task_id = ? ORDER BY position",
                [task_id],
            )
            if task is not None else []
        )
        outputs = {
            str(row["unit_id"]): _object(row.get("output_json"))
            for row in await self._db.fetch_all(
                "SELECT unit_id, output_json "
                "FROM screenplay_agent_task_outputs WHERE task_id = ?",
                [task_id],
            )
        }
        return {
            "id": task_id,
            "projectId": str(turn["project_id"]),
            "sessionId": int(turn["session_id"]),
            "turnId": str(turn["id"]),
            "status": _operation_task_status(turn.get("operation_status")),
            "targetRole": str(turn.get("operation_target_role") or ""),
            "intent": _object(turn.get("intent_json")),
            "plannerRunId": str(turn.get("planner_run_id") or "") or None,
            "totalUnits": int((task or {}).get("total_units") or 0),
            "completedUnits": int((task or {}).get("completed_units") or 0),
            "resultRevisionId": str(
                turn.get("operation_result_revision_id") or ""
            ) or None,
            "error": _object(turn.get("operation_error_json")) or None,
            "units": [
                _unit_view(unit, outputs.get(str(unit["unit_id"]), {}))
                for unit in units
            ],
            "createdAt": turn.get("operation_create_time"),
            "updatedAt": turn.get("operation_update_time"),
        }

    async def list_events(
        self,
        *,
        project_id: str,
        session_id: int,
        after: int,
        limit: int,
    ) -> dict[str, Any]:
        rows = await self._db.fetch_all(
            "SELECT * FROM screenplay_agent_events "
            "WHERE project_id = ? AND session_id = ? AND id > ? "
            "ORDER BY id LIMIT ?",
            [project_id, int(session_id), max(0, int(after)), int(limit) + 1],
        )
        page = rows[:limit]
        return {
            "events": [_event_view(row) for row in page],
            "nextCursor": int(page[-1]["id"]) if page else max(0, int(after)),
            "hasMore": len(rows) > limit,
        }

    async def load_turn(self, turn_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            _TURN_WITH_OPERATION_SQL + " WHERE t.id = ?",
            [turn_id],
        )
        return _turn_view(row) if row is not None else None

    async def _finish_turn(
        self,
        turn_id: str,
        *,
        status: str,
        assistant_content: str,
        event_type: str,
        event_payload: Mapping[str, Any],
        require_planning_owner: bool,
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            turn = (
                await self._require_owned_turn(turn_id)
                if require_planning_owner
                else await self._require_turn(turn_id)
            )
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = ?, "
                "assistant_content = ?, execution_owner_id = NULL, "
                "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [status, assistant_content, turn_id],
            )
            await self._event_for_turn(turn, event_type, event_payload)
            return _turn_view(await self._require_turn(turn_id))

    async def _require_session(
        self,
        project_id: str,
        session_id: int,
        *,
        writable: bool = True,
    ) -> Mapping[str, Any]:
        row = await self._db.fetch_one(
            "SELECT s.*, p.status AS project_status "
            "FROM ai_sessions AS s JOIN screenplay_projects AS p "
            "ON p.id = s.screenplay_project_id "
            "WHERE s.id = ? AND s.screenplay_project_id = ? "
            "AND s.scope = 'screenplay'",
            [int(session_id), project_id],
        )
        if row is None:
            raise NotFoundError("剧本对话不存在")
        if writable and (
            bool(row.get("closed")) or row.get("project_status") == "archived"
        ):
            raise AppError("当前剧本项目不能继续对话", 409)
        return row

    async def _require_turn(self, turn_id: str) -> Mapping[str, Any]:
        row = await self._db.fetch_one(
            _TURN_WITH_OPERATION_SQL + " WHERE t.id = ?",
            [turn_id],
        )
        if row is None:
            raise NotFoundError("剧本 Agent Turn 不存在")
        return row

    async def _operation_for_turn(
        self,
        turn_id: str,
    ) -> Mapping[str, Any] | None:
        return await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_operations WHERE turn_id = ?",
            [turn_id],
        )

    async def _require_owned_turn(self, turn_id: str) -> Mapping[str, Any]:
        turn = await self._require_turn(turn_id)
        if (
            str(turn["status"]) != "planning"
            or str(turn.get("execution_owner_id") or "") != self._owner_id
        ):
            raise AppError("剧本 Agent Turn 执行租约已失效", 409)
        return turn

    async def _event_for_turn(
        self,
        turn: Mapping[str, Any],
        event_type: str,
        payload: Mapping[str, Any],
        *,
        task_id: str | None = None,
    ) -> None:
        await self._event(
            project_id=str(turn["project_id"]),
            session_id=int(turn["session_id"]),
            turn_id=str(turn["id"]),
            task_id=task_id,
            event_type=event_type,
            payload=payload,
        )

    async def _event(
        self,
        *,
        project_id: str,
        session_id: int,
        event_type: str,
        payload: Mapping[str, Any],
        turn_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        await self._db.execute(
            "INSERT INTO screenplay_agent_events "
            "(project_id, session_id, turn_id, task_id, event_type, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                project_id,
                int(session_id),
                turn_id,
                task_id,
                event_type,
                _dump(payload),
            ],
        )


def _turn_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "commandId": str(row.get("command_id") or ""),
        "projectId": str(row["project_id"]),
        "sessionId": int(row["session_id"]),
        "status": str(row["status"]),
        "userContent": str(row["user_content"]),
        "assistantContent": str(row.get("assistant_content") or ""),
        "runtimeProfile": _object(row.get("runtime_profile_json")),
        "intent": _object(row.get("intent_json")) or None,
        "plannerRunId": str(row.get("planner_run_id") or "") or None,
        "operationId": str(row.get("authoritative_operation_id") or "") or None,
        "taskId": str(row.get("authoritative_task_id") or "") or None,
        "targetRole": str(row.get("operation_target_role") or "") or None,
        "resultRevisionId": str(
            row.get("operation_result_revision_id") or ""
        ) or None,
        "error": _object(row.get("operation_error_json")) or None,
        "createdAt": row.get("create_time"),
        "updatedAt": row.get("update_time"),
    }


def _unit_view(
    row: Mapping[str, Any],
    output: Mapping[str, Any],
) -> dict[str, Any]:
    metadata = _object(row.get("metadata_json"))
    error_code = str(row.get("error_code") or "")
    return {
        "id": str(row["unit_id"]),
        "position": int(row["position"]),
        "kind": str(metadata.get("unitKind") or ""),
        "status": str(row["status"]),
        "input": dict(metadata.get("input") or {}),
        "output": dict(output),
        "error": (
            {"code": error_code, "message": error_code}
            if error_code
            else None
        ),
        "attempt": int(row.get("attempt") or 0),
    }


def _operation_task_status(value: object) -> str:
    status = str(value or "")
    return "completed" if status == "succeeded" else status


def _event_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "cursor": int(row["id"]),
        "turnId": str(row.get("turn_id") or "") or None,
        "taskId": str(row.get("task_id") or "") or None,
        "type": str(row["event_type"]),
        "payload": _object(row.get("payload_json")),
        "createdAt": row.get("create_time"),
    }


def _dump(value: object) -> str:
    return json.dumps(
        dict(value) if isinstance(value, Mapping) else value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _required(value: object, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _marks(values: Sequence[object]) -> str:
    if not values:
        raise ValueError("SQL placeholder values cannot be empty")
    return ",".join("?" for _ in values)


_TURN_WITH_OPERATION_SQL = (
    "SELECT t.*, o.id AS authoritative_operation_id, "
    "o.status AS operation_status, "
    "o.long_task_id AS authoritative_task_id, "
    "o.target_role AS operation_target_role, "
    "o.result_revision_id AS operation_result_revision_id, "
    "COALESCE(o.error_json, (SELECT json_extract(e.payload_json, '$.error') "
    "FROM screenplay_agent_events AS e WHERE e.turn_id = t.id "
    "AND e.event_type IN ('screenplay.agent.turn.failed', "
    "'screenplay.agent.task.failed', 'screenplay.agent.task.paused') "
    "ORDER BY e.id DESC LIMIT 1)) AS operation_error_json "
    "FROM screenplay_agent_turns AS t "
    "LEFT JOIN screenplay_agent_operations AS o ON o.turn_id = t.id"
)


__all__ = ["LEASE_MS", "SqliteScreenplayAgentRepository"]
