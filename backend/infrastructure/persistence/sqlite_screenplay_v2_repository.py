"""SQLite persistence for the screenplay v2 project aggregate."""

from __future__ import annotations

import json
import hashlib
import time
import uuid
from collections.abc import Mapping
from typing import Any

from domains.screenplay.project_aggregate import (
    applicable_deliverable_roles,
    derive_stage,
    next_actions,
    public_format,
)
from database.crud.screenplay_project_deletion import (
    delete_screenplay_project_data,
)
from exceptions import AppError, NotFoundError
from utils.id_utils import short_id8


def _dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _nullable_object(value: object) -> dict[str, Any] | None:
    if value is None or str(value).strip() == "":
        return None
    return _object(value)


def _head_content_map(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        str(row["role"]): _object(row.get("content_json"))
        for row in rows
    }


def _deliverable_id(project_id: str, role: str) -> str:
    return f"spdel:{project_id}:{role}"


class SqliteScreenplayV2Repository:
    def __init__(self, db) -> None:
        self._db = db

    async def list_projects(
        self,
        *,
        include_archived: bool,
    ) -> list[dict[str, Any]]:
        where = "" if include_archived else "AND status != 'archived' "
        rows = await self._db.fetch_all(
            "SELECT id FROM screenplay_projects "
            "WHERE source_snapshot_json IS NOT NULL "
            f"{where}ORDER BY update_time DESC, create_time DESC"
        )
        return [
            (await self.get_workspace(str(row["id"])))["project"]
            for row in rows
        ]

    async def find_create_project_receipt(
        self,
        *,
        command_id: str,
        request_digest: str,
    ) -> str | None:
        receipt = await self._db.fetch_one(
            "SELECT command_type, project_id, request_digest "
            "FROM screenplay_command_receipts WHERE command_id = ?",
            [command_id],
        )
        if receipt is None:
            return None
        if (
            str(receipt.get("command_type") or "") != "createProject"
            or str(receipt.get("request_digest") or "") != request_digest
        ):
            raise AppError(
                "同一个 Idempotency-Key 不能用于不同的剧本项目请求",
                409,
            )
        return str(receipt["project_id"])

    async def find_command_receipt(
        self,
        *,
        command_id: str,
        command_type: str,
        request_digest: str,
    ) -> dict[str, Any] | None:
        receipt = await self._db.fetch_one(
            "SELECT * FROM screenplay_command_receipts WHERE command_id = ?",
            [command_id],
        )
        if receipt is None:
            return None
        if (
            str(receipt.get("command_type") or "") != command_type
            or str(receipt.get("request_digest") or "") != request_digest
        ):
            raise AppError(
                "同一个 Idempotency-Key 不能用于不同的剧本写入请求",
                409,
            )
        return _object(receipt.get("response_json"))

    async def update_project(
        self,
        *,
        command_id: str,
        request_digest: str,
        project_id: str,
        expected_project_revision: int,
        title: str,
    ) -> dict[str, Any]:
        """Update native project metadata behind its aggregate CAS boundary."""

        async with self._db.transaction(cancellation_linearizable=True):
            replay = await self.find_command_receipt(
                command_id=command_id,
                command_type="updateProject",
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            project = await self._require_native_project(project_id)
            if str(project.get("status") or "active") == "archived":
                raise AppError("项目已归档，恢复项目后才能修改", 409)
            actual_revision = int(project.get("revision") or 1)
            if actual_revision != int(expected_project_revision):
                raise AppError("项目已被其他操作更新，请刷新后重试", 409)
            next_revision = actual_revision + 1
            await self._db.execute(
                "UPDATE screenplay_projects SET title = ?, revision = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ? AND revision = ?",
                [title, next_revision, project_id, actual_revision],
            )
            response = {
                "projectId": project_id,
                "projectRevision": next_revision,
            }
            await self._record_command_receipt(
                command_id=command_id,
                command_type="updateProject",
                project_id=project_id,
                request_digest=request_digest,
                result_ref=f"screenplay-project://{project_id}",
                response=response,
            )
            await self._record_outbox(
                aggregate_type="screenplayProject",
                aggregate_id=project_id,
                event_type="screenplay.project.updated",
                payload={**response, "changedFields": ["title"]},
            )
            return response

    async def set_project_lifecycle(
        self,
        *,
        command_id: str,
        request_digest: str,
        project_id: str,
        expected_project_revision: int,
        lifecycle: str,
    ) -> dict[str, Any]:
        """Archive or restore a native project without racing active work."""

        if lifecycle not in {"active", "archived"}:
            raise ValueError("unsupported screenplay project lifecycle")
        action = "archive" if lifecycle == "archived" else "restore"
        command_type = f"{action}Project"
        async with self._db.transaction(cancellation_linearizable=True):
            replay = await self.find_command_receipt(
                command_id=command_id,
                command_type=command_type,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            project = await self._require_native_project(project_id)
            actual_revision = int(project.get("revision") or 1)
            if actual_revision != int(expected_project_revision):
                raise AppError("项目已被其他操作更新，请刷新后重试", 409)
            current = str(project.get("status") or "active")
            if lifecycle == "archived":
                await self._require_no_active_operation(project_id)
            next_revision = actual_revision
            if current != lifecycle:
                next_revision += 1
                await self._db.execute(
                    "UPDATE screenplay_projects SET status = ?, revision = ?, "
                    "update_time = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND revision = ?",
                    [lifecycle, next_revision, project_id, actual_revision],
                )
                await self._record_outbox(
                    aggregate_type="screenplayProject",
                    aggregate_id=project_id,
                    event_type=f"screenplay.project.{lifecycle}",
                    payload={
                        "projectId": project_id,
                        "projectRevision": next_revision,
                        "lifecycle": lifecycle,
                    },
                )
            response = {
                "projectId": project_id,
                "projectRevision": next_revision,
                "lifecycle": lifecycle,
            }
            await self._record_command_receipt(
                command_id=command_id,
                command_type=command_type,
                project_id=project_id,
                request_digest=request_digest,
                result_ref=f"screenplay-project://{project_id}",
                response=response,
            )
            return response

    async def delete_project(
        self,
        *,
        command_id: str,
        request_digest: str,
        project_id: str,
        expected_project_revision: int,
    ) -> dict[str, Any]:
        """Hard-delete one native aggregate and retain an idempotent tombstone."""

        async with self._db.transaction(cancellation_linearizable=True):
            replay = await self.find_command_receipt(
                command_id=command_id,
                command_type="deleteProject",
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            project = await self._require_native_project(project_id)
            actual_revision = int(project.get("revision") or 1)
            if actual_revision != int(expected_project_revision):
                raise AppError("项目已被其他操作更新，请刷新后重试", 409)
            await self._require_no_active_operation(project_id)
            deleted = await delete_screenplay_project_data(
                self._db,
                project_id,
            )
            if not deleted:
                raise NotFoundError("剧本项目不存在")
            response = {"projectId": project_id, "deleted": True}
            # The cascade removes the project's former receipts and outbox.
            # Persist this command after it as a deletion tombstone so a lost
            # response can be retried without treating success as a 404.
            await self._record_command_receipt(
                command_id=command_id,
                command_type="deleteProject",
                project_id=project_id,
                request_digest=request_digest,
                result_ref=f"screenplay-project-deleted://{project_id}",
                response=response,
            )
            await self._record_outbox(
                aggregate_type="screenplayProject",
                aggregate_id=project_id,
                event_type="screenplay.project.deleted",
                payload=response,
            )
            return response

    async def _require_native_project(self, project_id: str) -> dict[str, Any]:
        project = await self._db.fetch_one(
            "SELECT * FROM screenplay_projects "
            "WHERE id = ? AND source_snapshot_json IS NOT NULL",
            [project_id],
        )
        if project is None:
            raise NotFoundError("剧本项目不存在")
        return project

    async def _require_no_active_operation(self, project_id: str) -> None:
        active = await self._db.fetch_one(
            "SELECT id FROM screenplay_operations WHERE project_id = ? "
            "AND status IN ('queued', 'running', 'paused') LIMIT 1",
            [project_id],
        )
        if active is not None:
            raise AppError(
                "项目仍有活动 Operation，请先完成或取消后再操作",
                409,
            )

    async def create_operation(
        self,
        *,
        command_id: str,
        request_digest: str,
        project_id: str,
        expected_project_revision: int,
        target_role: str,
        intent: Mapping[str, Any],
        conversation: Mapping[str, Any],
        within_transaction: bool = False,
    ) -> dict[str, Any]:
        """Create the durable command boundary before any Agent Run exists."""

        transaction = self._db.transaction(
            cancellation_linearizable=not within_transaction,
        )
        async with transaction:
            replay = await self.find_command_receipt(
                command_id=command_id,
                command_type="startOperation",
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            project = await self._require_native_project(project_id)
            if str(project.get("status") or "") == "archived":
                raise AppError("项目已归档，不能启动 Operation", 409)
            actual_revision = int(project.get("revision") or 1)
            if actual_revision != int(expected_project_revision):
                raise AppError("项目已被其他操作更新，请刷新后重试", 409)
            deliverable = await self._db.fetch_one(
                "SELECT id FROM screenplay_deliverables "
                "WHERE project_id = ? AND role = ?",
                [project_id, target_role],
            )
            if deliverable is None:
                raise AppError("目标交付物不属于该剧本项目", 409)
            heads = await self._head_rows(project_id)
            base_heads = {
                str(row["role"]): str(row["revision_id"])
                for row in heads
            }
            prerequisite = _prerequisite_role(
                target_role,
                str(project.get("source_kind") or "original"),
            )
            if prerequisite and prerequisite not in base_heads:
                raise AppError("启动该 Operation 前必须先接受上游版本", 409)
            active = await self._db.fetch_one(
                "SELECT id FROM screenplay_operations WHERE project_id = ? "
                "AND target_role = ? "
                "AND status IN ('queued', 'running', 'paused') LIMIT 1",
                [project_id, target_role],
            )
            if active is not None:
                raise AppError("同一目标已有未结束的 Operation", 409)

            operation_id = f"spop_{uuid.uuid4().hex}"
            stored_intent = {
                **dict(intent),
                "conversation": dict(conversation),
            }
            await self._db.execute(
                "INSERT INTO screenplay_operations "
                "(id, project_id, command_id, target_role, intent_json, "
                "base_project_revision, base_heads_json, status, progress_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)",
                [
                    operation_id,
                    project_id,
                    command_id,
                    target_role,
                    _dump(stored_intent),
                    actual_revision,
                    _dump(base_heads),
                    _dump({"phase": "queued", "percent": 0}),
                ],
            )
            queued_payload = {
                "operationId": operation_id,
                "projectId": project_id,
                "targetRole": target_role,
                "status": "queued",
            }
            await self._record_operation_event(
                operation_id=operation_id,
                sequence=1,
                event_type="screenplay.operation.queued",
                payload=queued_payload,
            )
            next_project_revision = actual_revision + 1
            await self._db.execute(
                "UPDATE screenplay_projects SET revision = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ? AND revision = ?",
                [next_project_revision, project_id, actual_revision],
            )
            row = await self._db.fetch_one(
                "SELECT * FROM screenplay_operations WHERE id = ?",
                [operation_id],
            )
            response = {
                "operation": _operation_view(row),
                "projectRevision": next_project_revision,
            }
            await self._record_command_receipt(
                command_id=command_id,
                command_type="startOperation",
                project_id=project_id,
                request_digest=request_digest,
                result_ref=f"screenplay-operation://{operation_id}",
                response=response,
            )
            await self._record_outbox(
                aggregate_type="screenplayOperation",
                aggregate_id=operation_id,
                event_type="screenplay.operation.queued",
                payload=queued_payload,
            )
            return response

    async def require_executable_operation(
        self,
        *,
        operation_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM screenplay_operations WHERE id = ? AND project_id = ?",
            [operation_id, project_id],
        )
        if row is None:
            raise NotFoundError("剧本 Operation 不存在")
        if str(row.get("status") or "") not in {"queued", "running"}:
            raise AppError("剧本 Operation 当前不能接管新的 Agent Run", 409)
        return _operation_view(row)

    async def activate_bound_run(
        self,
        *,
        operation_id: str,
        project_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Atomically claim one newly-created Run for an executable Operation."""

        async with self._db.transaction(cancellation_linearizable=True):
            operation = await self._db.fetch_one(
                "SELECT * FROM screenplay_operations "
                "WHERE id = ? AND project_id = ?",
                [operation_id, project_id],
            )
            if operation is None:
                raise NotFoundError("剧本 Operation 不存在")
            status = str(operation.get("status") or "")
            if status not in {"queued", "running"}:
                raise AppError("剧本 Operation 当前不能接管新的 Agent Run", 409)
            run = await self._db.fetch_one(
                "SELECT r.id, r.binding_namespace, "
                "r.binding_aggregate_id, r.binding_command_id, "
                "COALESCE(s.screenplay_project_id, root_s.screenplay_project_id, "
                "parent_s.screenplay_project_id) AS bound_project_id "
                "FROM ai_agent_runs AS r "
                "LEFT JOIN ai_sessions AS s ON s.id = r.session_id "
                "LEFT JOIN ai_agent_runs AS root ON root.id = r.root_run_id "
                "LEFT JOIN ai_sessions AS root_s ON root_s.id = root.session_id "
                "LEFT JOIN ai_agent_runs AS parent ON parent.id = r.parent_run_id "
                "LEFT JOIN ai_sessions AS parent_s ON parent_s.id = parent.session_id "
                "WHERE r.id = ?",
                [run_id],
            )
            if run is None:
                raise NotFoundError("Agent Run 不存在")
            binding_namespace = str(run.get("binding_namespace") or "").strip()
            if (
                binding_namespace != "screenplay.operation"
                or str(run.get("binding_aggregate_id") or "").strip()
                != project_id
                or str(run.get("binding_command_id") or "").strip()
                != operation_id
            ):
                raise AppError("Agent Run 的持久化业务绑定不匹配", 409)
            bound_project_id = str(run.get("bound_project_id") or "").strip()
            if bound_project_id and bound_project_id != project_id:
                raise AppError("Agent Run 不属于该剧本 Operation", 409)
            if status == "queued":
                await self._db.execute(
                    "UPDATE screenplay_operations SET status = 'running', "
                    "progress_json = ?, update_time = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND status = 'queued'",
                    [_dump({"phase": "running", "percent": 0}), operation_id],
                )
                await self._record_operation_event(
                    operation_id=operation_id,
                    sequence=await self._next_operation_sequence(operation_id),
                    event_type="screenplay.operation.started",
                    payload={
                        "operationId": operation_id,
                        "projectId": project_id,
                        "runId": run_id,
                    },
                )
            row = await self._db.fetch_one(
                "SELECT * FROM screenplay_operations WHERE id = ?",
                [operation_id],
            )
            return _operation_view(row)

    async def settle_operation_from_root_run(
        self,
        *,
        operation_id: str,
        project_id: str,
        run_id: str,
        run_status: str,
        error: str | None,
    ) -> dict[str, Any]:
        """Close an unfinished Operation when its owning root Run terminates."""

        normalized_status = str(run_status or "").strip()
        if normalized_status not in {"done", "blocked", "failed", "canceled"}:
            raise ValueError("root Agent Run status must be terminal")
        async with self._db.transaction(cancellation_linearizable=True):
            operation = await self._db.fetch_one(
                "SELECT * FROM screenplay_operations "
                "WHERE id = ? AND project_id = ?",
                [operation_id, project_id],
            )
            if operation is None:
                raise NotFoundError("剧本 Operation 不存在")
            run = await self._db.fetch_one(
                "SELECT binding_namespace, binding_aggregate_id, "
                "binding_command_id FROM ai_agent_runs WHERE id = ?",
                [run_id],
            )
            if run is None or (
                str(run.get("binding_namespace") or "") != "screenplay.operation"
                or str(run.get("binding_aggregate_id") or "") != project_id
                or str(run.get("binding_command_id") or "") != operation_id
            ):
                raise AppError("Agent Run 不属于该剧本 Operation", 409)
            current = str(operation.get("status") or "")
            if current in {"succeeded", "failed", "canceled", "paused"}:
                return _operation_view(operation)

            if normalized_status == "canceled":
                target = "paused"
                reason = "root_run_canceled"
                error_payload = None
                await self._checkpoint_operation_runtime(
                    operation_id=operation_id,
                    action="pause",
                )
                progress = {"phase": "paused", "reason": reason}
            else:
                target = "failed"
                reason = (
                    "candidate_not_produced"
                    if normalized_status == "done"
                    else f"root_run_{normalized_status}"
                )
                message = str(error or "").strip() or (
                    "Agent Run 已结束，但没有提交候选版本"
                    if normalized_status == "done"
                    else "Agent Run 未能完成剧本 Operation"
                )
                error_payload = {
                    "code": reason,
                    "message": message[:2_000],
                    "runId": run_id,
                }
                await self._checkpoint_operation_runtime(
                    operation_id=operation_id,
                    action="cancel",
                )
                progress = {"phase": "failed"}
            await self._db.execute(
                "UPDATE screenplay_operations SET status = ?, progress_json = ?, "
                "error_json = ?, update_time = CURRENT_TIMESTAMP WHERE id = ? "
                "AND status IN ('queued', 'running')",
                [
                    target,
                    _dump(progress),
                    _dump(error_payload) if error_payload is not None else None,
                    operation_id,
                ],
            )
            payload = {
                "operationId": operation_id,
                "projectId": project_id,
                "status": target,
                "reason": reason,
                "runId": run_id,
            }
            await self._record_operation_event(
                operation_id=operation_id,
                sequence=await self._next_operation_sequence(operation_id),
                event_type=f"screenplay.operation.{target}",
                payload=payload,
            )
            await self._record_outbox(
                aggregate_type="screenplayOperation",
                aggregate_id=operation_id,
                event_type=f"screenplay.operation.{target}",
                payload=payload,
            )
            updated = await self._db.fetch_one(
                "SELECT * FROM screenplay_operations WHERE id = ?",
                [operation_id],
            )
            return _operation_view(updated)

    async def control_operation(
        self,
        *,
        command_id: str,
        request_digest: str,
        operation_id: str,
        action: str,
    ) -> dict[str, Any]:
        """Checkpoint, requeue, or cancel an Operation and its runtime tree."""

        normalized_action = str(action or "").strip()
        if normalized_action not in {"pause", "resume", "cancel"}:
            raise ValueError("unsupported screenplay Operation action")
        command_type = f"{normalized_action}Operation"
        async with self._db.transaction(cancellation_linearizable=True):
            replay = await self.find_command_receipt(
                command_id=command_id,
                command_type=command_type,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            row = await self._db.fetch_one(
                "SELECT * FROM screenplay_operations WHERE id = ?",
                [operation_id],
            )
            if row is None:
                raise NotFoundError("剧本 Operation 不存在")
            current = str(row.get("status") or "")
            target = {
                "pause": "paused",
                "resume": "queued",
                "cancel": "canceled",
            }[normalized_action]
            no_op = (
                (normalized_action == "pause" and current == "paused")
                or (
                    normalized_action == "resume"
                    and current in {"queued", "running"}
                )
                or (normalized_action == "cancel" and current == "canceled")
            )
            if not no_op:
                allowed = {
                    "pause": {"queued", "running"},
                    "resume": {"paused"},
                    "cancel": {"queued", "running", "paused"},
                }[normalized_action]
                if current not in allowed:
                    raise AppError(
                        f"Operation 不能从 {current} 执行 {normalized_action}",
                        409,
                    )
                await self._checkpoint_operation_runtime(
                    operation_id=operation_id,
                    action=normalized_action,
                )
                progress = {
                    "phase": target,
                    **({"percent": 0} if target == "queued" else {}),
                }
                await self._db.execute(
                    "UPDATE screenplay_operations SET status = ?, "
                    "progress_json = ?, update_time = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    [target, _dump(progress), operation_id],
                )
                await self._record_operation_event(
                    operation_id=operation_id,
                    sequence=await self._next_operation_sequence(operation_id),
                    event_type=f"screenplay.operation.{target}",
                    payload={
                        "operationId": operation_id,
                        "projectId": str(row["project_id"]),
                        "status": target,
                    },
                )
                await self._record_outbox(
                    aggregate_type="screenplayOperation",
                    aggregate_id=operation_id,
                    event_type=f"screenplay.operation.{target}",
                    payload={
                        "operationId": operation_id,
                        "projectId": str(row["project_id"]),
                        "status": target,
                    },
                )
            updated = await self._db.fetch_one(
                "SELECT * FROM screenplay_operations WHERE id = ?",
                [operation_id],
            )
            response = {"operation": _operation_view(updated)}
            await self._record_command_receipt(
                command_id=command_id,
                command_type=command_type,
                project_id=str(row["project_id"]),
                request_digest=request_digest,
                result_ref=f"screenplay-operation://{operation_id}",
                response=response,
            )
            return response

    async def create_project(
        self,
        *,
        command_id: str,
        request_digest: str,
        title: str,
        source_kind: str,
        source_book_id: str | None,
        source_scope: Mapping[str, Any],
        source_snapshot: Mapping[str, Any],
        screenplay_format: str,
        approach: str,
        premise: str,
    ) -> str:
        async with self._db.transaction():
            existing_project_id = await self.find_create_project_receipt(
                command_id=command_id,
                request_digest=request_digest,
            )
            if existing_project_id is not None:
                return existing_project_id

            project_id = short_id8()
            stage = "orientation" if source_kind == "book" else "brief"
            await self._db.execute(
                "INSERT INTO screenplay_projects "
                "(id, title, source_kind, source_book_id, format, approach, "
                "premise, source_scope_json, active_stage, status, revision, "
                "source_snapshot_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'active', 1, ?)",
                [
                    project_id,
                    title,
                    source_kind,
                    source_book_id,
                    screenplay_format,
                    approach,
                    premise,
                    _dump(dict(source_scope)),
                    stage,
                    _dump(dict(source_snapshot)),
                ],
            )

            for role in applicable_deliverable_roles(source_kind):
                await self._db.execute(
                    "INSERT OR IGNORE INTO screenplay_deliverables "
                    "(id, project_id, role) VALUES (?, ?, ?)",
                    [_deliverable_id(project_id, role), project_id, role],
                )

            brief_deliverable_id = _deliverable_id(project_id, "creativeBrief")
            working_copy_id = f"spcopy_{uuid.uuid4().hex}"
            await self._db.execute(
                "INSERT INTO screenplay_working_copies "
                "(id, project_id, deliverable_id, content_manifest_json) "
                "VALUES (?, ?, ?, ?)",
                [
                    working_copy_id,
                    project_id,
                    brief_deliverable_id,
                    _dump({
                        "schemaVersion": 1,
                        "role": "creativeBrief",
                        "fields": {
                            "approach": approach,
                            "premise": premise,
                        },
                    }),
                ],
            )

            response = {"projectId": project_id}
            await self._db.execute(
                "INSERT INTO screenplay_command_receipts "
                "(command_id, command_type, project_id, request_digest, "
                "result_ref, response_json) VALUES (?, 'createProject', ?, ?, ?, ?)",
                [
                    command_id,
                    project_id,
                    request_digest,
                    f"screenplay-project://{project_id}",
                    _dump(response),
                ],
            )
            await self._db.execute(
                "INSERT INTO screenplay_outbox_events "
                "(id, aggregate_type, aggregate_id, event_type, payload_json) "
                "VALUES (?, 'screenplayProject', ?, 'screenplay.project.created', ?)",
                [
                    f"spout_{uuid.uuid4().hex}",
                    project_id,
                    _dump({
                        "projectId": project_id,
                        "projectRevision": 1,
                    }),
                ],
            )
            return project_id

    async def update_working_copy(
        self,
        *,
        working_copy_id: str,
        expected_revision: int,
        content: Mapping[str, Any],
    ) -> dict[str, Any]:
        async with self._db.transaction():
            row = await self._db.fetch_one(
                "SELECT w.*, p.status AS project_status "
                "FROM screenplay_working_copies AS w "
                "JOIN screenplay_projects AS p ON p.id = w.project_id "
                "WHERE w.id = ?",
                [working_copy_id],
            )
            if row is None:
                raise NotFoundError("剧本 Working Copy 不存在")
            if str(row.get("project_status") or "") == "archived":
                raise AppError("项目已归档，不能修改 Working Copy", 409)
            actual_revision = int(row.get("revision") or 1)
            if actual_revision != int(expected_revision):
                raise AppError(
                    "Working Copy 已被其他操作更新，请刷新后重试",
                    409,
                )
            next_revision = actual_revision + 1
            await self._db.execute(
                "UPDATE screenplay_working_copies SET content_manifest_json = ?, "
                "revision = ?, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ? AND revision = ?",
                [
                    _dump(dict(content)),
                    next_revision,
                    working_copy_id,
                    actual_revision,
                ],
            )
            await self._db.execute(
                "UPDATE screenplay_projects SET update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                [row["project_id"]],
            )
        updated = await self.get_working_copy(working_copy_id)
        if updated is None:
            raise RuntimeError("Working Copy 更新后无法读取")
        return updated

    async def create_working_copy_from_revision(
        self,
        *,
        command_id: str,
        request_digest: str,
        project_id: str,
        revision_id: str,
        expected_project_revision: int,
        expected_working_copy_revision: int | None,
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            replay = await self.find_command_receipt(
                command_id=command_id,
                command_type="createWorkingCopyFromRevision",
                request_digest=request_digest,
            )
            if replay is not None:
                return replay

            project = await self._db.fetch_one(
                "SELECT revision, status FROM screenplay_projects "
                "WHERE id = ? AND source_snapshot_json IS NOT NULL",
                [project_id],
            )
            if project is None:
                raise NotFoundError("剧本项目不存在")
            if str(project.get("status") or "") == "archived":
                raise AppError("项目已归档，不能创建 Working Copy", 409)
            if int(project.get("revision") or 1) != int(
                expected_project_revision
            ):
                raise AppError("项目已被其他操作更新，请刷新后重试", 409)

            revision = await self._db.fetch_one(
                "SELECT r.deliverable_id, d.role FROM screenplay_revisions AS r "
                "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
                "WHERE r.id = ? AND r.project_id = ?",
                [revision_id, project_id],
            )
            if revision is None:
                raise NotFoundError("剧本版本不存在")
            part_rows = await self._db.fetch_all(
                "SELECT part_type, part_key, position, payload_json, content_text "
                "FROM screenplay_revision_parts WHERE revision_id = ? "
                "ORDER BY part_type ASC, position ASC",
                [revision_id],
            )
            content = _working_copy_content_from_revision(
                role=str(revision["role"]),
                parts=part_rows,
            )
            existing = await self._db.fetch_one(
                "SELECT * FROM screenplay_working_copies "
                "WHERE project_id = ? AND deliverable_id = ?",
                [project_id, revision["deliverable_id"]],
            )
            if existing is None:
                if expected_working_copy_revision is not None:
                    raise AppError(
                        "Working Copy 已被其他操作更新，请刷新后重试",
                        409,
                    )
                working_copy_id = f"spcopy_{uuid.uuid4().hex}"
                await self._db.execute(
                    "INSERT INTO screenplay_working_copies "
                    "(id, project_id, deliverable_id, base_revision_id, "
                    "content_manifest_json) VALUES (?, ?, ?, ?, ?)",
                    [
                        working_copy_id,
                        project_id,
                        revision["deliverable_id"],
                        revision_id,
                        _dump(content),
                    ],
                )
            else:
                actual_copy_revision = int(existing.get("revision") or 1)
                if (
                    expected_working_copy_revision is None
                    or actual_copy_revision != int(expected_working_copy_revision)
                ):
                    raise AppError(
                        "Working Copy 已被其他操作更新，请刷新后重试",
                        409,
                    )
                working_copy_id = str(existing["id"])
                await self._db.execute(
                    "UPDATE screenplay_working_copies SET base_revision_id = ?, "
                    "content_manifest_json = ?, revision = ?, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ? AND revision = ?",
                    [
                        revision_id,
                        _dump(content),
                        actual_copy_revision + 1,
                        working_copy_id,
                        actual_copy_revision,
                    ],
                )

            row = await self._db.fetch_one(
                "SELECT w.*, d.role FROM screenplay_working_copies AS w "
                "JOIN screenplay_deliverables AS d ON d.id = w.deliverable_id "
                "WHERE w.id = ?",
                [working_copy_id],
            )
            if row is None:
                raise RuntimeError("Working Copy 创建后无法读取")
            response = _working_copy_view(row)
            await self._record_command_receipt(
                command_id=command_id,
                command_type="createWorkingCopyFromRevision",
                project_id=project_id,
                request_digest=request_digest,
                result_ref=f"screenplay-working-copy://{working_copy_id}",
                response=response,
            )
            await self._record_outbox(
                aggregate_type="screenplayWorkingCopy",
                aggregate_id=working_copy_id,
                event_type="screenplay.working-copy.rebased",
                payload={
                    "projectId": project_id,
                    "workingCopyId": working_copy_id,
                    "baseRevisionId": revision_id,
                    "role": str(revision["role"]),
                },
            )
            return response

    async def get_working_copy(
        self,
        working_copy_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT w.*, d.role FROM screenplay_working_copies AS w "
            "JOIN screenplay_deliverables AS d ON d.id = w.deliverable_id "
            "WHERE w.id = ?",
            [working_copy_id],
        )
        return _working_copy_view(row) if row is not None else None

    async def publish_working_copy(
        self,
        *,
        command_id: str,
        request_digest: str,
        working_copy_id: str,
        expected_project_revision: int,
        expected_working_copy_revision: int,
    ) -> tuple[str, str]:
        async with self._db.transaction():
            replay = await self.find_command_receipt(
                command_id=command_id,
                command_type="publishWorkingCopy",
                request_digest=request_digest,
            )
            if replay is not None:
                return str(replay["projectId"]), str(replay["revisionId"])

            row = await self._db.fetch_one(
                "SELECT w.*, d.role, p.revision AS project_revision, "
                "p.status AS project_status, p.source_kind "
                "FROM screenplay_working_copies AS w "
                "JOIN screenplay_deliverables AS d ON d.id = w.deliverable_id "
                "JOIN screenplay_projects AS p ON p.id = w.project_id "
                "WHERE w.id = ?",
                [working_copy_id],
            )
            if row is None:
                raise NotFoundError("剧本 Working Copy 不存在")
            if str(row.get("project_status") or "") == "archived":
                raise AppError("项目已归档，不能发布新版本", 409)
            if int(row.get("project_revision") or 1) != int(
                expected_project_revision
            ):
                raise AppError("项目已被其他操作更新，请刷新后重试", 409)
            if int(row.get("revision") or 1) != int(
                expected_working_copy_revision
            ):
                raise AppError("Working Copy 已被其他操作更新，请刷新后重试", 409)

            content = _object(row.get("content_manifest_json"))
            parts = _working_copy_candidate_parts(content)
            _validate_publishable_content(str(row["role"]), parts)
            content_digest = str(parts[0]["contentDigest"])
            latest = await self._db.fetch_one(
                "SELECT COALESCE(MAX(revision_no), 0) AS revision_no "
                "FROM screenplay_revisions WHERE deliverable_id = ?",
                [row["deliverable_id"]],
            )
            revision_no = int((latest or {}).get("revision_no") or 0) + 1
            revision_id = f"sprev_{uuid.uuid4().hex}"
            summary = {
                "role": str(row["role"]),
                "fieldCount": len(parts[0]["payload"]),
                "textLength": len(str(parts[0]["contentText"])),
                "partCount": len(parts),
            }
            await self._db.execute(
                "INSERT INTO screenplay_revisions "
                "(id, project_id, deliverable_id, revision_no, "
                "parent_revision_id, schema_version, content_digest, "
                "summary_json, created_by) VALUES (?, ?, ?, ?, ?, 1, ?, ?, 'user')",
                [
                    revision_id,
                    row["project_id"],
                    row["deliverable_id"],
                    revision_no,
                    row.get("base_revision_id"),
                    content_digest,
                    _dump(summary),
                ],
            )
            for part in parts:
                await self._db.execute(
                    "INSERT INTO screenplay_revision_parts "
                    "(revision_id, part_type, part_key, position, payload_json, "
                    "content_text, content_digest) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        revision_id,
                        part["partType"],
                        part["partKey"],
                        part["position"],
                        _dump(part["payload"]),
                        part["contentText"],
                        part["contentDigest"],
                    ],
                )
            await self._attach_current_inputs(
                revision_id=revision_id,
                project_id=str(row["project_id"]),
                role=str(row["role"]),
                source_kind=str(row.get("source_kind") or "original"),
            )
            if row.get("base_revision_id"):
                await self._db.execute(
                    "INSERT OR IGNORE INTO screenplay_revision_source_refs "
                    "(revision_id, source_type, source_id, source_revision, excerpt) "
                    "SELECT ?, source_type, source_id, source_revision, excerpt "
                    "FROM screenplay_revision_source_refs WHERE revision_id = ?",
                    [revision_id, row["base_revision_id"]],
                )

            next_copy_revision = int(row.get("revision") or 1) + 1
            await self._db.execute(
                "UPDATE screenplay_working_copies SET base_revision_id = ?, "
                "revision = ?, update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [revision_id, next_copy_revision, working_copy_id],
            )
            next_project_revision = int(row.get("project_revision") or 1) + 1
            await self._db.execute(
                "UPDATE screenplay_projects SET revision = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [next_project_revision, row["project_id"]],
            )
            response = {
                "projectId": str(row["project_id"]),
                "revisionId": revision_id,
                "projectRevision": next_project_revision,
            }
            await self._record_command_receipt(
                command_id=command_id,
                command_type="publishWorkingCopy",
                project_id=str(row["project_id"]),
                request_digest=request_digest,
                result_ref=f"screenplay-revision://{revision_id}",
                response=response,
            )
            await self._record_outbox(
                aggregate_type="screenplayRevision",
                aggregate_id=revision_id,
                event_type="screenplay.candidate.ready",
                payload={
                    "projectId": str(row["project_id"]),
                    "revisionId": revision_id,
                    "role": str(row["role"]),
                    "revisionNo": revision_no,
                },
            )
            return str(row["project_id"]), revision_id

    async def finalize_agent_candidate(
        self,
        *,
        finalizing_run_id: str,
        proposal_kind: str,
        target_role: str,
        title: str,
        content_json: Mapping[str, Any],
        content_text: str,
        derived_from_ids: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        """Atomically turn one Agent proposal into one v2 Candidate Revision.

        The caller is normally inside the Run-event transaction.  The nested
        transaction is therefore a SAVEPOINT: if either projection or event
        persistence fails, neither side becomes visible.  Runtime staging
        artifacts are intentionally left intact so a retry can replay the same
        proposal without regenerating creative content.
        """

        normalized_run_id = str(finalizing_run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("finalizing Run id is required")
        normalized_content = dict(content_json)
        normalized_text = str(content_text or "")
        proposal_content_digest = _content_digest(
            normalized_content,
            normalized_text,
        )
        proposal_digest = hashlib.sha256(_dump({
            "kind": proposal_kind,
            "role": target_role,
            "title": title,
            "contentDigest": proposal_content_digest,
            "derivedFromIds": list(derived_from_ids),
        }).encode("utf-8")).hexdigest()

        async with self._db.transaction():
            context = await self._agent_projection_context(
                finalizing_run_id=normalized_run_id,
                content_json=normalized_content,
            )
            if context is None:
                return None
            project = context["project"]
            if str(project.get("status") or "") == "archived":
                raise AppError("项目已归档，不能保存 Agent 候选版本", 409)

            project_id = str(project["id"])
            identity = _agent_proposal_identity(
                proposal_kind=proposal_kind,
                content_json=normalized_content,
                finalizing_run_id=normalized_run_id,
                content_digest=proposal_content_digest,
            )
            operation_id = str(context.get("operation_id") or "").strip()
            if not operation_id:
                raise AppError("剧本候选必须属于原生 Operation", 409)
            existing = await self._db.fetch_one(
                "SELECT * FROM screenplay_operations "
                "WHERE id = ? AND project_id = ?",
                [operation_id, project_id],
            )
            if existing is None:
                raise NotFoundError("剧本 Operation 不存在")
            intent = _object(existing.get("intent_json"))
            finalization = _object(intent.get("finalization"))
            recorded_digest = str(
                finalization.get("proposalDigest")
                or intent.get("proposalDigest")
                or ""
            )
            if (
                str(existing.get("target_role") or "") != target_role
                or (recorded_digest and recorded_digest != proposal_digest)
            ):
                raise AppError(
                    "同一个 Agent 产物身份对应了不同的候选内容",
                    409,
                )
            result_revision_id = str(
                existing.get("result_revision_id") or ""
            ).strip()
            if result_revision_id:
                revision = await self._db.fetch_one(
                    "SELECT r.*, d.role FROM screenplay_revisions AS r "
                    "JOIN screenplay_deliverables AS d "
                    "ON d.id = r.deliverable_id WHERE r.id = ?",
                    [result_revision_id],
                )
                if revision is None:
                    raise RuntimeError(
                        "succeeded screenplay Operation has no result Revision"
                    )
                await self._record_artifact_projection(
                    artifact_id=str(context.get("artifact_id") or ""),
                    revision_id=result_revision_id,
                    run_id=normalized_run_id,
                )
                return {
                    "projectId": project_id,
                    "operationId": str(existing["id"]),
                    "revisionId": result_revision_id,
                    "role": str(revision["role"]),
                    "revisionNo": int(revision.get("revision_no") or 1),
                    "replayed": True,
                }
            if str(existing.get("status") or "") != "running":
                raise AppError("Agent 候选版本的 Operation 不在运行中", 409)
            intent["finalization"] = {
                "proposalKind": proposal_kind,
                "title": title,
                "proposalDigest": proposal_digest,
                "runtimeIdentity": identity,
                "derivedFromIds": list(derived_from_ids),
                "artifactRef": str(
                    normalized_content.get("artifactRef") or ""
                ) or None,
                "longTaskId": str(
                    normalized_content.get("longTaskId") or ""
                ) or None,
            }
            await self._db.execute(
                "UPDATE screenplay_operations SET intent_json = ?, "
                "progress_json = ?, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'running'",
                [
                    _dump(intent),
                    _dump({"phase": "finalizing"}),
                    operation_id,
                ],
            )

            deliverable = await self._db.fetch_one(
                "SELECT id FROM screenplay_deliverables "
                "WHERE project_id = ? AND role = ?",
                [project_id, target_role],
            )
            if deliverable is None:
                raise AppError("Agent 产物不属于该项目的交付物流程", 409)
            base_heads = _object(existing.get("base_heads_json"))
            parent_revision_id = base_heads.get(target_role)

            latest = await self._db.fetch_one(
                "SELECT COALESCE(MAX(revision_no), 0) AS revision_no "
                "FROM screenplay_revisions WHERE deliverable_id = ?",
                [deliverable["id"]],
            )
            revision_no = int((latest or {}).get("revision_no") or 0) + 1
            revision_id = f"sprev_{uuid.uuid4().hex}"
            parts = await self._materialize_agent_candidate_parts(
                project_id=project_id,
                target_role=target_role,
                parent_revision_id=parent_revision_id,
                content_json=normalized_content,
                content_text=normalized_text,
            )
            document_part = parts[0]
            content_digest = str(document_part["contentDigest"])
            summary = {
                "role": target_role,
                "proposalKind": proposal_kind,
                "title": title,
                "textLength": len(str(document_part["contentText"])),
                "partCount": len(parts),
                "derivedFromIds": list(derived_from_ids),
            }
            await self._db.execute(
                "INSERT INTO screenplay_revisions "
                "(id, project_id, deliverable_id, revision_no, "
                "parent_revision_id, schema_version, content_digest, "
                "summary_json, operation_id, root_run_id, finalizing_run_id, "
                "created_by) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 'agent')",
                [
                    revision_id,
                    project_id,
                    deliverable["id"],
                    revision_no,
                    parent_revision_id,
                    content_digest,
                    _dump(summary),
                    operation_id,
                    context["root_run_id"],
                    normalized_run_id,
                ],
            )
            for part in parts:
                await self._db.execute(
                    "INSERT INTO screenplay_revision_parts "
                    "(revision_id, part_type, part_key, position, "
                    "payload_json, content_text, content_digest) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        revision_id,
                        part["partType"],
                        part["partKey"],
                        part["position"],
                        _dump(part["payload"]),
                        part["contentText"],
                        part["contentDigest"],
                    ],
                )
            await self._attach_current_inputs(
                revision_id=revision_id,
                project_id=project_id,
                role=target_role,
                source_kind=str(project.get("source_kind") or "original"),
                base_heads=base_heads,
            )
            await self._attach_agent_source_refs(
                revision_id=revision_id,
                project_id=project_id,
                run_ids=context["source_run_ids"],
            )
            await self._record_artifact_projection(
                artifact_id=str(context.get("artifact_id") or ""),
                revision_id=revision_id,
                run_id=normalized_run_id,
            )

            await self._db.execute(
                "UPDATE screenplay_operations SET status = 'succeeded', "
                "progress_json = ?, result_revision_id = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [
                    _dump({"phase": "completed", "percent": 100}),
                    revision_id,
                    operation_id,
                ],
            )
            candidate_payload = {
                "projectId": project_id,
                "operationId": operation_id,
                "revisionId": revision_id,
                "role": target_role,
                "revisionNo": revision_no,
            }
            await self._record_operation_event(
                operation_id=operation_id,
                sequence=await self._next_operation_sequence(operation_id),
                event_type="screenplay.candidate.ready",
                payload=candidate_payload,
            )
            await self._record_operation_event(
                operation_id=operation_id,
                sequence=await self._next_operation_sequence(operation_id),
                event_type="screenplay.operation.succeeded",
                payload=candidate_payload,
            )
            await self._record_outbox(
                aggregate_type="screenplayRevision",
                aggregate_id=revision_id,
                event_type="screenplay.candidate.ready",
                payload=candidate_payload,
            )
            return {**candidate_payload, "replayed": False}

    async def _record_artifact_projection(
        self,
        *,
        artifact_id: str,
        revision_id: str,
        run_id: str,
    ) -> None:
        normalized_artifact_id = str(artifact_id or "").strip()
        if not normalized_artifact_id:
            return
        result_ref = f"screenplay-revision://{revision_id}"
        await self._db.execute(
            "INSERT OR IGNORE INTO ai_agent_artifact_projections "
            "(artifact_id, projector_namespace, result_ref, projected_by_run_id) "
            "VALUES (?, 'purrtypos.screenplay.revision', ?, ?)",
            [normalized_artifact_id, result_ref, run_id],
        )
        stored = await self._db.fetch_one(
            "SELECT result_ref FROM ai_agent_artifact_projections "
            "WHERE artifact_id = ? "
            "AND projector_namespace = 'purrtypos.screenplay.revision'",
            [normalized_artifact_id],
        )
        if stored is None or str(stored.get("result_ref") or "") != result_ref:
            raise AppError("同一个 Agent Artifact 不能投影为多个剧本版本", 409)

    async def _materialize_agent_candidate_parts(
        self,
        *,
        project_id: str,
        target_role: str,
        parent_revision_id: str | None,
        content_json: Mapping[str, Any],
        content_text: str,
    ) -> list[dict[str, Any]]:
        """Build one immutable snapshot, reusing unchanged draft episodes."""

        current_parts = _agent_candidate_parts(
            content_json=content_json,
            content_text=content_text,
        )
        if target_role == "review":
            return await self._materialize_review_candidate_parts(
                project_id=project_id,
                current_parts=current_parts,
                content_json=content_json,
            )
        if target_role != "screenplayDraft" or not parent_revision_id:
            return current_parts
        parent_rows = await self._db.fetch_all(
            "SELECT part_key, payload_json, content_text "
            "FROM screenplay_revision_parts WHERE revision_id = ? "
            "AND part_type = 'episode' ORDER BY position ASC",
            [parent_revision_id],
        )
        if not parent_rows:
            return current_parts

        inherited = {
            str(row["part_key"]): {
                "payload": _object(row.get("payload_json")),
                "contentText": str(row.get("content_text") or ""),
            }
            for row in parent_rows
        }
        updates = {
            str(part["partKey"]): part
            for part in current_parts
            if part["partType"] == "episode"
        }
        episode_parts: list[dict[str, Any]] = []
        for position, part_key in enumerate(
            sorted(set(inherited) | set(updates), key=_episode_part_sort_key),
            start=1,
        ):
            previous = inherited.get(part_key)
            update = updates.get(part_key)
            if previous is not None and update is not None:
                payload = _merge_episode_payload(
                    previous["payload"],
                    update["payload"],
                )
                episode_text = str(payload.get("contentText") or "")
            elif update is not None:
                payload = dict(update["payload"])
                episode_text = str(update["contentText"])
            else:
                payload = dict(previous["payload"])
                episode_text = str(
                    payload.get("contentText")
                    or previous["contentText"]
                )
            episode_parts.append(_candidate_part(
                part_type="episode",
                part_key=part_key,
                position=position,
                payload=payload,
                content_text=episode_text,
            ))

        snapshot_content = dict(content_json)
        snapshot_content["episodeDrafts"] = [
            dict(part["payload"]) for part in episode_parts
        ]
        completed_ids = list(dict.fromkeys(
            str(scene_id).strip()
            for part in episode_parts
            for scene_id in part["payload"].get("sceneIds", [])
            if str(scene_id).strip()
        ))
        if completed_ids:
            snapshot_content["completedSceneIds"] = completed_ids
        snapshot_text = "\n\n".join(
            str(part["contentText"]).strip()
            for part in episode_parts
            if str(part["contentText"]).strip()
        )
        document_part = _candidate_part(
            part_type="document",
            part_key="main",
            position=0,
            payload=snapshot_content,
            content_text=snapshot_text,
        )
        return [document_part, *episode_parts]

    async def _materialize_review_candidate_parts(
        self,
        *,
        project_id: str,
        current_parts: list[dict[str, Any]],
        content_json: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Project a whole-review report into stable per-episode Parts."""

        rows = await self._db.fetch_all(
            "SELECT d.role, p.part_key, p.payload_json "
            "FROM screenplay_project_heads AS h "
            "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
            "JOIN screenplay_revision_parts AS p "
            "ON p.revision_id = h.revision_id AND p.part_type = 'episode' "
            "WHERE h.project_id = ? AND d.role IN ('sceneList', 'screenplayDraft')",
            [project_id],
        )
        episode_keys = {
            str(row["part_key"])
            for row in rows
            if str(row.get("role") or "") == "screenplayDraft"
        }
        scene_episode: dict[str, str] = {}
        for row in rows:
            if str(row.get("role") or "") != "sceneList":
                continue
            episode_key = str(row["part_key"])
            payload = _object(row.get("payload_json"))
            for scene in payload.get("scenes", []):
                if not isinstance(scene, Mapping):
                    continue
                scene_id = str(scene.get("id") or "").strip()
                if scene_id:
                    scene_episode[scene_id] = episode_key
                    episode_keys.add(episode_key)

        issues = [
            dict(item)
            for item in content_json.get("issues", [])
            if isinstance(item, Mapping)
        ]
        issue_episode: dict[str, set[str]] = {}
        for issue in issues:
            issue_id = str(issue.get("id") or "").strip()
            affected = {
                scene_episode[scene_id]
                for scene_id in (
                    str(value).strip()
                    for value in issue.get("sceneIds", [])
                )
                if scene_id in scene_episode
            }
            if issue_id:
                issue_episode[issue_id] = affected
            episode_keys.update(affected)

        previous_review_id = str(
            content_json.get("verificationOfReviewId") or ""
        ).strip()
        if previous_review_id:
            previous_parts = await self._db.fetch_all(
                "SELECT part_key, payload_json FROM screenplay_revision_parts "
                "WHERE revision_id = ? AND part_type = 'episode'",
                [previous_review_id],
            )
            for row in previous_parts:
                episode_key = str(row["part_key"])
                payload = _object(row.get("payload_json"))
                for issue in payload.get("issues", []):
                    if not isinstance(issue, Mapping):
                        continue
                    issue_id = str(issue.get("id") or "").strip()
                    if issue_id:
                        issue_episode.setdefault(issue_id, set()).add(
                            episode_key
                        )

        verification_results = [
            dict(item)
            for item in content_json.get("verificationResults", [])
            if isinstance(item, Mapping)
        ]
        grouped = {
            episode_key: {"issues": [], "verificationResults": []}
            for episode_key in episode_keys
        }
        for issue in issues:
            for episode_key in issue_episode.get(
                str(issue.get("id") or "").strip(),
                set(),
            ):
                grouped.setdefault(
                    episode_key,
                    {"issues": [], "verificationResults": []},
                )["issues"].append(issue)
        for result in verification_results:
            for episode_key in issue_episode.get(
                str(result.get("issueId") or "").strip(),
                set(),
            ):
                grouped.setdefault(
                    episode_key,
                    {"issues": [], "verificationResults": []},
                )["verificationResults"].append(result)

        episode_parts: list[dict[str, Any]] = []
        for position, episode_key in enumerate(
            sorted(grouped, key=_episode_part_sort_key),
            start=1,
        ):
            values = grouped[episode_key]
            payload = {
                "episodeNumber": _positive_int(episode_key) or position,
                "reviewedDraftId": str(
                    content_json.get("reviewedDraftId") or ""
                ),
                "verdict": str(content_json.get("verdict") or ""),
                "issues": values["issues"],
                "verificationResults": values["verificationResults"],
            }
            episode_parts.append(_candidate_part(
                part_type="episode",
                part_key=episode_key,
                position=position,
                payload=payload,
                content_text="",
            ))
        return [current_parts[0], *episode_parts]

    async def _agent_projection_context(
        self,
        *,
        finalizing_run_id: str,
        content_json: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        run = await self._db.fetch_one(
            "SELECT r.id, r.session_id, r.parent_run_id, r.root_run_id, "
            "r.binding_namespace, "
            "r.binding_aggregate_id, r.binding_command_id, "
            "root.binding_namespace AS root_binding_namespace, "
            "root.binding_aggregate_id AS root_binding_aggregate_id, "
            "root.binding_command_id AS root_binding_command_id, "
            "parent.binding_namespace AS parent_binding_namespace, "
            "parent.binding_aggregate_id AS parent_binding_aggregate_id, "
            "parent.binding_command_id AS parent_binding_command_id, "
            "s.screenplay_project_id AS run_project_id, "
            "root.session_id AS root_session_id, "
            "root_session.screenplay_project_id AS root_project_id, "
            "parent.session_id AS parent_session_id, "
            "parent_session.screenplay_project_id AS parent_project_id "
            "FROM ai_agent_runs AS r "
            "LEFT JOIN ai_sessions AS s ON s.id = r.session_id "
            "LEFT JOIN ai_agent_runs AS root ON root.id = r.root_run_id "
            "LEFT JOIN ai_sessions AS root_session "
            "ON root_session.id = root.session_id "
            "LEFT JOIN ai_agent_runs AS parent ON parent.id = r.parent_run_id "
            "LEFT JOIN ai_sessions AS parent_session "
            "ON parent_session.id = parent.session_id "
            "WHERE r.id = ?",
            [finalizing_run_id],
        )
        project_ids: set[str] = set()
        operation_ids: set[str] = set()
        source_run_ids: set[str] = {finalizing_run_id}
        root_run_id = finalizing_run_id
        if run is not None:
            root_run_id = str(
                run.get("root_run_id") or finalizing_run_id
            ).strip()
            source_run_ids.update(
                str(value).strip()
                for value in (
                    run.get("parent_run_id"),
                    run.get("root_run_id"),
                )
                if str(value or "").strip()
            )
            project_ids.update(
                str(value).strip()
                for value in (
                    run.get("run_project_id"),
                    run.get("root_project_id"),
                    run.get("parent_project_id"),
                )
                if str(value or "").strip()
            )
            for prefix in ("", "root_", "parent_"):
                namespace = str(
                    run.get(f"{prefix}binding_namespace") or ""
                ).strip()
                if namespace != "screenplay.operation":
                    continue
                project_ids.add(str(
                    run.get(f"{prefix}binding_aggregate_id") or ""
                ).strip())
                operation_ids.add(str(
                    run.get(f"{prefix}binding_command_id") or ""
                ).strip())

        artifact_ref = str(content_json.get("artifactRef") or "").strip()
        if artifact_ref:
            artifact_id = artifact_ref.rsplit("/", 1)[-1].strip()
            artifact = await self._db.fetch_one(
                "SELECT owner_id, run_id, created_by_run_id "
                "FROM ai_agent_artifacts WHERE id = ? AND resource_ref = ? "
                "AND namespace = 'purrtypos.screenplay'",
                [artifact_id, artifact_ref],
            )
            if artifact is None:
                raise AppError("Agent 提案引用的 Artifact 不存在", 409)
            project_ids.add(str(artifact["owner_id"]))
            source_run_ids.update(
                str(value).strip()
                for value in (
                    artifact.get("run_id"),
                    artifact.get("created_by_run_id"),
                )
                if str(value or "").strip()
            )

        long_task_id = str(content_json.get("longTaskId") or "").strip()
        if long_task_id:
            long_task = await self._db.fetch_one(
                "SELECT owner_id, created_by_run_id "
                "FROM ai_agent_long_tasks "
                "WHERE id = ? AND namespace = 'purrtypos.screenplay'",
                [long_task_id],
            )
            if long_task is None:
                raise AppError("Agent 提案引用的 Long Task 不存在", 409)
            project_ids.add(str(long_task["owner_id"]))
            source_run_ids.add(str(long_task["created_by_run_id"]))
            unit_runs = await self._db.fetch_all(
                "SELECT run_id FROM ai_agent_long_task_units "
                "WHERE task_id = ? AND run_id IS NOT NULL",
                [long_task_id],
            )
            source_run_ids.update(
                str(row.get("run_id") or "").strip()
                for row in unit_runs
                if str(row.get("run_id") or "").strip()
            )

        project_ids.discard("")
        if not project_ids:
            return None
        if len(project_ids) != 1:
            raise AppError("Agent 提案的项目归属不一致", 409)
        operation_ids.discard("")
        if len(operation_ids) > 1:
            raise AppError("Agent 提案的 Operation 归属不一致", 409)
        project_id = next(iter(project_ids))
        project = await self._db.fetch_one(
            "SELECT * FROM screenplay_projects "
            "WHERE id = ? AND source_snapshot_json IS NOT NULL",
            [project_id],
        )
        if project is None:
            raise NotFoundError("剧本项目不存在")
        return {
            "project": project,
            "root_run_id": root_run_id,
            "source_run_ids": tuple(sorted(source_run_ids)),
            "artifact_id": (
                artifact_ref.rsplit("/", 1)[-1].strip()
                if artifact_ref
                else None
            ),
            "long_task_id": long_task_id or None,
            "operation_id": next(iter(operation_ids), None),
        }

    async def _attach_agent_source_refs(
        self,
        *,
        revision_id: str,
        project_id: str,
        run_ids: tuple[str, ...],
    ) -> None:
        normalized = tuple(dict.fromkeys(
            str(value).strip() for value in run_ids if str(value).strip()
        ))
        if not normalized:
            return
        placeholders = ",".join("?" for _ in normalized)
        rows = await self._db.fetch_all(
            "SELECT r.source_type, r.source_id, r.source_revision, r.excerpt "
            "FROM screenplay_source_receipts AS r "
            "JOIN (SELECT source_type, source_id, MAX(id) AS latest_id "
            "FROM screenplay_source_receipts WHERE project_id = ? "
            f"AND agent_run_id IN ({placeholders}) "
            "GROUP BY source_type, source_id) AS latest "
            "ON latest.latest_id = r.id ORDER BY r.id ASC",
            [project_id, *normalized],
        )
        for row in rows:
            await self._db.execute(
                "INSERT INTO screenplay_revision_source_refs "
                "(revision_id, source_type, source_id, source_revision, excerpt) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    revision_id,
                    row["source_type"],
                    row["source_id"],
                    row["source_revision"],
                    str(row.get("excerpt") or ""),
                ],
            )

    async def _record_operation_event(
        self,
        *,
        operation_id: str,
        sequence: int,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        await self._db.execute(
            "INSERT INTO screenplay_operation_events "
            "(operation_id, sequence, event_type, payload_json) "
            "VALUES (?, ?, ?, ?)",
            [operation_id, sequence, event_type, _dump(dict(payload))],
        )

    async def _next_operation_sequence(self, operation_id: str) -> int:
        row = await self._db.fetch_one(
            "SELECT COALESCE(MAX(sequence), 0) AS sequence "
            "FROM screenplay_operation_events WHERE operation_id = ?",
            [operation_id],
        )
        return int((row or {}).get("sequence") or 0) + 1

    async def _checkpoint_operation_runtime(
        self,
        *,
        operation_id: str,
        action: str,
    ) -> None:
        if action == "resume":
            # A fresh root Run claims the queued Operation. Its existing paused
            # Long Task is resumed by ScreenplayLongTaskDispatcher after the new
            # Run is linked to the same Work Item.
            return
        runtime = await self._operation_runtime_ids(operation_id)
        run_ids = runtime["runs"]
        task_ids = runtime["long_tasks"]
        artifact_ids = runtime["artifacts"]
        work_item_ids = runtime["work_items"]
        requested_at_ms = int(time.time() * 1_000)
        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            await self._db.execute(
                "UPDATE ai_agent_runs SET cancel_requested_at_ms = COALESCE("
                "cancel_requested_at_ms, ?), update_time = CURRENT_TIMESTAMP "
                f"WHERE id IN ({placeholders}) AND status = 'running'",
                [requested_at_ms, *run_ids],
            )
        if action == "pause":
            if task_ids:
                placeholders = ",".join("?" for _ in task_ids)
                await self._db.execute(
                    "UPDATE ai_agent_long_task_units SET status = 'pending', "
                    "max_attempts = max_attempts + 1, worker_id = NULL, "
                    "lease_expires_at_ms = NULL, error_code = 'operation_paused', "
                    "update_time = CURRENT_TIMESTAMP "
                    f"WHERE task_id IN ({placeholders}) "
                    "AND status IN ('claimed', 'running')",
                    list(task_ids),
                )
                await self._db.execute(
                    "UPDATE ai_agent_long_tasks SET status = 'paused', "
                    "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
                    f"WHERE id IN ({placeholders}) "
                    "AND status IN ('pending', 'running')",
                    list(task_ids),
                )
            return
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'canceled', "
                "worker_id = NULL, lease_expires_at_ms = NULL, "
                f"update_time = CURRENT_TIMESTAMP WHERE task_id IN ({placeholders}) "
                "AND status IN ('pending', 'claimed', 'running')",
                list(task_ids),
            )
            await self._db.execute(
                "UPDATE ai_agent_long_tasks SET status = 'canceled', "
                "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
                f"WHERE id IN ({placeholders}) "
                "AND status IN ('pending', 'running', 'paused')",
                list(task_ids),
            )
        if artifact_ids:
            placeholders = ",".join("?" for _ in artifact_ids)
            await self._db.execute(
                "UPDATE ai_agent_artifacts SET status = 'aborted', "
                "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
                f"WHERE id IN ({placeholders}) AND status = 'open'",
                list(artifact_ids),
            )
        if work_item_ids:
            placeholders = ",".join("?" for _ in work_item_ids)
            await self._db.execute(
                "UPDATE ai_agent_work_items SET status = 'canceled', "
                "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
                f"WHERE id IN ({placeholders}) AND status = 'open'",
                list(work_item_ids),
            )

    async def _operation_runtime_ids(
        self,
        operation_id: str,
    ) -> dict[str, tuple[str, ...]]:
        roots = await self._db.fetch_all(
            "SELECT r.id FROM ai_agent_runs AS r "
            "JOIN screenplay_operations AS o ON o.id = ? "
            "WHERE r.binding_namespace = 'screenplay.operation' "
            "AND r.binding_aggregate_id = o.project_id "
            "AND r.binding_command_id = o.id",
            [operation_id],
        )
        root_ids = tuple(str(row["id"]) for row in roots)
        if not root_ids:
            return {
                "runs": (),
                "work_items": (),
                "long_tasks": (),
                "artifacts": (),
            }
        root_placeholders = ",".join("?" for _ in root_ids)
        run_rows = await self._db.fetch_all(
            "SELECT id FROM ai_agent_runs "
            f"WHERE id IN ({root_placeholders}) "
            f"OR root_run_id IN ({root_placeholders})",
            [*root_ids, *root_ids],
        )
        run_ids = tuple(dict.fromkeys(str(row["id"]) for row in run_rows))
        run_placeholders = ",".join("?" for _ in run_ids)
        work_rows = await self._db.fetch_all(
            "SELECT DISTINCT w.id FROM ai_agent_work_items AS w "
            "LEFT JOIN ai_agent_work_item_runs AS wr ON wr.work_item_id = w.id "
            f"WHERE w.created_by_run_id IN ({run_placeholders}) "
            f"OR wr.run_id IN ({run_placeholders})",
            [*run_ids, *run_ids],
        )
        work_item_ids = tuple(str(row["id"]) for row in work_rows)
        task_clauses = [f"created_by_run_id IN ({run_placeholders})"]
        task_params: list[Any] = list(run_ids)
        if work_item_ids:
            work_placeholders = ",".join("?" for _ in work_item_ids)
            task_clauses.append(f"work_item_id IN ({work_placeholders})")
            task_params.extend(work_item_ids)
        task_rows = await self._db.fetch_all(
            "SELECT id FROM ai_agent_long_tasks WHERE " + " OR ".join(task_clauses),
            task_params,
        )
        artifact_clauses = [
            f"run_id IN ({run_placeholders})",
            f"created_by_run_id IN ({run_placeholders})",
        ]
        artifact_params: list[Any] = [*run_ids, *run_ids]
        if work_item_ids:
            work_placeholders = ",".join("?" for _ in work_item_ids)
            artifact_clauses.append(f"work_item_id IN ({work_placeholders})")
            artifact_params.extend(work_item_ids)
        artifact_rows = await self._db.fetch_all(
            "SELECT id FROM ai_agent_artifacts WHERE "
            + " OR ".join(artifact_clauses),
            artifact_params,
        )
        return {
            "runs": run_ids,
            "work_items": work_item_ids,
            "long_tasks": tuple(str(row["id"]) for row in task_rows),
            "artifacts": tuple(str(row["id"]) for row in artifact_rows),
        }

    async def recover_operations_after_restart(self) -> tuple[str, ...]:
        """Checkpoint running Operations after their process-owned Runs stop."""

        async with self._db.transaction(cancellation_linearizable=True):
            rows = await self._db.fetch_all(
                "SELECT id, project_id FROM screenplay_operations "
                "WHERE status = 'running' ORDER BY create_time ASC"
            )
            operation_ids: list[str] = []
            for row in rows:
                operation_id = str(row["id"])
                await self._checkpoint_operation_runtime(
                    operation_id=operation_id,
                    action="pause",
                )
                await self._db.execute(
                    "UPDATE screenplay_operations SET status = 'paused', "
                    "progress_json = ?, update_time = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND status = 'running'",
                    [_dump({"phase": "paused", "reason": "restart"}), operation_id],
                )
                await self._record_operation_event(
                    operation_id=operation_id,
                    sequence=await self._next_operation_sequence(operation_id),
                    event_type="screenplay.operation.paused",
                    payload={
                        "operationId": operation_id,
                        "projectId": str(row["project_id"]),
                        "status": "paused",
                        "reason": "execution_recovery_after_restart",
                    },
                )
                operation_ids.append(operation_id)
            return tuple(operation_ids)

    async def settle_terminal_root_operations(self) -> tuple[str, ...]:
        """Reconcile Operations whose latest owning root Run is terminal.

        The ordinary live stream settles an Operation when it observes the
        root ``AgentRunResult``.  A lost execution lease is terminalized by a
        separate process monitor, so that result cannot travel through the
        original stream.  Reconcile the durable aggregate from persisted Run
        state and checkpoint its long task instead of leaving a zombie
        ``running`` Operation behind.
        """

        rows = await self._db.fetch_all(
            "SELECT o.id AS operation_id, o.project_id, r.id AS run_id, "
            "r.status AS run_status FROM screenplay_operations AS o "
            "JOIN ai_agent_runs AS r "
            "ON r.binding_namespace = 'screenplay.operation' "
            "AND r.binding_aggregate_id = o.project_id "
            "AND r.binding_command_id = o.id "
            "WHERE o.status IN ('queued', 'running') "
            "AND r.parent_run_id IS NULL "
            "AND r.status IN ('done', 'blocked', 'failed', 'canceled') "
            "AND r.rowid = ("
            "SELECT latest.rowid FROM ai_agent_runs AS latest "
            "WHERE latest.binding_namespace = 'screenplay.operation' "
            "AND latest.binding_aggregate_id = o.project_id "
            "AND latest.binding_command_id = o.id "
            "AND latest.parent_run_id IS NULL "
            "ORDER BY latest.create_time DESC, latest.rowid DESC LIMIT 1"
            ") ORDER BY o.create_time ASC, o.id ASC"
        )
        settled: list[str] = []
        for row in rows:
            operation_id = str(row.get("operation_id") or "").strip()
            project_id = str(row.get("project_id") or "").strip()
            run_id = str(row.get("run_id") or "").strip()
            run_status = str(row.get("run_status") or "").strip()
            if not operation_id or not project_id or not run_id:
                continue
            operation = await self.settle_operation_from_root_run(
                operation_id=operation_id,
                project_id=project_id,
                run_id=run_id,
                run_status=run_status,
                error=None,
            )
            if str(operation.get("status") or "") in {
                "succeeded",
                "failed",
                "canceled",
                "paused",
            }:
                settled.append(operation_id)
        return tuple(settled)

    async def accept_revision(
        self,
        *,
        command_id: str,
        request_digest: str,
        project_id: str,
        revision_id: str,
        expected_project_revision: int,
        confirm_invalidation: bool,
        actor: str = "user",
    ) -> dict[str, Any]:
        async with self._db.transaction():
            replay = await self.find_command_receipt(
                command_id=command_id,
                command_type="acceptRevision",
                request_digest=request_digest,
            )
            if replay is not None:
                return replay

            project = await self._db.fetch_one(
                "SELECT * FROM screenplay_projects WHERE id = ?",
                [project_id],
            )
            if project is None:
                raise NotFoundError("剧本项目不存在")
            if str(project.get("status") or "") == "archived":
                raise AppError("项目已归档，不能接受新版本", 409)
            actual_project_revision = int(project.get("revision") or 1)
            if actual_project_revision != int(expected_project_revision):
                raise AppError("项目已被其他操作更新，请刷新后重试", 409)

            revision = await self._db.fetch_one(
                "SELECT r.*, d.role FROM screenplay_revisions AS r "
                "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
                "WHERE r.id = ? AND r.project_id = ?",
                [revision_id, project_id],
            )
            if revision is None:
                raise NotFoundError("剧本版本不存在")
            role = str(revision["role"])
            heads = await self._head_rows(project_id)
            await self._validate_revision_inputs(
                revision_id=revision_id,
                role=role,
                source_kind=str(project.get("source_kind") or "original"),
                heads=heads,
            )

            role_order = [
                item
                for item in applicable_deliverable_roles(
                    str(project.get("source_kind") or "original")
                )
            ]
            current_index = role_order.index(role)
            downstream_roles = set(role_order[current_index + 1:])
            invalidated = [
                {
                    "role": str(row["role"]),
                    "revisionId": str(row["revision_id"]),
                }
                for row in heads
                if str(row["role"]) in downstream_roles
            ]
            if invalidated and not confirm_invalidation:
                raise AppError(
                    "接受该版本会使下游版本失效，请确认后重试",
                    409,
                )

            previous = next(
                (row for row in heads if str(row["role"]) == role),
                None,
            )
            previous_revision_id = (
                str(previous["revision_id"]) if previous is not None else None
            )
            if downstream_roles:
                placeholders = ",".join("?" for _ in downstream_roles)
                await self._db.execute(
                    "DELETE FROM screenplay_project_heads WHERE project_id = ? "
                    "AND deliverable_id IN (SELECT id FROM screenplay_deliverables "
                    f"WHERE project_id = ? AND role IN ({placeholders}))",
                    [project_id, project_id, *sorted(downstream_roles)],
                )
            await self._db.execute(
                "INSERT INTO screenplay_project_heads "
                "(project_id, deliverable_id, revision_id) VALUES (?, ?, ?) "
                "ON CONFLICT(project_id, deliverable_id) DO UPDATE SET "
                "revision_id = excluded.revision_id, "
                "update_time = CURRENT_TIMESTAMP",
                [project_id, revision["deliverable_id"], revision_id],
            )

            acceptance_id = f"spaccept_{uuid.uuid4().hex}"
            await self._db.execute(
                "INSERT INTO screenplay_acceptance_events "
                "(id, project_id, command_id, deliverable_id, revision_id, "
                "previous_revision_id, invalidated_heads_json, actor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    acceptance_id,
                    project_id,
                    command_id,
                    revision["deliverable_id"],
                    revision_id,
                    previous_revision_id,
                    _dump(invalidated),
                    actor,
                ],
            )
            current_heads = await self._head_rows(project_id)
            head_contents = _head_content_map(current_heads)
            stage = derive_stage(
                source_kind=str(project.get("source_kind") or "original"),
                head_roles=(str(row["role"]) for row in current_heads),
                head_contents=head_contents,
            )
            next_project_revision = actual_project_revision + 1
            await self._db.execute(
                "UPDATE screenplay_projects SET revision = ?, active_stage = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [next_project_revision, stage, project_id],
            )
            response = {
                "projectId": project_id,
                "acceptedRevisionId": revision_id,
                "acceptanceEventId": acceptance_id,
                "previousHeadRevisionId": previous_revision_id,
                "invalidatedHeads": invalidated,
                "projectRevision": next_project_revision,
                "workflow": {
                    "stage": stage,
                    "nextActions": next_actions(
                        stage,
                        head_contents=head_contents,
                    ),
                },
            }
            await self._record_command_receipt(
                command_id=command_id,
                command_type="acceptRevision",
                project_id=project_id,
                request_digest=request_digest,
                result_ref=f"screenplay-acceptance://{acceptance_id}",
                response=response,
            )
            await self._record_outbox(
                aggregate_type="screenplayProject",
                aggregate_id=project_id,
                event_type="screenplay.revision.accepted",
                payload=response,
            )
            return response

    async def _attach_current_inputs(
        self,
        *,
        revision_id: str,
        project_id: str,
        role: str,
        source_kind: str,
        base_heads: Mapping[str, str] | None = None,
    ) -> None:
        prerequisite = _prerequisite_role(role, source_kind)
        if prerequisite is None:
            return
        if base_heads is not None:
            input_revision_id = str(base_heads.get(prerequisite) or "").strip()
            if not input_revision_id:
                return
        else:
            head = await self._db.fetch_one(
                "SELECT h.revision_id FROM screenplay_project_heads AS h "
                "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
                "WHERE h.project_id = ? AND d.role = ?",
                [project_id, prerequisite],
            )
            if head is None:
                return
            input_revision_id = str(head["revision_id"])
        await self._db.execute(
            "INSERT INTO screenplay_revision_inputs "
            "(revision_id, input_role, input_revision_id) VALUES (?, ?, ?)",
            [revision_id, prerequisite, input_revision_id],
        )

    async def _validate_revision_inputs(
        self,
        *,
        revision_id: str,
        role: str,
        source_kind: str,
        heads: list[dict[str, Any]],
    ) -> None:
        prerequisite = _prerequisite_role(role, source_kind)
        if prerequisite is None:
            return
        current = next(
            (row for row in heads if str(row["role"]) == prerequisite),
            None,
        )
        if current is None:
            raise AppError("接受该版本前必须先接受上游版本", 409)
        input_row = await self._db.fetch_one(
            "SELECT input_revision_id FROM screenplay_revision_inputs "
            "WHERE revision_id = ? AND input_role = ?",
            [revision_id, prerequisite],
        )
        if (
            input_row is None
            or str(input_row.get("input_revision_id") or "")
            != str(current["revision_id"])
        ):
            raise AppError("该版本绑定的上游版本已变化，不能直接接受", 409)

    async def _head_rows(self, project_id: str) -> list[dict[str, Any]]:
        return await self._db.fetch_all(
            "SELECT d.role, h.deliverable_id, h.revision_id, "
            "p.payload_json AS content_json "
            "FROM screenplay_project_heads AS h "
            "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
            "LEFT JOIN screenplay_revision_parts AS p "
            "ON p.revision_id = h.revision_id "
            "AND p.part_type = 'document' AND p.part_key = 'main' "
            "WHERE h.project_id = ?",
            [project_id],
        )

    async def _record_command_receipt(
        self,
        *,
        command_id: str,
        command_type: str,
        project_id: str,
        request_digest: str,
        result_ref: str,
        response: Mapping[str, Any],
    ) -> None:
        await self._db.execute(
            "INSERT INTO screenplay_command_receipts "
            "(command_id, command_type, project_id, request_digest, "
            "result_ref, response_json) VALUES (?, ?, ?, ?, ?, ?)",
            [
                command_id,
                command_type,
                project_id,
                request_digest,
                result_ref,
                _dump(dict(response)),
            ],
        )

    async def _record_outbox(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        await self._db.execute(
            "INSERT INTO screenplay_outbox_events "
            "(id, aggregate_type, aggregate_id, event_type, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                f"spout_{uuid.uuid4().hex}",
                aggregate_type,
                aggregate_id,
                event_type,
                _dump(dict(payload)),
            ],
        )

    async def get_workspace(self, project_id: str) -> dict[str, Any]:
        project = await self._require_native_project(project_id)

        deliverables = await self._db.fetch_all(
            "SELECT * FROM screenplay_deliverables WHERE project_id = ? "
            "ORDER BY create_time ASC, role ASC",
            [project_id],
        )
        heads = await self._db.fetch_all(
            "SELECT d.role, r.*, p.payload_json AS head_content_json "
            "FROM screenplay_project_heads AS h "
            "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
            "JOIN screenplay_revisions AS r ON r.id = h.revision_id "
            "LEFT JOIN screenplay_revision_parts AS p "
            "ON p.revision_id = r.id AND p.part_type = 'document' "
            "AND p.part_key = 'main' "
            "WHERE h.project_id = ? ORDER BY d.role ASC",
            [project_id],
        )
        candidates = await self._db.fetch_all(
            "SELECT d.role, r.* FROM screenplay_revisions AS r "
            "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
            "LEFT JOIN screenplay_project_heads AS h "
            "ON h.project_id = r.project_id AND h.revision_id = r.id "
            "WHERE r.project_id = ? AND h.revision_id IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM screenplay_acceptance_events AS a "
            "WHERE a.project_id = r.project_id AND a.revision_id = r.id) "
            "ORDER BY r.create_time DESC, r.revision_no DESC",
            [project_id],
        )
        operations = await self._db.fetch_all(
            "SELECT * FROM screenplay_operations WHERE project_id = ? "
            "AND status IN ('queued', 'running', 'paused') "
            "ORDER BY create_time ASC",
            [project_id],
        )
        working_copies = await self._db.fetch_all(
            "SELECT w.*, d.role FROM screenplay_working_copies AS w "
            "JOIN screenplay_deliverables AS d ON d.id = w.deliverable_id "
            "WHERE w.project_id = ? ORDER BY w.update_time DESC",
            [project_id],
        )

        source_kind = str(project.get("source_kind") or "original")
        head_by_role = {
            str(row["role"]): _revision_summary(row)
            for row in heads
        }
        head_revision_by_role = {
            str(row["role"]): str(row["id"])
            for row in heads
        }
        candidate_views: list[dict[str, Any]] = []
        for row in candidates:
            candidate = _revision_summary(row)
            candidate["applicability"] = (
                await self._revision_applicability(
                    revision_id=str(row["id"]),
                    head_revision_by_role=head_revision_by_role,
                    role=str(row["role"]),
                    source_kind=source_kind,
                )
            )
            candidate_views.append(candidate)
        head_contents = {
            str(row["role"]): _object(row.get("head_content_json"))
            for row in heads
        }
        stage = derive_stage(
            source_kind=source_kind,
            head_roles=head_by_role,
            head_contents=head_contents,
        )

        source_snapshot = _nullable_object(project.get("source_snapshot_json"))
        if source_snapshot is None:
            raise RuntimeError("native screenplay project has no source snapshot")

        roles = applicable_deliverable_roles(source_kind)
        return {
            "project": {
                "id": str(project["id"]),
                "revision": int(project.get("revision") or 1),
                "title": str(project.get("title") or ""),
                "format": public_format(project.get("format")),
                "source": source_snapshot,
                "brief": {
                    "approach": str(project.get("approach") or ""),
                    "premise": str(project.get("premise") or ""),
                },
                "lifecycle": (
                    "archived"
                    if str(project.get("status") or "") == "archived"
                    else "active"
                ),
                "stage": stage,
                "createdAt": project.get("create_time"),
                "updatedAt": project.get("update_time"),
            },
            "workflow": {
                "stage": stage,
                "heads": {
                    role: head_by_role.get(role)
                    for role in roles
                },
                "nextActions": next_actions(stage, head_contents=head_contents),
            },
            "deliverables": [{
                "id": str(row["id"]),
                "role": str(row["role"]),
                "headRevisionId": (
                    head_by_role.get(str(row["role"]), {}).get("id")
                    if str(row["role"]) in head_by_role
                    else None
                ),
            } for row in deliverables if str(row["role"]) in roles],
            "candidates": candidate_views,
            "activeOperations": [_operation_summary(row) for row in operations],
            "workingCopies": [{
                "id": str(row["id"]),
                "deliverableId": str(row["deliverable_id"]),
                "role": str(row["role"]),
                "baseRevisionId": row.get("base_revision_id"),
                "revision": int(row.get("revision") or 1),
                "content": _object(row.get("content_manifest_json")),
                "updatedAt": row.get("update_time"),
            } for row in working_copies],
        }

    async def _revision_applicability(
        self,
        *,
        revision_id: str,
        head_revision_by_role: Mapping[str, str],
        role: str,
        source_kind: str,
    ) -> str:
        inputs = await self._db.fetch_all(
            "SELECT input_role, input_revision_id "
            "FROM screenplay_revision_inputs WHERE revision_id = ?",
            [revision_id],
        )
        input_by_role = {
            str(row["input_role"]): str(row["input_revision_id"])
            for row in inputs
        }
        prerequisite = _prerequisite_role(role, source_kind)
        if prerequisite is not None and prerequisite not in input_by_role:
            return "stale"
        if all(
            head_revision_by_role.get(str(row["input_role"]))
            == str(row["input_revision_id"])
            for row in inputs
        ):
            return "current"
        return "stale"

    async def get_revision(
        self,
        revision_id: str,
        *,
        include_content: bool,
    ) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT r.*, d.role, p.source_kind FROM screenplay_revisions AS r "
            "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
            "JOIN screenplay_projects AS p ON p.id = r.project_id "
            "WHERE r.id = ?",
            [revision_id],
        )
        if row is None:
            raise NotFoundError("剧本版本不存在")
        heads = await self._head_rows(str(row["project_id"]))
        view = {
            **_revision_summary(row),
            "projectId": str(row["project_id"]),
            "schemaVersion": int(row.get("schema_version") or 1),
            "createdBy": str(row.get("created_by") or ""),
            "rootRunId": row.get("root_run_id"),
            "finalizingRunId": row.get("finalizing_run_id"),
            "applicability": await self._revision_applicability(
                revision_id=revision_id,
                head_revision_by_role={
                    str(item["role"]): str(item["revision_id"])
                    for item in heads
                },
                role=str(row["role"]),
                source_kind=str(row.get("source_kind") or "original"),
            ),
        }
        if not include_content:
            return view
        inputs = await self._db.fetch_all(
            "SELECT input_role, input_revision_id "
            "FROM screenplay_revision_inputs WHERE revision_id = ? "
            "ORDER BY input_role ASC",
            [revision_id],
        )
        parts = await self._db.fetch_all(
            "SELECT part_type, part_key, position, payload_json, content_text, "
            "content_digest FROM screenplay_revision_parts "
            "WHERE revision_id = ? ORDER BY part_type ASC, position ASC",
            [revision_id],
        )
        sources = await self._db.fetch_all(
            "SELECT source_type, source_id, source_revision, excerpt "
            "FROM screenplay_revision_source_refs WHERE revision_id = ? "
            "ORDER BY source_type ASC, source_id ASC",
            [revision_id],
        )
        return {
            **view,
            "inputRevisions": {
                str(item["input_role"]): str(item["input_revision_id"])
                for item in inputs
            },
            "parts": [{
                "type": str(item["part_type"]),
                "key": str(item["part_key"]),
                "position": int(item.get("position") or 0),
                "payload": _object(item.get("payload_json")),
                "contentText": str(item.get("content_text") or ""),
                "contentDigest": str(item.get("content_digest") or ""),
            } for item in parts],
            "sources": [{
                "type": str(item["source_type"]),
                "id": str(item["source_id"]),
                "revision": str(item["source_revision"]),
                "excerpt": str(item.get("excerpt") or ""),
            } for item in sources],
        }

    async def list_revision_history(
        self,
        *,
        project_id: str,
        role: str,
        before_revision_no: int | None,
        limit: int,
    ) -> dict[str, Any]:
        deliverable = await self._db.fetch_one(
            "SELECT d.id, d.role, p.source_kind "
            "FROM screenplay_deliverables AS d "
            "JOIN screenplay_projects AS p ON p.id = d.project_id "
            "WHERE d.project_id = ? AND d.role = ?",
            [project_id, role],
        )
        if deliverable is None:
            raise NotFoundError("剧本交付物不存在")
        page_size = max(1, min(100, int(limit)))
        clauses = ["r.deliverable_id = ?"]
        params: list[Any] = [deliverable["id"]]
        if before_revision_no is not None:
            clauses.append("r.revision_no < ?")
            params.append(int(before_revision_no))
        params.append(page_size + 1)
        rows = await self._db.fetch_all(
            "SELECT r.*, d.role, "
            "CASE WHEN h.revision_id IS NOT NULL THEN 'current' "
            "WHEN EXISTS (SELECT 1 FROM screenplay_acceptance_events AS a "
            "WHERE a.project_id = r.project_id AND a.revision_id = r.id) "
            "THEN 'historical' ELSE 'candidate' END AS projection_status "
            "FROM screenplay_revisions AS r "
            "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
            "LEFT JOIN screenplay_project_heads AS h "
            "ON h.project_id = r.project_id AND h.revision_id = r.id "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY r.revision_no DESC LIMIT ?",
            params,
        )
        has_more = len(rows) > page_size
        page = rows[:page_size]
        heads = await self._head_rows(project_id)
        head_revision_by_role = {
            str(item["role"]): str(item["revision_id"])
            for item in heads
        }
        items: list[dict[str, Any]] = []
        for row in page:
            item = _revision_summary(row)
            item["status"] = str(row["projection_status"])
            item["applicability"] = await self._revision_applicability(
                revision_id=str(row["id"]),
                head_revision_by_role=head_revision_by_role,
                role=str(row["role"]),
                source_kind=str(deliverable.get("source_kind") or "original"),
            )
            items.append(item)
        return {
            "projectId": project_id,
            "deliverableId": str(deliverable["id"]),
            "role": str(deliverable["role"]),
            "items": items,
            "nextCursor": (
                str(page[-1]["revision_no"])
                if has_more and page
                else None
            ),
        }

    async def get_operation(self, operation_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM screenplay_operations WHERE id = ?",
            [operation_id],
        )
        if row is None:
            raise NotFoundError("剧本 Operation 不存在")
        return _operation_view(row)

    async def list_operation_events(
        self,
        operation_id: str,
        *,
        after: int,
        limit: int,
    ) -> dict[str, Any]:
        await self.get_operation(operation_id)
        rows = await self._db.fetch_all(
            "SELECT sequence, event_type, payload_json, create_time "
            "FROM screenplay_operation_events "
            "WHERE operation_id = ? AND sequence > ? "
            "ORDER BY sequence ASC LIMIT ?",
            [operation_id, max(0, int(after)), max(1, min(500, int(limit)))],
        )
        events = [{
            "sequence": int(row["sequence"]),
            "type": str(row["event_type"]),
            "payload": _object(row.get("payload_json")),
            "createdAt": row.get("create_time"),
        } for row in rows]
        return {
            "operationId": operation_id,
            "events": events,
            "nextAfter": (
                events[-1]["sequence"] if events else max(0, int(after))
            ),
        }


def _revision_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "deliverableId": str(row["deliverable_id"]),
        "role": str(row["role"]),
        "revisionNo": int(row.get("revision_no") or 1),
        "parentRevisionId": row.get("parent_revision_id"),
        "contentDigest": str(row.get("content_digest") or ""),
        "summary": _object(row.get("summary_json")),
        "operationId": row.get("operation_id"),
        "rootRunId": row.get("root_run_id"),
        "finalizingRunId": row.get("finalizing_run_id"),
        "createdAt": row.get("create_time"),
    }


def _operation_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "targetRole": str(row.get("target_role") or ""),
        "status": str(row.get("status") or "queued"),
        "progress": _object(row.get("progress_json")),
        "resultRevisionId": row.get("result_revision_id"),
        "updatedAt": row.get("update_time"),
    }


def _operation_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **_operation_summary(row),
        "projectId": str(row["project_id"]),
        "commandId": str(row.get("command_id") or ""),
        "intent": _object(row.get("intent_json")),
        "baseProjectRevision": int(row.get("base_project_revision") or 1),
        "baseHeads": _object(row.get("base_heads_json")),
        "error": _nullable_object(row.get("error_json")),
        "createdAt": row.get("create_time"),
    }


def _working_copy_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "projectId": str(row["project_id"]),
        "deliverableId": str(row["deliverable_id"]),
        "role": str(row["role"]),
        "baseRevisionId": row.get("base_revision_id"),
        "revision": int(row.get("revision") or 1),
        "content": _object(row.get("content_manifest_json")),
        "updatedAt": row.get("update_time"),
    }


def _working_copy_content_from_revision(
    *,
    role: str,
    parts: list[dict[str, Any]],
) -> dict[str, Any]:
    main = next(
        (
            part for part in parts
            if str(part.get("part_type") or "") == "document"
            and str(part.get("part_key") or "") == "main"
        ),
        None,
    )
    if main is None:
        raise AppError("该 Revision 缺少主文档 Part，不能创建 Working Copy", 409)
    return {
        "schemaVersion": 1,
        "role": role,
        "contentJson": _object(main.get("payload_json")),
        "contentText": str(main.get("content_text") or ""),
        "parts": [{
            "type": str(part.get("part_type") or ""),
            "key": str(part.get("part_key") or ""),
            "position": int(part.get("position") or 0),
            "payload": _object(part.get("payload_json")),
            "contentText": str(part.get("content_text") or ""),
        } for part in parts if part is not main],
    }


def _content_digest(content: Mapping[str, Any], content_text: str) -> str:
    payload = _dump({
        "payload": dict(content),
        "contentText": content_text,
    }).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _agent_proposal_identity(
    *,
    proposal_kind: str,
    content_json: Mapping[str, Any],
    finalizing_run_id: str,
    content_digest: str,
) -> str:
    artifact_ref = str(content_json.get("artifactRef") or "").strip()
    if artifact_ref:
        return f"artifact:{artifact_ref}"
    long_task_id = str(content_json.get("longTaskId") or "").strip()
    if long_task_id:
        return f"long-task:{long_task_id}"
    # Small proposals that do not use an Artifact still receive a stable
    # identity within their Run. Re-emitting the same DomainEffect is a replay;
    # emitting different content is a distinct candidate.
    return (
        f"run:{finalizing_run_id}:kind:{proposal_kind}:"
        f"content:{content_digest}"
    )


def _agent_candidate_parts(
    *,
    content_json: Mapping[str, Any],
    content_text: str,
) -> list[dict[str, Any]]:
    content = dict(content_json)
    parts = [_candidate_part(
        part_type="document",
        part_key="main",
        position=0,
        payload=content,
        content_text=content_text,
    )]
    episode_payloads: list[Mapping[str, Any]] = []
    if isinstance(content.get("episodeDrafts"), list):
        episode_payloads = [
            item for item in content["episodeDrafts"]
            if isinstance(item, Mapping)
        ]
    elif (
        str(content.get("documentKind") or "") == "episode_outline"
        and isinstance(content.get("episodes"), list)
    ):
        episode_payloads = [
            item for item in content["episodes"]
            if isinstance(item, Mapping)
        ]
    elif isinstance(content.get("scenes"), list):
        grouped: dict[int, list[dict[str, Any]]] = {}
        for raw_scene in content["scenes"]:
            if not isinstance(raw_scene, Mapping):
                continue
            raw_episode = raw_scene.get("episodeNumber")
            episode_number = (
                raw_episode
                if isinstance(raw_episode, int)
                and not isinstance(raw_episode, bool)
                and raw_episode > 0
                else 1
            )
            grouped.setdefault(episode_number, []).append(dict(raw_scene))
        if grouped and (
            len(grouped) > 1
            or any(
                isinstance(item, Mapping) and item.get("episodeNumber")
                for item in content["scenes"]
            )
        ):
            episode_payloads = [
                {"episodeNumber": number, "scenes": scenes}
                for number, scenes in sorted(grouped.items())
            ]

    occupied_keys: set[str] = set()
    for index, raw_episode in enumerate(episode_payloads, start=1):
        episode = dict(raw_episode)
        raw_number = episode.get("episodeNumber") or episode.get("number")
        part_key = str(raw_number or index)
        if part_key in occupied_keys:
            part_key = f"{part_key}-{index}"
        occupied_keys.add(part_key)
        episode_text = str(episode.get("contentText") or "")
        parts.append(_candidate_part(
            part_type="episode",
            part_key=part_key,
            position=index,
            payload=episode,
            content_text=episode_text,
        ))
    return parts


def _working_copy_candidate_parts(
    content: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if "contentJson" not in content and not isinstance(content.get("parts"), list):
        return _agent_candidate_parts(
            content_json=content,
            content_text=str(content.get("contentText") or ""),
        )

    result = [_candidate_part(
        part_type="document",
        part_key="main",
        position=0,
        payload=_object(content.get("contentJson")),
        content_text=str(content.get("contentText") or ""),
    )]
    occupied_keys = {("document", "main")}
    occupied_positions = {("document", 0)}
    allowed_types = {"document", "episode", "scene", "reviewIssueGroup"}
    for raw in content.get("parts", []):
        if not isinstance(raw, Mapping):
            raise AppError("Working Copy Part 格式无效", 422)
        part_type = str(raw.get("type") or "").strip()
        part_key = str(raw.get("key") or "").strip()
        if part_type not in allowed_types or not part_key:
            raise AppError("Working Copy Part 类型或 Key 无效", 422)
        try:
            position = int(raw.get("position") or 0)
        except (TypeError, ValueError) as error:
            raise AppError("Working Copy Part position 无效", 422) from error
        if position < 0:
            raise AppError("Working Copy Part position 无效", 422)
        if (part_type, part_key) in occupied_keys:
            raise AppError("Working Copy 包含重复 Part Key", 422)
        if (part_type, position) in occupied_positions:
            raise AppError("Working Copy 包含重复 Part position", 422)
        occupied_keys.add((part_type, part_key))
        occupied_positions.add((part_type, position))
        result.append(_candidate_part(
            part_type=part_type,
            part_key=part_key,
            position=position,
            payload=_object(raw.get("payload")),
            content_text=str(raw.get("contentText") or ""),
        ))
    return result


def _episode_part_sort_key(value: str) -> tuple[int, int | str]:
    try:
        return 0, int(value)
    except (TypeError, ValueError):
        return 1, value


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _merge_episode_payload(
    previous: Mapping[str, Any],
    update: Mapping[str, Any],
) -> dict[str, Any]:
    """Overlay changed scenes while preserving the rest of one episode."""

    result = {**dict(previous), **dict(update)}
    scene_ids = list(dict.fromkeys(
        str(value).strip()
        for source in (previous, update)
        for value in source.get("sceneIds", [])
        if str(value).strip()
    ))
    if scene_ids:
        result["sceneIds"] = scene_ids

    scene_text_by_id = {
        str(item.get("sceneId") or "").strip(): dict(item)
        for source in (previous, update)
        for item in source.get("sceneTexts", [])
        if isinstance(item, Mapping)
        and str(item.get("sceneId") or "").strip()
    }
    if scene_text_by_id:
        ordered_texts = [
            scene_text_by_id[scene_id]
            for scene_id in scene_ids
            if scene_id in scene_text_by_id
        ]
        result["sceneTexts"] = ordered_texts
        result["contentText"] = "\n\n".join(
            str(item.get("contentText") or "").strip()
            for item in ordered_texts
            if str(item.get("contentText") or "").strip()
        )

    execution_by_id = {
        str(item.get("sceneId") or "").strip(): dict(item)
        for source in (previous, update)
        for item in source.get("sceneExecutions", [])
        if isinstance(item, Mapping)
        and str(item.get("sceneId") or "").strip()
    }
    if execution_by_id:
        result["sceneExecutions"] = [
            execution_by_id[scene_id]
            for scene_id in scene_ids
            if scene_id in execution_by_id
        ]
    return result


def _candidate_part(
    *,
    part_type: str,
    part_key: str,
    position: int,
    payload: Mapping[str, Any],
    content_text: str,
) -> dict[str, Any]:
    normalized_payload = dict(payload)
    normalized_text = str(content_text or "")
    return {
        "partType": part_type,
        "partKey": part_key,
        "position": position,
        "payload": normalized_payload,
        "contentText": normalized_text,
        "contentDigest": _content_digest(
            normalized_payload,
            normalized_text,
        ),
    }


def _validate_publishable_content(
    role: str,
    parts: list[Mapping[str, Any]],
) -> None:
    if not parts:
        raise AppError("Working Copy 内容为空，不能发布版本", 409)
    main = parts[0]
    content = (
        dict(main.get("payload"))
        if isinstance(main.get("payload"), Mapping)
        else {}
    )
    content_text = str(main.get("contentText") or "").strip()
    if not content and not content_text and len(parts) == 1:
        raise AppError("Working Copy 内容为空，不能发布版本", 409)
    if role != "creativeBrief":
        return
    fields = content.get("fields")
    field_values = dict(fields) if isinstance(fields, Mapping) else {}
    approach = str(field_values.get("approach") or "").strip()
    premise = str(field_values.get("premise") or "").strip()
    if not approach and not premise and not content_text:
        raise AppError("创作简报 Working Copy 还没有可发布内容", 409)


def _prerequisite_role(role: str, source_kind: str) -> str | None:
    if role == "sourceAnalysis":
        return None
    if role == "creativeBrief":
        return "sourceAnalysis" if source_kind == "book" else None
    return {
        "structure": "creativeBrief",
        "sceneList": "structure",
        "screenplayDraft": "sceneList",
        "review": "screenplayDraft",
    }.get(role)


__all__ = ["SqliteScreenplayV2Repository"]
