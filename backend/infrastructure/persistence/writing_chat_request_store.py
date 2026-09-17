"""Durable product receipts for renderer-initiated Writing chat requests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from purra.contracts import RunBinding


ACTIVE_STATUSES = frozenset({"accepted", "starting"})
TERMINAL_STATUSES = frozenset({"rejected", "canceled"})


class WritingChatRequestConflictError(ValueError):
    pass


@dataclass(frozen=True)
class WritingChatRequestReceipt:
    request_id: str
    session_id: int
    request_digest: str
    status: str
    run_id: str | None
    cancel_requested_at_ms: int | None
    cancel_applied_run_id: str | None
    rejection_code: str | None
    revision: int

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "sessionId": self.session_id,
            "status": self.status,
            "runId": self.run_id,
            "cancelRequested": self.cancel_requested_at_ms is not None,
            "rejectionCode": self.rejection_code,
            "revision": self.revision,
        }


def writing_chat_request_digest(value: Mapping[str, Any]) -> str:
    semantic = dict(value)
    # Bind retries to the same credential without persisting the secret. A
    # renderer cannot reserve with one account/key and replay the same command
    # under another credential while keeping the request identity.
    api_key = str(semantic.pop("apiKey", "") or "")
    semantic["apiKeyDigest"] = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    semantic.pop("streamId", None)
    encoded = json.dumps(
        semantic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class SqliteWritingChatRequestStore:
    def __init__(self, db) -> None:
        self._db = db

    async def reserve(
        self,
        *,
        request_id: str,
        session_id: int,
        request_digest: str,
        book_id: str | None,
        chapter_id: str | None,
        expected_conversation_ids: list[int] | None = None,
        expected_run_ids: list[str] | None = None,
    ) -> WritingChatRequestReceipt:
        normalized = _required_text(request_id, "request id")
        digest = _required_text(request_digest, "request digest")
        async with self._db.transaction(cancellation_linearizable=True):
            session = await self._db.fetch_one(
                "SELECT id, book_id, chapter_id FROM ai_sessions WHERE id = ?",
                [int(session_id)],
            )
            if session is None:
                raise ValueError("Writing chat session does not exist")
            _require_session_scope(
                session,
                book_id=book_id,
                chapter_id=chapter_id,
            )
            existing = await self._get(normalized)
            if existing is not None:
                _require_same(existing, int(session_id), digest)
                return existing
            active_request = await self._db.fetch_one(
                "SELECT request_id FROM ai_writing_chat_requests "
                "WHERE session_id = ? AND status IN ('accepted', 'starting') "
                "LIMIT 1",
                [int(session_id)],
            )
            active_run = await self._db.fetch_one(
                "SELECT id FROM ai_agent_runs WHERE session_id = ? "
                "AND status IN ('pending', 'queued', 'running', 'paused') "
                "LIMIT 1",
                [int(session_id)],
            )
            if active_request is not None or active_run is not None:
                raise WritingChatRequestConflictError(
                    "Writing chat session already has active work"
                )
            if not await self._history_frontier_matches(
                int(session_id),
                expected_conversation_ids=expected_conversation_ids,
                expected_run_ids=expected_run_ids,
            ):
                raise WritingChatRequestConflictError(
                    "Writing chat history changed; reload before sending"
                )
            await self._db.execute(
                "INSERT INTO ai_writing_chat_requests "
                "(request_id, session_id, request_digest, status) "
                "VALUES (?, ?, ?, 'accepted')",
                [normalized, int(session_id), digest],
            )
            return _receipt(await self._require_row(normalized))

    async def claim(
        self,
        *,
        request_id: str,
        session_id: int,
        request_digest: str,
        book_id: str | None,
        chapter_id: str | None,
        expected_conversation_ids: list[int] | None = None,
        expected_run_ids: list[str] | None = None,
    ) -> tuple[WritingChatRequestReceipt, bool]:
        normalized = _required_text(request_id, "request id")
        async with self._db.transaction(cancellation_linearizable=True):
            existing = await self._get(normalized)
            if existing is None:
                raise ValueError("Writing chat request was not reserved")
            _require_same(existing, int(session_id), request_digest)
            if existing.status != "accepted":
                return existing, False
            session = await self._db.fetch_one(
                "SELECT id, book_id, chapter_id FROM ai_sessions WHERE id = ?",
                [int(session_id)],
            )
            if session is None:
                await self._reject_in_transaction(
                    normalized,
                    "session_deleted_before_start",
                )
                return _receipt(await self._require_row(normalized)), False
            _require_session_scope(
                session,
                book_id=book_id,
                chapter_id=chapter_id,
            )
            if not await self._history_frontier_matches(
                int(session_id),
                expected_conversation_ids=expected_conversation_ids,
                expected_run_ids=expected_run_ids,
            ):
                await self._reject_in_transaction(
                    normalized,
                    "history_changed_before_start",
                )
                return _receipt(await self._require_row(normalized)), False
            await self._db.execute(
                "UPDATE ai_writing_chat_requests SET status = 'starting', "
                "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
                "WHERE request_id = ? AND status = 'accepted'",
                [normalized],
            )
            return _receipt(await self._require_row(normalized)), True

    async def _history_frontier_matches(
        self,
        session_id: int,
        *,
        expected_conversation_ids: list[int] | None,
        expected_run_ids: list[str] | None,
    ) -> bool:
        if expected_conversation_ids is not None:
            rows = await self._db.fetch_all(
                "SELECT id FROM ai_conversations WHERE session_id = ? "
                "ORDER BY id ASC",
                [int(session_id)],
            )
            current = [int(row["id"]) for row in rows]
            expected = sorted({int(value) for value in expected_conversation_ids})
            if current != expected:
                return False
        if expected_run_ids is not None:
            from infrastructure.persistence.run_store import ROOT_RUN_SESSION_FILTER

            rows = await self._db.fetch_all(
                "SELECT id FROM ai_agent_runs WHERE session_id = ? "
                f"AND {ROOT_RUN_SESSION_FILTER} "
                "ORDER BY rowid ASC",
                [int(session_id)],
            )
            current = {str(row["id"]) for row in rows}
            expected = {
                str(value).strip() for value in expected_run_ids
                if str(value).strip()
            }
            if current != expected:
                return False
        return True

    async def before_submit(self, request_id: str) -> bool:
        normalized = _required_text(request_id, "request id")
        async with self._db.transaction(cancellation_linearizable=True):
            row = await self._db.fetch_one(
                "SELECT r.status, r.cancel_requested_at_ms, s.id AS owner_id "
                "FROM ai_writing_chat_requests AS r "
                "LEFT JOIN ai_sessions AS s ON s.id = r.session_id "
                "WHERE r.request_id = ?",
                [normalized],
            )
            return bool(
                row
                and row.get("owner_id") is not None
                and row.get("status") == "starting"
                and row.get("cancel_requested_at_ms") is None
            )

    async def bind_run(
        self,
        request_id: str,
        run_id: str,
    ) -> WritingChatRequestReceipt:
        normalized = _required_text(request_id, "request id")
        normalized_run = _required_text(run_id, "run id")
        async with self._db.transaction(cancellation_linearizable=True):
            return await self._bind_run_in_transaction(normalized, normalized_run)

    async def _bind_run_in_transaction(
        self,
        normalized: str,
        normalized_run: str,
    ) -> WritingChatRequestReceipt:
        receipt = _receipt(await self._require_row(normalized))
        run = await self._db.fetch_one(
                "SELECT session_id, binding_namespace, binding_aggregate_id, "
                "binding_command_id FROM ai_agent_runs WHERE id = ?",
                [normalized_run],
            )
        if run is None:
            raise ValueError("Writing chat Run does not exist")
        expected = RunBinding(
                namespace="writing.chat.request",
                aggregate_id=str(receipt.session_id),
                command_id=normalized,
            )
        if (
                int(run.get("session_id") or 0) != receipt.session_id
                or run.get("binding_namespace") != expected.namespace
                or run.get("binding_aggregate_id") != expected.aggregate_id
                or run.get("binding_command_id") != expected.command_id
        ):
            raise WritingChatRequestConflictError(
                "Run binding does not own this Writing chat request"
            )
        if receipt.run_id and receipt.run_id != normalized_run:
            raise WritingChatRequestConflictError(
                "Writing chat request is already bound to another Run"
            )
        if receipt.status in {"starting", "run_bound"}:
            await self._db.execute(
                    "UPDATE ai_writing_chat_requests SET "
                    "status = 'run_bound', run_id = COALESCE(run_id, ?), "
                    "cancel_applied_run_id = CASE "
                    "WHEN cancel_requested_at_ms IS NOT NULL "
                    "THEN COALESCE(cancel_applied_run_id, ?) "
                    "ELSE cancel_applied_run_id END, "
                    "revision = revision + CASE "
                    "WHEN status <> 'run_bound' OR run_id IS NULL "
                    "OR (cancel_requested_at_ms IS NOT NULL "
                    "AND cancel_applied_run_id IS NULL) THEN 1 ELSE 0 END, "
                    "update_time = CURRENT_TIMESTAMP WHERE request_id = ?",
                [normalized_run, normalized_run, normalized],
            )
        if receipt.cancel_requested_at_ms is not None:
            await self._db.execute(
                    "UPDATE ai_agent_runs SET cancel_requested_at_ms = "
                    "COALESCE(cancel_requested_at_ms, ?), "
                    "update_time = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND status = 'running'",
                [receipt.cancel_requested_at_ms, normalized_run],
            )
        return _receipt(await self._require_row(normalized))

    async def request_cancel(
        self,
        request_id: str,
        *,
        timestamp_ms: int,
    ) -> WritingChatRequestReceipt | None:
        receipt, _application_needed = await self.request_cancel_and_claim(
            request_id,
            timestamp_ms=timestamp_ms,
        )
        return receipt

    async def request_cancel_and_claim(
        self,
        request_id: str,
        *,
        timestamp_ms: int,
    ) -> tuple[WritingChatRequestReceipt | None, bool]:
        normalized = _required_text(request_id, "request id")
        async with self._db.transaction(cancellation_linearizable=True):
            receipt = await self._get(normalized)
            if receipt is None:
                return None, False
            if receipt.status in TERMINAL_STATUSES:
                return receipt, False
            if receipt.status == "accepted":
                await self._db.execute(
                    "UPDATE ai_writing_chat_requests SET status = 'canceled', "
                    "cancel_requested_at_ms = COALESCE(cancel_requested_at_ms, ?), "
                    "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
                    "WHERE request_id = ? AND status = 'accepted'",
                    [int(timestamp_ms), normalized],
                )
            else:
                await self._db.execute(
                    "UPDATE ai_writing_chat_requests SET "
                    "cancel_requested_at_ms = COALESCE(cancel_requested_at_ms, ?), "
                    "revision = revision + CASE "
                    "WHEN cancel_requested_at_ms IS NULL THEN 1 ELSE 0 END, "
                    "update_time = CURRENT_TIMESTAMP WHERE request_id = ?",
                    [int(timestamp_ms), normalized],
                )
            current = _receipt(await self._require_row(normalized))
            application_needed = bool(
                current.run_id
                and current.cancel_applied_run_id != current.run_id
            )
            if current.run_id:
                await self._db.execute(
                    "UPDATE ai_agent_runs SET cancel_requested_at_ms = "
                    "COALESCE(cancel_requested_at_ms, ?), "
                    "update_time = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND status = 'running'",
                    [int(timestamp_ms), current.run_id],
                )
            return (
                _receipt(await self._require_row(normalized)),
                application_needed,
            )

    async def mark_cancel_applied(
        self,
        request_id: str,
        run_id: str,
    ) -> WritingChatRequestReceipt:
        normalized = _required_text(request_id, "request id")
        normalized_run = _required_text(run_id, "run id")
        async with self._db.transaction(cancellation_linearizable=True):
            receipt = _receipt(await self._require_row(normalized))
            if receipt.run_id != normalized_run:
                raise WritingChatRequestConflictError(
                    "Writing chat cancel result does not own this Run"
                )
            await self._db.execute(
                "UPDATE ai_writing_chat_requests SET "
                "cancel_applied_run_id = COALESCE(cancel_applied_run_id, ?), "
                "update_time = CURRENT_TIMESTAMP WHERE request_id = ?",
                [normalized_run, normalized],
            )
            return _receipt(await self._require_row(normalized))

    async def reject(self, request_id: str, code: str) -> None:
        async with self._db.transaction(cancellation_linearizable=True):
            await self._reject_in_transaction(request_id, code)

    async def finish_before_run(
        self,
        request_id: str,
        rejection_code: str,
    ) -> WritingChatRequestReceipt:
        normalized = _required_text(request_id, "request id")
        async with self._db.transaction(cancellation_linearizable=True):
            receipt = _receipt(await self._require_row(normalized))
            exact_run = await self._db.fetch_one(
                "SELECT id FROM ai_agent_runs WHERE binding_namespace = ? "
                "AND binding_aggregate_id = ? AND binding_command_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                [
                    "writing.chat.request",
                    str(receipt.session_id),
                    receipt.request_id,
                ],
            )
            if exact_run is not None:
                return await self._bind_run_in_transaction(
                    normalized,
                    str(exact_run["id"]),
                )
            if receipt.run_id is not None or receipt.status not in ACTIVE_STATUSES:
                return receipt
            if receipt.cancel_requested_at_ms is not None:
                await self._db.execute(
                    "UPDATE ai_writing_chat_requests SET status = 'canceled', "
                    "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
                    "WHERE request_id = ? AND status IN ('accepted', 'starting') "
                    "AND run_id IS NULL",
                    [normalized],
                )
            else:
                await self._reject_in_transaction(normalized, rejection_code)
            return _receipt(await self._require_row(normalized))

    async def get(self, request_id: str) -> WritingChatRequestReceipt | None:
        return await self._get(_required_text(request_id, "request id"))

    async def latest_active_for_session(
        self,
        session_id: int,
    ) -> WritingChatRequestReceipt | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_writing_chat_requests WHERE session_id = ? "
            "AND status IN ('accepted', 'starting') "
            "ORDER BY create_time DESC, rowid DESC LIMIT 1",
            [int(session_id)],
        )
        return _receipt(row) if row else None

    async def recover_unbound(self) -> tuple[str, ...]:
        repaired: list[str] = []
        async with self._db.transaction(cancellation_linearizable=True):
            rows = await self._db.fetch_all(
                "SELECT * FROM ai_writing_chat_requests "
                "WHERE status IN ('accepted', 'starting') "
                "ORDER BY create_time, request_id"
            )
            for row in rows:
                receipt = _receipt(row)
                run = await self._db.fetch_one(
                    "SELECT id FROM ai_agent_runs WHERE binding_namespace = ? "
                    "AND binding_aggregate_id = ? AND binding_command_id = ? "
                    "ORDER BY rowid DESC LIMIT 1",
                    ["writing.chat.request", str(receipt.session_id), receipt.request_id],
                )
                if run:
                    await self._bind_run_in_transaction(
                        receipt.request_id,
                        str(run["id"]),
                    )
                    repaired.append(receipt.request_id)
                elif receipt.cancel_requested_at_ms is not None:
                    await self._db.execute(
                        "UPDATE ai_writing_chat_requests SET status = 'canceled', "
                        "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
                        "WHERE request_id = ?",
                        [receipt.request_id],
                    )
                else:
                    await self._reject_in_transaction(
                        receipt.request_id,
                        "execution_recovery_before_run",
                    )
        return tuple(repaired)

    async def _reject_in_transaction(self, request_id: str, code: str) -> None:
        await self._db.execute(
            "UPDATE ai_writing_chat_requests SET status = 'rejected', "
            "rejection_code = ?, revision = revision + 1, "
            "update_time = CURRENT_TIMESTAMP WHERE request_id = ? "
            "AND status IN ('accepted', 'starting') AND run_id IS NULL",
            [_required_text(code, "rejection code"), request_id],
        )

    async def _get(self, request_id: str) -> WritingChatRequestReceipt | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_writing_chat_requests WHERE request_id = ?",
            [request_id],
        )
        return _receipt(row) if row else None

    async def _require_row(self, request_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_writing_chat_requests WHERE request_id = ?",
            [request_id],
        )
        if row is None:
            raise RuntimeError("Writing chat request receipt was not persisted")
        return row


def _receipt(row: Mapping[str, Any]) -> WritingChatRequestReceipt:
    return WritingChatRequestReceipt(
        request_id=str(row["request_id"]),
        session_id=int(row["session_id"]),
        request_digest=str(row["request_digest"]),
        status=str(row["status"]),
        run_id=str(row["run_id"]) if row.get("run_id") else None,
        cancel_requested_at_ms=(
            int(row["cancel_requested_at_ms"])
            if row.get("cancel_requested_at_ms") is not None else None
        ),
        cancel_applied_run_id=(
            str(row["cancel_applied_run_id"])
            if row.get("cancel_applied_run_id") else None
        ),
        rejection_code=(
            str(row["rejection_code"]) if row.get("rejection_code") else None
        ),
        revision=int(row.get("revision") or 1),
    )


def _required_text(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _require_same(
    receipt: WritingChatRequestReceipt,
    session_id: int,
    request_digest: str,
) -> None:
    if receipt.session_id != session_id or receipt.request_digest != request_digest:
        raise WritingChatRequestConflictError(
            "Writing chat request id was reused with different input"
        )


def _require_session_scope(
    session: Mapping[str, Any],
    *,
    book_id: str | None,
    chapter_id: str | None,
) -> None:
    expected_book = str(session.get("book_id") or "").strip() or None
    expected_chapter = str(session.get("chapter_id") or "").strip() or None
    actual_book = str(book_id or "").strip() or None
    actual_chapter = str(chapter_id or "").strip() or None
    if actual_book != expected_book or actual_chapter != expected_chapter:
        raise WritingChatRequestConflictError(
            "Writing chat request scope does not own this session"
        )
