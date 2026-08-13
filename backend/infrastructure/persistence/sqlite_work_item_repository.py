"""SQLite persistence for durable Work Items and immutable Run links."""

from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager
from typing import Any

from purra.json_values import thaw_json_mapping
from purra.work_items.contracts import (
    WorkItemCreateCommand,
    WorkItemRecord,
    WorkItemRunLink,
    WorkItemRunLinkCommand,
    WorkItemRunRelation,
    WorkItemStatus,
    WorkItemTransitionCommand,
)
from purra.work_items.errors import (
    WorkItemConflictError,
    WorkItemNotFoundError,
    WorkItemStateError,
)


class SqliteWorkItemRepository:
    """Atomic CAS adapter for multi-Run task identity."""

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
        work_item_id: str,
        command: WorkItemCreateCommand,
    ) -> WorkItemRecord:
        normalized_id = _required_text(work_item_id, "work item id")
        try:
            async with self._mutation_transaction():
                await self._db.execute(
                    "INSERT INTO ai_agent_work_items "
                    "(id, namespace, kind, owner_id, created_by_run_id, "
                    "metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        normalized_id,
                        command.namespace,
                        command.kind,
                        command.owner_id,
                        command.created_by_run_id,
                        json.dumps(
                            thaw_json_mapping(command.metadata),
                            ensure_ascii=False,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                    ],
                )
                if command.created_by_run_id is not None:
                    await self._db.execute(
                        "INSERT INTO ai_agent_work_item_runs "
                        "(work_item_id, run_id, relation, work_item_revision) "
                        "VALUES (?, ?, ?, 1)",
                        [
                            normalized_id,
                            command.created_by_run_id,
                            WorkItemRunRelation.CREATED.value,
                        ],
                    )
                row = await self._db.fetch_one(
                    "SELECT * FROM ai_agent_work_items WHERE id = ?",
                    [normalized_id],
                )
                result = _require_work_item(row, normalized_id)
        except sqlite3.IntegrityError as error:
            existing = await self.load(normalized_id)
            if existing is not None and _matches_create(existing, command):
                return existing
            raise WorkItemConflictError(
                "work item id already exists",
                code="work_item_id_conflict",
                details={"workItemId": normalized_id},
            ) from error
        return result

    async def load(self, work_item_id: str) -> WorkItemRecord | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_work_items WHERE id = ?",
            [str(work_item_id or "").strip()],
        )
        return _work_item_record(row) if row is not None else None

    async def link_run(
        self,
        command: WorkItemRunLinkCommand,
    ) -> WorkItemRunLink:
        async with self._db.transaction(cancellation_linearizable=True):
            existing = await self._db.fetch_one(
                "SELECT * FROM ai_agent_work_item_runs "
                "WHERE work_item_id = ? AND run_id = ?",
                [command.work_item_id, command.run_id],
            )
            if existing is not None:
                link = _work_item_run_link(existing)
                if (
                    link.relation is command.relation
                    and link.work_item_revision == command.expected_revision
                ):
                    return link
                raise WorkItemConflictError(
                    "Run is already linked to the Work Item differently",
                    code="work_item_run_link_conflict",
                    details={
                        "workItemId": command.work_item_id,
                        "runId": command.run_id,
                        "relation": link.relation.value,
                        "workItemRevision": link.work_item_revision,
                    },
                )
            row = await self._db.fetch_one(
                "SELECT * FROM ai_agent_work_items WHERE id = ?",
                [command.work_item_id],
            )
            item = _require_work_item(row, command.work_item_id)
            _require_revision(item, command.expected_revision)
            if command.relation is WorkItemRunRelation.CREATED:
                raise WorkItemStateError(
                    "creator Run link is immutable after Work Item creation",
                    code="work_item_creator_link_immutable",
                    details={"workItemId": item.id},
                )
            if (
                command.relation.writes_work_item
                and item.status is not WorkItemStatus.OPEN
            ):
                raise WorkItemStateError(
                    "work item is not open",
                    code="work_item_not_open",
                    details={"status": item.status.value},
                )
            await self._db.execute(
                "INSERT INTO ai_agent_work_item_runs "
                "(work_item_id, run_id, relation, work_item_revision) "
                "VALUES (?, ?, ?, ?)",
                [
                    item.id,
                    command.run_id,
                    command.relation.value,
                    item.revision,
                ],
            )
            persisted = await self._db.fetch_one(
                "SELECT * FROM ai_agent_work_item_runs "
                "WHERE work_item_id = ? AND run_id = ?",
                [item.id, command.run_id],
            )
            if persisted is None:
                raise RuntimeError("Work Item Run link was not persisted")
            return _work_item_run_link(persisted)

    async def list_run_links(
        self,
        work_item_id: str,
    ) -> tuple[WorkItemRunLink, ...]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ai_agent_work_item_runs WHERE work_item_id = ? "
            "ORDER BY create_time ASC, run_id ASC",
            [str(work_item_id or "").strip()],
        )
        return tuple(_work_item_run_link(row) for row in rows)

    async def complete(
        self,
        command: WorkItemTransitionCommand,
    ) -> WorkItemRecord:
        return await self._transition(command, WorkItemStatus.COMPLETED)

    async def cancel(
        self,
        command: WorkItemTransitionCommand,
    ) -> WorkItemRecord:
        return await self._transition(command, WorkItemStatus.CANCELED)

    async def _transition(
        self,
        command: WorkItemTransitionCommand,
        target: WorkItemStatus,
    ) -> WorkItemRecord:
        async with self._db.transaction(cancellation_linearizable=True):
            row = await self._db.fetch_one(
                "SELECT * FROM ai_agent_work_items WHERE id = ?",
                [command.work_item_id],
            )
            item = _require_work_item(row, command.work_item_id)
            _require_revision(item, command.expected_revision)
            if item.status is not WorkItemStatus.OPEN:
                raise WorkItemStateError(
                    "work item is not open",
                    code="work_item_not_open",
                    details={"status": item.status.value},
                )
            await self._db.execute(
                "UPDATE ai_agent_work_items SET status = ?, revision = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ? "
                "AND status = 'open' AND revision = ?",
                [target.value, item.revision + 1, item.id, item.revision],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            if int((changed or {}).get("count") or 0) != 1:
                raise WorkItemConflictError(
                    "work item changed during transition",
                    code="work_item_revision_conflict",
                    details={"expectedRevision": item.revision},
                )
            updated = await self._db.fetch_one(
                "SELECT * FROM ai_agent_work_items WHERE id = ?",
                [item.id],
            )
            return _require_work_item(updated, item.id)


def _work_item_record(row: dict[str, Any]) -> WorkItemRecord:
    return WorkItemRecord(
        id=str(row["id"]),
        namespace=str(row["namespace"]),
        kind=str(row["kind"]),
        owner_id=str(row["owner_id"]),
        created_by_run_id=row.get("created_by_run_id"),
        status=str(row.get("status") or WorkItemStatus.OPEN.value),
        revision=int(row.get("revision") or 1),
        metadata=_json_mapping(row.get("metadata_json")),
    )


def _work_item_run_link(row: dict[str, Any]) -> WorkItemRunLink:
    return WorkItemRunLink(
        work_item_id=str(row["work_item_id"]),
        run_id=str(row["run_id"]),
        relation=str(row["relation"]),
        work_item_revision=int(row["work_item_revision"]),
    )


def _require_work_item(
    row: dict[str, Any] | None,
    work_item_id: str,
) -> WorkItemRecord:
    if row is None:
        raise WorkItemNotFoundError(
            "work item does not exist",
            code="work_item_not_found",
            details={"workItemId": work_item_id},
        )
    return _work_item_record(row)


def _require_revision(item: WorkItemRecord, expected: int) -> None:
    if item.revision != int(expected):
        raise WorkItemConflictError(
            "work item revision does not match",
            code="work_item_revision_conflict",
            details={
                "expectedRevision": int(expected),
                "actualRevision": item.revision,
            },
        )


def _matches_create(
    item: WorkItemRecord,
    command: WorkItemCreateCommand,
) -> bool:
    return bool(
        item.namespace == command.namespace
        and item.kind == command.kind
        and item.owner_id == command.owner_id
        and item.created_by_run_id == command.created_by_run_id
        and thaw_json_mapping(item.metadata) == thaw_json_mapping(command.metadata)
    )


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["SqliteWorkItemRepository"]
