"""Auditable cleanup plan for legacy screenplay Agent runtime data.

The dry-run path is read-only and selects rows only through persisted identity
and relationship columns. Business documents and published artifacts are
reported as protected data and are never included in a delete selection.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import quote

import aiosqlite


Scalar = str | int
_SCREENPLAY_BINDING_NAMESPACES = frozenset({
    "screenplay.agent.turn",
    "screenplay.agent.task",
    "screenplay.agent.job",
    "screenplay.operation",
    "screenplay.conversation_turn",
})


class CleanupPlanDigestMismatch(RuntimeError):
    pass


class CleanupApplyInjectedFailure(RuntimeError):
    """Test seam proving that the entire delete transaction rolls back."""


@dataclass(frozen=True, slots=True)
class CleanupTableSelection:
    table: str
    key_columns: tuple[str, ...]
    keys: tuple[tuple[Scalar, ...], ...]
    integer_ranges: tuple[tuple[int, int], ...] = ()

    @property
    def count(self) -> int:
        return len(self.keys) + sum(
            end - start + 1 for start, end in self.integer_ranges
        )

    def to_mapping(self) -> dict[str, object]:
        result: dict[str, object] = {
            "table": self.table,
            "keyColumns": list(self.key_columns),
            "count": self.count,
        }
        if self.keys:
            result["keys"] = [list(key) for key in self.keys]
        if self.integer_ranges:
            result["integerRanges"] = [
                [start, end] for start, end in self.integer_ranges
            ]
        return result


@dataclass(frozen=True, slots=True)
class ScreenplayRuntimeCleanupPlan:
    project_ids: tuple[str, ...]
    session_ids: tuple[int, ...]
    turn_ids: tuple[str, ...]
    operation_ids: tuple[str, ...]
    task_ids: tuple[str, ...]
    run_ids: tuple[str, ...]
    conversation_ids: tuple[int, ...]
    delete_selections: tuple[CleanupTableSelection, ...]
    protected_counts: dict[str, int]
    digest: str

    @property
    def table_counts(self) -> dict[str, int]:
        return {
            selection.table: selection.count
            for selection in self.delete_selections
        }

    def to_report(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "planDigest": self.digest,
            "scope": {
                "projectIds": list(self.project_ids),
                "sessionIds": list(self.session_ids),
                "turnIds": list(self.turn_ids),
                "operationIds": list(self.operation_ids),
                "taskIds": list(self.task_ids),
                "runIds": list(self.run_ids),
                "conversationIds": list(self.conversation_ids),
            },
            "deleteCounts": self.table_counts,
            "deleteRows": [
                selection.to_mapping()
                for selection in self.delete_selections
            ],
            "protectedCounts": dict(sorted(self.protected_counts.items())),
            "containsContent": False,
            "applyExecuted": False,
        }


async def build_cleanup_plan(
    db,
    *,
    project_ids: Sequence[str] | None = None,
) -> ScreenplayRuntimeCleanupPlan:
    tables = await _table_columns(db)
    existing_projects = await _column_values(
        db,
        tables,
        "screenplay_projects",
        "id",
    )
    requested = {
        str(project_id).strip()
        for project_id in (project_ids or existing_projects)
        if str(project_id).strip()
    }
    unknown = requested.difference(str(value) for value in existing_projects)
    if unknown:
        raise ValueError(f"unknown screenplay project ids: {sorted(unknown)}")
    projects = tuple(sorted(requested))

    session_rows = await _rows(db, tables, "ai_sessions", (
        "id",
        "screenplay_project_id",
    ))
    sessions = {
        int(row["id"])
        for row in session_rows
        if str(row.get("screenplay_project_id") or "") in requested
    }

    turn_rows = await _rows(db, tables, "screenplay_agent_turns", (
        "id",
        "project_id",
        "session_id",
        "planner_run_id",
        "task_id",
        "operation_id",
    ))
    # Protocol v1 stored the Root identity in this legacy physical column.
    # Normalize immediately so all cleanup graph semantics remain root-owned.
    for row in turn_rows:
        row["root_run_id"] = row.pop("planner_run_id", None)
    scoped_turns = [
        row for row in turn_rows
        if str(row.get("project_id") or "") in requested
    ]
    turn_ids = _texts(row.get("id") for row in scoped_turns)
    sessions.update(_ints(row.get("session_id") for row in scoped_turns))

    operation_rows = await _rows(db, tables, "screenplay_agent_operations", (
        "id",
        "turn_id",
        "project_id",
        "session_id",
        "long_task_id",
    ))
    scoped_operations = [
        row for row in operation_rows
        if (
            str(row.get("project_id") or "") in requested
            or str(row.get("turn_id") or "") in turn_ids
        )
    ]
    operation_ids = _texts(row.get("id") for row in scoped_operations)
    sessions.update(_ints(row.get("session_id") for row in scoped_operations))
    task_ids = _texts(
        [row.get("task_id") for row in scoped_turns]
        + [row.get("long_task_id") for row in scoped_operations]
    )

    operation_usage_rows = await _rows(
        db,
        tables,
        "screenplay_agent_operation_usage",
        ("operation_id", "run_id"),
    )
    scoped_operation_usage = [
        row for row in operation_usage_rows
        if str(row.get("operation_id") or "") in operation_ids
    ]
    run_ids = _texts(
        [row.get("root_run_id") for row in scoped_turns]
        + [row.get("run_id") for row in scoped_operation_usage]
    )

    conversation_rows = await _rows(
        db,
        tables,
        "ai_conversations",
        ("id", "session_id"),
    )
    conversation_ids = {
        int(row["id"])
        for row in conversation_rows
        if _as_int(row.get("session_id")) in sessions
    }

    run_rows = await _rows(db, tables, "ai_agent_runs", (
        "id",
        "session_id",
        "conversation_id",
        "binding_namespace",
        "binding_aggregate_id",
        "parent_run_id",
        "root_run_id",
        "delegation_id",
    ))
    for row in run_rows:
        namespace = str(row.get("binding_namespace") or "")
        aggregate_id = str(row.get("binding_aggregate_id") or "")
        if (
            _as_int(row.get("session_id")) in sessions
            or (
                namespace in _SCREENPLAY_BINDING_NAMESPACES
                and aggregate_id in requested
            )
        ):
            run_ids.update(_texts((row.get("id"),)))

    work_item_rows = await _rows(db, tables, "ai_agent_work_items", (
        "id",
        "namespace",
        "owner_id",
        "created_by_run_id",
    ))
    long_task_rows = await _rows(db, tables, "ai_agent_long_tasks", (
        "id",
        "work_item_id",
        "namespace",
        "owner_id",
        "created_by_run_id",
    ))
    long_task_unit_rows = await _rows(
        db,
        tables,
        "ai_agent_long_task_units",
        ("task_id", "unit_id", "run_id"),
    )
    long_task_usage_rows = await _rows(
        db,
        tables,
        "ai_agent_long_task_usage",
        ("task_id", "run_id"),
    )
    delegation_rows = await _rows(db, tables, "ai_agent_delegations", (
        "id",
        "parent_run_id",
        "root_run_id",
        "child_run_id",
    ))

    work_item_ids = {
        str(row["id"])
        for row in work_item_rows
        if (
            str(row.get("namespace") or "") == "purrtypos.screenplay"
            and str(row.get("owner_id") or "") in requested
        )
    }
    task_ids.update(
        str(row["id"])
        for row in long_task_rows
        if (
            str(row.get("namespace") or "") == "purrtypos.screenplay"
            and str(row.get("owner_id") or "") in requested
        )
    )

    changed = True
    while changed:
        before = (len(run_ids), len(task_ids), len(work_item_ids))
        for row in long_task_rows:
            task_id = str(row.get("id") or "")
            created_by = str(row.get("created_by_run_id") or "")
            if task_id in task_ids or created_by in run_ids:
                task_ids.add(task_id)
                work_item_ids.update(_texts((row.get("work_item_id"),)))
                run_ids.update(_texts((created_by,)))
        for row in long_task_unit_rows:
            if str(row.get("task_id") or "") in task_ids:
                run_ids.update(_texts((row.get("run_id"),)))
        for row in long_task_usage_rows:
            if str(row.get("task_id") or "") in task_ids:
                run_ids.update(_texts((row.get("run_id"),)))
        for row in work_item_rows:
            created_by = str(row.get("created_by_run_id") or "")
            if str(row.get("id") or "") in work_item_ids or created_by in run_ids:
                work_item_ids.update(_texts((row.get("id"),)))
                run_ids.update(_texts((created_by,)))
        _expand_run_graph(run_ids, run_rows, delegation_rows)
        changed = before != (len(run_ids), len(task_ids), len(work_item_ids))

    for row in run_rows:
        if str(row.get("id") or "") in run_ids:
            conversation = _as_int(row.get("conversation_id"))
            if conversation is not None:
                conversation_ids.add(conversation)

    artifact_rows = await _rows(db, tables, "ai_agent_artifacts", (
        "id",
        "namespace",
        "owner_id",
        "work_item_id",
    ))
    protected_artifact_ids = {
        str(row["id"])
        for row in artifact_rows
        if (
            str(row.get("namespace") or "") == "purrtypos.screenplay"
            and str(row.get("owner_id") or "") in requested
        )
    }
    protected_work_item_ids = _texts(
        row.get("work_item_id")
        for row in artifact_rows
        if str(row.get("id") or "") in protected_artifact_ids
    )
    claim_rows = await _rows(db, tables, "ai_agent_artifact_claims", (
        "artifact_id",
        "work_item_id",
    ))
    for row in claim_rows:
        if str(row.get("artifact_id") or "") in protected_artifact_ids:
            protected_work_item_ids.update(_texts((row.get("work_item_id"),)))
    deletable_work_item_ids = work_item_ids.difference(protected_work_item_ids)

    selections = await _delete_selections(
        db,
        tables,
        project_ids=requested,
        session_ids=sessions,
        turn_ids=turn_ids,
        operation_ids=operation_ids,
        task_ids=task_ids,
        run_ids=run_ids,
        conversation_ids=conversation_ids,
        work_item_ids=deletable_work_item_ids,
    )
    protected_counts = await _protected_counts(
        db,
        tables,
        requested,
        protected_artifact_ids,
        protected_work_item_ids,
    )
    digest_payload = _digest_payload(
        projects,
        sessions,
        turn_ids,
        operation_ids,
        task_ids,
        run_ids,
        conversation_ids,
        selections,
        protected_counts,
    )
    digest = "sha256:" + hashlib.sha256(
        _canonical_json(digest_payload).encode("utf-8")
    ).hexdigest()
    return ScreenplayRuntimeCleanupPlan(
        project_ids=projects,
        session_ids=tuple(sorted(sessions)),
        turn_ids=tuple(sorted(turn_ids)),
        operation_ids=tuple(sorted(operation_ids)),
        task_ids=tuple(sorted(task_ids)),
        run_ids=tuple(sorted(run_ids)),
        conversation_ids=tuple(sorted(conversation_ids)),
        delete_selections=selections,
        protected_counts=protected_counts,
        digest=digest,
    )


async def apply_cleanup(
    db,
    plan_digest: str,
    *,
    project_ids: Sequence[str] | None = None,
    fail_after_table: int | None = None,
) -> ScreenplayRuntimeCleanupPlan:
    """Apply the exact current plan once inside one rollback-safe transaction."""

    if not str(plan_digest).startswith("sha256:"):
        raise ValueError("cleanup requires a SHA-256 plan digest")
    async with db.transaction(cancellation_linearizable=True):
        plan = await build_cleanup_plan(db, project_ids=project_ids)
        if plan.digest != plan_digest:
            raise CleanupPlanDigestMismatch(
                "cleanup scope changed after dry-run; generate a new plan"
            )
        applied_tables = 0
        for selection in plan.delete_selections:
            if not selection.count:
                continue
            if selection.integer_ranges:
                column = selection.key_columns[0]
                for start, end in selection.integer_ranges:
                    await db.execute(
                        f"DELETE FROM {selection.table} "
                        f"WHERE {column} BETWEEN ? AND ?",
                        [start, end],
                    )
            for key in selection.keys:
                where = " AND ".join(
                    f"{column} = ?" for column in selection.key_columns
                )
                await db.execute(
                    f"DELETE FROM {selection.table} WHERE {where}",
                    list(key),
                )
            applied_tables += 1
            if (
                fail_after_table is not None
                and applied_tables >= fail_after_table
            ):
                raise CleanupApplyInjectedFailure(
                    "injected cleanup failure"
                )
        return plan


async def write_read_only_report(
    database_path: Path,
    output_path: Path,
    *,
    project_ids: Sequence[str] | None = None,
) -> ScreenplayRuntimeCleanupPlan:
    database_path = database_path.expanduser().resolve()
    uri = f"file:{quote(str(database_path), safe='/')}?mode=ro"
    connection = await aiosqlite.connect(uri, uri=True)
    connection.row_factory = aiosqlite.Row
    db = _ReadOnlyDatabase(connection)
    try:
        plan = await build_cleanup_plan(db, project_ids=project_ids)
    finally:
        await connection.close()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(plan.to_report(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return plan


async def apply_existing_database(
    database_path: Path,
    plan_digest: str,
    *,
    project_ids: Sequence[str] | None = None,
) -> ScreenplayRuntimeCleanupPlan:
    """Apply an approved plan without running application schema migrations."""

    database_path = database_path.expanduser().resolve()
    connection = await aiosqlite.connect(database_path)
    connection.row_factory = aiosqlite.Row
    db = _ExistingDatabase(connection)
    try:
        return await apply_cleanup(
            db,
            plan_digest,
            project_ids=project_ids,
        )
    finally:
        await connection.close()


async def retire_legacy_output_tables(database_path: Path) -> tuple[str, ...]:
    """Drop retired stores only after the approved cleanup made them empty."""

    database_path = database_path.expanduser().resolve()
    connection = await aiosqlite.connect(database_path)
    connection.row_factory = aiosqlite.Row
    db = _ExistingDatabase(connection)
    retired = ("screenplay_agent_chunks", "screenplay_agent_events")
    try:
        async with db.transaction(cancellation_linearizable=True):
            existing = {
                str(row["name"])
                for row in await db.fetch_all(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name IN ('screenplay_agent_chunks', "
                    "'screenplay_agent_events')"
                )
            }
            for table in retired:
                if table not in existing:
                    continue
                row = await db.fetch_one(f"SELECT COUNT(*) AS count FROM {table}")
                if int((row or {}).get("count") or 0) != 0:
                    raise RuntimeError(
                        f"refusing to retire non-empty legacy table: {table}"
                    )
            for table in retired:
                if table in existing:
                    await db.execute(f"DROP TABLE {table}")
            return tuple(table for table in retired if table in existing)
    finally:
        await connection.close()


class _ReadOnlyDatabase:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def fetch_all(self, sql: str, params=()):
        cursor = await self._connection.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_one(self, sql: str, params=()):
        cursor = await self._connection.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row is not None else None


class _ExistingDatabase(_ReadOnlyDatabase):
    """Small transaction adapter deliberately excluding schema initialization."""

    @asynccontextmanager
    async def transaction(self, **_options):
        await self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self
        except BaseException:
            await _await_uninterruptibly(self._connection.rollback())
            raise
        else:
            await _await_uninterruptibly(self._connection.commit())

    async def execute(self, sql: str, params=()) -> None:
        await self._connection.execute(sql, params)


async def _await_uninterruptibly(awaitable) -> None:
    task = asyncio.create_task(awaitable)
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            await asyncio.shield(task)
            break
        except asyncio.CancelledError as error:
            if task.done():
                task.result()
                raise
            if cancellation is None:
                cancellation = error
    if cancellation is not None:
        raise cancellation


async def _table_columns(db) -> dict[str, frozenset[str]]:
    rows = await db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )
    result: dict[str, frozenset[str]] = {}
    for row in rows:
        name = str(row["name"])
        columns = await db.fetch_all(f"PRAGMA table_info({name})")
        result[name] = frozenset(str(column["name"]) for column in columns)
    return result


async def _rows(
    db,
    tables: dict[str, frozenset[str]],
    table: str,
    columns: Sequence[str],
) -> list[dict[str, Any]]:
    available = tables.get(table)
    if not available:
        return []
    selected = tuple(column for column in columns if column in available)
    if not selected:
        return []
    return await db.fetch_all(f"SELECT {', '.join(selected)} FROM {table}")


async def _column_values(db, tables, table: str, column: str) -> tuple[Scalar, ...]:
    rows = await _rows(db, tables, table, (column,))
    return tuple(row[column] for row in rows)


def _expand_run_graph(
    run_ids: set[str],
    run_rows: Sequence[dict[str, Any]],
    delegation_rows: Sequence[dict[str, Any]],
) -> None:
    changed = True
    while changed:
        before = len(run_ids)
        for row in run_rows:
            linked = _texts((
                row.get("id"),
                row.get("parent_run_id"),
                row.get("root_run_id"),
            ))
            if linked.intersection(run_ids):
                run_ids.update(linked)
        for row in delegation_rows:
            linked = _texts((
                row.get("parent_run_id"),
                row.get("root_run_id"),
                row.get("child_run_id"),
            ))
            if linked.intersection(run_ids):
                run_ids.update(linked)
        changed = len(run_ids) != before


async def _delete_selections(
    db,
    tables,
    *,
    project_ids: set[str],
    session_ids: set[int],
    turn_ids: set[str],
    operation_ids: set[str],
    task_ids: set[str],
    run_ids: set[str],
    conversation_ids: set[int],
    work_item_ids: set[str],
) -> tuple[CleanupTableSelection, ...]:
    specs: list[tuple[str, tuple[str, ...], Any]] = [
        (
            "ai_agent_run_cancellations",
            ("root_run_id",),
            lambda row: str(row.get("root_run_id") or "") in run_ids,
        ),
        ("ai_error_reports", ("id",), lambda row: (
            str(row.get("agent_run_id") or "") in run_ids
            or _as_int(row.get("conversation_id")) in conversation_ids
        )),
        ("ai_agent_run_reviews", ("id",), lambda row: str(row.get("run_id") or "") in run_ids),
        ("ai_agent_output_streams", ("id",), lambda row: str(row.get("run_id") or "") in run_ids),
        ("ai_agent_approvals", ("id",), lambda row: str(row.get("run_id") or "") in run_ids),
        ("ai_agent_tool_receipts", ("run_id", "tool_call_id"), lambda row: str(row.get("run_id") or "") in run_ids),
        ("ai_agent_run_todos", ("id",), lambda row: str(row.get("run_id") or "") in run_ids),
        ("ai_agent_run_events", ("id",), lambda row: str(row.get("run_id") or "") in run_ids),
        ("ai_agent_delegations", ("id",), lambda row: bool(_texts((row.get("parent_run_id"), row.get("root_run_id"), row.get("child_run_id"))).intersection(run_ids))),
        ("ai_agent_host_child_runs", ("host_child_key",), lambda row: str(row.get("run_id") or "") in run_ids),
        ("ai_agent_long_task_usage", ("task_id", "run_id"), lambda row: str(row.get("task_id") or "") in task_ids or str(row.get("run_id") or "") in run_ids),
        ("ai_agent_long_task_units", ("task_id", "unit_id"), lambda row: str(row.get("task_id") or "") in task_ids),
        ("ai_agent_work_item_runs", ("work_item_id", "run_id"), lambda row: str(row.get("work_item_id") or "") in work_item_ids or str(row.get("run_id") or "") in run_ids),
        ("screenplay_checkpoint_plans", ("operation_id", "checkpoint_key"), lambda row: str(row.get("task_id") or "") in task_ids or str(row.get("operation_id") or "") in operation_ids or str(row.get("root_run_id") or "") in run_ids),
        ("ai_agent_long_tasks", ("id",), lambda row: str(row.get("id") or "") in task_ids),
        ("ai_agent_work_items", ("id",), lambda row: str(row.get("id") or "") in work_item_ids),
        ("screenplay_agent_operation_usage", ("operation_id", "run_id"), lambda row: str(row.get("operation_id") or "") in operation_ids),
        ("screenplay_agent_operation_commands", ("command_id",), lambda row: str(row.get("operation_id") or "") in operation_ids),
        ("screenplay_agent_cancel_commands", ("command_id",), lambda row: str(row.get("turn_id") or "") in turn_ids or str(row.get("operation_id") or "") in operation_ids),
        ("screenplay_agent_chunks", ("id",), lambda row: str(row.get("project_id") or "") in project_ids),
        ("screenplay_agent_events", ("id",), lambda row: str(row.get("project_id") or "") in project_ids),
        ("screenplay_agent_operations", ("id",), lambda row: str(row.get("id") or "") in operation_ids),
        ("screenplay_agent_turns", ("id",), lambda row: str(row.get("id") or "") in turn_ids),
        ("ai_conversation_summaries", ("session_id",), lambda row: _as_int(row.get("session_id")) in session_ids),
        ("ai_conversations", ("id",), lambda row: _as_int(row.get("id")) in conversation_ids),
        ("ai_agent_runs", ("id",), lambda row: str(row.get("id") or "") in run_ids),
    ]
    relationship_columns = {
        "ai_agent_run_cancellations": ("root_run_id",),
        "ai_error_reports": ("agent_run_id", "conversation_id"),
        "ai_agent_run_reviews": ("run_id",),
        "ai_agent_output_streams": ("run_id",),
        "ai_agent_approvals": ("run_id",),
        "ai_agent_tool_receipts": ("run_id",),
        "ai_agent_run_todos": ("run_id",),
        "ai_agent_run_events": ("run_id",),
        "ai_agent_delegations": ("parent_run_id", "root_run_id", "child_run_id"),
        "ai_agent_host_child_runs": ("run_id",),
        "ai_agent_long_task_usage": ("task_id", "run_id"),
        "ai_agent_long_task_units": ("task_id",),
        "ai_agent_work_item_runs": ("work_item_id", "run_id"),
        "screenplay_checkpoint_plans": ("task_id", "operation_id", "root_run_id"),
        "ai_agent_long_tasks": (),
        "ai_agent_work_items": (),
        "screenplay_agent_operation_usage": ("operation_id",),
        "screenplay_agent_operation_commands": ("operation_id",),
        "screenplay_agent_cancel_commands": ("turn_id", "operation_id"),
        "screenplay_agent_chunks": ("project_id",),
        "screenplay_agent_events": ("project_id",),
        "screenplay_agent_operations": (),
        "screenplay_agent_turns": (),
        "ai_conversation_summaries": (),
        "ai_conversations": (),
        "ai_agent_runs": (),
    }
    selections: list[CleanupTableSelection] = []
    for table, key_columns, predicate in specs:
        columns = key_columns + relationship_columns[table]
        rows = await _rows(db, tables, table, columns)
        keys = {
            tuple(row[column] for column in key_columns)
            for row in rows
            if predicate(row)
        }
        ordered_keys = tuple(sorted(
            keys,
            key=lambda key: tuple(str(item) for item in key),
        ))
        integer_ranges: tuple[tuple[int, int], ...] = ()
        if (
            len(key_columns) == 1
            and all(isinstance(key[0], int) for key in ordered_keys)
        ):
            integer_ranges = _integer_ranges(
                int(key[0]) for key in ordered_keys
            )
            ordered_keys = ()
        selections.append(CleanupTableSelection(
            table=table,
            key_columns=key_columns,
            keys=ordered_keys,
            integer_ranges=integer_ranges,
        ))
    return tuple(selections)


async def _protected_counts(
    db,
    tables,
    project_ids: set[str],
    artifact_ids: set[str],
    protected_work_item_ids: set[str],
) -> dict[str, int]:
    revision_rows = await _rows(
        db,
        tables,
        "screenplay_revisions",
        ("id", "project_id"),
    )
    revision_ids = {
        str(row["id"])
        for row in revision_rows
        if str(row.get("project_id") or "") in project_ids
    }
    deliverable_rows = await _rows(
        db,
        tables,
        "screenplay_deliverables",
        ("id", "project_id"),
    )
    deliverable_ids = {
        str(row["id"])
        for row in deliverable_rows
        if str(row.get("project_id") or "") in project_ids
    }
    specs = {
        "screenplay_projects": (("id",), lambda row: str(row.get("id") or "") in project_ids),
        "screenplay_deliverables": (("id",), lambda row: str(row.get("id") or "") in deliverable_ids),
        "screenplay_revisions": (("id",), lambda row: str(row.get("id") or "") in revision_ids),
        "screenplay_revision_parts": (("revision_id",), lambda row: str(row.get("revision_id") or "") in revision_ids),
        "screenplay_revision_inputs": (("revision_id", "input_revision_id"), lambda row: str(row.get("revision_id") or "") in revision_ids or str(row.get("input_revision_id") or "") in revision_ids),
        "screenplay_revision_source_refs": (("revision_id",), lambda row: str(row.get("revision_id") or "") in revision_ids),
        "screenplay_project_heads": (("project_id",), lambda row: str(row.get("project_id") or "") in project_ids),
        "screenplay_working_copies": (("project_id",), lambda row: str(row.get("project_id") or "") in project_ids),
        "screenplay_acceptance_events": (("project_id",), lambda row: str(row.get("project_id") or "") in project_ids),
        "screenplay_review_decisions": (("project_id",), lambda row: str(row.get("project_id") or "") in project_ids),
        "screenplay_review_decision_events": (("project_id",), lambda row: str(row.get("project_id") or "") in project_ids),
        "screenplay_finalization_events": (("project_id",), lambda row: str(row.get("project_id") or "") in project_ids),
        "ai_agent_artifacts": (("id",), lambda row: str(row.get("id") or "") in artifact_ids),
        "ai_agent_artifact_batches": (("artifact_id",), lambda row: str(row.get("artifact_id") or "") in artifact_ids),
        "ai_agent_artifact_claims": (("artifact_id",), lambda row: str(row.get("artifact_id") or "") in artifact_ids),
        "ai_agent_artifact_projections": (("artifact_id",), lambda row: str(row.get("artifact_id") or "") in artifact_ids),
        "ai_agent_work_items_referenced_by_artifacts": (("id",), lambda row: str(row.get("id") or "") in protected_work_item_ids),
    }
    counts: dict[str, int] = {}
    for report_name, (columns, predicate) in specs.items():
        table = (
            "ai_agent_work_items"
            if report_name == "ai_agent_work_items_referenced_by_artifacts"
            else report_name
        )
        rows = await _rows(db, tables, table, columns)
        counts[report_name] = sum(1 for row in rows if predicate(row))
    return counts


def _digest_payload(
    projects,
    sessions,
    turns,
    operations,
    tasks,
    runs,
    conversations,
    selections,
    protected_counts,
) -> dict[str, object]:
    return {
        "scope": {
            "projectIds": sorted(projects),
            "sessionIds": sorted(sessions),
            "turnIds": sorted(turns),
            "operationIds": sorted(operations),
            "taskIds": sorted(tasks),
            "runIds": sorted(runs),
            "conversationIds": sorted(conversations),
        },
        "deleteRows": [selection.to_mapping() for selection in selections],
        "protectedCounts": dict(sorted(protected_counts.items())),
    }


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _texts(values: Iterable[object]) -> set[str]:
    return {
        text
        for value in values
        if (text := str(value or "").strip())
    }


def _ints(values: Iterable[object]) -> set[int]:
    return {
        parsed for value in values
        if (parsed := _as_int(value)) is not None
    }


def _as_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer_ranges(values: Iterable[int]) -> tuple[tuple[int, int], ...]:
    ordered = sorted(set(values))
    if not ordered:
        return ()
    result: list[tuple[int, int]] = []
    start = previous = ordered[0]
    for value in ordered[1:]:
        if value == previous + 1:
            previous = value
            continue
        result.append((start, previous))
        start = previous = value
    result.append((start, previous))
    return tuple(result)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--project-id", action="append", dest="project_ids")
    parser.add_argument("--apply-digest")
    parser.add_argument("--retire-legacy-output-tables", action="store_true")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    if args.retire_legacy_output_tables:
        retired = await retire_legacy_output_tables(args.database)
        print(json.dumps({"retiredTables": retired}, ensure_ascii=False))
        return
    if args.apply_digest:
        plan = await apply_existing_database(
            args.database,
            args.apply_digest,
            project_ids=args.project_ids,
        )
        print(json.dumps({
            "appliedPlanDigest": plan.digest,
            "deletedCounts": plan.table_counts,
        }, ensure_ascii=False, sort_keys=True))
        return
    plan = await write_read_only_report(
        args.database,
        args.output,
        project_ids=args.project_ids,
    )
    print(json.dumps({
        "planDigest": plan.digest,
        "deleteCounts": plan.table_counts,
        "protectedCounts": plan.protected_counts,
        "output": str(args.output),
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
