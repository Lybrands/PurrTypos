"""Atomic product Turn projection for replacement Screenplay Roots."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from agents.screenplay.contracts import SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE
from agents.screenplay.profile import SCREENPLAY_REPLACEMENT_PROFILE_ID
from agents.shared.implementation import REPLACEMENT_IMPLEMENTATION_ID
from exceptions import AppError, NotFoundError
from infrastructure.persistence.run_execution_store import now_ms
from purra.contracts import AgentRunRequest, AgentRunResult, RunCreateParams, RunStatus
from purra.errors import ContractViolationError, RunCommitProjectionError
from purra.json_values import canonical_json_digest, thaw_json_mapping
from purra.ports import RunCommit


SCREENPLAY_REPLACEMENT_ROOT_BINDING = (
    "purrtypos.screenplay.conversation_turn.v1"
)
_LEASE_MS = 30_000


class ScreenplayReplacementRootProjectionError(RunCommitProjectionError):
    default_code = "screenplay_replacement_root_projection_failed"


class ScreenplayReplacementConversationStore:
    """Persist replacement Turn admission without calling legacy repositories."""

    def __init__(self, db, *, owner_id: str) -> None:
        self._db = db
        self.owner_id = _required(owner_id, "Screenplay replacement owner")

    async def find_command(self, *, project_id: str, command_id: str):
        row = await self._db.fetch_one(
            "SELECT *, planner_run_id AS root_run_id FROM screenplay_agent_turns "
            "WHERE project_id = ? AND command_id = ?",
            [_required(project_id, "project id"), _required(command_id, "command id")],
        )
        return _turn_view(row) if row is not None else None

    async def reserve_resume(
        self,
        *,
        operation_id: str,
        command_id: str,
        expected_operation_revision: int,
        runtime_binding: Mapping[str, Any],
        turn_id: str,
        source_run_id: str,
        project_id: str,
        session_id: int,
    ) -> dict[str, Any]:
        """Reserve one continuation dispatch and replay it by request identity."""

        operation = _required(operation_id, "Operation id")
        command = _required(command_id, "resume command id")
        turn = _required(turn_id, "Turn id")
        source = _required(source_run_id, "source Root id")
        project = _required(project_id, "project id")
        request_digest = canonical_json_digest({
            "operationId": operation,
            "expectedOperationRevision": int(expected_operation_revision),
            "runtimeBinding": dict(runtime_binding),
        })
        identity_digest = canonical_json_digest({
            "commandId": command,
            "operationId": operation,
            "turnId": turn,
            "sourceRootRunId": source,
            "sessionId": int(session_id),
            "projectId": project,
            "requestDigest": request_digest,
        })
        current = now_ms()
        async with self._db.transaction(cancellation_linearizable=True):
            existing = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_operation_commands "
                "WHERE command_id = ?",
                [command],
            )
            if existing is not None:
                if (
                    str(existing.get("operation_id") or "") != operation
                    or str(existing.get("command_type") or "") != "resume"
                    or str(existing.get("request_digest") or "") != request_digest
                ):
                    raise AppError("同一个恢复命令对应了不同请求", 409)
                status = str(existing.get("continuation_status") or "reserved")
                lease_expires = int(
                    existing.get("continuation_lease_expires_at_ms") or 0
                )
                if status == "reserved" or (
                    status == "starting" and lease_expires <= current
                ):
                    await self._require_resume_scope(
                        operation_id=operation,
                        turn_id=turn,
                        source_run_id=source,
                        project_id=project,
                        session_id=session_id,
                        expected_operation_revision=expected_operation_revision,
                    )
                    await self._db.execute(
                        "UPDATE screenplay_agent_operation_commands SET "
                        "continuation_status = 'starting', "
                        "continuation_owner_id = ?, "
                        "continuation_lease_expires_at_ms = ?, "
                        "continuation_epoch = continuation_epoch + 1 "
                        "WHERE command_id = ? AND (continuation_status = 'reserved' "
                        "OR (continuation_status = 'starting' AND "
                        "continuation_lease_expires_at_ms <= ?))",
                        [self.owner_id, current + _LEASE_MS, command, current],
                    )
                    if await _changes(self._db) == 1:
                        refreshed = await self._db.fetch_one(
                            "SELECT * FROM screenplay_agent_operation_commands "
                            "WHERE command_id = ?",
                            [command],
                        )
                        assert refreshed is not None
                        return await self._resume_receipt(
                            refreshed, dispatch_required=True
                        )
                return await self._resume_receipt(existing, dispatch_required=False)
            equivalent = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_operation_commands "
                "WHERE operation_id = ? AND command_type = 'resume' "
                "AND request_digest = ?",
                [operation, request_digest],
            )
            if equivalent is not None:
                return await self._resume_receipt(
                    equivalent, dispatch_required=False
                )
            await self._require_resume_scope(
                operation_id=operation,
                turn_id=turn,
                source_run_id=source,
                project_id=project,
                session_id=session_id,
                expected_operation_revision=expected_operation_revision,
            )
            capability_digest = _required(
                runtime_binding.get("capabilityDigest"),
                "capability digest",
            )
            receipt = {
                "operationId": operation,
                "turnId": turn,
                "status": "paused",
                "revision": int(expected_operation_revision),
                "capabilitySnapshotDigest": capability_digest,
                "continuationCommand": command,
                "continuationRootRunId": None,
            }
            await self._db.execute(
                "INSERT INTO screenplay_agent_operation_commands "
                "(command_id, operation_id, command_type, request_digest, "
                "receipt_id, response_json, continuation_status, "
                "continuation_owner_id, continuation_lease_expires_at_ms, "
                "continuation_epoch, continuation_identity_digest, "
                "continuation_source_root_run_id, continuation_turn_id, "
                "continuation_session_id, continuation_project_id) "
                "VALUES (?, ?, 'resume', ?, ?, ?, 'starting', ?, ?, 1, ?, ?, ?, ?, ?)",
                [
                    command, operation, request_digest, command, _dump(receipt),
                    self.owner_id, current + _LEASE_MS, identity_digest, source,
                    turn, int(session_id), project,
                ],
            )
            return {**receipt, "dispatchRequired": True}

    async def load_resume_reservation(self, command_id: str):
        row = await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_operation_commands "
            "WHERE command_id = ? AND command_type = 'resume'",
            [_required(command_id, "resume command id")],
        )
        return dict(row) if row is not None else None

    async def release_resume_reservation(self, command_id: str) -> None:
        await self._db.execute(
            "UPDATE screenplay_agent_operation_commands SET "
            "continuation_status = 'reserved', continuation_owner_id = NULL, "
            "continuation_lease_expires_at_ms = NULL WHERE command_id = ? "
            "AND command_type = 'resume' AND continuation_status = 'starting' "
            "AND continuation_owner_id = ?",
            [_required(command_id, "resume command id"), self.owner_id],
        )

    async def _resume_receipt(
        self, row: Mapping[str, Any], *, dispatch_required: bool
    ) -> dict[str, Any]:
        response = _object(row.get("response_json"))
        operation = await self._db.fetch_one(
            "SELECT status, revision, turn_id FROM screenplay_agent_operations "
            "WHERE id = ?",
            [row["operation_id"]],
        )
        if operation is not None:
            response.update({
                "operationId": str(row["operation_id"]),
                "turnId": str(operation["turn_id"]),
                "status": str(operation["status"]),
                "revision": int(operation["revision"]),
                "continuationCommand": str(row["command_id"]),
                "continuationRootRunId": (
                    str(row.get("continuation_root_run_id") or "") or None
                ),
            })
        response["dispatchRequired"] = bool(dispatch_required)
        return response

    async def _require_resume_scope(
        self,
        *,
        operation_id: str,
        turn_id: str,
        source_run_id: str,
        project_id: str,
        session_id: int,
        expected_operation_revision: int,
    ) -> None:
        operation = await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_operations WHERE id = ?",
            [operation_id],
        )
        turn = await self._db.fetch_one(
            "SELECT *, planner_run_id AS root_run_id "
            "FROM screenplay_agent_turns WHERE id = ?",
            [turn_id],
        )
        source = await self._db.fetch_one(
            "SELECT status FROM ai_agent_runs WHERE id = ?",
            [source_run_id],
        )
        if (
            operation is None
            or turn is None
            or str(operation.get("turn_id") or "") != turn_id
            or str(operation.get("project_id") or "") != project_id
            or int(operation.get("session_id") or 0) != int(session_id)
            or str(operation.get("status") or "") != "paused"
            or int(operation.get("revision") or 0)
            != int(expected_operation_revision)
            or operation.get("cancel_requested_at_ms") is not None
            or str(turn.get("status") or "") != "paused"
            or str(turn.get("root_run_id") or "") != source_run_id
            or turn.get("cancel_requested_at_ms") is not None
            or source != {"status": RunStatus.CANCELED.value}
        ):
            raise AppError("剧本恢复状态已发生变化", 409)

    async def admit(
        self,
        *,
        request: AgentRunRequest,
        command_id: str,
        user_content: str,
        stage_command: Mapping[str, Any],
    ) -> dict[str, Any]:
        domain = thaw_json_mapping(request.domain_context.payload)
        metadata = thaw_json_mapping(request.metadata)
        project_id = _required(domain.get("projectId"), "project id")
        turn_id = _required(domain.get("turnId"), "turn id")
        command = _required(command_id, "command id")
        content = _required(user_content, "user content")
        session_id = request.session_id
        if type(session_id) is not int or session_id < 1:
            raise ValueError("Screenplay replacement session is invalid")
        if domain.get("commandId") != command:
            raise ValueError("Screenplay replacement command identity conflicts")
        recipe = metadata.get("screenplayRecipe")
        runtime_binding = metadata.get("runtimeBinding")
        if not isinstance(recipe, Mapping) or not isinstance(runtime_binding, Mapping):
            raise ValueError("Screenplay replacement admission metadata is incomplete")
        recipe = dict(recipe)
        runtime_binding = dict(runtime_binding)
        target_role = _required(recipe.get("targetRole"), "target role")
        operation_id = "spop_" + uuid.uuid4().hex
        intent = {
            "action": str(stage_command.get("action") or ""),
            "instruction": content,
            "scope": dict(stage_command.get("scope") or {}),
            "constraints": [],
            "preserve": [],
            "requestedDeliverable": target_role,
        }
        requirements = {
            "schemaVersion": 1,
            "implementation": "screenplay.purra-native.v1",
            "hostRecipe": recipe,
            "runtimeBinding": runtime_binding,
            "capabilitySnapshot": request.model.capability_snapshot.to_mapping(
                include_digest=True
            ),
        }
        runtime_profile = {
            "provider": request.model.provider,
            "model": request.model.model,
            "contextWindow": request.context_window,
            "runtimeBinding": runtime_binding,
        }
        async with self._db.transaction(cancellation_linearizable=True):
            existing = await self._db.fetch_one(
                "SELECT *, planner_run_id AS root_run_id "
                "FROM screenplay_agent_turns "
                "WHERE project_id = ? AND command_id = ?",
                [project_id, command],
            )
            if existing is not None:
                if any((
                    int(existing["session_id"]) != session_id,
                    str(existing["user_content"]) != content,
                    _object(existing.get("stage_command_json")) != dict(stage_command),
                )):
                    raise AppError("同一个对话命令对应了不同请求", 409)
                return _turn_view(existing)
            session = await self._db.fetch_one(
                "SELECT s.closed, p.status AS project_status "
                "FROM ai_sessions AS s JOIN screenplay_projects AS p "
                "ON p.id = s.screenplay_project_id "
                "WHERE s.id = ? AND s.screenplay_project_id = ? "
                "AND s.scope = 'screenplay'",
                [session_id, project_id],
            )
            if session is None:
                raise NotFoundError("剧本对话不存在")
            if bool(session.get("closed")) or session.get("project_status") == "archived":
                raise AppError("当前剧本项目不能继续对话", 409)
            active = await self._db.fetch_one(
                "SELECT id FROM screenplay_agent_turns WHERE project_id = ? "
                "AND status IN ('queued', 'planning', 'running') LIMIT 1",
                [project_id],
            )
            if active is not None:
                raise AppError("当前剧本项目仍在处理上一条消息", 409)
            await self._db.execute(
                "INSERT INTO screenplay_agent_turns "
                "(id, project_id, session_id, command_id, status, user_content, "
                "stage_command_json, intent_json, runtime_profile_json, "
                "implementation_id, operation_id, target_role) "
                "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)",
                [
                    turn_id, project_id, session_id, command, content,
                    _dump(stage_command), _dump(intent), _dump(runtime_profile),
                    REPLACEMENT_IMPLEMENTATION_ID,
                    operation_id, target_role,
                ],
            )
            await self._db.execute(
                "INSERT INTO screenplay_agent_operations "
                "(id, turn_id, project_id, session_id, status, target_role, "
                "requirements_json, manifest_digest) "
                "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)",
                [
                    operation_id, turn_id, project_id, session_id, target_role,
                    _dump(requirements), canonical_json_digest(recipe),
                ],
            )
            return _turn_view(await self._require_turn(turn_id))

    async def admit_ordinary(
        self,
        *,
        request: AgentRunRequest,
        command_id: str,
        user_content: str,
    ) -> dict[str, Any]:
        domain = thaw_json_mapping(request.domain_context.payload)
        metadata = thaw_json_mapping(request.metadata)
        project_id = _required(domain.get("projectId"), "project id")
        turn_id = _required(domain.get("turnId"), "turn id")
        command = _required(command_id, "command id")
        content = _required(user_content, "user content")
        session_id = request.session_id
        runtime_binding = metadata.get("runtimeBinding")
        if (
            type(session_id) is not int
            or session_id < 1
            or domain.get("commandId") != command
            or metadata.get("interactionKind") != "ordinary"
            or not isinstance(runtime_binding, Mapping)
        ):
            raise ValueError("Screenplay ordinary admission is invalid")
        runtime_profile = {
            "provider": request.model.provider,
            "model": request.model.model,
            "contextWindow": request.context_window,
            "runtimeBinding": dict(runtime_binding),
        }
        async with self._db.transaction(cancellation_linearizable=True):
            existing = await self._db.fetch_one(
                "SELECT *, planner_run_id AS root_run_id "
                "FROM screenplay_agent_turns "
                "WHERE project_id = ? AND command_id = ?",
                [project_id, command],
            )
            if existing is not None:
                if any((
                    int(existing["session_id"]) != session_id,
                    str(existing["user_content"]) != content,
                    existing.get("stage_command_json") is not None,
                    existing.get("operation_id") is not None,
                )):
                    raise AppError("同一个对话命令对应了不同请求", 409)
                return _turn_view(existing)
            session = await self._db.fetch_one(
                "SELECT s.closed, p.status AS project_status "
                "FROM ai_sessions AS s JOIN screenplay_projects AS p "
                "ON p.id = s.screenplay_project_id WHERE s.id = ? "
                "AND s.screenplay_project_id = ? AND s.scope = 'screenplay'",
                [session_id, project_id],
            )
            if session is None:
                raise NotFoundError("剧本对话不存在")
            if bool(session.get("closed")) or session.get("project_status") == "archived":
                raise AppError("当前剧本项目不能继续对话", 409)
            active = await self._db.fetch_one(
                "SELECT id FROM screenplay_agent_turns WHERE project_id = ? "
                "AND status IN ('queued', 'planning', 'running') LIMIT 1",
                [project_id],
            )
            if active is not None:
                raise AppError("当前剧本项目仍在处理上一条消息", 409)
            await self._db.execute(
                "INSERT INTO screenplay_agent_turns "
                "(id, project_id, session_id, command_id, status, user_content, "
                "stage_command_json, intent_json, runtime_profile_json, "
                "implementation_id) "
                "VALUES (?, ?, ?, ?, 'queued', ?, NULL, ?, ?, ?)",
                [
                    turn_id, project_id, session_id, command, content,
                    _dump({"action": "answer", "requestedDeliverable": None}),
                    _dump(runtime_profile),
                    REPLACEMENT_IMPLEMENTATION_ID,
                ],
            )
            return _turn_view(await self._require_turn(turn_id))

    async def load_turn(self, turn_id: str):
        row = await self._db.fetch_one(
            "SELECT *, planner_run_id AS root_run_id "
            "FROM screenplay_agent_turns WHERE id = ?",
            [_required(turn_id, "turn id")],
        )
        return _turn_view(row) if row is not None else None

    async def load_execution(self, turn_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT t.*, t.planner_run_id AS root_run_id, o.requirements_json "
            "FROM screenplay_agent_turns AS t "
            "JOIN screenplay_agent_operations AS o ON o.turn_id = t.id "
            "WHERE t.id = ?",
            [_required(turn_id, "turn id")],
        )
        if row is None:
            return None
        return {
            "turn": _turn_view(row),
            "requirements": _object(row.get("requirements_json")),
        }

    async def request_cancel(
        self,
        turn_id: str,
        *,
        command_id: str,
    ) -> dict[str, Any]:
        """Fence product state before asking PurrA to cancel the owning Root."""

        identity = _required(turn_id, "turn id")
        command = _required(command_id, "cancel command id")
        request_digest = canonical_json_digest({"turnId": identity})
        async with self._db.transaction(cancellation_linearizable=True):
            replay = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_cancel_commands "
                "WHERE command_id = ?",
                [command],
            )
            if replay is not None:
                if (
                    str(replay.get("turn_id") or "") != identity
                    or str(replay.get("request_digest") or "") != request_digest
                ):
                    raise AppError("同一个取消命令对应了不同 Turn", 409)
                return await self._cancel_view(identity)

            turn = await self._require_turn(identity)
            operation = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_operations WHERE turn_id = ?",
                [identity],
            )
            if str(turn.get("status") or "") in {
                "completed", "failed", "canceled",
            }:
                response = await self._cancel_view(identity)
                receipt_id = (
                    str(turn.get("cancel_receipt_id") or "").strip()
                    or "spacancel_" + uuid.uuid4().hex
                )
                await self._db.execute(
                    "INSERT INTO screenplay_agent_cancel_commands "
                    "(command_id, turn_id, operation_id, request_digest, "
                    "receipt_id, response_json) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        command, identity, (operation or {}).get("id"), request_digest,
                        receipt_id, _dump(response),
                    ],
                )
                return response
            requested_at = int(
                turn.get("cancel_requested_at_ms") or now_ms()
            )
            receipt_id = str(turn.get("cancel_receipt_id") or "").strip()
            if not receipt_id:
                receipt_id = "spacancel_" + uuid.uuid4().hex
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET cancel_requested_at_ms = "
                "COALESCE(cancel_requested_at_ms, ?), cancel_receipt_id = "
                "COALESCE(cancel_receipt_id, ?), update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                [requested_at, receipt_id, identity],
            )
            if operation is not None:
                await self._db.execute(
                    "UPDATE screenplay_agent_operations SET cancel_requested_at_ms = "
                    "COALESCE(cancel_requested_at_ms, ?), cancel_receipt_id = "
                    "COALESCE(cancel_receipt_id, ?), revision = revision + CASE "
                    "WHEN cancel_requested_at_ms IS NULL THEN 1 ELSE 0 END, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                    [requested_at, receipt_id, operation["id"]],
                )
            task_id = str((operation or {}).get("long_task_id") or "").strip()
            root_run_id = str(turn.get("root_run_id") or "").strip()
            if not task_id and root_run_id:
                task_id = await _task_id_for_root(self._db, root_run_id) or ""
            if task_id:
                await _request_task_cancel(self._db, task_id, requested_at)

            # Before a Root exists there is no framework execution to drain.
            # A paused Root is already terminal, so its retained checkpoint can
            # also be closed synchronously after the product fence is durable.
            status = str(turn.get("status") or "")
            if status in {"queued", "planning", "paused"} and (
                not root_run_id or status == "paused"
            ):
                if operation is None:
                    await self._db.execute(
                        "UPDATE screenplay_agent_turns SET status = 'canceled', "
                        "assistant_content = '', execution_owner_id = NULL, "
                        "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                        "update_time = CURRENT_TIMESTAMP WHERE id = ? "
                        "AND cancel_requested_at_ms IS NOT NULL",
                        [identity],
                    )
                else:
                    await _settle_product_cancel(
                        self._db,
                        turn_id=identity,
                        operation_id=str(operation["id"]),
                        task_id=task_id or None,
                    )

            response = await self._cancel_view(identity)
            await self._db.execute(
                "INSERT INTO screenplay_agent_cancel_commands "
                "(command_id, turn_id, operation_id, request_digest, "
                "receipt_id, response_json) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    command, identity, (operation or {}).get("id"), request_digest,
                    receipt_id, _dump(response),
                ],
            )
            return response

    async def cancel_view(self, turn_id: str) -> dict[str, Any]:
        return await self._cancel_view(_required(turn_id, "turn id"))

    async def _cancel_view(self, turn_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT t.status, t.planner_run_id AS root_run_id, "
            "t.cancel_requested_at_ms, "
            "t.cancel_receipt_id, o.id AS operation_id, "
            "o.status AS operation_status, o.revision AS operation_revision "
            "FROM screenplay_agent_turns AS t "
            "LEFT JOIN screenplay_agent_operations AS o ON o.turn_id = t.id "
            "WHERE t.id = ?",
            [turn_id],
        )
        if row is None:
            raise NotFoundError("剧本 Agent Turn 不存在")
        return {
            "turnId": turn_id,
            "operationId": str(row.get("operation_id") or "") or None,
            "receiptId": str(row.get("cancel_receipt_id") or "") or None,
            "status": str(row["status"]),
            "operationStatus": str(row.get("operation_status") or "") or None,
            "operationRevision": (
                int(row["operation_revision"])
                if row.get("operation_revision") is not None else None
            ),
            "rootRunId": str(row.get("root_run_id") or "") or None,
            "cancelRequestedAtMs": row.get("cancel_requested_at_ms"),
        }

    async def _require_turn(self, turn_id: str):
        row = await self._db.fetch_one(
            "SELECT *, planner_run_id AS root_run_id "
            "FROM screenplay_agent_turns WHERE id = ?", [turn_id]
        )
        if row is None:
            raise NotFoundError("剧本 Agent Turn 不存在")
        return row


class ScreenplayReplacementTurnLifecycle:
    def __init__(self, db, store, *, turn_id: str) -> None:
        self._db = db
        self._store = store
        self._turn_id = _required(turn_id, "turn id")
        self._owner_id = f"{store.owner_id}:turn:{uuid.uuid4().hex}"
        self._attempt: int | None = None

    async def validate(self) -> None:
        turn = await self._store.load_turn(self._turn_id)
        if turn is None or turn["status"] != "queued":
            raise ValueError("Screenplay replacement Turn is not startable")
        self._attempt = int(turn["attempt"]) + 1

    def run_binding_attributes(self) -> dict[str, object]:
        if self._attempt is None:
            raise ValueError("Screenplay replacement lifecycle is not validated")
        return {
            "turnExecutionOwner": self._owner_id,
            "turnAttempt": self._attempt,
        }

    async def before_submit(self) -> None:
        if self._attempt is None:
            raise ValueError("Screenplay replacement lifecycle is not validated")
        current = now_ms()
        async with self._db.transaction(cancellation_linearizable=True):
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = 'planning', "
                "execution_owner_id = ?, lease_expires_at_ms = ?, "
                "heartbeat_at_ms = ?, attempt = ?, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'queued' AND attempt = ? "
                "AND cancel_requested_at_ms IS NULL",
                [
                    self._owner_id, current + _LEASE_MS, current,
                    self._attempt, self._turn_id, self._attempt - 1,
                ],
            )
            if await _changes(self._db) != 1:
                raise ValueError("Screenplay replacement Turn claim was lost")

    async def on_run_started(self, run_id: str) -> None:
        turn = await self._store.load_turn(self._turn_id)
        if (
            turn is None
            or turn["status"] != "running"
            or turn.get("rootRunId") != run_id
        ):
            raise ValueError("Screenplay replacement Root binding is missing")

    async def on_run_finished(self, result: AgentRunResult) -> None:
        turn = await self._store.load_turn(self._turn_id)
        expected = {
            RunStatus.DONE: {"completed"},
            RunStatus.FAILED: {"failed", "paused"},
            # A canceled framework Run is a product cancellation only when a
            # durable user cancellation fence exists. Otherwise it is a
            # failure; a paused LongTask remains recoverable.
            RunStatus.CANCELED: {"canceled", "failed", "paused"},
            RunStatus.BLOCKED: {"failed", "paused"},
        }.get(result.status)
        if turn is None or expected is None:
            raise ValueError("Screenplay replacement terminal status is invalid")
        if turn["status"] not in expected:
            raise ValueError("Screenplay replacement terminal projection is missing")

    async def on_start_failed(self, code: str):
        error = {
            "code": str(code or "screenplay_replacement_root_start_failed"),
            "message": "剧本任务启动失败。",
        }
        async with self._db.transaction(cancellation_linearizable=True):
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = 'failed', error_json = ?, "
                "execution_owner_id = NULL, lease_expires_at_ms = NULL, "
                "heartbeat_at_ms = NULL, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ? AND ((status = 'queued' AND attempt = ?) OR "
                "(status = 'planning' AND execution_owner_id = ? AND attempt = ?))",
                [
                    _dump(error), self._turn_id, int(self._attempt or 0) - 1,
                    self._owner_id, int(self._attempt or 0),
                ],
            )
            if await _changes(self._db) == 1:
                await self._db.execute(
                    "UPDATE screenplay_agent_operations SET status = 'failed', "
                    "error_json = ?, revision = revision + 1, "
                    "update_time = CURRENT_TIMESTAMP WHERE turn_id = ? "
                    "AND status = 'queued'",
                    [_dump(error), self._turn_id],
                )
        return None


class ScreenplayReplacementRunBeginProjector:
    def __init__(self, db) -> None:
        self._db = db

    async def project(self, run_id: str, params: RunCreateParams) -> None:
        binding = params.binding
        if binding is None or binding.namespace != SCREENPLAY_REPLACEMENT_ROOT_BINDING:
            return None
        attributes = thaw_json_mapping(binding.attributes)
        if (
            attributes.get("agentProfile") != SCREENPLAY_REPLACEMENT_PROFILE_ID
            or attributes.get("domainNamespace")
            != SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE
        ):
            raise ContractViolationError("Screenplay replacement Root profile conflicts")
        owner = _required(attributes.get("turnExecutionOwner"), "turn owner")
        attempt = attributes.get("turnAttempt")
        if type(attempt) is not int or attempt < 1:
            raise ContractViolationError("Screenplay replacement Turn attempt is invalid")
        continuation_of = str(attributes.get("continuationOf") or "").strip()
        operation_id = str(attributes.get("operationId") or "").strip()
        ordinary = attributes.get("interactionKind") == "ordinary"
        if ordinary and (continuation_of or operation_id):
            raise ContractViolationError(
                "Screenplay ordinary Root cannot be a continuation"
            )
        if bool(continuation_of) != bool(operation_id):
            raise ContractViolationError(
                "Screenplay replacement continuation identity is incomplete"
            )
        if continuation_of:
            await self._project_continuation(
                run_id,
                params,
                owner=owner,
                attempt=attempt,
                source_run_id=continuation_of,
                operation_id=operation_id,
            )
            return None
        await self._db.execute(
            "UPDATE screenplay_agent_turns SET status = 'running', "
            "planner_run_id = ?, execution_owner_id = NULL, "
            "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? AND project_id = ? "
            "AND session_id = ? AND command_id = ? AND status = 'planning' "
            "AND planner_run_id IS NULL AND execution_owner_id = ? "
            "AND attempt = ? AND lease_expires_at_ms > ? "
            "AND cancel_requested_at_ms IS NULL",
            [
                run_id, str(params.turn_id or ""), str(binding.aggregate_id or ""),
                int(params.session_id or 0), str(binding.command_id or ""),
                owner, attempt, now_ms(),
            ],
        )
        if await _changes(self._db) != 1:
            raise ContractViolationError("Screenplay replacement Turn claim was lost")
        if ordinary:
            turn = await self._db.fetch_one(
                "SELECT operation_id FROM screenplay_agent_turns WHERE id = ?",
                [str(params.turn_id or "")],
            )
            if turn is None or turn.get("operation_id") is not None:
                raise ContractViolationError(
                    "Screenplay ordinary Turn owns a formal Operation"
                )
            return None
        await self._db.execute(
            "UPDATE screenplay_agent_operations SET status = 'running', "
            "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
            "WHERE turn_id = ? AND status = 'queued'",
            [str(params.turn_id or "")],
        )
        if await _changes(self._db) != 1:
            raise ContractViolationError("Screenplay replacement Operation is unavailable")
        return None

    async def _project_continuation(
        self,
        run_id: str,
        params: RunCreateParams,
        *,
        owner: str,
        attempt: int,
        source_run_id: str,
        operation_id: str,
    ) -> None:
        binding = params.binding
        assert binding is not None
        command_id = _required(binding.command_id, "continuation command")
        attributes = thaw_json_mapping(binding.attributes)
        continuation_owner = _required(
            attributes.get("continuationOwner"), "continuation owner"
        )
        identity_digest = _required(
            attributes.get("continuationIdentityDigest"),
            "continuation identity digest",
        )
        continuation_epoch = attributes.get("continuationEpoch")
        if type(continuation_epoch) is not int or continuation_epoch < 1:
            raise ContractViolationError(
                "Screenplay replacement continuation epoch is invalid"
            )
        source = await self._db.fetch_one(
            "SELECT status FROM ai_agent_runs WHERE id = ?",
            [source_run_id],
        )
        if source is None or source.get("status") != RunStatus.CANCELED.value:
            raise ContractViolationError(
                "Screenplay replacement continuation source is not canceled"
            )
        reservation = await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_operation_commands "
            "WHERE command_id = ? AND command_type = 'resume'",
            [command_id],
        )
        if (
            reservation is None
            or str(reservation.get("operation_id") or "") != operation_id
            or str(reservation.get("continuation_status") or "") != "starting"
            or str(reservation.get("continuation_owner_id") or "")
            != continuation_owner
            or int(reservation.get("continuation_epoch") or 0)
            != continuation_epoch
            or str(reservation.get("continuation_identity_digest") or "")
            != identity_digest
            or str(reservation.get("continuation_source_root_run_id") or "")
            != source_run_id
            or str(reservation.get("continuation_turn_id") or "")
            != str(params.turn_id or "")
            or int(reservation.get("continuation_session_id") or 0)
            != int(params.session_id or 0)
            or str(reservation.get("continuation_project_id") or "")
            != str(binding.aggregate_id or "")
        ):
            raise ContractViolationError(
                "Screenplay replacement continuation reservation was lost"
            )
        await self._db.execute(
            "UPDATE screenplay_agent_turns SET status = 'running', "
            "planner_run_id = ?, execution_owner_id = NULL, "
            "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? AND project_id = ? "
            "AND session_id = ? AND status = 'planning' "
            "AND planner_run_id = ? AND operation_id = ? "
            "AND execution_owner_id = ? AND attempt = ? "
            "AND lease_expires_at_ms > ? AND cancel_requested_at_ms IS NULL",
            [
                run_id, str(params.turn_id or ""),
                str(params.binding.aggregate_id or ""),
                int(params.session_id or 0), source_run_id, operation_id,
                owner, attempt, now_ms(),
            ],
        )
        if await _changes(self._db) != 1:
            raise ContractViolationError(
                "Screenplay replacement continuation claim was lost"
            )
        await self._db.execute(
            "UPDATE screenplay_agent_operations SET status = 'running', "
            "revision = revision + 1, error_json = NULL, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? AND turn_id = ? "
            "AND status = 'paused' AND cancel_requested_at_ms IS NULL",
            [operation_id, str(params.turn_id or "")],
        )
        if await _changes(self._db) != 1:
            raise ContractViolationError(
                "Screenplay replacement continuation Operation is unavailable"
            )
        operation = await self._db.fetch_one(
            "SELECT status, revision, turn_id FROM screenplay_agent_operations "
            "WHERE id = ?",
            [operation_id],
        )
        assert operation is not None
        response = _object(reservation.get("response_json"))
        response.update({
            "operationId": operation_id,
            "turnId": str(operation["turn_id"]),
            "status": str(operation["status"]),
            "revision": int(operation["revision"]),
            "continuationCommand": command_id,
            "continuationRootRunId": run_id,
        })
        await self._db.execute(
            "UPDATE screenplay_agent_operation_commands SET "
            "continuation_status = 'bound', continuation_root_run_id = ?, "
            "continuation_lease_expires_at_ms = NULL, response_json = ? "
            "WHERE command_id = ? AND continuation_status = 'starting' "
            "AND continuation_owner_id = ? AND continuation_epoch = ? "
            "AND continuation_identity_digest = ?",
            [
                run_id, _dump(response), command_id, continuation_owner,
                continuation_epoch, identity_digest,
            ],
        )
        if await _changes(self._db) != 1:
            raise ContractViolationError(
                "Screenplay replacement continuation reservation was lost"
            )


class ScreenplayReplacementRunCommitProjector:
    def __init__(self, db) -> None:
        self._db = db

    async def project(self, run_id: str, commit: RunCommit) -> None:
        if commit.terminal_status is None:
            return None
        row = await self._db.fetch_one(
            "SELECT session_id, binding_namespace, binding_aggregate_id, "
            "binding_command_id, binding_attributes_json "
            "FROM ai_agent_runs WHERE id = ?",
            [run_id],
        )
        if row is None or row.get("binding_namespace") != SCREENPLAY_REPLACEMENT_ROOT_BINDING:
            return None
        try:
            turns = await self._db.fetch_all(
                "SELECT *, planner_run_id AS root_run_id "
                "FROM screenplay_agent_turns WHERE planner_run_id = ?",
                [run_id],
            )
            if len(turns) != 1:
                raise ValueError("Screenplay replacement Root Turn is unavailable")
            turn = turns[0]
            run_attributes = _object(row.get("binding_attributes_json"))
            continuation_of = str(
                run_attributes.get("continuationOf") or ""
            ).strip()
            ordinary = run_attributes.get("interactionKind") == "ordinary"
            command_matches = (
                str(turn.get("command_id") or "")
                == str(row.get("binding_command_id") or "")
                if not continuation_of
                else (
                    str(run_attributes.get("operationId") or "")
                    == str(turn.get("operation_id") or "")
                    and bool(str(row.get("binding_command_id") or "").strip())
                )
            )
            if (
                turn.get("project_id") != row.get("binding_aggregate_id")
                or int(turn.get("session_id") or 0) != int(row.get("session_id") or 0)
                or not command_matches
                or turn.get("status") != "running"
            ):
                raise ValueError("Screenplay replacement Root identity conflicts")
            if ordinary:
                if continuation_of or turn.get("operation_id") is not None:
                    raise ValueError("Screenplay ordinary Root identity conflicts")
                await self._settle_ordinary(turn, commit)
                return None
            operation = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_operations WHERE turn_id = ?",
                [turn["id"]],
            )
            if operation is None or operation.get("status") != "running":
                raise ValueError("Screenplay replacement Operation is unavailable")
            if commit.terminal_status is RunStatus.DONE:
                await self._complete(run_id, turn, operation)
            else:
                await self._settle_terminal(turn, operation, commit)
            if continuation_of:
                await self._settle_resume_receipt(
                    run_id=run_id,
                    command_id=str(row.get("binding_command_id") or ""),
                )
        except ScreenplayReplacementRootProjectionError:
            raise
        except Exception as error:
            raise ScreenplayReplacementRootProjectionError(
                "Screenplay replacement product state could not be committed"
            ) from error
        return None

    async def _settle_ordinary(self, turn, commit: RunCommit) -> None:
        explicit_cancel = turn.get("cancel_requested_at_ms") is not None
        if commit.terminal_status is RunStatus.DONE:
            response = str(commit.final_response or "").strip()
            if not response:
                raise ValueError("Screenplay ordinary response is empty")
            status = "completed"
            error = None
        elif commit.terminal_status is RunStatus.CANCELED and explicit_cancel:
            response = ""
            status = "canceled"
            error = {"code": "screenplay_task_canceled", "message": "问答已取消。"}
        else:
            response = ""
            status = "failed"
            error = {
                "code": "screenplay_ordinary_failed",
                "message": str(commit.error or "剧本问答未完成。"),
            }
        await self._db.execute(
            "UPDATE screenplay_agent_turns SET status = ?, assistant_content = ?, "
            "error_json = ?, update_time = CURRENT_TIMESTAMP WHERE id = ? "
            "AND status = 'running' AND operation_id IS NULL",
            [status, response, _dump(error) if error else None, turn["id"]],
        )
        if await _changes(self._db) != 1:
            raise ValueError("Screenplay ordinary Turn settlement was lost")

    async def _settle_resume_receipt(
        self, *, run_id: str, command_id: str
    ) -> None:
        command = await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_operation_commands "
            "WHERE command_id = ? AND command_type = 'resume' "
            "AND continuation_root_run_id = ?",
            [command_id, run_id],
        )
        if command is None or command.get("continuation_status") != "bound":
            raise ValueError("Screenplay replacement resume receipt is unavailable")
        operation = await self._db.fetch_one(
            "SELECT status, revision, turn_id FROM screenplay_agent_operations "
            "WHERE id = ?",
            [command["operation_id"]],
        )
        if operation is None:
            raise ValueError("Screenplay replacement resume Operation is unavailable")
        response = _object(command.get("response_json"))
        response.update({
            "operationId": str(command["operation_id"]),
            "turnId": str(operation["turn_id"]),
            "status": str(operation["status"]),
            "revision": int(operation["revision"]),
            "continuationCommand": command_id,
            "continuationRootRunId": run_id,
        })
        await self._db.execute(
            "UPDATE screenplay_agent_operation_commands SET "
            "continuation_status = ?, continuation_owner_id = NULL, "
            "response_json = ? WHERE command_id = ? "
            "AND continuation_status = 'bound' AND continuation_root_run_id = ?",
            [str(operation["status"]), _dump(response), command_id, run_id],
        )
        if await _changes(self._db) != 1:
            raise ValueError("Screenplay replacement resume receipt settlement was lost")

    async def _complete(self, run_id, turn, operation) -> None:
        tasks = await _tasks_for_operation_root(
            self._db,
            operation=operation,
            run_id=run_id,
        )
        if len(tasks) != 1 or tasks[0].get("status") != "completed":
            raise ValueError("Screenplay replacement durable Task is incomplete")
        task = tasks[0]
        receipt = await self._db.fetch_one(
            "SELECT revision_id, operation_scope_id "
            "FROM screenplay_replacement_projection_receipts "
            "WHERE task_id = ?",
            [task["id"]],
        )
        if receipt is None:
            raise ValueError("Screenplay replacement Revision receipt is missing")
        revision = await self._db.fetch_one(
            "SELECT id FROM screenplay_revisions WHERE id = ? "
            "AND project_id = ? AND agent_task_id = ?",
            [receipt["revision_id"], turn["project_id"], task["id"]],
        )
        if revision is None:
            raise ValueError("Screenplay replacement Revision is unavailable")
        role = str(operation["target_role"])
        assistant_content = _completion_message(role)
        await self._db.execute(
            "UPDATE screenplay_agent_operations SET status = 'succeeded', "
            "long_task_id = ?, result_revision_id = ?, finalization_receipt_id = ?, "
            "usage_json = ?, "
            "error_json = NULL, revision = revision + 1, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
            [
                task["id"], revision["id"], receipt["operation_scope_id"],
                task.get("usage_json") or "{}", operation["id"],
            ],
        )
        if await _changes(self._db) != 1:
            raise ValueError("Screenplay replacement Operation settlement was lost")
        await self._db.execute(
            "UPDATE screenplay_agent_turns SET status = 'completed', "
            "task_id = ?, target_role = ?, result_revision_id = ?, "
            "assistant_content = ?, error_json = NULL, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
            [task["id"], role, revision["id"], assistant_content, turn["id"]],
        )
        if await _changes(self._db) != 1:
            raise ValueError("Screenplay replacement Turn settlement was lost")

    async def _settle_terminal(self, turn, operation, commit) -> None:
        tasks = await _tasks_for_operation_root(
            self._db,
            operation=operation,
            run_id=turn["root_run_id"],
        )
        if len(tasks) > 1:
            raise ValueError("Screenplay replacement Root owns multiple Tasks")
        task = tasks[0] if tasks else None
        explicit_cancel = turn.get("cancel_requested_at_ms") is not None
        if task is not None and task.get("status") == "paused":
            status = "paused"
        elif commit.terminal_status is RunStatus.CANCELED and explicit_cancel:
            status = "canceled"
        else:
            status = "failed"
        error = {
            "code": (
                "screenplay_task_paused"
                if status == "paused"
                else "screenplay_task_canceled"
                if status == "canceled"
                else "screenplay_task_failed"
            ),
            "message": str(commit.error or "剧本任务未完成。"),
        }
        await self._db.execute(
            "UPDATE screenplay_agent_operations SET status = ?, long_task_id = ?, "
            "usage_json = ?, error_json = ?, revision = revision + 1, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? AND status = 'running'",
            [
                status, (task or {}).get("id"), (task or {}).get("usage_json") or "{}",
                _dump(error), operation["id"],
            ],
        )
        if await _changes(self._db) != 1:
            raise ValueError("Screenplay replacement Operation terminal settlement was lost")
        await self._db.execute(
            "UPDATE screenplay_agent_turns SET status = ?, task_id = ?, "
            "assistant_content = '', error_json = ?, update_time = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'running'",
            [status, (task or {}).get("id"), _dump(error), turn["id"]],
        )
        if await _changes(self._db) != 1:
            raise ValueError("Screenplay replacement Turn terminal settlement was lost")


class ScreenplayReplacementRunCancellationProjector:
    """Project PurrA's cancellation fence into the replacement aggregate."""

    def __init__(self, db) -> None:
        self._db = db

    async def project(self, run_id: str, receipt) -> None:
        row = await self._db.fetch_one(
            "SELECT session_id, binding_namespace, binding_aggregate_id, "
            "binding_command_id, binding_attributes_json FROM ai_agent_runs "
            "WHERE id = ?",
            [run_id],
        )
        if row is None or row.get("binding_namespace") != (
            SCREENPLAY_REPLACEMENT_ROOT_BINDING
        ):
            return None
        attributes = _object(row.get("binding_attributes_json"))
        if (
            attributes.get("agentProfile") != SCREENPLAY_REPLACEMENT_PROFILE_ID
            or attributes.get("domainNamespace")
            != SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE
        ):
            raise ContractViolationError(
                "Screenplay replacement cancellation profile conflicts"
            )
        turns = await self._db.fetch_all(
            "SELECT *, planner_run_id AS root_run_id "
            "FROM screenplay_agent_turns WHERE planner_run_id = ?",
            [run_id],
        )
        if len(turns) != 1:
            raise ContractViolationError(
                "Screenplay replacement cancellation Turn is unavailable"
            )
        turn = turns[0]
        if (
            str(turn.get("project_id") or "")
            != str(row.get("binding_aggregate_id") or "")
            or int(turn.get("session_id") or 0) != int(row.get("session_id") or 0)
        ):
            raise ContractViolationError(
                "Screenplay replacement cancellation identity conflicts"
            )
        ordinary = attributes.get("interactionKind") == "ordinary"
        if ordinary:
            if (
                turn.get("operation_id") is not None
                or str(turn.get("command_id") or "")
                != str(row.get("binding_command_id") or "")
            ):
                raise ContractViolationError(
                    "Screenplay ordinary cancellation identity conflicts"
                )
            requested_at = int(
                turn.get("cancel_requested_at_ms")
                or getattr(receipt, "requested_at_ms", None)
                or now_ms()
            )
            epoch = int(getattr(receipt, "cancellation_epoch", 0) or 0)
            receipt_id = str(turn.get("cancel_receipt_id") or "").strip()
            if not receipt_id:
                if epoch < 1:
                    raise ContractViolationError(
                        "Screenplay ordinary cancellation epoch is invalid"
                    )
                receipt_id = f"run-cancel:{run_id}:{epoch}"
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET cancel_requested_at_ms = "
                "COALESCE(cancel_requested_at_ms, ?), cancel_receipt_id = "
                "COALESCE(cancel_receipt_id, ?), update_time = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status IN ('planning','running')",
                [requested_at, receipt_id, turn["id"]],
            )
            return None
        operation = await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_operations WHERE turn_id = ?",
            [turn["id"]],
        )
        if operation is None:
            raise ContractViolationError(
                "Screenplay replacement cancellation Operation is unavailable"
            )
        continuation_of = str(attributes.get("continuationOf") or "").strip()
        if (
            (
                not continuation_of
                and str(turn.get("command_id") or "")
                != str(row.get("binding_command_id") or "")
            )
            or (
                continuation_of
                and str(attributes.get("operationId") or "")
                != str(operation.get("id") or "")
            )
        ):
            raise ContractViolationError(
                "Screenplay replacement cancellation command conflicts"
            )
        requested_at = int(
            turn.get("cancel_requested_at_ms")
            or getattr(receipt, "requested_at_ms", None)
            or now_ms()
        )
        receipt_id = str(turn.get("cancel_receipt_id") or "").strip()
        if not receipt_id:
            epoch = int(getattr(receipt, "cancellation_epoch", 0) or 0)
            if epoch < 1:
                raise ContractViolationError(
                    "Screenplay replacement cancellation epoch is invalid"
                )
            receipt_id = f"run-cancel:{run_id}:{epoch}"
        await self._db.execute(
            "UPDATE screenplay_agent_turns SET cancel_requested_at_ms = "
            "COALESCE(cancel_requested_at_ms, ?), cancel_receipt_id = "
            "COALESCE(cancel_receipt_id, ?), update_time = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status IN ('planning','running','paused')",
            [requested_at, receipt_id, turn["id"]],
        )
        await self._db.execute(
            "UPDATE screenplay_agent_operations SET cancel_requested_at_ms = "
            "COALESCE(cancel_requested_at_ms, ?), cancel_receipt_id = "
            "COALESCE(cancel_receipt_id, ?), revision = revision + CASE "
            "WHEN cancel_requested_at_ms IS NULL THEN 1 ELSE 0 END, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ? "
            "AND status IN ('queued','running','paused')",
            [requested_at, receipt_id, operation["id"]],
        )
        task_id = str(operation.get("long_task_id") or "").strip()
        if not task_id:
            task_id = await _task_id_for_root(self._db, run_id) or ""
        if task_id:
            await _request_task_cancel(self._db, task_id, requested_at)
        return None


def _completion_message(role: str) -> str:
    labels = {
        "sourceAnalysis": "原作分析",
        "creativeBrief": "创作简报",
        "structure": "分集结构",
        "sceneList": "场景表",
        "screenplayDraft": "剧本正文",
        "review": "审阅报告",
    }
    return f"{labels.get(role, '剧本')}候选版本已生成，等待你审阅和接受。"


def _turn_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "commandId": str(row.get("command_id") or ""),
        "projectId": str(row["project_id"]),
        "sessionId": int(row["session_id"]),
        "status": str(row["status"]),
        "userContent": str(row.get("user_content") or ""),
        "stageCommand": _object(row.get("stage_command_json")) or None,
        "assistantContent": str(row.get("assistant_content") or ""),
        "runtimeProfile": _object(row.get("runtime_profile_json")),
        "attempt": int(row.get("attempt") or 0),
        "rootRunId": str(row.get("root_run_id") or "") or None,
        "operationId": str(row.get("operation_id") or "") or None,
        "taskId": str(row.get("task_id") or "") or None,
        "targetRole": str(row.get("target_role") or "") or None,
        "resultRevisionId": str(row.get("result_revision_id") or "") or None,
        "cancelReceiptId": str(row.get("cancel_receipt_id") or "") or None,
        "cancelRequestedAtMs": row.get("cancel_requested_at_ms"),
        "error": _object(row.get("error_json")) or None,
        "createdAt": row.get("create_time"),
        "updatedAt": row.get("update_time"),
    }


def _required(value: object, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"Screenplay replacement {label} is required")
    return result


def _object(value: object) -> dict[str, Any]:
    try:
        result = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(result) if isinstance(result, Mapping) else {}


def _dump(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


async def _changes(db) -> int:
    row = await db.fetch_one("SELECT changes() AS count")
    return int((row or {}).get("count") or 0)


async def _request_task_cancel(db, task_id: str, requested_at: int) -> None:
    await db.execute(
        "UPDATE ai_agent_long_tasks SET cancel_requested_at_ms = "
        "COALESCE(cancel_requested_at_ms, ?), revision = revision + CASE "
        "WHEN cancel_requested_at_ms IS NULL THEN 1 ELSE 0 END, "
        "update_time = CURRENT_TIMESTAMP WHERE id = ? "
        "AND status IN ('pending','running','paused')",
        [requested_at, task_id],
    )


async def _task_id_for_root(db, run_id: str) -> str | None:
    rows = await db.fetch_all(
        "SELECT id FROM ai_agent_long_tasks WHERE created_by_run_id = ? "
        "AND namespace = ? ORDER BY create_time, id",
        [run_id, SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE],
    )
    if len(rows) > 1:
        raise ValueError("Screenplay replacement Root owns multiple Tasks")
    return str(rows[0]["id"]) if rows else None


async def _tasks_for_operation_root(db, *, operation, run_id: str):
    task_id = str(operation.get("long_task_id") or "").strip()
    if task_id:
        rows = await db.fetch_all(
            "SELECT task.id, task.status, task.usage_json "
            "FROM ai_agent_long_tasks AS task "
            "JOIN ai_agent_long_task_runs AS binding ON binding.task_id = task.id "
            "WHERE task.id = ? AND task.namespace = ? AND binding.run_id = ? "
            "AND binding.relation IN ('created','continuation')",
            [task_id, SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE, run_id],
        )
        return rows
    return await db.fetch_all(
        "SELECT id, status, usage_json FROM ai_agent_long_tasks "
        "WHERE created_by_run_id = ? AND namespace = ?",
        [run_id, SCREENPLAY_REPLACEMENT_DOMAIN_NAMESPACE],
    )


async def _settle_product_cancel(
    db,
    *,
    turn_id: str,
    operation_id: str,
    task_id: str | None,
) -> None:
    if task_id:
        await db.execute(
            "UPDATE ai_agent_long_tasks SET status = 'canceled', "
            "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status IN ('pending','running','paused') "
            "AND cancel_requested_at_ms IS NOT NULL",
            [task_id],
        )
        await db.execute(
            "UPDATE ai_agent_long_task_units SET status = 'canceled', "
            "worker_id = NULL, lease_expires_at_ms = NULL, "
            "update_time = CURRENT_TIMESTAMP WHERE task_id = ? "
            "AND status IN ('pending','waiting_retry','claimed','running',"
            "'needs_split','blocked')",
            [task_id],
        )
    await db.execute(
        "UPDATE screenplay_agent_operations SET status = 'canceled', "
        "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
        "WHERE id = ? AND status IN ('queued','running','paused') "
        "AND cancel_requested_at_ms IS NOT NULL",
        [operation_id],
    )
    await db.execute(
        "UPDATE screenplay_agent_turns SET status = 'canceled', "
        "assistant_content = '', execution_owner_id = NULL, "
        "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
        "update_time = CURRENT_TIMESTAMP WHERE id = ? "
        "AND status IN ('queued','planning','running','paused') "
        "AND cancel_requested_at_ms IS NOT NULL",
        [turn_id],
    )


__all__ = [
    "SCREENPLAY_REPLACEMENT_ROOT_BINDING",
    "ScreenplayReplacementConversationStore",
    "ScreenplayReplacementRootProjectionError",
    "ScreenplayReplacementRunBeginProjector",
    "ScreenplayReplacementRunCancellationProjector",
    "ScreenplayReplacementRunCommitProjector",
    "ScreenplayReplacementTurnLifecycle",
]
