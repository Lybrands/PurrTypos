"""SQLite unit of work for native screenplay conversation Turns and events."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any

from application.run_provenance import digest_model_endpoint
from exceptions import AppError, NotFoundError
from infrastructure.persistence.run_execution_store import now_ms
from infrastructure.persistence.sqlite_screenplay_v2_repository import (
    SqliteScreenplayV2Repository,
)


TURN_LEASE_DURATION_MS = 30_000


def _dump(value: object) -> str:
    return json.dumps(
        value,
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


def _digest(value: object) -> str:
    return hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


def _operation_command_id(command_id: str) -> str:
    digest = hashlib.sha256(command_id.encode("utf-8")).hexdigest()
    return f"screenplay:conversation-operation:{digest}"


class SqliteScreenplayConversationRepository:
    def __init__(self, db, *, owner_id: str) -> None:
        self._db = db
        self._owner_id = str(owner_id or "").strip()
        self._operations = SqliteScreenplayV2Repository(db)
        if not self._owner_id:
            raise ValueError("conversation execution owner id is required")

    async def begin_turn(
        self,
        *,
        command_id: str,
        request_digest: str,
        project_id: str,
        session_id: int,
        route: str,
        content: str,
        runtime_profile: Mapping[str, Any],
        operation: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            replay = await self._find_receipt(
                command_id=command_id,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            await self._require_project_session(project_id, session_id)
            turn_id = f"spturn_{uuid.uuid4().hex}"
            await self._db.execute(
                "INSERT INTO screenplay_conversation_turns "
                "(id, project_id, session_id, command_id, request_digest, "
                "route, status, user_content, runtime_profile_json) "
                "VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
                [
                    turn_id,
                    project_id,
                    int(session_id),
                    command_id,
                    request_digest,
                    route,
                    content,
                    _dump(dict(runtime_profile)),
                ],
            )
            await self._append_event(
                turn_id=turn_id,
                project_id=project_id,
                session_id=session_id,
                event_type="screenplay.conversation.turn_queued",
                payload={"route": route},
            )

            operation_result: dict[str, Any] | None = None
            if operation is not None:
                operation_command_id = _operation_command_id(command_id)
                operation_payload = {
                    "projectId": project_id,
                    "expectedProjectRevision": int(
                        operation["expectedProjectRevision"]
                    ),
                    "targetRole": str(operation["targetRole"]),
                    "intent": dict(operation["intent"]),
                    "conversation": {
                        "sessionId": int(session_id),
                        "userMessageId": turn_id,
                    },
                }
                operation_result = await self._operations.create_operation(
                    command_id=operation_command_id,
                    request_digest=_digest(operation_payload),
                    project_id=project_id,
                    expected_project_revision=int(
                        operation["expectedProjectRevision"]
                    ),
                    target_role=str(operation["targetRole"]),
                    intent=dict(operation["intent"]),
                    conversation={
                        "sessionId": int(session_id),
                        "userMessageId": turn_id,
                    },
                    within_transaction=True,
                )
                operation_id = str(
                    operation_result["operation"]["id"]
                )
                await self._db.execute(
                    "UPDATE screenplay_conversation_turns SET operation_id = ?, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                    [operation_id, turn_id],
                )
                await self._append_event(
                    turn_id=turn_id,
                    project_id=project_id,
                    session_id=session_id,
                    event_type="screenplay.conversation.operation_queued",
                    payload={
                        "operationId": operation_id,
                        "targetRole": str(operation["targetRole"]),
                    },
                )

            response = {
                "turnId": turn_id,
                "operationId": (
                    operation_result["operation"]["id"]
                    if operation_result is not None
                    else None
                ),
            }
            await self._db.execute(
                "INSERT INTO screenplay_command_receipts "
                "(command_id, command_type, project_id, request_digest, "
                "result_ref, response_json) VALUES "
                "(?, 'submitConversationTurn', ?, ?, ?, ?)",
                [
                    command_id,
                    project_id,
                    request_digest,
                    f"screenplay-conversation-turn://{turn_id}",
                    _dump(response),
                ],
            )
            return _turn_view(await self._require_turn(turn_id))

    async def _find_receipt(
        self,
        *,
        command_id: str,
        request_digest: str,
    ) -> dict[str, Any] | None:
        receipt = await self._db.fetch_one(
            "SELECT command_type, request_digest, response_json "
            "FROM screenplay_command_receipts WHERE command_id = ?",
            [command_id],
        )
        if receipt is None:
            return None
        if (
            str(receipt.get("command_type") or "")
            != "submitConversationTurn"
            or str(receipt.get("request_digest") or "") != request_digest
        ):
            raise AppError(
                "同一个 Idempotency-Key 不能用于不同的对话请求",
                409,
            )
        turn_id = str(
            _object(receipt.get("response_json")).get("turnId") or ""
        )
        if not turn_id:
            raise AppError("对话命令回执缺少 Turn 引用", 409)
        return _turn_view(await self._require_turn(turn_id))

    async def claim_execution(self, turn_id: str) -> bool:
        current_ms = now_ms()
        lease_expires_at_ms = current_ms + TURN_LEASE_DURATION_MS
        async with self._db.transaction(cancellation_linearizable=True):
            turn = await self._require_turn(turn_id)
            if str(turn["status"]) in {"completed", "failed", "canceled"}:
                return False
            existing_owner = str(turn.get("execution_owner_id") or "")
            existing_lease = int(turn.get("lease_expires_at_ms") or 0)
            if (
                existing_owner
                and existing_owner != self._owner_id
                and existing_lease > current_ms
            ):
                return False
            if existing_owner == self._owner_id and existing_lease > current_ms:
                return False
            await self._db.execute(
                "UPDATE screenplay_conversation_turns SET status = 'running', "
                "execution_owner_id = ?, lease_expires_at_ms = ?, "
                "heartbeat_at_ms = ?, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status IN ('queued', 'running')",
                [self._owner_id, lease_expires_at_ms, current_ms, turn_id],
            )
            await self._append_event_for_turn(
                turn,
                "screenplay.conversation.turn_started",
                {},
            )
            return True

    async def heartbeat_execution(self, turn_id: str) -> bool:
        current_ms = now_ms()
        async with self._db.transaction():
            owned = await self._db.fetch_one(
                "SELECT 1 AS owned FROM screenplay_conversation_turns "
                "WHERE id = ? AND status = 'running' "
                "AND execution_owner_id = ?",
                [turn_id, self._owner_id],
            )
            if owned is None:
                return False
            await self._db.execute(
                "UPDATE screenplay_conversation_turns SET heartbeat_at_ms = ?, "
                "lease_expires_at_ms = ?, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                [
                    current_ms,
                    current_ms + TURN_LEASE_DURATION_MS,
                    turn_id,
                ],
            )
            return True

    async def load_turn(self, turn_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM screenplay_conversation_turns WHERE id = ?",
            [str(turn_id or "").strip()],
        )
        return _turn_view(row) if row is not None else None

    async def prepare_resume(
        self,
        turn_id: str,
        *,
        command_id: str,
        request_digest: str,
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            turn = await self._require_turn(turn_id)
            receipt = await self._db.fetch_one(
                "SELECT command_type, request_digest FROM "
                "screenplay_command_receipts WHERE command_id = ?",
                [command_id],
            )
            if receipt is not None:
                if (
                    str(receipt.get("command_type") or "")
                    != "resumeConversationTurn"
                    or str(receipt.get("request_digest") or "")
                    != request_digest
                ):
                    raise AppError(
                        "同一个 Idempotency-Key 不能用于不同的恢复请求",
                        409,
                    )
                return _turn_view(turn)
            if str(turn["status"]) in {"completed", "failed", "canceled"}:
                raise AppError("剧本对话 Turn 已结束，不能恢复", 409)
            await self._db.execute(
                "INSERT INTO screenplay_command_receipts "
                "(command_id, command_type, project_id, request_digest, "
                "result_ref, response_json) VALUES "
                "(?, 'resumeConversationTurn', ?, ?, ?, ?)",
                [
                    command_id,
                    str(turn["project_id"]),
                    request_digest,
                    f"screenplay-conversation-turn://{turn_id}",
                    _dump({"turnId": turn_id}),
                ],
            )
            return _turn_view(turn)

    async def recover_after_restart(self) -> tuple[str, ...]:
        """Release process-owned Turn leases without inventing credentials.

        A recovered Turn returns to ``queued`` and is marked retryable by the
        snapshot. The client must re-inject runtime credentials through the
        explicit resume command before a new Run can be claimed.
        """

        async with self._db.transaction(cancellation_linearizable=True):
            rows = await self._db.fetch_all(
                "SELECT * FROM screenplay_conversation_turns "
                "WHERE status = 'running' ORDER BY rowid ASC"
            )
            recovered: list[str] = []
            for turn in rows:
                turn_id = str(turn["id"])
                await self._db.execute(
                    "UPDATE screenplay_conversation_turns SET "
                    "status = 'queued', execution_owner_id = NULL, "
                    "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ? "
                    "AND status = 'running'",
                    [turn_id],
                )
                await self._append_event_for_turn(
                    turn,
                    "screenplay.conversation.turn_recovery_required",
                    {"reason": "process_restart"},
                )
                recovered.append(turn_id)
            return tuple(recovered)

    async def history_messages(
        self,
        *,
        session_id: int,
        before_turn_id: str,
    ) -> list[dict[str, str]]:
        current = await self._require_turn(before_turn_id)
        current_position = await self._db.fetch_one(
            "SELECT rowid AS position FROM screenplay_conversation_turns "
            "WHERE id = ?",
            [before_turn_id],
        )
        rows = await self._db.fetch_all(
            "SELECT id, user_content, assistant_content FROM "
            "screenplay_conversation_turns WHERE session_id = ? "
            "AND rowid < ? ORDER BY rowid ASC",
            [int(session_id), int(current_position["position"])],
        )
        messages: list[dict[str, str]] = []
        for row in rows:
            messages.append({"role": "user", "content": str(row["user_content"])})
            assistant = str(row.get("assistant_content") or "").strip()
            if assistant:
                messages.append({"role": "assistant", "content": assistant})
        messages.append({
            "role": "user",
            "content": str(current["user_content"]),
        })
        return messages

    async def bind_run(self, turn_id: str, run_id: str) -> None:
        async with self._db.transaction():
            turn = await self._require_owned_running_turn(turn_id)
            existing = str(turn.get("run_id") or "").strip()
            if existing and existing != run_id:
                raise AppError("一个对话 Turn 不能绑定多个 Agent Run", 409)
            await self._db.execute(
                "UPDATE screenplay_conversation_turns SET run_id = ?, "
                "heartbeat_at_ms = ?, lease_expires_at_ms = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [
                    run_id,
                    now_ms(),
                    now_ms() + TURN_LEASE_DURATION_MS,
                    turn_id,
                ],
            )
            await self._append_event_for_turn(
                turn,
                "screenplay.conversation.run_started",
                {"runId": run_id},
            )

    async def append_chunk(
        self,
        turn_id: str,
        *,
        chunk: Mapping[str, Any],
        assistant_delta: str = "",
        revision_id: str | None = None,
    ) -> None:
        async with self._db.transaction():
            turn = await self._require_owned_running_turn(turn_id)
            if assistant_delta:
                await self._db.execute(
                    "UPDATE screenplay_conversation_turns SET "
                    "assistant_content = assistant_content || ?, "
                    "heartbeat_at_ms = ?, lease_expires_at_ms = ?, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                    [
                        assistant_delta,
                        now_ms(),
                        now_ms() + TURN_LEASE_DURATION_MS,
                        turn_id,
                    ],
                )
            if revision_id:
                await self._db.execute(
                    "UPDATE screenplay_conversation_turns SET revision_id = ?, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                    [revision_id, turn_id],
                )
            await self._append_event_for_turn(
                turn,
                "screenplay.conversation.chunk",
                {"chunk": dict(chunk)},
            )

    async def complete_turn(
        self,
        turn_id: str,
        *,
        final_response: str,
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            turn = await self._require_turn(turn_id)
            if str(turn["status"]) == "completed":
                return _turn_view(turn)
            if str(turn["status"]) == "canceled":
                return _turn_view(turn)
            content = str(turn.get("assistant_content") or "")
            if not content.strip():
                content = str(final_response or "")
            await self._db.execute(
                "UPDATE screenplay_conversation_turns SET status = 'completed', "
                "assistant_content = ?, execution_owner_id = NULL, "
                "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [content, turn_id],
            )
            await self._append_event_for_turn(
                turn,
                "screenplay.conversation.turn_completed",
                {
                    "runId": turn.get("run_id"),
                    "operationId": turn.get("operation_id"),
                    "revisionId": turn.get("revision_id"),
                },
            )
            return _turn_view(await self._require_turn(turn_id))

    async def fail_turn(
        self,
        turn_id: str,
        *,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            turn = await self._require_turn(turn_id)
            if str(turn["status"]) in {"completed", "failed", "canceled"}:
                return _turn_view(turn)
            error = {"code": code, "message": str(message or "")[:2_000]}
            await self._db.execute(
                "UPDATE screenplay_conversation_turns SET status = 'failed', "
                "error_json = ?, execution_owner_id = NULL, "
                "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [_dump(error), turn_id],
            )
            await self._append_event_for_turn(
                turn,
                "screenplay.conversation.turn_failed",
                {"error": error},
            )
            return _turn_view(await self._require_turn(turn_id))

    async def cancel_turn(
        self,
        turn_id: str,
        *,
        command_id: str,
        request_digest: str,
    ) -> dict[str, Any]:
        async with self._db.transaction(cancellation_linearizable=True):
            turn = await self._require_turn(turn_id)
            receipt = await self._db.fetch_one(
                "SELECT command_type, request_digest FROM "
                "screenplay_command_receipts WHERE command_id = ?",
                [command_id],
            )
            if receipt is not None:
                if (
                    str(receipt.get("command_type") or "")
                    != "cancelConversationTurn"
                    or str(receipt.get("request_digest") or "")
                    != request_digest
                ):
                    raise AppError(
                        "同一个 Idempotency-Key 不能用于不同的取消请求",
                        409,
                    )
                return _turn_view(turn)
            if str(turn["status"]) not in {"completed", "failed", "canceled"}:
                await self._db.execute(
                    "UPDATE screenplay_conversation_turns SET status = 'canceled', "
                    "execution_owner_id = NULL, lease_expires_at_ms = NULL, "
                    "heartbeat_at_ms = NULL, update_time = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    [turn_id],
                )
                await self._append_event_for_turn(
                    turn,
                    "screenplay.conversation.turn_canceled",
                    {
                        "runId": turn.get("run_id"),
                        "operationId": turn.get("operation_id"),
                    },
                )
            await self._db.execute(
                "INSERT INTO screenplay_command_receipts "
                "(command_id, command_type, project_id, request_digest, "
                "result_ref, response_json) VALUES "
                "(?, 'cancelConversationTurn', ?, ?, ?, ?)",
                [
                    command_id,
                    str(turn["project_id"]),
                    request_digest,
                    f"screenplay-conversation-turn://{turn_id}",
                    _dump({"turnId": turn_id}),
                ],
            )
            return _turn_view(await self._require_turn(turn_id))

    async def get_snapshot(
        self,
        *,
        project_id: str,
        session_id: int,
    ) -> dict[str, Any]:
        await self._require_project_session(
            project_id,
            session_id,
            require_writable=False,
        )
        turns = await self._db.fetch_all(
            "SELECT * FROM screenplay_conversation_turns "
            "WHERE project_id = ? AND session_id = ? "
            "ORDER BY rowid ASC",
            [project_id, int(session_id)],
        )
        cursor = await self._db.fetch_one(
            "SELECT COALESCE(MAX(id), 0) AS cursor "
            "FROM screenplay_conversation_events WHERE session_id = ?",
            [int(session_id)],
        )
        return {
            "projectId": project_id,
            "sessionId": int(session_id),
            "turns": [_turn_view(turn) for turn in turns],
            "cursor": int((cursor or {}).get("cursor") or 0),
        }

    async def list_events(
        self,
        *,
        project_id: str,
        session_id: int,
        after: int,
        limit: int,
    ) -> dict[str, Any]:
        await self._require_project_session(
            project_id,
            session_id,
            require_writable=False,
        )
        rows = await self._db.fetch_all(
            "SELECT * FROM screenplay_conversation_events "
            "WHERE project_id = ? AND session_id = ? AND id > ? "
            "ORDER BY id ASC LIMIT ?",
            [project_id, int(session_id), max(0, int(after)), int(limit) + 1],
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        events = [_event_view(row) for row in page]
        return {
            "events": events,
            "nextCursor": (
                int(page[-1]["id"]) if page else max(0, int(after))
            ),
            "hasMore": has_more,
        }

    async def _require_project_session(
        self,
        project_id: str,
        session_id: int,
        *,
        require_writable: bool = True,
    ) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT s.*, p.status AS project_status "
            "FROM ai_sessions AS s JOIN screenplay_projects AS p "
            "ON p.id = s.screenplay_project_id "
            "WHERE s.id = ? AND s.screenplay_project_id = ? "
            "AND s.scope = 'screenplay' "
            "AND p.source_snapshot_json IS NOT NULL",
            [int(session_id), project_id],
        )
        if row is None:
            raise NotFoundError("剧本对话不存在")
        if require_writable and bool(row.get("closed")):
            raise AppError("剧本对话已关闭，不能继续提交", 409)
        if (
            require_writable
            and str(row.get("project_status") or "") == "archived"
        ):
            raise AppError("项目已归档，不能继续对话", 409)
        return row

    async def _require_turn(self, turn_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM screenplay_conversation_turns WHERE id = ?",
            [str(turn_id or "").strip()],
        )
        if row is None:
            raise NotFoundError("剧本对话 Turn 不存在")
        return row

    async def _require_owned_running_turn(self, turn_id: str) -> dict[str, Any]:
        turn = await self._require_turn(turn_id)
        if (
            str(turn.get("status") or "") != "running"
            or str(turn.get("execution_owner_id") or "") != self._owner_id
        ):
            raise AppError("剧本对话 Turn 的执行租约已失效", 409)
        return turn

    async def _append_event_for_turn(
        self,
        turn: Mapping[str, Any],
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        await self._append_event(
            turn_id=str(turn["id"]),
            project_id=str(turn["project_id"]),
            session_id=int(turn["session_id"]),
            event_type=event_type,
            payload=payload,
        )

    async def _append_event(
        self,
        *,
        turn_id: str,
        project_id: str,
        session_id: int,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        sequence = await self._db.fetch_one(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence "
            "FROM screenplay_conversation_events WHERE turn_id = ?",
            [turn_id],
        )
        await self._db.execute(
            "INSERT INTO screenplay_conversation_events "
            "(project_id, session_id, turn_id, sequence, event_type, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                project_id,
                int(session_id),
                turn_id,
                int((sequence or {}).get("sequence") or 1),
                event_type,
                _dump(dict(payload)),
            ],
        )


def runtime_profile(
    *,
    api_provider: str,
    base_url: str | None,
    options: Mapping[str, Any],
    locale: str,
    context_window: str | None,
) -> dict[str, Any]:
    return {
        "apiProvider": str(api_provider or "openai"),
        "model": str(options.get("model") or ""),
        "modelProfile": str(options.get("model_profile") or "") or None,
        "endpointDigest": digest_model_endpoint(base_url),
        "locale": str(locale or "zh-CN"),
        "contextWindow": str(context_window or "") or None,
    }


def _turn_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "projectId": str(row["project_id"]),
        "sessionId": int(row["session_id"]),
        "commandId": str(row["command_id"]),
        "route": str(row["route"]),
        "status": str(row["status"]),
        "userContent": str(row.get("user_content") or ""),
        "assistantContent": str(row.get("assistant_content") or ""),
        "runtimeProfile": _object(row.get("runtime_profile_json")),
        "operationId": str(row.get("operation_id") or "") or None,
        "runId": str(row.get("run_id") or "") or None,
        "revisionId": str(row.get("revision_id") or "") or None,
        "error": _object(row.get("error_json")) or None,
        "retryable": (
            str(row.get("status") or "") in {"queued", "running"}
            and int(row.get("lease_expires_at_ms") or 0) <= now_ms()
        ),
        "createdAt": row.get("create_time"),
        "updatedAt": row.get("update_time"),
    }


def _event_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "cursor": int(row["id"]),
        "turnId": str(row["turn_id"]),
        "sequence": int(row["sequence"]),
        "type": str(row["event_type"]),
        "payload": _object(row.get("payload_json")),
        "createdAt": row.get("create_time"),
    }


__all__ = [
    "SqliteScreenplayConversationRepository",
    "TURN_LEASE_DURATION_MS",
    "runtime_profile",
]
