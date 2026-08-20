"""SQLite Turn messages and UI projections over screenplay Operations."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

from domains.screenplay_agent import ScreenplayIntent
from exceptions import AppError, NotFoundError
from infrastructure.persistence.run_execution_store import now_ms


LEASE_MS = 30_000


class SqliteScreenplayAgentRepository:
    """Own Turn state while Operation and PurrA own execution and output."""

    def __init__(self, db, *, owner_id: str) -> None:
        self._db = db
        self._owner_id = _required(owner_id, "screenplay Agent owner id")

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @asynccontextmanager
    async def _mutation_transaction(self):
        if self._db.current_task_owns_transaction():
            yield
            return
        async with self._db.transaction(cancellation_linearizable=True):
            yield

    async def begin_turn(
        self,
        *,
        command_id: str,
        project_id: str,
        session_id: int,
        content: str,
        stage_command: Mapping[str, Any] | None,
        runtime_profile: Mapping[str, Any],
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            existing = await self._db.fetch_one(
                _TURN_WITH_OPERATION_SQL
                + " WHERE t.project_id = ? AND t.command_id = ?",
                [project_id, command_id],
            )
            if existing is not None:
                existing_command = (
                    _object(existing.get("stage_command_json")) or None
                )
                requested_command = (
                    dict(stage_command) if stage_command is not None else None
                )
                if (
                    int(existing["session_id"]) != int(session_id)
                    or str(existing["user_content"]) != content
                    or existing_command != requested_command
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
                "stage_command_json, runtime_profile_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    turn_id,
                    project_id,
                    int(session_id),
                    command_id,
                    content,
                    _dump(stage_command) if stage_command is not None else None,
                    _dump(runtime_profile),
                ],
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
                cancel_requested = bool(
                    turn.get("cancel_requested_at_ms") is not None
                    or (
                        operation is not None
                        and operation.get("cancel_requested_at_ms") is not None
                    )
                )
                if cancel_requested:
                    task_id = str(
                        (operation or {}).get("long_task_id") or ""
                    ) or None
                    if task_id:
                        await self._db.execute(
                            "UPDATE ai_agent_long_tasks SET status = 'canceled', "
                            "cancel_requested_at_ms = COALESCE("
                            "cancel_requested_at_ms, ?), revision = revision + 1, "
                            "update_time = CURRENT_TIMESTAMP WHERE id = ? "
                            "AND status IN ('pending', 'running', 'paused')",
                            [
                                int(turn.get("cancel_requested_at_ms") or now_ms()),
                                task_id,
                            ],
                        )
                        await self._db.execute(
                            "UPDATE ai_agent_long_task_units SET status = 'canceled', "
                            "worker_id = NULL, lease_expires_at_ms = NULL, "
                            "update_time = CURRENT_TIMESTAMP WHERE task_id = ? "
                            "AND status IN ('pending', 'waiting_retry', 'claimed', "
                            "'running', 'needs_split', 'blocked')",
                            [task_id],
                        )
                        await self._db.execute(
                            "UPDATE ai_agent_runs SET cancel_requested_at_ms = "
                            "COALESCE(cancel_requested_at_ms, ?), "
                            "update_time = CURRENT_TIMESTAMP WHERE id IN ("
                            "SELECT run_id FROM ai_agent_long_task_units "
                            "WHERE task_id = ? AND run_id IS NOT NULL) "
                            "AND status = 'running'",
                            [
                                int(turn.get("cancel_requested_at_ms") or now_ms()),
                                task_id,
                            ],
                        )
                    if operation is not None:
                        await self._db.execute(
                            "UPDATE screenplay_agent_operations SET "
                            "status = 'canceled', revision = revision + 1, "
                            "update_time = CURRENT_TIMESTAMP "
                            "WHERE id = ? AND status IN "
                            "('queued', 'running', 'paused')",
                            [operation["id"]],
                        )
                    await self._db.execute(
                        "UPDATE screenplay_agent_turns SET status = 'canceled', "
                        "assistant_content = '', execution_owner_id = NULL, "
                        "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                        "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                        [turn["id"]],
                    )
                    continue
                turn_status = "failed"
                assistant_content = ""
                if operation is not None and str(operation["status"]) in {
                    "queued", "running", "paused",
                }:
                    turn_status = "paused"
                    assistant_content = ""
                    await self._db.execute(
                        "UPDATE screenplay_agent_operations SET status = 'paused', "
                        "error_json = ?, revision = revision + 1, "
                        "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                        [_dump(error), operation["id"]],
                    )
                await self._db.execute(
                    "UPDATE screenplay_agent_turns SET status = ?, "
                    "assistant_content = ?, error_json = ?, "
                    "execution_owner_id = NULL, "
                    "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                    [turn_status, assistant_content, _dump(error), turn["id"]],
                )
            return tuple(str(turn["id"]) for turn in turns)

    async def claim_turn(self, turn_id: str) -> bool:
        current = now_ms()
        async with self._db.transaction(cancellation_linearizable=True):
            turn = await self._require_turn(turn_id)
            if str(turn["status"]) not in {"queued", "planning"}:
                return False
            if turn.get("cancel_requested_at_ms") is not None:
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
            return True

    async def validate_claimed_turn_start(
        self,
        turn_id: str,
        *,
        attempt: int,
    ) -> bool:
        turn = await self._db.fetch_one(
            "SELECT status, planner_run_id AS root_run_id, execution_owner_id, "
            "lease_expires_at_ms, cancel_requested_at_ms, attempt "
            "FROM screenplay_agent_turns WHERE id = ?",
            [turn_id],
        )
        return bool(
            turn is not None
            and str(turn.get("status") or "") == "planning"
            and not str(turn.get("root_run_id") or "")
            and str(turn.get("execution_owner_id") or "") == self._owner_id
            and int(turn.get("lease_expires_at_ms") or 0) > now_ms()
            and turn.get("cancel_requested_at_ms") is None
            and int(turn.get("attempt") or 0) == int(attempt)
        )

    async def release_canceled_turn_claim(self, turn_id: str) -> bool:
        async with self._db.transaction(cancellation_linearizable=True):
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET execution_owner_id = NULL, "
                "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ? "
                "AND execution_owner_id = ? AND cancel_requested_at_ms IS NOT NULL",
                [turn_id, self._owner_id],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            return int((changed or {}).get("count") or 0) == 1

    async def release_expired_canceled_claim(
        self,
        turn_id: str,
        now: int,
    ) -> bool:
        """Release only an expired foreign claim after cancellation was fenced."""

        checked_at = int(now)
        async with self._mutation_transaction():
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET execution_owner_id = NULL, "
                "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ? "
                "AND status IN ('queued', 'planning', 'running', 'paused') "
                "AND cancel_requested_at_ms IS NOT NULL "
                "AND execution_owner_id IS NOT NULL "
                "AND lease_expires_at_ms IS NOT NULL "
                "AND lease_expires_at_ms <= ?",
                [turn_id, checked_at],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            return int((changed or {}).get("count") or 0) == 1

    async def record_intent(
        self,
        turn_id: str,
        *,
        intent: ScreenplayIntent,
        root_run_id: str | None,
    ) -> None:
        async with self._db.transaction():
            turn = await self._require_owned_turn(turn_id)
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET intent_json = ?, "
                "planner_run_id = ?, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                [
                    _dump(intent.to_mapping()),
                    str(root_run_id or "").strip() or None,
                    turn_id,
                ],
            )

    async def attach_root_run(
        self,
        turn_id: str,
        root_run_id: str,
    ) -> dict[str, Any]:
        normalized_run_id = _required(root_run_id, "screenplay Root Run id")
        async with self._db.transaction(cancellation_linearizable=True):
            turn = await self._require_owned_turn(turn_id)
            existing = str(turn.get("root_run_id") or "").strip()
            if existing and existing != normalized_run_id:
                raise AppError("剧本 Agent Turn 已绑定另一 Root Run", 409)
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET planner_run_id = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [normalized_run_id, turn_id],
            )
            return _turn_view(await self._require_turn(turn_id))

    async def record_admitted_intent(
        self,
        turn_id: str,
        *,
        intent: ScreenplayIntent,
    ) -> None:
        async with self._mutation_transaction():
            turn = await self._require_turn(turn_id)
            if str(turn.get("status") or "") not in {"queued", "planning"}:
                raise AppError("剧本 Agent Turn 已不在规划状态", 409)
            encoded = _dump(intent.to_mapping())
            existing = _object(turn.get("intent_json"))
            if existing and existing != intent.to_mapping():
                raise AppError("剧本 Agent Turn 已绑定另一任务意图", 409)
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET intent_json = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [encoded, turn_id],
            )

    async def complete_answer(self, turn_id: str, reply: str) -> dict[str, Any]:
        return await self._finish_turn(
            turn_id,
            status="completed",
            assistant_content=reply,
            require_planning_owner=True,
        )

    async def attach_operation(
        self,
        turn_id: str,
        *,
        operation_id: str,
        task_id: str,
        target_role: str,
        root_run_id: str | None = None,
    ) -> dict[str, Any]:
        del target_role
        normalized_operation_id = _required(operation_id, "screenplay Operation id")
        normalized_task_id = _required(task_id, "screenplay LongTask id")
        normalized_root_run_id = _required(root_run_id, "screenplay Root Run id")
        async with self._mutation_transaction():
            turn = await self._require_turn(turn_id)
            existing_root_run_id = str(turn.get("root_run_id") or "").strip()
            if existing_root_run_id and existing_root_run_id != normalized_root_run_id:
                raise AppError("剧本 Agent Turn Root Run 不匹配", 409)
            if not existing_root_run_id:
                await self._db.execute(
                    "UPDATE screenplay_agent_turns SET planner_run_id = ? "
                    "WHERE id = ?",
                    [normalized_root_run_id, turn_id],
                )
            if str(turn.get("operation_id") or "") != normalized_operation_id:
                raise AppError("剧本 Agent Turn Operation 不匹配", 409)
            operation = await self._db.fetch_one(
                "SELECT long_task_id FROM screenplay_agent_operations WHERE id = ?",
                [normalized_operation_id],
            )
            if (
                operation is None
                or str(operation.get("long_task_id") or "") != normalized_task_id
            ):
                raise AppError("剧本 Agent Operation LongTask 不匹配", 409)
            if str(turn.get("status") or "") == "running":
                return _turn_view(turn)
            if str(turn.get("status") or "") not in {"queued", "planning"}:
                raise AppError("剧本 Agent Turn 已不在规划状态", 409)
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = 'running', "
                "operation_id = ?, execution_owner_id = NULL, "
                "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [normalized_operation_id, turn_id],
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
                "assistant_content = ?, error_json = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [assistant_content, turn_id],
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
        async with self._mutation_transaction():
            turn = await self._require_turn(turn_id)
            if str(turn["status"]) == "paused":
                return _turn_view(turn)
            if str(turn["status"]) in {"completed", "failed", "canceled"}:
                return _turn_view(turn)
            error = {"code": code, "message": message}
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = 'paused', "
                "assistant_content = '', error_json = ?, "
                "execution_owner_id = NULL, lease_expires_at_ms = NULL, "
                "heartbeat_at_ms = NULL, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                [_dump(error), turn_id],
            )
            return _turn_view(await self._require_turn(turn_id))

    async def _fail(
        self,
        turn_id: str,
        *,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        async with self._mutation_transaction():
            turn = await self._require_turn(turn_id)
            if str(turn["status"]) in {
                "completed", "paused", "failed", "canceled",
            }:
                return _turn_view(turn)
            error = {"code": code, "message": message}
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = 'failed', "
                "assistant_content = '', error_json = ?, "
                "execution_owner_id = NULL, lease_expires_at_ms = NULL, "
                "heartbeat_at_ms = NULL, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                [_dump(error), turn_id],
            )
            return _turn_view(await self._require_turn(turn_id))

    async def truncate_from_turn(self, turn_id: str) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            turn = await self._require_turn(turn_id)
            rows = await self._db.fetch_all(
                "SELECT t.*, t.planner_run_id AS root_run_id, "
                "o.id AS authoritative_operation_id, "
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
            root_ids = [
                str(row["root_run_id"])
                for row in rows
                if row.get("root_run_id")
            ]
            await self._require_truncation_barrier(
                turn_ids=turn_ids,
                root_ids=root_ids,
                task_ids=task_ids,
            )
            turn_marks = _marks(turn_ids)
            if task_ids:
                await self._delete_tasks(task_ids)
            await self._db.execute(
                f"DELETE FROM screenplay_agent_cancel_commands "
                f"WHERE turn_id IN ({turn_marks})"
                + (
                    f" OR operation_id IN ({_marks(operation_ids)})"
                    if operation_ids else ""
                ),
                [*turn_ids, *operation_ids],
            )
            if operation_ids:
                operation_marks = _marks(operation_ids)
                await self._db.execute(
                    f"DELETE FROM screenplay_agent_operation_usage "
                    f"WHERE operation_id IN ({operation_marks})",
                    operation_ids,
                )
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
                "deletedOperationIds": operation_ids,
            }

    async def _require_truncation_barrier(
        self,
        *,
        turn_ids: Sequence[str],
        root_ids: Sequence[str],
        task_ids: Sequence[str],
    ) -> None:
        if root_ids:
            marks = _marks(root_ids)
            active_runs = await self._db.fetch_one(
                "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE "
                f"id IN ({marks}) AND "
                "(status = 'running' OR execution_owner_id IS NOT NULL OR "
                "lease_expires_at_ms IS NOT NULL)",
                list(root_ids),
            )
            active_delegations = await self._db.fetch_one(
                "SELECT COUNT(*) AS count FROM ai_agent_delegations WHERE "
                f"run_id IN ({marks}) AND status IN ('queued', 'running')",
                list(root_ids),
            )
            if int((active_runs or {}).get("count") or 0) or int(
                (active_delegations or {}).get("count") or 0
            ):
                raise AppError("screenplay truncate runtime is still active", 409)
        if turn_ids:
            active_turns = await self._db.fetch_one(
                "SELECT COUNT(*) AS count FROM screenplay_agent_turns WHERE "
                f"id IN ({_marks(turn_ids)}) AND (status IN "
                "('queued', 'planning', 'running', 'paused') OR "
                "execution_owner_id IS NOT NULL OR lease_expires_at_ms IS NOT NULL)",
                list(turn_ids),
            )
            if int((active_turns or {}).get("count") or 0):
                raise AppError("screenplay truncate Turn is still active", 409)
        if task_ids:
            active_units = await self._db.fetch_one(
                "SELECT COUNT(*) AS count FROM ai_agent_long_task_units WHERE "
                f"task_id IN ({_marks(task_ids)}) AND (status IN "
                "('claimed', 'running') OR worker_id IS NOT NULL OR "
                "lease_expires_at_ms IS NOT NULL)",
                list(task_ids),
            )
            if int((active_units or {}).get("count") or 0):
                raise AppError("screenplay truncate task is still active", 409)

    async def _delete_tasks(self, task_ids: Sequence[str]) -> None:
        marks = _marks(task_ids)
        await self._remove_task_candidates(task_ids)
        await self._db.execute(
            f"DELETE FROM screenplay_checkpoint_plans "
            f"WHERE task_id IN ({marks})",
            list(task_ids),
        )
        for table, column in (
            ("ai_agent_long_task_usage", "task_id"),
            ("ai_agent_long_task_units", "task_id"),
            ("ai_agent_long_task_runs", "task_id"),
            ("ai_agent_long_tasks", "id"),
        ):
            await self._db.execute(
                f"DELETE FROM {table} WHERE {column} IN ({marks})",
                list(task_ids),
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
            "SELECT t.*, t.planner_run_id AS root_run_id, "
            "o.id AS authoritative_operation_id, "
            "o.status AS operation_status, "
            "o.long_task_id AS authoritative_task_id, "
            "o.revision AS operation_revision, "
            "o.target_role AS operation_target_role, "
            "o.requirements_json AS operation_requirements_json, "
            "o.result_revision_id AS operation_result_revision_id, "
            "o.finalization_receipt_id AS operation_finalization_receipt_id, "
            "o.cancel_receipt_id AS operation_cancel_receipt_id, "
            "o.cancel_requested_at_ms AS operation_cancel_requested_at_ms, "
            "o.usage_json AS operation_usage_json, "
            "COALESCE(o.error_json, t.error_json) AS operation_error_json, "
            "o.create_time AS operation_create_time, "
            "o.update_time AS operation_update_time "
            "FROM screenplay_agent_turns AS t "
            "LEFT JOIN screenplay_agent_operations AS o ON o.turn_id = t.id "
            "WHERE t.project_id = ? AND t.session_id = ? ORDER BY t.rowid",
            [project_id, int(session_id)],
        )
        cursor = await self._db.fetch_one(
            "SELECT COALESCE(MAX(e.id), 0) AS cursor "
            "FROM ai_agent_run_events AS e "
            "JOIN ai_agent_runs AS r ON r.id = e.run_id "
            "WHERE r.session_id = ? AND e.event_id IS NOT NULL "
            "AND e.visibility = 'public'",
            [int(session_id)],
        )
        tasks = [
            await self._task_view(turn)
            for turn in turns
            if turn.get("authoritative_task_id")
        ]
        tasks_by_turn = {str(task["turnId"]): task for task in tasks}
        return {
            "projectId": project_id,
            "sessionId": int(session_id),
            "cursor": int((cursor or {}).get("cursor") or 0),
            "turns": [_turn_view(row) for row in turns],
            "tasks": tasks,
            "operations": [
                _operation_view(turn, tasks_by_turn.get(str(turn["id"])))
                for turn in turns
                if turn.get("authoritative_operation_id")
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
        return {
            "id": task_id,
            "projectId": str(turn["project_id"]),
            "sessionId": int(turn["session_id"]),
            "turnId": str(turn["id"]),
            "status": _operation_task_status(turn.get("operation_status")),
            "targetRole": str(turn.get("operation_target_role") or ""),
            "intent": _object(turn.get("intent_json")),
            "rootRunId": str(turn.get("root_run_id") or "") or None,
            "totalUnits": int((task or {}).get("total_units") or 0),
            "completedUnits": int((task or {}).get("completed_units") or 0),
            "usage": _object((task or {}).get("usage_json")),
            "resultRevisionId": str(
                turn.get("operation_result_revision_id") or ""
            ) or None,
            "error": _object(turn.get("operation_error_json")) or None,
            "units": [
                _unit_view(unit)
                for unit in units
            ],
            "createdAt": turn.get("operation_create_time"),
            "updatedAt": turn.get("operation_update_time"),
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

def _turn_view(row: Mapping[str, Any]) -> dict[str, Any]:
    root_run_id = str(row.get("root_run_id") or "") or None
    return {
        "id": str(row["id"]),
        "commandId": str(row.get("command_id") or ""),
        "projectId": str(row["project_id"]),
        "sessionId": int(row["session_id"]),
        "status": str(row["status"]),
        "userContent": str(row["user_content"]),
        "stageCommand": _object(row.get("stage_command_json")) or None,
        "assistantContent": str(row.get("assistant_content") or ""),
        "runtimeProfile": _object(row.get("runtime_profile_json")),
        "executionOwnerId": str(row.get("execution_owner_id") or "") or None,
        "leaseExpiresAtMs": row.get("lease_expires_at_ms"),
        "cancelRequestedAtMs": row.get("cancel_requested_at_ms"),
        "attempt": int(row.get("attempt") or 0),
        "intent": _object(row.get("intent_json")) or None,
        "rootRunId": root_run_id,
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
) -> dict[str, Any]:
    metadata = _object(row.get("metadata_json"))
    error_code = str(row.get("error_code") or "")
    unit_input = dict(metadata.get("input") or {})
    episode_number = int(unit_input.get("episodeNumber") or 0)
    unit_kind = str(metadata.get("unitKind") or "")
    error_message = (
        f"第 {episode_number} 集审阅失败"
        if error_code
        and episode_number > 0
        and unit_kind in {"generate_review_dimension", "validate_manifest_part"}
        else error_code
    )
    return {
        "id": str(row["unit_id"]),
        "semanticKey": str(row.get("semantic_key") or row["unit_id"]),
        "position": int(row["position"]),
        "kind": unit_kind,
        "status": str(row["status"]),
        "input": unit_input,
        "outputRef": str(row.get("output_ref") or "") or None,
        "artifactDigest": str(row.get("artifact_digest") or "") or None,
        "validationReceipt": _object(row.get("validation_receipt_json")),
        "error": (
            {"code": error_code, "message": error_message}
            if error_code
            else None
        ),
        "attempt": int(row.get("attempt") or 0),
    }


def _operation_task_status(value: object) -> str:
    status = str(value or "")
    return "completed" if status == "succeeded" else status


def _operation_view(
    turn: Mapping[str, Any],
    task: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "id": str(turn["authoritative_operation_id"]),
        "turnId": str(turn["id"]),
        "taskId": str(turn.get("authoritative_task_id") or "") or None,
        "status": str(turn.get("operation_status") or ""),
        "revision": int(turn.get("operation_revision") or 1),
        "targetRole": str(turn.get("operation_target_role") or ""),
        "parts": list((task or {}).get("units") or ()),
        "resultRevisionId": str(
            turn.get("operation_result_revision_id") or ""
        ) or None,
        "finalizationReceiptId": str(
            turn.get("operation_finalization_receipt_id") or ""
        ) or None,
        "cancelReceiptId": str(
            turn.get("operation_cancel_receipt_id") or ""
        ) or None,
        "cancelRequestedAt": (
            str(turn["operation_cancel_requested_at_ms"])
            if turn.get("operation_cancel_requested_at_ms") is not None
            else None
        ),
        "error": _object(turn.get("operation_error_json")) or None,
        "usage": _object(turn.get("operation_usage_json")),
        "createdAt": turn.get("operation_create_time"),
        "updatedAt": turn.get("operation_update_time"),
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
    "SELECT t.*, t.planner_run_id AS root_run_id, "
    "o.id AS authoritative_operation_id, "
    "o.status AS operation_status, "
    "o.long_task_id AS authoritative_task_id, "
    "o.revision AS operation_revision, "
    "o.target_role AS operation_target_role, "
    "o.result_revision_id AS operation_result_revision_id, "
    "o.finalization_receipt_id AS operation_finalization_receipt_id, "
    "o.cancel_receipt_id AS operation_cancel_receipt_id, "
    "o.cancel_requested_at_ms AS operation_cancel_requested_at_ms, "
    "o.usage_json AS operation_usage_json, "
    "COALESCE(o.error_json, t.error_json) AS operation_error_json "
    "FROM screenplay_agent_turns AS t "
    "LEFT JOIN screenplay_agent_operations AS o ON o.turn_id = t.id"
)


__all__ = ["LEASE_MS", "SqliteScreenplayAgentRepository"]
