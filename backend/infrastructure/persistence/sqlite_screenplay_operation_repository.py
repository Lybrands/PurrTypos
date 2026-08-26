"""SQLite authority for product-level screenplay Operations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

from domains.screenplay_agent.operation import (
    CancelOperationReceipt,
    OperationUsage,
    ScreenplayOperationCreateCommand,
    ScreenplayOperationRecord,
    ScreenplayOperationStatus,
)
from purra.json_values import thaw_json_mapping


class SqliteScreenplayOperationRepository:
    def __init__(self, db) -> None:
        self._db = db

    @asynccontextmanager
    async def _mutation_transaction(self):
        if self._db.current_task_owns_transaction():
            yield
            return
        async with self._db.transaction(cancellation_linearizable=True):
            yield

    async def create(
        self,
        command: ScreenplayOperationCreateCommand,
    ) -> ScreenplayOperationRecord:
        existing = await self.load_for_turn(command.turn_id)
        if existing is not None:
            _require_same_create(existing, command)
            return existing
        operation_id = f"spaop_{uuid.uuid4().hex}"
        try:
            async with self._db.transaction(cancellation_linearizable=True):
                await self._require_turn_scope(command)
                await self._db.execute(
                    "INSERT INTO screenplay_agent_operations "
                    "(id, turn_id, project_id, session_id, target_role, "
                    "requirements_json, manifest_digest) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        operation_id,
                        command.turn_id,
                        command.project_id,
                        command.session_id,
                        command.target_role,
                        _dump(command.requirements_json),
                        command.manifest_digest,
                    ],
                )
                await self._db.execute(
                    "UPDATE screenplay_agent_turns SET operation_id = ?, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                    [operation_id, command.turn_id],
                )
                await self._record_command(
                    operation_id=operation_id,
                    command_id=f"operation:create:{command.turn_id}",
                    command_type="create",
                    request={
                        "turnId": command.turn_id,
                        "manifestDigest": command.manifest_digest,
                    },
                    receipt_id=operation_id,
                    response={"operationId": operation_id, "status": "queued"},
                )
                return await self._require(operation_id)
        except sqlite3.IntegrityError as error:
            existing = await self.load_for_turn(command.turn_id)
            if existing is not None:
                _require_same_create(existing, command)
                return existing
            active = await self.find_active(
                project_id=command.project_id,
                session_id=command.session_id,
            )
            if active is not None:
                raise ValueError(
                    "screenplay session already has an active Operation"
                ) from error
            raise

    async def load(self, operation_id: str) -> ScreenplayOperationRecord | None:
        row = await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_operations WHERE id = ?",
            [str(operation_id or "").strip()],
        )
        return _operation(row) if row is not None else None

    async def load_for_turn(self, turn_id: str) -> ScreenplayOperationRecord | None:
        row = await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_operations WHERE turn_id = ?",
            [str(turn_id or "").strip()],
        )
        return _operation(row) if row is not None else None

    async def find_active(
        self,
        *,
        project_id: str,
        session_id: int,
    ) -> ScreenplayOperationRecord | None:
        row = await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_operations WHERE project_id = ? "
            "AND session_id = ? AND status IN ('queued', 'running', 'paused') "
            "ORDER BY create_time DESC LIMIT 1",
            [project_id, int(session_id)],
        )
        return _operation(row) if row is not None else None

    async def record_usage(
        self,
        operation_id: str,
        *,
        run_id: str,
        usage: OperationUsage,
        expected_revision: int,
    ) -> ScreenplayOperationRecord:
        normalized_id = _required(operation_id, "screenplay Operation id")
        normalized_run_id = _required(run_id, "Operation usage Run id")
        if not isinstance(usage, OperationUsage):
            raise TypeError("screenplay Operation usage must be OperationUsage")
        async with self._mutation_transaction():
            existing = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_operation_usage "
                "WHERE operation_id = ? AND run_id = ?",
                [normalized_id, normalized_run_id],
            )
            if existing is not None:
                if _usage_row(existing) != usage:
                    raise ValueError("screenplay Operation Run usage conflicts")
                return await self._require(normalized_id)
            operation = await self._require(normalized_id)
            if operation.revision != int(expected_revision):
                raise ValueError("screenplay Operation revision conflict")
            await self._db.execute(
                "INSERT INTO screenplay_agent_operation_usage "
                "(operation_id, run_id, invocation_count, input_tokens, "
                "output_tokens, reasoning_tokens) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    operation.id,
                    normalized_run_id,
                    usage.invocation_count,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.reasoning_tokens,
                ],
            )
            aggregate = await self._db.fetch_one(
                "SELECT SUM(invocation_count) AS invocation_count, "
                "SUM(input_tokens) AS input_tokens, "
                "SUM(output_tokens) AS output_tokens, "
                "SUM(reasoning_tokens) AS reasoning_tokens, "
                "SUM(CASE WHEN reasoning_tokens IS NULL THEN 1 ELSE 0 END) "
                "AS unknown_reasoning FROM screenplay_agent_operation_usage "
                "WHERE operation_id = ?",
                [operation.id],
            )
            total = _aggregate_usage(aggregate)
            await self._db.execute(
                "UPDATE screenplay_agent_operations SET usage_json = ?, "
                "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                [_dump(total.to_mapping()), operation.id],
            )
            return await self._require(operation.id)

    async def attach_long_task(
        self,
        operation_id: str,
        *,
        long_task_id: str,
        command_id: str,
    ) -> ScreenplayOperationRecord:
        return await self._transition(
            operation_id,
            command_id=command_id,
            command_type="attachLongTask",
            allowed={ScreenplayOperationStatus.QUEUED, ScreenplayOperationStatus.RUNNING},
            target=ScreenplayOperationStatus.RUNNING,
            values={"long_task_id": _required(long_task_id, "long task id")},
        )

    async def resume_with_model(
        self,
        operation_id: str,
        *,
        command_id: str,
        expected_revision: int,
        capability_snapshot: Mapping[str, Any],
    ) -> ScreenplayOperationRecord:
        normalized_id = _required(operation_id, "screenplay Operation id")
        normalized_command = _required(command_id, "resume command id")
        snapshot = dict(capability_snapshot)
        digest = _required(snapshot.get("digest"), "capability snapshot digest")
        request = {
            "expectedOperationRevision": int(expected_revision),
            "capabilitySnapshotDigest": digest,
        }
        request_digest = _digest(request)
        async with self._mutation_transaction():
            replay = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_operation_commands "
                "WHERE command_id = ?",
                [normalized_command],
            )
            if replay is not None:
                if (
                    str(replay["operation_id"]) != normalized_id
                    or str(replay["command_type"]) != "resume"
                    or str(replay["request_digest"]) != request_digest
                ):
                    raise ValueError("screenplay Operation resume command conflicts")
                return await self._require(normalized_id)
            equivalent = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_operation_commands "
                "WHERE operation_id = ? AND command_type = 'resume' "
                "AND request_digest = ?",
                [normalized_id, request_digest],
            )
            if equivalent is not None:
                return await self._require(normalized_id)
            operation = await self._require(normalized_id)
            if operation.status is not ScreenplayOperationStatus.PAUSED:
                raise ValueError("only a paused screenplay Operation can resume")
            if operation.revision != int(expected_revision):
                raise ValueError("screenplay Operation revision conflict")
            if operation.cancel_requested_at_ms is not None:
                raise ValueError("canceled screenplay Operation cannot resume")
            if not operation.long_task_id:
                raise ValueError("paused screenplay Operation has no LongTask")
            task = await self._db.fetch_one(
                "SELECT * FROM ai_agent_long_tasks WHERE id = ?",
                [operation.long_task_id],
            )
            if task is None or str(task.get("status") or "") != "paused":
                raise ValueError("screenplay Operation LongTask is not paused")
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'pending', "
                "max_attempts = max_attempts + 1, worker_id = NULL, "
                "lease_expires_at_ms = NULL, update_time = CURRENT_TIMESTAMP "
                "WHERE task_id = ? AND status = 'blocked'",
                [operation.long_task_id],
            )
            await self._db.execute(
                "UPDATE ai_agent_long_tasks SET status = 'running', "
                "failed_units = 0, revision = revision + 1, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ? AND status = 'paused'",
                [operation.long_task_id],
            )
            await self._db.execute(
                "UPDATE screenplay_agent_operations SET status = 'running', "
                "error_json = NULL, active_capability_snapshot_json = ?, "
                "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'paused' AND revision = ?",
                [_dump(snapshot), operation.id, operation.revision],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            if int((changed or {}).get("count") or 0) != 1:
                raise ValueError("screenplay Operation revision conflict")
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = 'running', "
                "assistant_content = '', update_time = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'paused'",
                [operation.turn_id],
            )
            resumed = await self._require(operation.id)
            await self._record_command(
                operation_id=operation.id,
                command_id=normalized_command,
                command_type="resume",
                request=request,
                receipt_id=normalized_command,
                response={
                    "operationId": operation.id,
                    "status": resumed.status.value,
                    "revision": resumed.revision,
                    "capabilitySnapshotDigest": digest,
                },
            )
            return resumed

    async def claim_continuation_start(
        self,
        *,
        command_id: str,
        operation_id: str,
        turn_id: str,
        source_root_run_id: str,
        session_id: int,
        project_id: str,
        owner_id: str,
        lease_duration_ms: int = 30_000,
    ) -> dict[str, Any]:
        command = _required(command_id, "continuation command id")
        operation = _required(operation_id, "continuation Operation id")
        turn = _required(turn_id, "continuation Turn id")
        source = _required(source_root_run_id, "continuation source Root id")
        project = _required(project_id, "continuation project id")
        owner = _required(owner_id, "continuation reservation owner")
        lease_ms = int(lease_duration_ms)
        if lease_ms <= 0:
            raise ValueError("continuation reservation lease must be positive")
        now = int(time.time() * 1000)
        identity = {
            "commandId": command,
            "operationId": operation,
            "turnId": turn,
            "sourceRootRunId": source,
            "sessionId": int(session_id),
            "projectId": project,
        }
        async with self._mutation_transaction():
            row = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_operation_commands "
                "WHERE command_id = ?",
                [command],
            )
            if (
                row is None
                or str(row.get("operation_id") or "") != operation
                or str(row.get("command_type") or "") != "resume"
            ):
                raise ValueError("screenplay continuation command conflicts")
            identity["requestDigest"] = str(row.get("request_digest") or "")
            identity_digest = _digest(identity)
            persisted_digest = str(
                row.get("continuation_identity_digest") or ""
            ).strip()
            if persisted_digest and persisted_digest != identity_digest:
                raise ValueError("screenplay continuation identity conflicts")
            status = str(row.get("continuation_status") or "reserved")
            bound_root = str(row.get("continuation_root_run_id") or "").strip()
            scope = await self._db.fetch_one(
                "SELECT t.project_id, t.session_id, t.operation_id, "
                "t.planner_run_id AS root_run_id, r.status AS source_status "
                "FROM screenplay_agent_turns AS t "
                "LEFT JOIN ai_agent_runs AS r ON r.id = ? WHERE t.id = ?",
                [source, turn],
            )
            if (
                scope is None
                or str(scope.get("project_id") or "") != project
                or int(scope.get("session_id") or 0) != int(session_id)
                or str(scope.get("operation_id") or "") != operation
                or str(scope.get("root_run_id") or "")
                not in ({source, bound_root} if status == "bound" else {source})
                or str(scope.get("source_status") or "") != "canceled"
            ):
                raise ValueError("screenplay continuation scope conflicts")
            lease_expires = int(
                row.get("continuation_lease_expires_at_ms") or 0
            )
            acquired = status == "reserved" or (
                status == "starting" and lease_expires <= now
            )
            if acquired:
                await self._db.execute(
                    "UPDATE screenplay_agent_operation_commands SET "
                    "continuation_status = 'starting', "
                    "continuation_owner_id = ?, "
                    "continuation_lease_expires_at_ms = ?, "
                    "continuation_epoch = continuation_epoch + 1, "
                    "continuation_identity_digest = ?, "
                    "continuation_source_root_run_id = ?, "
                    "continuation_turn_id = ?, continuation_session_id = ?, "
                    "continuation_project_id = ? WHERE command_id = ? "
                    "AND (continuation_status IS NULL OR "
                    "continuation_status = 'reserved' OR "
                    "(continuation_status = 'starting' AND "
                    "continuation_lease_expires_at_ms <= ?))",
                    [
                        owner,
                        now + lease_ms,
                        identity_digest,
                        source,
                        turn,
                        int(session_id),
                        project,
                        command,
                        now,
                    ],
                )
                changed = await self._db.fetch_one("SELECT changes() AS count")
                acquired = int((changed or {}).get("count") or 0) == 1
            current = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_operation_commands "
                "WHERE command_id = ?",
                [command],
            )
            assert current is not None
            return {
                **dict(current),
                "identity_digest": str(
                    current.get("continuation_identity_digest") or ""
                ),
                "_acquired": acquired,
            }

    async def load_continuation_command(
        self,
        command_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_operation_commands "
            "WHERE command_id = ? AND command_type = 'resume'",
            [_required(command_id, "continuation command id")],
        )
        return dict(row) if row is not None else None

    async def release_continuation_start(
        self,
        *,
        command_id: str,
        owner_id: str,
        epoch: int,
    ) -> bool:
        async with self._mutation_transaction():
            await self._db.execute(
                "UPDATE screenplay_agent_operation_commands SET "
                "continuation_status = 'reserved', "
                "continuation_owner_id = NULL, "
                "continuation_lease_expires_at_ms = NULL "
                "WHERE command_id = ? AND command_type = 'resume' "
                "AND continuation_status = 'starting' "
                "AND continuation_owner_id = ? AND continuation_epoch = ?",
                [
                    _required(command_id, "continuation command id"),
                    _required(owner_id, "continuation reservation owner"),
                    int(epoch),
                ],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            return int((changed or {}).get("count") or 0) == 1

    async def pause(
        self,
        operation_id: str,
        *,
        code: str,
        message: str,
        command_id: str,
    ) -> ScreenplayOperationRecord:
        return await self._transition(
            operation_id,
            command_id=command_id,
            command_type="pause",
            allowed={
                ScreenplayOperationStatus.QUEUED,
                ScreenplayOperationStatus.RUNNING,
                ScreenplayOperationStatus.PAUSED,
            },
            target=ScreenplayOperationStatus.PAUSED,
            values={"error_json": _dump({"code": code, "message": message})},
        )

    async def succeed(
        self,
        operation_id: str,
        *,
        result_revision_id: str,
        finalization_receipt_id: str,
        command_id: str,
    ) -> ScreenplayOperationRecord:
        return await self._transition(
            operation_id,
            command_id=command_id,
            command_type="succeed",
            allowed={
                ScreenplayOperationStatus.RUNNING,
                ScreenplayOperationStatus.SUCCEEDED,
            },
            target=ScreenplayOperationStatus.SUCCEEDED,
            values={
                "result_revision_id": _required(
                    result_revision_id,
                    "screenplay Operation result Revision id",
                ),
                "finalization_receipt_id": _required(
                    finalization_receipt_id,
                    "screenplay Operation finalization receipt id",
                ),
                "error_json": None,
            },
        )

    async def fail(
        self,
        operation_id: str,
        *,
        code: str,
        message: str,
        command_id: str,
    ) -> ScreenplayOperationRecord:
        return await self._transition(
            operation_id,
            command_id=command_id,
            command_type="fail",
            allowed={
                ScreenplayOperationStatus.QUEUED,
                ScreenplayOperationStatus.RUNNING,
                ScreenplayOperationStatus.PAUSED,
                ScreenplayOperationStatus.FAILED,
            },
            target=ScreenplayOperationStatus.FAILED,
            values={"error_json": _dump({"code": code, "message": message})},
        )

    async def request_cancel(
        self,
        turn_id: str,
        *,
        idempotency_key: str,
    ) -> CancelOperationReceipt:
        normalized_turn_id = _required(turn_id, "screenplay Turn id")
        command_id = _required(idempotency_key, "cancel idempotency key")
        request = {"turnId": normalized_turn_id}
        request_digest = _digest(request)
        async with self._mutation_transaction():
            replay = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_cancel_commands "
                "WHERE command_id = ?",
                [command_id],
            )
            if replay is not None:
                if (
                    str(replay["turn_id"]) != normalized_turn_id
                    or str(replay["request_digest"]) != request_digest
                ):
                    raise ValueError("screenplay cancel command conflicts")
                return await self._current_cancel_receipt(
                    normalized_turn_id,
                    receipt_id=str(replay["receipt_id"]),
                )
            turn = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_turns WHERE id = ?",
                [normalized_turn_id],
            )
            if turn is None:
                raise LookupError("screenplay Agent Turn does not exist")
            operation_row = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_operations WHERE turn_id = ?",
                [normalized_turn_id],
            )
            operation = (
                _operation(operation_row) if operation_row is not None else None
            )
            requested_at_ms = int(
                turn.get("cancel_requested_at_ms") or time.time() * 1000
            )
            receipt_id = str(turn.get("cancel_receipt_id") or "").strip()
            if operation is not None:
                requested_at_ms = int(
                    operation.cancel_requested_at_ms or requested_at_ms
                )
                receipt_id = operation.cancel_receipt_id or receipt_id
            if not receipt_id:
                receipt_id = f"spacancel_{uuid.uuid4().hex}"
            await self._db.execute(
                "UPDATE screenplay_agent_turns SET cancel_requested_at_ms = "
                "COALESCE(cancel_requested_at_ms, ?), cancel_receipt_id = "
                "COALESCE(cancel_receipt_id, ?), update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                [requested_at_ms, receipt_id, normalized_turn_id],
            )
            if operation is not None and not operation.status.terminal:
                await self._db.execute(
                    "UPDATE screenplay_agent_operations SET "
                    "cancel_requested_at_ms = COALESCE(cancel_requested_at_ms, ?), "
                    "cancel_receipt_id = COALESCE(cancel_receipt_id, ?), "
                    "revision = revision + CASE WHEN cancel_requested_at_ms IS NULL "
                    "THEN 1 ELSE 0 END, update_time = CURRENT_TIMESTAMP WHERE id = ? "
                    "AND status IN ('queued', 'running', 'paused')",
                    [requested_at_ms, receipt_id, operation.id],
                )
                if operation.long_task_id:
                    await self._db.execute(
                        "UPDATE ai_agent_long_tasks SET "
                        "cancel_requested_at_ms = COALESCE(cancel_requested_at_ms, ?), "
                        "revision = revision + CASE "
                        "WHEN cancel_requested_at_ms IS NULL THEN 1 ELSE 0 END, "
                        "update_time = CURRENT_TIMESTAMP WHERE id = ? "
                        "AND status IN ('pending', 'running', 'paused')",
                        [requested_at_ms, operation.long_task_id],
                    )
            current = await self._current_cancel_receipt(
                normalized_turn_id,
                receipt_id=receipt_id,
            )
            await self._db.execute(
                "INSERT INTO screenplay_agent_cancel_commands "
                "(command_id, turn_id, operation_id, request_digest, "
                "receipt_id, response_json) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    command_id,
                    normalized_turn_id,
                    operation.id if operation is not None else None,
                    request_digest,
                    receipt_id,
                    _dump(current.to_mapping()),
                ],
            )
            return current

    async def settle_cancel(
        self,
        turn_id: str,
        *,
        receipt_id: str,
    ) -> CancelOperationReceipt:
        normalized_turn_id = _required(turn_id, "screenplay Turn id")
        normalized_receipt = _required(receipt_id, "cancel receipt id")
        async with self._mutation_transaction():
            turn = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_turns WHERE id = ?",
                [normalized_turn_id],
            )
            if turn is None:
                raise LookupError("screenplay Agent Turn does not exist")
            if str(turn.get("cancel_receipt_id") or "") != normalized_receipt:
                raise ValueError("screenplay cancel receipt conflicts")
            operation_row = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_operations WHERE turn_id = ?",
                [normalized_turn_id],
            )
            operation = (
                _operation(operation_row) if operation_row is not None else None
            )
            if operation is not None and not operation.status.terminal:
                if operation.cancel_requested_at_ms is None:
                    raise ValueError("screenplay Operation cancellation was not requested")
                await self._db.execute(
                    "UPDATE screenplay_agent_operations SET status = 'canceled', "
                    "cancel_receipt_id = ?, revision = revision + 1, "
                    "update_time = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND status IN ('queued', 'running', 'paused') "
                    "AND cancel_requested_at_ms IS NOT NULL",
                    [normalized_receipt, operation.id],
                )
            current = await self._current_cancel_receipt(
                normalized_turn_id,
                receipt_id=normalized_receipt,
            )
            if current.terminal_status in {"succeeded", "failed"}:
                return current
            changed = False
            if str(turn.get("status") or "") in {
                "queued",
                "planning",
                "running",
                "paused",
            }:
                await self._db.execute(
                    "UPDATE screenplay_agent_turns SET status = 'canceled', "
                    "assistant_content = '', execution_owner_id = NULL, "
                    "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ? "
                    "AND cancel_requested_at_ms IS NOT NULL AND status IN "
                    "('queued', 'planning', 'running', 'paused')",
                    [normalized_turn_id],
                )
                changed_row = await self._db.fetch_one(
                    "SELECT changes() AS count"
                )
                changed = int((changed_row or {}).get("count") or 0) == 1
            return await self._current_cancel_receipt(
                normalized_turn_id,
                receipt_id=normalized_receipt,
            )

    async def _current_cancel_receipt(
        self,
        turn_id: str,
        *,
        receipt_id: str,
    ) -> CancelOperationReceipt:
        turn = await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_turns WHERE id = ?",
            [turn_id],
        )
        if turn is None:
            raise LookupError("screenplay Agent Turn does not exist")
        operation_row = await self._db.fetch_one(
            "SELECT * FROM screenplay_agent_operations WHERE turn_id = ?",
            [turn_id],
        )
        operation = _operation(operation_row) if operation_row is not None else None
        if operation is not None:
            status = {
                ScreenplayOperationStatus.SUCCEEDED: "succeeded",
                ScreenplayOperationStatus.FAILED: "failed",
                ScreenplayOperationStatus.CANCELED: "canceled",
            }.get(operation.status, "cancel_requested")
            requested_at = (
                operation.cancel_requested_at_ms
                or turn.get("cancel_requested_at_ms")
            )
        else:
            status = {
                "completed": "succeeded",
                "failed": "failed",
                "canceled": "canceled",
            }.get(str(turn.get("status") or ""), "cancel_requested")
            requested_at = turn.get("cancel_requested_at_ms")
        return CancelOperationReceipt(
            id=receipt_id,
            operation_id=operation.id if operation is not None else None,
            turn_id=turn_id,
            requested_at=str(int(requested_at or time.time() * 1000)),
            terminal_status=status,
        )

    async def _transition(
        self,
        operation_id: str,
        *,
        command_id: str,
        command_type: str,
        allowed: set[ScreenplayOperationStatus],
        target: ScreenplayOperationStatus,
        values: Mapping[str, Any],
    ) -> ScreenplayOperationRecord:
        normalized_id = _required(operation_id, "screenplay Operation id")
        normalized_command = _required(command_id, "screenplay Operation command id")
        # A command id is the idempotency boundary.  The same transition may be
        # valid again after an intervening resume (for example pause -> resume
        # -> pause with the same error).  Include the command identity in the
        # persisted digest so the table's semantic uniqueness constraint does
        # not collapse two distinct lifecycle occurrences.
        request = {
            "commandId": normalized_command,
            "target": target.value,
            **dict(values),
        }
        digest = _digest(request)
        async with self._mutation_transaction():
            receipt = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_operation_commands "
                "WHERE command_id = ?",
                [normalized_command],
            )
            if receipt is not None:
                if (
                    str(receipt["operation_id"]) != normalized_id
                    or str(receipt["command_type"]) != command_type
                    or str(receipt["request_digest"]) != digest
                ):
                    raise ValueError("screenplay Operation command conflicts")
                return await self._require(normalized_id)
            operation = await self._require(normalized_id)
            if operation.status not in allowed:
                raise ValueError(
                    f"screenplay Operation cannot transition from {operation.status.value}"
                )
            assignments = ["status = ?"]
            params: list[Any] = [target.value]
            for name, value in values.items():
                if name not in {
                    "long_task_id",
                    "result_revision_id",
                    "finalization_receipt_id",
                    "error_json",
                }:
                    raise ValueError("unsupported screenplay Operation transition field")
                assignments.append(f"{name} = ?")
                params.append(value)
            params.append(normalized_id)
            await self._db.execute(
                "UPDATE screenplay_agent_operations SET "
                + ", ".join(assignments)
                + ", revision = revision + 1, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                params,
            )
            await self._record_command(
                operation_id=normalized_id,
                command_id=normalized_command,
                command_type=command_type,
                request=request,
                receipt_id=(
                    str(values.get("finalization_receipt_id") or "")
                    or normalized_command
                ),
                response={"operationId": normalized_id, "status": target.value},
            )
            return await self._require(normalized_id)

    async def _record_command(
        self,
        *,
        operation_id: str,
        command_id: str,
        command_type: str,
        request: Mapping[str, Any],
        receipt_id: str,
        response: Mapping[str, Any],
    ) -> None:
        await self._db.execute(
            "INSERT INTO screenplay_agent_operation_commands "
            "(command_id, operation_id, command_type, request_digest, "
            "receipt_id, response_json) VALUES (?, ?, ?, ?, ?, ?)",
            [
                command_id,
                operation_id,
                command_type,
                _digest(request),
                receipt_id,
                _dump(response),
            ],
        )

    async def _require_turn_scope(
        self,
        command: ScreenplayOperationCreateCommand,
    ) -> None:
        turn = await self._db.fetch_one(
            "SELECT project_id, session_id FROM screenplay_agent_turns WHERE id = ?",
            [command.turn_id],
        )
        if turn is None:
            raise LookupError("screenplay Agent Turn does not exist")
        if (
            str(turn["project_id"]) != command.project_id
            or int(turn["session_id"]) != command.session_id
        ):
            raise ValueError("screenplay Operation scope does not match its Turn")

    async def _require(self, operation_id: str) -> ScreenplayOperationRecord:
        operation = await self.load(operation_id)
        if operation is None:
            raise LookupError("screenplay Operation does not exist")
        return operation


def _operation(row: Mapping[str, Any]) -> ScreenplayOperationRecord:
    return ScreenplayOperationRecord(
        id=str(row["id"]),
        turn_id=str(row["turn_id"]),
        project_id=str(row["project_id"]),
        session_id=int(row["session_id"]),
        status=str(row["status"]),
        revision=int(row.get("revision") or 1),
        long_task_id=row.get("long_task_id"),
        target_role=str(row["target_role"]),
        requirements_json=_object(row.get("requirements_json")),
        manifest_digest=str(row["manifest_digest"]),
        result_revision_id=row.get("result_revision_id"),
        finalization_receipt_id=row.get("finalization_receipt_id"),
        cancel_receipt_id=row.get("cancel_receipt_id"),
        cancel_requested_at_ms=row.get("cancel_requested_at_ms"),
        usage=_usage_mapping(_object(row.get("usage_json"))),
        error=_object(row.get("error_json")) or None,
        create_time=row.get("create_time"),
        update_time=row.get("update_time"),
    )


def _require_same_create(
    operation: ScreenplayOperationRecord,
    command: ScreenplayOperationCreateCommand,
) -> None:
    if (
        operation.project_id != command.project_id
        or operation.session_id != command.session_id
        or operation.target_role != command.target_role
        or operation.manifest_digest != command.manifest_digest
        or thaw_json_mapping(operation.requirements_json)
        != thaw_json_mapping(command.requirements_json)
    ):
        raise ValueError("screenplay Operation turn id conflicts")


def _dump(value: object) -> str:
    return json.dumps(
        thaw_json_mapping(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _usage_mapping(value: Mapping[str, Any]) -> OperationUsage:
    return OperationUsage(
        invocation_count=int(value.get("invocationCount") or 0),
        input_tokens=int(value.get("inputTokens") or 0),
        output_tokens=int(value.get("outputTokens") or 0),
        reasoning_tokens=(
            None
            if value.get("reasoningTokens") is None
            and int(value.get("invocationCount") or 0) > 0
            else int(value.get("reasoningTokens") or 0)
        ),
    )


def _usage_row(row: Mapping[str, Any]) -> OperationUsage:
    return OperationUsage(
        invocation_count=int(row.get("invocation_count") or 0),
        input_tokens=int(row.get("input_tokens") or 0),
        output_tokens=int(row.get("output_tokens") or 0),
        reasoning_tokens=(
            None
            if row.get("reasoning_tokens") is None
            else int(row["reasoning_tokens"])
        ),
    )


def _aggregate_usage(row: Mapping[str, Any] | None) -> OperationUsage:
    value = row or {}
    return OperationUsage(
        invocation_count=int(value.get("invocation_count") or 0),
        input_tokens=int(value.get("input_tokens") or 0),
        output_tokens=int(value.get("output_tokens") or 0),
        reasoning_tokens=(
            None
            if int(value.get("unknown_reasoning") or 0) > 0
            else int(value.get("reasoning_tokens") or 0)
        ),
    )


def _digest(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


def _required(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


__all__ = ["SqliteScreenplayOperationRepository"]
