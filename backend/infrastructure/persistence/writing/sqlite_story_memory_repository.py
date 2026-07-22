"""SQLite implementation of the versioned Story Memory ledger."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Mapping, Sequence
from uuid import uuid4

from domains.writing.story_memory import (
    ChapterMemoryInvalidationReceipt,
    SourceReference,
    StoryMemoryApplyReceipt,
    StoryMemoryChange,
    StoryMemoryConflictError,
    StoryMemoryDelta,
    StoryMemoryDeltaDraft,
    StoryMemoryDeltaStatus,
    StoryMemoryKind,
    StoryMemoryLifecycle,
    StoryMemoryNotFoundError,
    StoryMemoryOperation,
    StoryMemoryProvenanceStatus,
    StoryMemoryRecord,
    StoryMemorySourceStatus,
    StoryMemoryStatus,
    StoryMemoryVersion,
    validate_delta_draft,
)

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


class SqliteStoryMemoryRepository:
    """Persist chapter deltas and atomically project them into current state."""

    def __init__(self, db: DatabaseConnection):
        self._db = db

    async def create_delta(self, draft: StoryMemoryDeltaDraft) -> StoryMemoryDelta:
        validate_delta_draft(draft)
        delta_id = str(uuid4())
        async with self._db.transaction():
            await self._db.execute(
                "INSERT INTO story_memory_deltas "
                "(id, book_id, chapter_id, source_revision, source_type, note) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    delta_id,
                    str(draft.book_id).strip(),
                    str(draft.chapter_id).strip(),
                    str(draft.source_revision or "").strip(),
                    str(draft.source_type or "manual").strip() or "manual",
                    str(draft.note or "").strip(),
                ],
            )
            for index, change in enumerate(draft.changes):
                source_id = str(uuid4())
                source_revision = (
                    str(change.source.source_revision or "").strip()
                    or str(draft.source_revision or "").strip()
                )
                await self._db.execute(
                    "INSERT INTO story_memory_sources "
                    "(id, delta_id, operation_index, book_id, chapter_id, "
                    "source_revision, excerpt, locator_json, narrative_order, "
                    "story_time, story_time_precision) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        source_id,
                        delta_id,
                        index,
                        str(draft.book_id).strip(),
                        str(draft.chapter_id).strip(),
                        source_revision,
                        str(change.source.excerpt).strip(),
                        _json_dump(change.source.locator),
                        change.source.narrative_order,
                        change.source.story_time,
                        change.source.story_time_precision,
                    ],
                )
                await self._db.execute(
                    "INSERT INTO story_memory_delta_operations "
                    "(delta_id, operation_index, operation, target_key, kind, "
                    "subject_id, after_payload_json, after_status, confidence, "
                    "source_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        delta_id,
                        index,
                        change.operation.value,
                        str(change.target_key).strip(),
                        change.kind.value,
                        _optional_text(change.subject_id),
                        _json_dump(change.payload),
                        change.status.value,
                        float(change.confidence),
                        source_id,
                    ],
                )
        created = await self.get_delta(delta_id)
        if created is None:  # pragma: no cover - durable insert invariant
            raise StoryMemoryNotFoundError(delta_id)
        return created

    async def get_delta(self, delta_id: str) -> StoryMemoryDelta | None:
        row = await self._db.fetch_one(
            "SELECT * FROM story_memory_deltas WHERE id = ?",
            [str(delta_id)],
        )
        if row is None:
            return None
        operation_rows = await self._db.fetch_all(
            "SELECT o.*, s.chapter_id AS source_chapter_id, "
            "s.source_revision AS source_revision, s.excerpt AS source_excerpt, "
            "s.status AS source_status, "
            "s.locator_json AS source_locator_json, "
            "s.narrative_order AS source_narrative_order, "
            "s.story_time AS source_story_time, "
            "s.story_time_precision AS source_story_time_precision "
            "FROM story_memory_delta_operations AS o "
            "JOIN story_memory_sources AS s ON s.id = o.source_id "
            "WHERE o.delta_id = ? ORDER BY o.operation_index ASC",
            [str(delta_id)],
        )
        changes = tuple(_change_from_row(item) for item in operation_rows)
        return StoryMemoryDelta(
            id=str(row["id"]),
            book_id=str(row["book_id"]),
            chapter_id=str(row["chapter_id"]),
            source_revision=str(row.get("source_revision") or ""),
            source_type=str(row.get("source_type") or "manual"),
            note=str(row.get("note") or ""),
            status=StoryMemoryDeltaStatus(str(row["status"])),
            changes=changes,
            create_time=_optional_text(row.get("create_time")),
            applied_at=_optional_text(row.get("applied_at")),
            reverted_at=_optional_text(row.get("reverted_at")),
            invalidated_at=_optional_text(row.get("invalidated_at")),
        )

    async def apply_delta(self, delta_id: str) -> StoryMemoryApplyReceipt:
        normalized_id = str(delta_id).strip()
        changed_keys: tuple[str, ...] = ()
        result_status = StoryMemoryDeltaStatus.APPLIED
        async with self._db.transaction(cancellation_linearizable=True):
            delta = await self._require_delta_row(normalized_id)
            operations = await self._operation_rows(normalized_id)
            changed_keys = tuple(str(row["target_key"]) for row in operations)
            current_status = StoryMemoryDeltaStatus(str(delta["status"]))
            if current_status in {
                StoryMemoryDeltaStatus.APPLIED,
                StoryMemoryDeltaStatus.NEEDS_REVIEW,
            }:
                result_status = current_status
            elif current_status is not StoryMemoryDeltaStatus.PENDING:
                raise StoryMemoryConflictError(
                    f"delta {normalized_id} cannot be applied from {current_status.value}"
                )
            else:
                for operation in operations:
                    await self._apply_operation(delta, operation)
                await self._db.execute(
                    "UPDATE story_memory_deltas SET status = 'applied', "
                    "applied_at = datetime('now'), invalidated_at = NULL "
                    "WHERE id = ?",
                    [normalized_id],
                )
                await self._db.execute(
                    "UPDATE story_memory_evolution_reviews SET "
                    "review_status = 'resolved', resolution = 'accepted', "
                    "resolved_delta_id = ?, "
                    "resolution_actor = COALESCE(resolution_actor, 'direct_apply'), "
                    "resolved_at = COALESCE(resolved_at, datetime('now')), "
                    "update_time = datetime('now') WHERE delta_id = ?",
                    [normalized_id, normalized_id],
                )
        return StoryMemoryApplyReceipt(
            delta_id=normalized_id,
            status=result_status,
            changed_keys=changed_keys,
        )

    async def rollback_delta(self, delta_id: str) -> StoryMemoryApplyReceipt:
        normalized_id = str(delta_id).strip()
        changed_keys: tuple[str, ...] = ()
        async with self._db.transaction(cancellation_linearizable=True):
            delta = await self._require_delta_row(normalized_id)
            operations = await self._operation_rows(normalized_id, descending=True)
            changed_keys = tuple(
                str(row["target_key"]) for row in reversed(operations)
            )
            current_status = StoryMemoryDeltaStatus(str(delta["status"]))
            if current_status is StoryMemoryDeltaStatus.REVERTED:
                return StoryMemoryApplyReceipt(
                    delta_id=normalized_id,
                    status=StoryMemoryDeltaStatus.REVERTED,
                    changed_keys=changed_keys,
                )
            if current_status not in {
                StoryMemoryDeltaStatus.APPLIED,
                StoryMemoryDeltaStatus.NEEDS_REVIEW,
            }:
                raise StoryMemoryConflictError(
                    f"delta {normalized_id} cannot be rolled back from "
                    f"{current_status.value}"
                )
            for operation in operations:
                await self._rollback_operation(delta, operation)
            await self._db.execute(
                "UPDATE story_memory_deltas SET status = 'reverted', "
                "reverted_at = datetime('now') WHERE id = ?",
                [normalized_id],
            )
            await self._db.execute(
                "UPDATE story_memory_evolution_reviews SET "
                "review_status = 'resolved', resolution = 'reverted', "
                "update_time = datetime('now') WHERE delta_id = ?",
                [normalized_id],
            )
        return StoryMemoryApplyReceipt(
            delta_id=normalized_id,
            status=StoryMemoryDeltaStatus.REVERTED,
            changed_keys=changed_keys,
        )

    async def get_record(
        self,
        book_id: str,
        memory_key: str,
        *,
        include_reverted: bool = False,
    ) -> StoryMemoryRecord | None:
        lifecycle_clause = "" if include_reverted else " AND lifecycle = 'active'"
        row = await self._db.fetch_one(
            "SELECT * FROM story_memory_records WHERE book_id = ? "
            f"AND memory_key = ?{lifecycle_clause}",
            [str(book_id).strip(), str(memory_key).strip()],
        )
        return _record_from_row(row) if row is not None else None

    async def list_records(
        self,
        book_id: str,
        *,
        kinds: Sequence[str] = (),
        include_reverted: bool = False,
    ) -> tuple[StoryMemoryRecord, ...]:
        where = ["book_id = ?"]
        params: list[Any] = [str(book_id).strip()]
        if not include_reverted:
            where.append("lifecycle = 'active'")
        clean_kinds = [str(value).strip() for value in kinds if str(value).strip()]
        if clean_kinds:
            marks = ",".join("?" for _ in clean_kinds)
            where.append(f"kind IN ({marks})")
            params.extend(clean_kinds)
        rows = await self._db.fetch_all(
            "SELECT * FROM story_memory_records WHERE "
            + " AND ".join(where)
            + " ORDER BY kind ASC, memory_key ASC",
            params,
        )
        return tuple(_record_from_row(row) for row in rows)

    async def list_versions(
        self,
        book_id: str,
        memory_key: str,
    ) -> tuple[StoryMemoryVersion, ...]:
        rows = await self._db.fetch_all(
            "SELECT * FROM story_memory_versions WHERE book_id = ? "
            "AND memory_key = ? ORDER BY version ASC",
            [str(book_id).strip(), str(memory_key).strip()],
        )
        return tuple(_version_from_row(row) for row in rows)

    async def invalidate_chapter(
        self,
        book_id: str,
        chapter_id: str,
        *,
        current_revision: str | None = None,
    ) -> ChapterMemoryInvalidationReceipt:
        normalized_book_id = str(book_id).strip()
        normalized_chapter_id = str(chapter_id).strip()
        if not normalized_book_id or not normalized_chapter_id:
            raise ValueError("book_id and chapter_id are required")

        stale_source_count = 0
        invalidated_count = 0
        review_count = 0
        stale_record_count = 0
        async with self._db.transaction(cancellation_linearizable=True):
            where = [
                "book_id = ?",
                "chapter_id = ?",
                "status = 'valid'",
            ]
            params: list[Any] = [normalized_book_id, normalized_chapter_id]
            if current_revision is not None:
                where.append("source_revision != ?")
                params.append(str(current_revision))
            sources = await self._db.fetch_all(
                "SELECT id, delta_id FROM story_memory_sources WHERE "
                + " AND ".join(where),
                params,
            )
            source_ids = [str(row["id"]) for row in sources]
            delta_ids = list(dict.fromkeys(str(row["delta_id"]) for row in sources))
            stale_source_count = len(source_ids)
            if source_ids:
                marks = ",".join("?" for _ in source_ids)
                await self._db.execute(
                    f"UPDATE story_memory_sources SET status = 'stale' "
                    f"WHERE id IN ({marks})",
                    source_ids,
                )
            if delta_ids:
                marks = ",".join("?" for _ in delta_ids)
                delta_rows = await self._db.fetch_all(
                    f"SELECT id, status FROM story_memory_deltas "
                    f"WHERE id IN ({marks})",
                    delta_ids,
                )
                pending_ids = [
                    str(row["id"])
                    for row in delta_rows
                    if row["status"] == StoryMemoryDeltaStatus.PENDING.value
                ]
                applied_ids = [
                    str(row["id"])
                    for row in delta_rows
                    if row["status"] == StoryMemoryDeltaStatus.APPLIED.value
                ]
                invalidated_count = len(pending_ids)
                review_count = len(applied_ids)
                if pending_ids:
                    pending_marks = ",".join("?" for _ in pending_ids)
                    await self._db.execute(
                        f"UPDATE story_memory_deltas SET status = 'invalidated', "
                        f"invalidated_at = datetime('now') "
                        f"WHERE id IN ({pending_marks})",
                        pending_ids,
                    )
                    await self._db.execute(
                        "UPDATE story_memory_evolution_reviews SET "
                        "review_status = 'stale', update_time = datetime('now') "
                        f"WHERE delta_id IN ({pending_marks})",
                        pending_ids,
                    )
                if applied_ids:
                    applied_marks = ",".join("?" for _ in applied_ids)
                    await self._db.execute(
                        f"UPDATE story_memory_deltas SET status = 'needs_review' "
                        f"WHERE id IN ({applied_marks})",
                        applied_ids,
                    )
                    await self._db.execute(
                        "UPDATE story_memory_evolution_reviews SET "
                        "review_status = 'stale', update_time = datetime('now') "
                        f"WHERE delta_id IN ({applied_marks})",
                        applied_ids,
                    )
                    records = await self._db.fetch_all(
                        "SELECT id FROM story_memory_records "
                        f"WHERE last_delta_id IN ({applied_marks}) "
                        "AND provenance_status != 'stale'",
                        applied_ids,
                    )
                    stale_record_count = len(records)
                    await self._db.execute(
                        "UPDATE story_memory_records SET provenance_status = 'stale', "
                        "update_time = datetime('now') "
                        f"WHERE last_delta_id IN ({applied_marks})",
                        applied_ids,
                    )
        return ChapterMemoryInvalidationReceipt(
            chapter_id=normalized_chapter_id,
            stale_sources=stale_source_count,
            invalidated_pending_deltas=invalidated_count,
            review_required_deltas=review_count,
            stale_records=stale_record_count,
        )

    async def _require_delta_row(self, delta_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM story_memory_deltas WHERE id = ?",
            [delta_id],
        )
        if row is None:
            raise StoryMemoryNotFoundError(delta_id)
        return row

    async def _operation_rows(
        self,
        delta_id: str,
        *,
        descending: bool = False,
    ) -> list[dict[str, Any]]:
        direction = "DESC" if descending else "ASC"
        return await self._db.fetch_all(
            "SELECT * FROM story_memory_delta_operations WHERE delta_id = ? "
            f"ORDER BY operation_index {direction}",
            [delta_id],
        )

    async def _apply_operation(
        self,
        delta: Mapping[str, Any],
        operation: Mapping[str, Any],
    ) -> None:
        book_id = str(delta["book_id"])
        target_key = str(operation["target_key"])
        current = await self._db.fetch_one(
            "SELECT * FROM story_memory_records WHERE book_id = ? AND memory_key = ?",
            [book_id, target_key],
        )
        before = _record_snapshot(current) if current is not None else None
        next_version = int(current.get("version") or 0) + 1 if current else 1
        operation_type = StoryMemoryOperation(str(operation["operation"]))
        if operation_type is StoryMemoryOperation.REMOVE and current is None:
            raise StoryMemoryConflictError(
                f"cannot remove missing story-memory record {target_key}"
            )

        record_id = str(current["id"]) if current else str(uuid4())
        if operation_type is StoryMemoryOperation.UPSERT:
            kind = str(operation["kind"])
            subject_id = _optional_text(operation.get("subject_id"))
            payload_json = str(operation["after_payload_json"])
            status = str(operation["after_status"])
            lifecycle = StoryMemoryLifecycle.ACTIVE.value
        else:
            kind = str(current["kind"])
            subject_id = _optional_text(current.get("subject_id"))
            payload_json = str(current["payload_json"])
            status = str(current["status"])
            lifecycle = StoryMemoryLifecycle.REVERTED.value

        values = {
            "id": record_id,
            "book_id": book_id,
            "memory_key": target_key,
            "kind": kind,
            "subject_id": subject_id,
            "payload_json": payload_json,
            "status": status,
            "lifecycle": lifecycle,
            "provenance_status": StoryMemoryProvenanceStatus.VALID.value,
            "version": next_version,
            "last_delta_id": str(delta["id"]),
            "last_source_id": str(operation["source_id"]),
        }
        if current is None:
            await self._db.execute(
                "INSERT INTO story_memory_records "
                "(id, book_id, memory_key, kind, subject_id, payload_json, "
                "status, lifecycle, provenance_status, version, last_delta_id, "
                "last_source_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                list(values.values()),
            )
        else:
            await self._db.execute(
                "UPDATE story_memory_records SET kind = ?, subject_id = ?, "
                "payload_json = ?, status = ?, lifecycle = ?, "
                "provenance_status = ?, version = ?, last_delta_id = ?, "
                "last_source_id = ?, update_time = datetime('now') WHERE id = ?",
                [
                    kind,
                    subject_id,
                    payload_json,
                    status,
                    lifecycle,
                    StoryMemoryProvenanceStatus.VALID.value,
                    next_version,
                    str(delta["id"]),
                    str(operation["source_id"]),
                    record_id,
                ],
            )
        after = dict(values)
        await self._db.execute(
            "UPDATE story_memory_delta_operations SET before_record_json = ?, "
            "after_record_json = ?, applied_version = ? WHERE id = ?",
            [
                _json_dump(before) if before is not None else None,
                _json_dump(after),
                next_version,
                operation["id"],
            ],
        )
        await self._insert_version(values, action="apply")

    async def _rollback_operation(
        self,
        delta: Mapping[str, Any],
        operation: Mapping[str, Any],
    ) -> None:
        target_key = str(operation["target_key"])
        current = await self._db.fetch_one(
            "SELECT * FROM story_memory_records WHERE book_id = ? AND memory_key = ?",
            [str(delta["book_id"]), target_key],
        )
        if current is None or str(current.get("last_delta_id") or "") != str(delta["id"]):
            raise StoryMemoryConflictError(
                f"story-memory record {target_key} changed after delta {delta['id']}"
            )
        next_version = int(current["version"]) + 1
        before = _json_load_optional(operation.get("before_record_json"))
        if before is None:
            restored = {
                "id": str(current["id"]),
                "book_id": str(current["book_id"]),
                "memory_key": target_key,
                "kind": str(current["kind"]),
                "subject_id": _optional_text(current.get("subject_id")),
                "payload_json": str(current["payload_json"]),
                "status": str(current["status"]),
                "lifecycle": StoryMemoryLifecycle.REVERTED.value,
                "provenance_status": StoryMemoryProvenanceStatus.VALID.value,
                "version": next_version,
                "last_delta_id": None,
                "last_source_id": None,
            }
        else:
            restored = {
                "id": str(current["id"]),
                "book_id": str(current["book_id"]),
                "memory_key": target_key,
                "kind": str(before["kind"]),
                "subject_id": _optional_text(before.get("subject_id")),
                "payload_json": str(before["payload_json"]),
                "status": str(before["status"]),
                "lifecycle": str(before["lifecycle"]),
                "provenance_status": str(before["provenance_status"]),
                "version": next_version,
                "last_delta_id": _optional_text(before.get("last_delta_id")),
                "last_source_id": _optional_text(before.get("last_source_id")),
            }
        await self._db.execute(
            "UPDATE story_memory_records SET kind = ?, subject_id = ?, "
            "payload_json = ?, status = ?, lifecycle = ?, provenance_status = ?, "
            "version = ?, last_delta_id = ?, last_source_id = ?, "
            "update_time = datetime('now') WHERE id = ?",
            [
                restored["kind"],
                restored["subject_id"],
                restored["payload_json"],
                restored["status"],
                restored["lifecycle"],
                restored["provenance_status"],
                restored["version"],
                restored["last_delta_id"],
                restored["last_source_id"],
                restored["id"],
            ],
        )
        await self._insert_version(
            restored,
            action="rollback",
            delta_id=str(delta["id"]),
            source_id=str(operation["source_id"]),
        )

    async def _insert_version(
        self,
        values: Mapping[str, Any],
        *,
        action: str,
        delta_id: str | None = None,
        source_id: str | None = None,
    ) -> None:
        await self._db.execute(
            "INSERT INTO story_memory_versions "
            "(record_id, book_id, memory_key, version, delta_id, action, kind, "
            "subject_id, payload_json, status, lifecycle, provenance_status, "
            "source_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                values["id"],
                values["book_id"],
                values["memory_key"],
                values["version"],
                delta_id or values["last_delta_id"],
                action,
                values["kind"],
                values.get("subject_id"),
                values["payload_json"],
                values["status"],
                values["lifecycle"],
                values["provenance_status"],
                source_id if source_id is not None else values.get("last_source_id"),
            ],
        )


def _change_from_row(row: Mapping[str, Any]) -> StoryMemoryChange:
    return StoryMemoryChange(
        target_key=str(row["target_key"]),
        kind=StoryMemoryKind(str(row["kind"])),
        operation=StoryMemoryOperation(str(row["operation"])),
        subject_id=_optional_text(row.get("subject_id")),
        payload=_json_load_object(row.get("after_payload_json")),
        status=StoryMemoryStatus(str(row["after_status"])),
        confidence=float(row.get("confidence") or 0.0),
        source=SourceReference(
            chapter_id=str(row["source_chapter_id"]),
            excerpt=str(row.get("source_excerpt") or ""),
            source_revision=str(row.get("source_revision") or ""),
            status=StoryMemorySourceStatus(
                str(row.get("source_status") or StoryMemorySourceStatus.VALID.value)
            ),
            locator=_json_load_object(row.get("source_locator_json")),
            narrative_order=(
                int(row["source_narrative_order"])
                if row.get("source_narrative_order") is not None
                else None
            ),
            story_time=_optional_text(row.get("source_story_time")),
            story_time_precision=_optional_text(
                row.get("source_story_time_precision")
            ),
        ),
    )


def _record_from_row(row: Mapping[str, Any]) -> StoryMemoryRecord:
    return StoryMemoryRecord(
        id=str(row["id"]),
        book_id=str(row["book_id"]),
        memory_key=str(row["memory_key"]),
        kind=StoryMemoryKind(str(row["kind"])),
        subject_id=_optional_text(row.get("subject_id")),
        payload=_json_load_object(row.get("payload_json")),
        status=StoryMemoryStatus(str(row["status"])),
        lifecycle=StoryMemoryLifecycle(str(row["lifecycle"])),
        provenance_status=StoryMemoryProvenanceStatus(
            str(row["provenance_status"])
        ),
        version=int(row["version"]),
        last_delta_id=_optional_text(row.get("last_delta_id")),
        last_source_id=_optional_text(row.get("last_source_id")),
        create_time=_optional_text(row.get("create_time")),
        update_time=_optional_text(row.get("update_time")),
    )


def _version_from_row(row: Mapping[str, Any]) -> StoryMemoryVersion:
    return StoryMemoryVersion(
        record_id=str(row["record_id"]),
        book_id=str(row["book_id"]),
        memory_key=str(row["memory_key"]),
        version=int(row["version"]),
        delta_id=str(row["delta_id"]),
        action=str(row["action"]),
        payload=_json_load_object(row.get("payload_json")),
        status=StoryMemoryStatus(str(row["status"])),
        lifecycle=StoryMemoryLifecycle(str(row["lifecycle"])),
        provenance_status=StoryMemoryProvenanceStatus(
            str(row["provenance_status"])
        ),
        source_id=_optional_text(row.get("source_id")),
        create_time=_optional_text(row.get("create_time")),
    )


def _record_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "book_id": str(row["book_id"]),
        "memory_key": str(row["memory_key"]),
        "kind": str(row["kind"]),
        "subject_id": _optional_text(row.get("subject_id")),
        "payload_json": str(row["payload_json"]),
        "status": str(row["status"]),
        "lifecycle": str(row["lifecycle"]),
        "provenance_status": str(row["provenance_status"]),
        "version": int(row["version"]),
        "last_delta_id": _optional_text(row.get("last_delta_id")),
        "last_source_id": _optional_text(row.get("last_source_id")),
    }


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_load_optional(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return _json_load_object(value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["SqliteStoryMemoryRepository"]
