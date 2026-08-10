"""SQLite persistence for the screenplay v2 project aggregate."""

from __future__ import annotations

import json
import hashlib
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from domains.screenplay.project_aggregate import (
    applicable_deliverable_roles,
    derive_stage,
    next_actions,
    public_format,
)
from domains.screenplay.review_adjudication import derive_review_state
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

    async def publish_screenplay_agent_task_candidate(
        self,
        *,
        task_id: str,
        project_id: str,
        target_role: str,
        proposal_kind: str,
        title: str,
        content_json: Mapping[str, Any],
        content_text: str,
        planner_run_id: str | None,
        finalizing_run_id: str | None,
        base_revision_id: str | None,
        source_run_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Publish one idempotent candidate owned by a PurrA task."""

        async with self._db.transaction(cancellation_linearizable=True):
            existing = await self._db.fetch_one(
                "SELECT r.*, d.role FROM screenplay_revisions AS r "
                "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
                "WHERE r.agent_task_id = ?",
                [task_id],
            )
            if existing is not None:
                return {
                    "projectId": str(existing["project_id"]),
                    "taskId": task_id,
                    "revisionId": str(existing["id"]),
                    "role": str(existing["role"]),
                    "revisionNo": int(existing["revision_no"]),
                    "replayed": True,
                }
            project = await self._db.fetch_one(
                "SELECT * FROM screenplay_projects WHERE id = ?",
                [project_id],
            )
            if project is None:
                raise NotFoundError("剧本项目不存在")
            if str(project.get("status") or "") == "archived":
                raise AppError("项目已归档，不能生成候选版本", 409)
            deliverable = await self._db.fetch_one(
                "SELECT id FROM screenplay_deliverables "
                "WHERE project_id = ? AND role = ?",
                [project_id, target_role],
            )
            if deliverable is None:
                raise AppError("剧本任务目标不属于当前项目", 409)
            head_rows = await self._db.fetch_all(
                "SELECT d.role, h.revision_id FROM screenplay_project_heads AS h "
                "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
                "WHERE h.project_id = ?",
                [project_id],
            )
            base_heads = {
                str(row["role"]): str(row["revision_id"])
                for row in head_rows
            }
            parent_revision_id = str(base_revision_id or "").strip() or base_heads.get(
                target_role
            )
            if parent_revision_id:
                parent = await self._db.fetch_one(
                    "SELECT id FROM screenplay_revisions WHERE id = ? "
                    "AND project_id = ? AND deliverable_id = ?",
                    [parent_revision_id, project_id, deliverable["id"]],
                )
                if parent is None:
                    raise AppError("剧本任务的基础版本已失效", 409)
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
                content_json=dict(content_json),
                content_text=str(content_text or ""),
            )
            document_part = parts[0]
            summary = {
                "role": target_role,
                "proposalKind": proposal_kind,
                "title": title,
                "textLength": len(str(document_part["contentText"])),
                "partCount": len(parts),
                "derivedFromIds": list(base_heads.values()),
            }
            await self._db.execute(
                "INSERT INTO screenplay_revisions "
                "(id, project_id, deliverable_id, revision_no, parent_revision_id, "
                "schema_version, content_digest, summary_json, root_run_id, "
                "finalizing_run_id, created_by, agent_task_id) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, 'screenplay_agent_task', ?)",
                [
                    revision_id,
                    project_id,
                    deliverable["id"],
                    revision_no,
                    parent_revision_id,
                    str(document_part["contentDigest"]),
                    _dump(summary),
                    str(planner_run_id or "").strip() or None,
                    str(finalizing_run_id or "").strip() or None,
                    task_id,
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
                project_id=project_id,
                role=target_role,
                source_kind=str(project.get("source_kind") or "original"),
                base_heads=base_heads,
            )
            run_ids = tuple(dict.fromkeys(
                value for value in (
                    str(planner_run_id or "").strip(),
                    str(finalizing_run_id or "").strip(),
                    *(str(value or "").strip() for value in source_run_ids),
                ) if value
            ))
            await self._attach_agent_source_refs(
                revision_id=revision_id,
                project_id=project_id,
                run_ids=run_ids,
            )
            payload = {
                "projectId": project_id,
                "taskId": task_id,
                "revisionId": revision_id,
                "role": target_role,
                "revisionNo": revision_no,
            }
            await self._record_outbox(
                aggregate_type="screenplayRevision",
                aggregate_id=revision_id,
                event_type="screenplay.candidate.ready",
                payload=payload,
            )
            return {**payload, "replayed": False}

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

        if any(
            key in content_json
            for key in (
                "error",
                "errorCode",
                "executionError",
                "failedEpisodes",
                "failure",
                "failureCode",
            )
        ):
            return current_parts

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
        completed_by_episode = {
            str(_positive_int(item.get("episodeNumber"))): dict(item)
            for item in content_json.get("episodeReviews", [])
            if isinstance(item, Mapping)
            and _positive_int(item.get("episodeNumber")) is not None
        }
        episode_keys.update(completed_by_episode)
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
            completed = completed_by_episode.get(episode_key, {})
            payload = {
                "episodeNumber": _positive_int(episode_key) or position,
                "reviewedDraftId": str(
                    content_json.get("reviewedDraftId") or ""
                ),
                "reviewStatus": "completed",
                "verdict": str(
                    completed.get("verdict")
                    or content_json.get("verdict")
                    or ""
                ),
                "issues": values["issues"],
                "verificationResults": values["verificationResults"],
                "reviewedContentDigest": str(
                    completed.get("reviewedContentDigest") or ""
                ),
                "inputContractVersion": int(
                    completed.get("inputContractVersion")
                    or content_json.get("inputContractVersion")
                    or 0
                ),
            }
            episode_parts.append(_candidate_part(
                part_type="episode",
                part_key=episode_key,
                position=position,
                payload=payload,
                content_text=str(completed.get("contentText") or ""),
            ))
        return [current_parts[0], *episode_parts]

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
            review_state = await self._review_state(
                project_id=project_id,
                project={**dict(project), "completion_source": None},
                heads=current_heads,
            )
            stage = derive_stage(
                source_kind=str(project.get("source_kind") or "original"),
                head_roles=(str(row["role"]) for row in current_heads),
                head_contents=head_contents,
            )
            next_project_revision = actual_project_revision + 1
            await self._db.execute(
                "UPDATE screenplay_projects SET revision = ?, active_stage = ?, "
                "completion_source = NULL, "
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
                        review_state=review_state,
                    ),
                    "review": review_state,
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

    async def adjudicate_review(
        self,
        *,
        command_id: str,
        request_digest: str,
        project_id: str,
        expected_project_revision: int,
        review_revision_id: str,
        decisions: Sequence[Mapping[str, object]],
        actor: str,
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            replay = await self.find_command_receipt(
                command_id=command_id,
                command_type="adjudicateReview",
                request_digest=request_digest,
            )
            if replay is not None:
                return replay

            project = await self._db.fetch_one(
                "SELECT * FROM screenplay_projects WHERE id = ? "
                "AND source_snapshot_json IS NOT NULL",
                [project_id],
            )
            if project is None:
                raise NotFoundError("剧本项目不存在")
            if str(project.get("status") or "") == "archived":
                raise AppError("项目已归档，不能处理审阅意见", 409)
            if str(project.get("completion_source") or "") in {
                "user",
                "legacyAgentVerdict",
            }:
                raise AppError("项目已经定稿，不能修改审阅裁决", 409)
            actual_revision = int(project.get("revision") or 1)
            if actual_revision != int(expected_project_revision):
                raise AppError("项目已被其他操作更新，请刷新后重试", 409)

            heads = await self._head_rows(project_id)
            review_head = next(
                (row for row in heads if str(row["role"]) == "review"),
                None,
            )
            draft_head = next(
                (row for row in heads if str(row["role"]) == "screenplayDraft"),
                None,
            )
            if (
                review_head is None
                or str(review_head["revision_id"]) != review_revision_id
            ):
                raise AppError("当前审阅版本已变化，请刷新后重试", 409)
            if draft_head is None:
                raise AppError("当前剧本正文不存在，不能处理审阅意见", 409)
            review_content = _object(review_head.get("content_json"))
            draft_revision_id = str(draft_head["revision_id"])
            if str(review_content.get("reviewedDraftId") or "") != draft_revision_id:
                raise AppError("当前审阅报告对应的不是当前剧本版本", 409)
            valid_issue_ids = {
                str(item.get("id") or "").strip()
                for item in review_content.get("issues", [])
                if isinstance(item, Mapping)
            }
            requested_issue_ids = [
                str(decision.get("issueId") or "").strip()
                for decision in decisions
            ]
            if (
                not requested_issue_ids
                or any(issue_id not in valid_issue_ids for issue_id in requested_issue_ids)
            ):
                raise AppError("提交的审阅意见不属于当前审阅报告", 409)

            for decision in decisions:
                issue_id = str(decision.get("issueId") or "").strip()
                status = str(decision.get("status") or "").strip()
                note = str(decision.get("note") or "").strip()
                previous = await self._db.fetch_one(
                    "SELECT status FROM screenplay_review_decisions "
                    "WHERE review_revision_id = ? AND issue_id = ?",
                    [review_revision_id, issue_id],
                )
                await self._db.execute(
                    "INSERT INTO screenplay_review_decisions "
                    "(project_id, review_revision_id, draft_revision_id, issue_id, "
                    "status, note, actor) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(review_revision_id, issue_id) DO UPDATE SET "
                    "draft_revision_id = excluded.draft_revision_id, "
                    "status = excluded.status, note = excluded.note, "
                    "actor = excluded.actor, decided_at = CURRENT_TIMESTAMP, "
                    "update_time = CURRENT_TIMESTAMP",
                    [
                        project_id,
                        review_revision_id,
                        draft_revision_id,
                        issue_id,
                        status,
                        note,
                        actor,
                    ],
                )
                await self._db.execute(
                    "INSERT INTO screenplay_review_decision_events "
                    "(id, project_id, command_id, review_revision_id, "
                    "draft_revision_id, issue_id, previous_status, status, "
                    "note, actor) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        f"sprevd_{uuid.uuid4().hex}",
                        project_id,
                        command_id,
                        review_revision_id,
                        draft_revision_id,
                        issue_id,
                        (previous or {}).get("status"),
                        status,
                        note,
                        actor,
                    ],
                )

            next_project_revision = actual_revision + 1
            await self._db.execute(
                "UPDATE screenplay_projects SET revision = ?, "
                "active_stage = 'review', completion_source = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [next_project_revision, project_id],
            )
            response = {
                "projectId": project_id,
                "projectRevision": next_project_revision,
                "reviewRevisionId": review_revision_id,
                "decisionCount": len(decisions),
            }
            await self._record_command_receipt(
                command_id=command_id,
                command_type="adjudicateReview",
                project_id=project_id,
                request_digest=request_digest,
                result_ref=f"screenplay-review://{review_revision_id}",
                response=response,
            )
            await self._record_outbox(
                aggregate_type="screenplayReview",
                aggregate_id=review_revision_id,
                event_type="screenplay.review.adjudicated",
                payload=response,
            )
            return response

    async def finalize_project(
        self,
        *,
        command_id: str,
        request_digest: str,
        project_id: str,
        expected_project_revision: int,
        draft_revision_id: str,
        review_revision_id: str,
        actor: str,
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            replay = await self.find_command_receipt(
                command_id=command_id,
                command_type="finalizeProject",
                request_digest=request_digest,
            )
            if replay is not None:
                return replay

            project = await self._db.fetch_one(
                "SELECT * FROM screenplay_projects WHERE id = ? "
                "AND source_snapshot_json IS NOT NULL",
                [project_id],
            )
            if project is None:
                raise NotFoundError("剧本项目不存在")
            if str(project.get("status") or "") == "archived":
                raise AppError("项目已归档，不能确认定稿", 409)
            actual_revision = int(project.get("revision") or 1)
            if actual_revision != int(expected_project_revision):
                raise AppError("项目已被其他操作更新，请刷新后重试", 409)

            heads = await self._head_rows(project_id)
            current_draft_id = next(
                (str(row["revision_id"]) for row in heads if str(row["role"]) == "screenplayDraft"),
                None,
            )
            current_review_id = next(
                (str(row["revision_id"]) for row in heads if str(row["role"]) == "review"),
                None,
            )
            if current_draft_id != draft_revision_id or current_review_id != review_revision_id:
                raise AppError("当前剧本或审阅版本已变化，请刷新后重试", 409)

            review_state = await self._review_state(
                project_id=project_id,
                project={**dict(project), "completion_source": None},
                heads=heads,
            )
            counts = dict(review_state.get("counts") or {})
            pending_count = int(counts.get("pending") or 0)
            planned_count = int(counts.get("planned") or 0)
            if pending_count:
                raise AppError(f"还有 {pending_count} 条审阅意见待处理", 409)
            if planned_count:
                raise AppError(f"还有 {planned_count} 条审阅意见等待修订", 409)
            hard_checks = list(review_state.get("hardChecks") or [])
            if hard_checks:
                first = hard_checks[0]
                message = (
                    str(first.get("message") or "当前剧本未通过定稿校验")
                    if isinstance(first, Mapping)
                    else "当前剧本未通过定稿校验"
                )
                raise AppError(message, 409)
            if review_state.get("canFinalize") is not True:
                raise AppError("当前剧本尚未满足定稿条件", 409)

            decision_rows = await self._db.fetch_all(
                "SELECT issue_id, status, note FROM screenplay_review_decisions "
                "WHERE project_id = ? AND review_revision_id = ? "
                "ORDER BY issue_id ASC",
                [project_id, review_revision_id],
            )
            decision_snapshot_hash = hashlib.sha256(_dump({
                "draftRevisionId": draft_revision_id,
                "reviewRevisionId": review_revision_id,
                "decisions": [dict(row) for row in decision_rows],
            }).encode("utf-8")).hexdigest()
            finalization_id = f"spfinal_{uuid.uuid4().hex}"
            await self._db.execute(
                "INSERT INTO screenplay_finalization_events "
                "(id, project_id, command_id, draft_revision_id, "
                "review_revision_id, decision_snapshot_hash, actor) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    finalization_id,
                    project_id,
                    command_id,
                    draft_revision_id,
                    review_revision_id,
                    decision_snapshot_hash,
                    actor,
                ],
            )
            next_project_revision = actual_revision + 1
            await self._db.execute(
                "UPDATE screenplay_projects SET revision = ?, "
                "active_stage = 'completed', completion_source = 'user', "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [next_project_revision, project_id],
            )
            response = {
                "projectId": project_id,
                "projectRevision": next_project_revision,
                "draftRevisionId": draft_revision_id,
                "reviewRevisionId": review_revision_id,
                "finalizationEventId": finalization_id,
                "decisionSnapshotHash": decision_snapshot_hash,
            }
            await self._record_command_receipt(
                command_id=command_id,
                command_type="finalizeProject",
                project_id=project_id,
                request_digest=request_digest,
                result_ref=f"screenplay-finalization://{finalization_id}",
                response=response,
            )
            await self._record_outbox(
                aggregate_type="screenplayProject",
                aggregate_id=project_id,
                event_type="screenplay.project.finalized",
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

    async def _review_state(
        self,
        *,
        project_id: str,
        project: Mapping[str, Any],
        heads: Sequence[Mapping[str, Any]],
    ) -> dict[str, object]:
        draft = next(
            (row for row in heads if str(row.get("role") or "") == "screenplayDraft"),
            None,
        )
        review = next(
            (row for row in heads if str(row.get("role") or "") == "review"),
            None,
        )
        draft_revision_id = str((draft or {}).get("id") or (draft or {}).get("revision_id") or "").strip() or None
        review_revision_id = str((review or {}).get("id") or (review or {}).get("revision_id") or "").strip() or None

        decisions: list[dict[str, Any]] = []
        if review_revision_id:
            rows = await self._db.fetch_all(
                "SELECT issue_id, status, note, actor, decided_at "
                "FROM screenplay_review_decisions "
                "WHERE project_id = ? AND review_revision_id = ? "
                "ORDER BY issue_id ASC",
                [project_id, review_revision_id],
            )
            decisions = [{
                "issueId": str(row["issue_id"]),
                "status": str(row["status"]),
                "note": str(row.get("note") or ""),
                "actor": row.get("actor"),
                "decidedAt": row.get("decided_at"),
            } for row in rows]

        matching_finalization = None
        if draft_revision_id and review_revision_id:
            matching_finalization = await self._db.fetch_one(
                "SELECT id FROM screenplay_finalization_events "
                "WHERE project_id = ? AND draft_revision_id = ? "
                "AND review_revision_id = ? ORDER BY create_time DESC LIMIT 1",
                [project_id, draft_revision_id, review_revision_id],
            )
        stored_completion = str(project.get("completion_source") or "").strip()
        completion_source = (
            "user"
            if matching_finalization is not None and stored_completion == "user"
            else "legacyAgentVerdict"
            if stored_completion == "legacyAgentVerdict"
            and str(project.get("active_stage") or "") == "completed"
            else None
        )

        def head_content(row: Mapping[str, Any] | None) -> dict[str, Any]:
            if row is None:
                return {}
            return _object(
                row.get("head_content_json")
                if "head_content_json" in row
                else row.get("content_json")
            )

        return derive_review_state(
            draft_revision_id=draft_revision_id,
            draft_content=head_content(draft),
            review_revision_id=review_revision_id,
            review_content=head_content(review),
            decisions=decisions,
            hard_checks=[],
            completion_source=completion_source,
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
        review_state = await self._review_state(
            project_id=project_id,
            project=project,
            heads=heads,
        )
        stage = derive_stage(
            source_kind=source_kind,
            head_roles=head_by_role,
            head_contents=head_contents,
            has_current_finalization=(
                review_state.get("completionSource") == "user"
            ),
            legacy_completed=(
                review_state.get("completionSource") == "legacyAgentVerdict"
            ),
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
                "nextActions": next_actions(
                    stage,
                    head_contents=head_contents,
                    review_state=review_state,
                ),
                "review": review_state,
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
        accepted = await self._db.fetch_one(
            "SELECT id FROM screenplay_acceptance_events "
            "WHERE project_id = ? AND revision_id = ? LIMIT 1",
            [str(row["project_id"]), revision_id],
        )
        status = (
            "current"
            if any(str(item["revision_id"]) == revision_id for item in heads)
            else "historical" if accepted is not None
            else "candidate"
        )
        view = {
            **_revision_summary(row),
            "projectId": str(row["project_id"]),
            "schemaVersion": int(row.get("schema_version") or 1),
            "createdBy": _revision_creator(row.get("created_by")),
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
            "status": status,
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

    async def get_latest_review_for_draft(
        self,
        *,
        project_id: str,
        draft_revision_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT r.id FROM screenplay_revisions AS r "
            "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
            "JOIN screenplay_revision_inputs AS i ON i.revision_id = r.id "
            "WHERE r.project_id = ? AND d.role = 'review' "
            "AND i.input_role = 'screenplayDraft' "
            "AND i.input_revision_id = ? "
            "ORDER BY r.revision_no DESC LIMIT 1",
            [project_id, draft_revision_id],
        )
        if row is None:
            return None
        return await self.get_revision(
            str(row["id"]),
            include_content=True,
        )

def _revision_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "deliverableId": str(row["deliverable_id"]),
        "role": str(row["role"]),
        "revisionNo": int(row.get("revision_no") or 1),
        "parentRevisionId": row.get("parent_revision_id"),
        "contentDigest": str(row.get("content_digest") or ""),
        "summary": _object(row.get("summary_json")),
        "agentTaskId": row.get("agent_task_id"),
        "rootRunId": row.get("root_run_id"),
        "finalizingRunId": row.get("finalizing_run_id"),
        "createdAt": row.get("create_time"),
    }


def _revision_creator(value: object) -> str:
    return "agent" if str(value or "") in {
        "agent",
        "screenplay_agent_task",
    } else "user"


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
