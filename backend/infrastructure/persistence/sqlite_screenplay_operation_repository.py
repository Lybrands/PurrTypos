"""SQLite authority for product-level screenplay Operations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Mapping
from typing import Any

from domains.screenplay_agent.operation import (
    CancelOperationReceipt,
    ScreenplayOperationCreateCommand,
    ScreenplayOperationRecord,
    ScreenplayOperationStatus,
)
from purra.json_values import thaw_json_mapping


class SqliteScreenplayOperationRepository:
    def __init__(self, db) -> None:
        self._db = db

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
        async with self._db.transaction(cancellation_linearizable=True):
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
                    "update_time = CURRENT_TIMESTAMP WHERE id = ? "
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
        async with self._db.transaction(cancellation_linearizable=True):
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
                    "cancel_receipt_id = ?, update_time = CURRENT_TIMESTAMP "
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
            if changed:
                task_id = operation.long_task_id if operation is not None else None
                await self._db.execute(
                    "INSERT INTO screenplay_agent_events "
                    "(project_id, session_id, turn_id, task_id, event_type, "
                    "payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        str(turn["project_id"]),
                        int(turn["session_id"]),
                        normalized_turn_id,
                        task_id,
                        (
                            "screenplay.agent.task.canceled"
                            if operation is not None
                            else "screenplay.agent.turn.canceled"
                        ),
                        _dump({
                            "taskId": task_id,
                            "cancelReceiptId": normalized_receipt,
                        }),
                    ],
                )
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
        request = {"target": target.value, **dict(values)}
        digest = _digest(request)
        async with self._db.transaction(cancellation_linearizable=True):
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
                + ", update_time = CURRENT_TIMESTAMP WHERE id = ?",
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
        long_task_id=row.get("long_task_id"),
        target_role=str(row["target_role"]),
        requirements_json=_object(row.get("requirements_json")),
        manifest_digest=str(row["manifest_digest"]),
        result_revision_id=row.get("result_revision_id"),
        finalization_receipt_id=row.get("finalization_receipt_id"),
        cancel_receipt_id=row.get("cancel_receipt_id"),
        cancel_requested_at_ms=row.get("cancel_requested_at_ms"),
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


def _digest(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


def _required(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


__all__ = ["SqliteScreenplayOperationRepository"]
