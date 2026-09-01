"""Durable delivery of committed business sources to the memory component."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from purra_mem0 import MemorySource

from application.memory_operations import (
    MemoryApplicationService,
    MemoryOperationError,
)


@dataclass(frozen=True, slots=True)
class MemoryDeliveryReceipt:
    operation_key: str
    status: str
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "operationKey": self.operation_key,
            "status": self.status,
            "errorCode": self.error_code,
        }


async def record_source_revision(
    db,
    *,
    book_id: str,
    source_id: str,
    text: str,
    metadata: Mapping[str, Any],
    inference: bool,
    state: str = "active",
) -> tuple[str, ...]:
    """Record source delivery in the transaction that commits the source."""

    if not db.current_task_owns_transaction():
        raise RuntimeError("memory source delivery must join the source transaction")
    clean_book = _required(book_id, "book_id")
    clean_source = _required(source_id, "source_id")
    current = await db.fetch_one(
        "SELECT revision, deleted FROM memory_source_heads "
        "WHERE book_id = ? AND source_id = ?",
        [clean_book, clean_source],
    )
    previous_revision = int(current["revision"]) if current else None
    revision = (previous_revision or 0) + 1
    keys: list[str] = []
    if previous_revision is not None and not bool(current.get("deleted")):
        key = _delivery_key(clean_book, clean_source, previous_revision, "revoke")
        await _insert_delivery(
            db,
            operation_key=key,
            book_id=clean_book,
            source_id=clean_source,
            source_revision=str(previous_revision),
            action="revoke_revision",
            payload={},
        )
        keys.append(key)

    await db.execute(
        "INSERT INTO memory_source_heads "
        "(book_id, source_id, revision, deleted, update_time) "
        "VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP) "
        "ON CONFLICT(book_id, source_id) DO UPDATE SET "
        "revision=excluded.revision, deleted=0, update_time=CURRENT_TIMESTAMP",
        [clean_book, clean_source, revision],
    )
    clean_text = str(text or "").strip()
    if clean_text:
        action = "extract" if inference else "add"
        key = _delivery_key(clean_book, clean_source, revision, action)
        await _insert_delivery(
            db,
            operation_key=key,
            book_id=clean_book,
            source_id=clean_source,
            source_revision=str(revision),
            action=action,
            payload={
                "text": clean_text,
                "metadata": dict(metadata),
                "state": state,
            },
        )
        keys.append(key)
    return tuple(keys)


async def record_source_deletion(
    db,
    *,
    book_id: str,
    source_id: str,
) -> tuple[str, ...]:
    if not db.current_task_owns_transaction():
        raise RuntimeError("memory source deletion must join the source transaction")
    clean_book = _required(book_id, "book_id")
    clean_source = _required(source_id, "source_id")
    current = await db.fetch_one(
        "SELECT revision FROM memory_source_heads "
        "WHERE book_id = ? AND source_id = ?",
        [clean_book, clean_source],
    )
    revision = int(current["revision"]) + 1 if current else 1
    await db.execute(
        "INSERT INTO memory_source_heads "
        "(book_id, source_id, revision, deleted, update_time) "
        "VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP) "
        "ON CONFLICT(book_id, source_id) DO UPDATE SET "
        "revision=excluded.revision, deleted=1, update_time=CURRENT_TIMESTAMP",
        [clean_book, clean_source, revision],
    )
    key = _delivery_key(clean_book, clean_source, revision, "delete")
    await _insert_delivery(
        db,
        operation_key=key,
        book_id=clean_book,
        source_id=clean_source,
        source_revision=None,
        action="revoke_source",
        payload={},
    )
    return (key,)


async def record_book_deletion(db, *, book_id: str) -> str:
    if not db.current_task_owns_transaction():
        raise RuntimeError("memory book deletion must join the book transaction")
    clean_book = _required(book_id, "book_id")
    operation_key = f"book-delete:{clean_book}"
    await db.execute(
        "INSERT OR IGNORE INTO memory_book_deletions "
        "(book_id, operation_key) VALUES (?, ?)",
        [clean_book, operation_key],
    )
    row = await db.fetch_one(
        "SELECT operation_key FROM memory_book_deletions WHERE book_id = ?",
        [clean_book],
    )
    if row != {"operation_key": operation_key}:
        raise MemoryOperationError("memory_delivery_idempotency_conflict")
    return operation_key


class MemoryDeliveryService:
    def __init__(self, db, memory: MemoryApplicationService) -> None:
        self._db = db
        self._memory = memory

    async def deliver_many(
        self,
        operation_keys: tuple[str, ...] | list[str],
    ) -> tuple[MemoryDeliveryReceipt, ...]:
        receipts = []
        for key in operation_keys:
            receipts.append(await self.deliver(key))
        return tuple(receipts)

    async def deliver(self, operation_key: str) -> MemoryDeliveryReceipt:
        row = await self._db.fetch_one(
            "SELECT * FROM memory_source_deliveries WHERE operation_key = ?",
            [operation_key],
        )
        if row is None:
            return MemoryDeliveryReceipt(operation_key, "missing", "memory_delivery_missing")
        if row["status"] == "completed":
            return MemoryDeliveryReceipt(operation_key, "completed")
        if row["status"] == "delivering":
            return MemoryDeliveryReceipt(operation_key, "delivering")

        await self._db.execute(
            "UPDATE memory_source_deliveries SET status='delivering', "
            "attempt_count=attempt_count+1, error_code=NULL, "
            "update_time=CURRENT_TIMESTAMP WHERE operation_key = ?",
            [operation_key],
        )
        try:
            payload = json.loads(row["payload_json"])
            receipt = await self._apply(row, payload)
        except Exception as error:
            code = getattr(error, "code", None)
            public_code = (
                str(code) if isinstance(code, str) and code
                else "memory_delivery_failed"
            )
            await self._db.execute(
                "UPDATE memory_source_deliveries SET status='failed', "
                "error_code=?, update_time=CURRENT_TIMESTAMP "
                "WHERE operation_key = ?",
                [public_code, operation_key],
            )
            return MemoryDeliveryReceipt(operation_key, "failed", public_code)

        await self._db.execute(
            "UPDATE memory_source_deliveries SET status='completed', "
            "receipt_json=?, error_code=NULL, update_time=CURRENT_TIMESTAMP "
            "WHERE operation_key = ?",
            [
                json.dumps(receipt, ensure_ascii=False, sort_keys=True),
                operation_key,
            ],
        )
        return MemoryDeliveryReceipt(operation_key, "completed")

    async def recover(self, *, limit: int = 50) -> tuple[MemoryDeliveryReceipt, ...]:
        rows = await self._db.fetch_all(
            "SELECT operation_key FROM memory_source_deliveries "
            "WHERE status IN ('pending', 'delivering', 'failed') "
            "AND attempt_count < 3 ORDER BY create_time ASC LIMIT ?",
            [limit],
        )
        if rows:
            await self._db.execute(
                "UPDATE memory_source_deliveries SET status='pending' "
                "WHERE status='delivering' AND attempt_count < 3"
            )
        receipts = list(await self.deliver_many(
            tuple(row["operation_key"] for row in rows)
        ))
        book_rows = await self._db.fetch_all(
            "SELECT operation_key FROM memory_book_deletions "
            "WHERE status IN ('pending', 'delivering', 'failed') "
            "AND attempt_count < 3 ORDER BY create_time ASC LIMIT ?",
            [limit],
        )
        if book_rows:
            await self._db.execute(
                "UPDATE memory_book_deletions SET status='pending' "
                "WHERE status='delivering' AND attempt_count < 3"
            )
        for row in book_rows:
            receipts.append(await self.deliver_book_deletion(
                row["operation_key"]
            ))
        return tuple(receipts)

    async def deliver_book_deletion(
        self,
        operation_key: str,
    ) -> MemoryDeliveryReceipt:
        row = await self._db.fetch_one(
            "SELECT * FROM memory_book_deletions WHERE operation_key = ?",
            [operation_key],
        )
        if row is None:
            return MemoryDeliveryReceipt(
                operation_key,
                "missing",
                "memory_delivery_missing",
            )
        if row["status"] == "completed":
            return MemoryDeliveryReceipt(operation_key, "completed")
        if row["status"] == "delivering":
            return MemoryDeliveryReceipt(operation_key, "delivering")
        await self._db.execute(
            "UPDATE memory_book_deletions SET status='delivering', "
            "attempt_count=attempt_count+1, error_code=NULL, "
            "update_time=CURRENT_TIMESTAMP WHERE operation_key = ?",
            [operation_key],
        )
        try:
            await self._memory.delete_book_scope(
                book_id=row["book_id"],
                operation_key=operation_key,
            )
        except Exception as error:
            code = getattr(error, "code", None)
            public_code = (
                str(code) if isinstance(code, str) and code
                else "memory_delivery_failed"
            )
            await self._db.execute(
                "UPDATE memory_book_deletions SET status='failed', "
                "error_code=?, update_time=CURRENT_TIMESTAMP "
                "WHERE operation_key = ?",
                [public_code, operation_key],
            )
            return MemoryDeliveryReceipt(
                operation_key,
                "failed",
                public_code,
            )
        await self._db.execute(
            "UPDATE memory_book_deletions SET status='completed', "
            "error_code=NULL, update_time=CURRENT_TIMESTAMP "
            "WHERE operation_key = ?",
            [operation_key],
        )
        await self._db.execute(
            "UPDATE memory_source_deliveries SET status='completed', "
            "receipt_json=?, error_code=NULL, update_time=CURRENT_TIMESTAMP "
            "WHERE book_id = ? AND status <> 'completed'",
            [
                json.dumps({"outcome": "book_deleted"}),
                row["book_id"],
            ],
        )
        return MemoryDeliveryReceipt(operation_key, "completed")

    async def _apply(self, row: Mapping[str, Any], payload: Mapping[str, Any]):
        action = row["action"]
        source = MemorySource(row["source_id"], row["source_revision"] or "1")
        if action == "add":
            return await self._memory.add_source(
                book_id=row["book_id"],
                key=row["operation_key"],
                text=payload["text"],
                source=source,
                metadata=payload["metadata"],
                state=payload.get("state", "active"),
            )
        if action == "extract":
            return await self._memory.extract_source(
                book_id=row["book_id"],
                key=row["operation_key"],
                messages=({"role": "user", "content": payload["text"]},),
                source=source,
                metadata=payload["metadata"],
            )
        if action in {"revoke_revision", "revoke_source"}:
            return await self._memory.revoke_source(
                book_id=row["book_id"],
                source_id=row["source_id"],
                revision=(
                    row["source_revision"]
                    if action == "revoke_revision"
                    else None
                ),
                operation_key=row["operation_key"],
            )
        raise MemoryOperationError("memory_delivery_action_invalid")


async def _insert_delivery(
    db,
    *,
    operation_key: str,
    book_id: str,
    source_id: str,
    source_revision: str | None,
    action: str,
    payload: Mapping[str, Any],
) -> None:
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    await db.execute(
        "INSERT OR IGNORE INTO memory_source_deliveries "
        "(operation_key, book_id, source_id, source_revision, action, payload_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [operation_key, book_id, source_id, source_revision, action, payload_json],
    )
    existing = await db.fetch_one(
        "SELECT book_id, source_id, source_revision, action, payload_json "
        "FROM memory_source_deliveries WHERE operation_key = ?",
        [operation_key],
    )
    expected = {
        "book_id": book_id,
        "source_id": source_id,
        "source_revision": source_revision,
        "action": action,
        "payload_json": payload_json,
    }
    if existing != expected:
        raise MemoryOperationError("memory_delivery_idempotency_conflict")


def _delivery_key(book_id: str, source_id: str, revision: int, action: str) -> str:
    value = f"source:{book_id}:{source_id}:{revision}:{action}"
    if len(value) > 512:
        raise MemoryOperationError("memory_delivery_key_too_long")
    return value


def _required(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise MemoryOperationError(f"memory_delivery_{name}_required")
    return text


__all__ = [
    "MemoryDeliveryReceipt",
    "MemoryDeliveryService",
    "record_source_deletion",
    "record_source_revision",
    "record_book_deletion",
]
