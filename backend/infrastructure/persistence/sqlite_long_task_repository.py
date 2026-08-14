"""SQLite adapter for durable multi-Run task execution."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from graphlib import CycleError, TopologicalSorter
from typing import Any

from purra.contracts import SessionId
from purra.json_values import thaw_json_mapping
from purra.long_tasks.contracts import (
    LongTaskCreateCommand,
    LongTaskRecord,
    LongTaskSplitResult,
    LongTaskStatus,
    LongTaskUnitRecord,
    LongTaskUnitResult,
    LongTaskUnitStatus,
    LongTaskUsage,
)
from purra.recovery import (
    FailureDecision,
    FailureDisposition,
    FailureScope,
)


class SqliteLongTaskRepository:
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
        task_id: str,
        command: LongTaskCreateCommand,
    ) -> LongTaskRecord:
        normalized_id = _required(task_id, "long task id")
        try:
            async with self._mutation_transaction():
                work_item = await self._db.fetch_one(
                    "SELECT namespace, kind, owner_id, status "
                    "FROM ai_agent_work_items WHERE id = ?",
                    [command.work_item_id],
                )
                if work_item is None or str(work_item.get("status")) != "open":
                    raise ValueError("long task requires an open Work Item")
                if (
                    str(work_item.get("namespace")) != command.namespace
                    or str(work_item.get("owner_id")) != command.owner_id
                ):
                    raise ValueError("long task scope does not match its Work Item")
                await self._db.execute(
                    "INSERT INTO ai_agent_long_tasks "
                    "(id, work_item_id, namespace, kind, owner_id, "
                    "created_by_run_id, total_units, max_parallelism, metadata_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        normalized_id,
                        command.work_item_id,
                        command.namespace,
                        command.kind,
                        command.owner_id,
                        command.created_by_run_id,
                        sum(1 for unit in command.units if unit.required),
                        command.max_parallelism,
                        _json_dump(command.metadata),
                    ],
                )
                for unit in command.units:
                    await self._db.execute(
                        "INSERT INTO ai_agent_long_task_units "
                        "(task_id, unit_id, semantic_key, position, dependencies_json, "
                        "parent_unit_id, required, input_ref, max_attempts, metadata_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [
                            normalized_id,
                            unit.id,
                            unit.semantic_key,
                            unit.position,
                            json.dumps(list(unit.dependencies), separators=(",", ":")),
                            unit.parent_unit_id,
                            int(unit.required),
                            unit.input_ref,
                            unit.max_attempts,
                            _json_dump(unit.metadata),
                        ],
                    )
                row = await self._db.fetch_one(
                    "SELECT * FROM ai_agent_long_tasks WHERE id = ?",
                    [normalized_id],
                )
                return _task(row)
        except sqlite3.IntegrityError as error:
            existing = await self.load(normalized_id)
            if existing is not None:
                units = await self.list_units(existing.id)
                if _matches_create(existing, units, command):
                    return existing
                raise ValueError("long task id conflicts") from error
            active = await self.find_active(
                namespace=command.namespace,
                owner_id=command.owner_id,
                kind=command.kind,
                session_id=_metadata_session_id(command.metadata),
                match_session=True,
            )
            if active is not None:
                return active
            raise ValueError("long task id or unit position conflicts") from error

    async def load(self, task_id: str) -> LongTaskRecord | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_long_tasks WHERE id = ?",
            [str(task_id or "").strip()],
        )
        return _task(row) if row is not None else None

    async def list_for_owner(
        self,
        *,
        namespace: str,
        owner_id: str,
        kind: str | None = None,
        limit: int = 20,
    ) -> tuple[LongTaskRecord, ...]:
        clauses = ["namespace = ?", "owner_id = ?"]
        params: list[Any] = [
            _required(namespace, "long task namespace"),
            _required(owner_id, "long task owner id"),
        ]
        normalized_kind = str(kind or "").strip()
        if normalized_kind:
            clauses.append("kind = ?")
            params.append(normalized_kind)
        params.append(max(1, min(100, int(limit))))
        rows = await self._db.fetch_all(
            "SELECT * FROM ai_agent_long_tasks WHERE "
            + " AND ".join(clauses)
            + " ORDER BY update_time DESC LIMIT ?",
            params,
        )
        return tuple(_task(row) for row in rows)

    async def find_active(
        self,
        *,
        namespace: str,
        owner_id: str,
        kind: str,
        session_id: SessionId | None = None,
        match_session: bool = False,
    ) -> LongTaskRecord | None:
        session_clause = ""
        params: list[Any] = [
            _required(namespace, "long task namespace"),
            _required(owner_id, "long task owner id"),
            _required(kind, "long task kind"),
        ]
        if match_session:
            session_clause = (
                "AND COALESCE(CAST(json_extract(metadata_json, '$.sessionId') AS TEXT), '') = ? "
            )
            params.append("" if session_id is None else str(session_id))
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_long_tasks WHERE namespace = ? "
            "AND owner_id = ? AND kind = ? "
            + session_clause
            + "AND status IN ('pending', 'running', 'paused') "
            "ORDER BY update_time DESC LIMIT 1",
            params,
        )
        return _task(row) if row is not None else None

    async def list_units(self, task_id: str) -> tuple[LongTaskUnitRecord, ...]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ai_agent_long_task_units WHERE task_id = ? "
            "ORDER BY position ASC",
            [str(task_id or "").strip()],
        )
        return tuple(_unit(row) for row in rows)

    async def record_usage(
        self,
        task_id: str,
        *,
        run_id: str,
        usage: LongTaskUsage,
        expected_revision: int,
    ) -> LongTaskRecord:
        normalized_run_id = _required(run_id, "long task usage Run id")
        if not isinstance(usage, LongTaskUsage):
            raise TypeError("long task usage must be LongTaskUsage")
        async with self._db.transaction(cancellation_linearizable=True):
            existing = await self._db.fetch_one(
                "SELECT * FROM ai_agent_long_task_usage "
                "WHERE task_id = ? AND run_id = ?",
                [task_id, normalized_run_id],
            )
            if existing is not None:
                if _usage_row(existing) != usage:
                    raise ValueError("long task Run usage conflicts")
                return await self._require(task_id)
            task = await self._require(task_id)
            if task.revision != int(expected_revision):
                raise ValueError("long task revision conflict")
            await self._db.execute(
                "INSERT INTO ai_agent_long_task_usage "
                "(task_id, run_id, invocation_count, input_tokens, "
                "output_tokens, reasoning_tokens) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    task.id,
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
                "AS unknown_reasoning FROM ai_agent_long_task_usage "
                "WHERE task_id = ?",
                [task.id],
            )
            total = _aggregate_usage(aggregate)
            await self._db.execute(
                "UPDATE ai_agent_long_tasks SET usage_json = ?, "
                "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                [_json_dump(total.to_mapping()), task.id],
            )
            return await self._require(task.id)

    async def start(
        self,
        task_id: str,
        *,
        expected_revision: int,
    ) -> LongTaskRecord:
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            if task.cancellation_requested_at_ms is not None:
                return await self._cancel_in_transaction(task)
            if task.status is LongTaskStatus.RUNNING:
                return task
            if task.status is not LongTaskStatus.PENDING:
                raise ValueError("only a pending long task can start")
            if task.revision != int(expected_revision):
                raise ValueError("long task revision conflict")
            await self._update_task_status(task, LongTaskStatus.RUNNING)
            return await self._require(task.id)

    async def claim_ready_unit(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_duration_ms: int,
    ) -> LongTaskUnitRecord | None:
        normalized_worker = _required(worker_id, "long task worker id")
        now_ms = int(time.time() * 1000)
        lease_expires = now_ms + int(lease_duration_ms)
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            if (
                task.status is not LongTaskStatus.RUNNING
                or task.cancellation_requested_at_ms is not None
            ):
                return None
            active = await self._db.fetch_one(
                "SELECT COUNT(*) AS count FROM ai_agent_long_task_units "
                "WHERE task_id = ? AND status IN ('claimed', 'running') "
                "AND COALESCE(lease_expires_at_ms, 0) > ?",
                [task.id, now_ms],
            )
            if int((active or {}).get("count") or 0) >= task.max_parallelism:
                return None
            row = await self._db.fetch_one(
                "SELECT u.* FROM ai_agent_long_task_units AS u "
                "WHERE u.task_id = ? AND u.attempt < u.max_attempts AND ("
                "u.status IN ('pending', 'waiting_retry') OR "
                "(u.status IN ('claimed', 'running') "
                "AND COALESCE(u.lease_expires_at_ms, 0) <= ?)) "
                "AND NOT EXISTS ("
                "SELECT 1 FROM json_each(u.dependencies_json) AS dep "
                "LEFT JOIN ai_agent_long_task_units AS prerequisite "
                "ON prerequisite.task_id = u.task_id "
                "AND prerequisite.unit_id = dep.value "
                "WHERE prerequisite.status IS NULL "
                "OR prerequisite.status <> 'completed') "
                "ORDER BY u.position ASC LIMIT 1",
                [task.id, now_ms],
            )
            if row is None:
                return None
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'claimed', "
                "attempt = attempt + 1, worker_id = ?, lease_expires_at_ms = ?, "
                "run_id = NULL, update_time = CURRENT_TIMESTAMP "
                "WHERE task_id = ? AND unit_id = ?",
                [normalized_worker, lease_expires, task.id, str(row["unit_id"])],
            )
            await self._touch_task(task)
            claimed = await self._db.fetch_one(
                "SELECT * FROM ai_agent_long_task_units "
                "WHERE task_id = ? AND unit_id = ?",
                [task.id, str(row["unit_id"])],
            )
            return _unit(claimed)

    async def bind_unit_run(
        self,
        task_id: str,
        unit_id: str,
        *,
        worker_id: str,
        run_id: str,
    ) -> LongTaskUnitRecord:
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            unit = await self._require_unit(task_id, unit_id)
            if task.cancellation_requested_at_ms is not None:
                await self._cancel_in_transaction(task)
                return await self._require_unit(task.id, unit.id)
            _require_worker(unit, worker_id)
            if unit.status not in {LongTaskUnitStatus.CLAIMED, LongTaskUnitStatus.RUNNING}:
                raise ValueError("long task unit is not claimed")
            metadata = thaw_json_mapping(unit.metadata)
            raw_history = metadata.get("runHistory")
            run_history = [
                dict(thaw_json_mapping(item))
                for item in (
                    raw_history if isinstance(raw_history, (list, tuple)) else ()
                )
                if isinstance(item, Mapping)
            ]
            normalized_run_id = _required(run_id, "long task unit run id")
            if not any(
                str(item.get("runId") or "") == normalized_run_id
                for item in run_history
            ):
                run_history.append({
                    "attempt": max(1, int(unit.attempt or 1)),
                    "runId": normalized_run_id,
                })
            metadata["runHistory"] = run_history[-8:]
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'running', run_id = ?, "
                "metadata_json = ?, update_time = CURRENT_TIMESTAMP "
                "WHERE task_id = ? AND unit_id = ?",
                [normalized_run_id, _json_dump(metadata), unit.task_id, unit.id],
            )
            return await self._require_unit(unit.task_id, unit.id)

    async def update_unit_progress(
        self,
        task_id: str,
        unit_id: str,
        *,
        worker_id: str,
        metadata,
    ) -> LongTaskUnitRecord:
        """Persist bounded live progress without completing the unit."""

        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            unit = await self._require_unit(task.id, unit_id)
            if task.cancellation_requested_at_ms is not None:
                await self._cancel_in_transaction(task)
                return await self._require_unit(task.id, unit.id)
            if task.status is not LongTaskStatus.RUNNING:
                return unit
            _require_worker(unit, worker_id)
            if unit.status not in {
                LongTaskUnitStatus.CLAIMED,
                LongTaskUnitStatus.RUNNING,
            }:
                raise ValueError("long task unit is not active")
            merged = {
                **thaw_json_mapping(unit.metadata),
                **thaw_json_mapping(metadata),
            }
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET metadata_json = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE task_id = ? AND unit_id = ?",
                [_json_dump(merged), unit.task_id, unit.id],
            )
            await self._touch_task(task)
            return await self._require_unit(unit.task_id, unit.id)

    async def complete_unit(
        self,
        task_id: str,
        unit_id: str,
        *,
        worker_id: str,
        result: LongTaskUnitResult,
    ) -> LongTaskRecord:
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            unit = await self._require_unit(task.id, unit_id)
            if unit.status is LongTaskUnitStatus.COMPLETED:
                if (
                    unit.output_ref == result.output_ref
                    and unit.artifact_digest == result.artifact_digest
                    and thaw_json_mapping(unit.validation_receipt)
                    == thaw_json_mapping(result.validation_receipt)
                ):
                    return task
                raise ValueError("long task unit completion conflicts")
            if task.cancellation_requested_at_ms is not None:
                return await self._cancel_in_transaction(task)
            if task.status is not LongTaskStatus.RUNNING:
                raise ValueError("long task is not running")
            _require_worker(unit, worker_id)
            if unit.status not in {LongTaskUnitStatus.CLAIMED, LongTaskUnitStatus.RUNNING}:
                raise ValueError("long task unit is not active")
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'completed', "
                "output_ref = ?, run_id = COALESCE(?, run_id), worker_id = NULL, "
                "artifact_digest = ?, validation_receipt_json = ?, "
                "lease_expires_at_ms = NULL, error_code = NULL, failure_json = '{}', "
                "disposition = NULL, "
                "metadata_json = ?, update_time = CURRENT_TIMESTAMP "
                "WHERE task_id = ? AND unit_id = ?",
                [
                    result.output_ref,
                    result.run_id,
                    result.artifact_digest,
                    _json_dump(result.validation_receipt),
                    _json_dump({
                        **thaw_json_mapping(unit.metadata),
                        **thaw_json_mapping(result.metadata),
                    }),
                    unit.task_id,
                    unit.id,
                ],
            )
            await self._refresh_task_totals(task.id)
            return await self._require(task.id)

    async def settle_unit_failure(
        self,
        task_id: str,
        unit_id: str,
        *,
        worker_id: str,
        decision: FailureDecision,
    ) -> LongTaskRecord:
        if not isinstance(decision, FailureDecision):
            raise TypeError("long task failure settlement requires a FailureDecision")
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            unit = await self._require_unit(task.id, unit_id)
            if task.cancellation_requested_at_ms is not None:
                return await self._cancel_in_transaction(task)
            _require_worker(unit, worker_id)
            disposition = decision.disposition
            if disposition in {
                FailureDisposition.RETRY_ATTEMPT,
                FailureDisposition.RESUME_CHECKPOINT,
            }:
                target = LongTaskUnitStatus.WAITING_RETRY
            elif disposition is FailureDisposition.SPLIT_PART:
                target = LongTaskUnitStatus.NEEDS_SPLIT
            elif disposition is FailureDisposition.PAUSE_RECOVERABLE:
                target = LongTaskUnitStatus.BLOCKED
            elif disposition is FailureDisposition.CANCEL:
                target = LongTaskUnitStatus.CANCELED
            else:
                target = LongTaskUnitStatus.FAILED
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = ?, worker_id = NULL, "
                "lease_expires_at_ms = NULL, error_code = ?, "
                "failure_json = ?, disposition = ?, "
                "max_attempts = max_attempts + ?, "
                "update_time = CURRENT_TIMESTAMP WHERE task_id = ? AND unit_id = ?",
                [
                    target.value,
                    decision.code[:240],
                    _json_dump(_failure_payload(decision)),
                    disposition.value,
                    int(
                        disposition is FailureDisposition.RESUME_CHECKPOINT
                        and unit.attempt >= unit.max_attempts
                    ),
                    unit.task_id,
                    unit.id,
                ],
            )
            if target in {
                LongTaskUnitStatus.WAITING_RETRY,
                LongTaskUnitStatus.NEEDS_SPLIT,
            }:
                await self._touch_task(task)
            elif target is LongTaskUnitStatus.BLOCKED:
                if decision.scope is FailureScope.SYSTEMIC:
                    await self._update_task_status(task, LongTaskStatus.PAUSED)
                else:
                    await self._pause_if_no_runnable_work(task)
            elif target is LongTaskUnitStatus.CANCELED:
                await self._update_task_status(task, LongTaskStatus.CANCELED)
            else:
                await self._db.execute(
                    "UPDATE ai_agent_long_tasks SET status = 'failed', "
                    "failed_units = failed_units + 1, revision = revision + 1, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                    [task.id],
                )
                # A terminal branch failure closes the whole DAG. Settle every
                # sibling in the same transaction so no UI or recovery worker
                # can observe a failed task with claimed/running units.
                await self._db.execute(
                    "UPDATE ai_agent_long_task_units SET status = 'canceled', "
                    "worker_id = NULL, lease_expires_at_ms = NULL, "
                    "error_code = COALESCE(error_code, 'task_failed_dependency'), "
                    "update_time = CURRENT_TIMESTAMP WHERE task_id = ? "
                    "AND unit_id <> ? AND status IN "
                    "('pending', 'claimed', 'running')",
                    [task.id, unit.id],
                )
            return await self._require(task.id)

    async def expand_unit(
        self,
        task_id: str,
        unit_id: str,
        *,
        worker_id: str,
        split: LongTaskSplitResult,
        decision: FailureDecision,
    ) -> LongTaskRecord:
        """Replace one required Part with stable child Parts atomically."""

        if not isinstance(split, LongTaskSplitResult):
            raise TypeError("long task expansion requires a LongTaskSplitResult")
        if not split.children:
            raise ValueError("long task expansion requires at least one child")
        if not isinstance(decision, FailureDecision):
            raise TypeError("long task expansion requires a FailureDecision")
        if decision.disposition is not FailureDisposition.SPLIT_PART:
            raise ValueError("long task expansion requires split_part disposition")
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            unit = await self._require_unit(task.id, unit_id)
            if task.cancellation_requested_at_ms is not None:
                return await self._cancel_in_transaction(task)
            if unit.status is LongTaskUnitStatus.EXPANDED:
                return task
            if task.status is not LongTaskStatus.RUNNING:
                raise ValueError("long task is not running")
            _require_worker(unit, worker_id)
            if unit.status not in {
                LongTaskUnitStatus.CLAIMED,
                LongTaskUnitStatus.RUNNING,
                LongTaskUnitStatus.NEEDS_SPLIT,
            }:
                raise ValueError("long task unit is not active")

            existing_rows = await self._db.fetch_all(
                "SELECT unit_id, semantic_key, position FROM ai_agent_long_task_units "
                "WHERE task_id = ?",
                [task.id],
            )
            existing_ids = {str(row["unit_id"]) for row in existing_rows}
            existing_keys = {str(row["semantic_key"]) for row in existing_rows}
            existing_positions = {int(row["position"]) for row in existing_rows}
            child_ids = {child.id for child in split.children}
            if existing_ids & child_ids:
                raise ValueError("split child id conflicts with task manifest")
            if existing_keys & {str(child.semantic_key) for child in split.children}:
                raise ValueError("split child semantic key conflicts with task manifest")
            if existing_positions & {child.position for child in split.children}:
                raise ValueError("split child position conflicts with task manifest")
            known_ids = existing_ids | child_ids
            for child in split.children:
                dependencies = _unique_ordered((*unit.dependencies, *child.dependencies))
                if unit.id in dependencies:
                    raise ValueError("split child cannot depend on expanded parent")
                unknown = set(dependencies) - known_ids
                if unknown:
                    raise ValueError("split child dependency is unknown")
                await self._db.execute(
                    "INSERT INTO ai_agent_long_task_units "
                    "(task_id, unit_id, semantic_key, position, status, "
                    "dependencies_json, parent_unit_id, required, input_ref, "
                    "max_attempts, metadata_json) "
                    "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)",
                    [
                        task.id,
                        child.id,
                        child.semantic_key,
                        child.position,
                        json.dumps(list(dependencies), separators=(",", ":")),
                        child.parent_unit_id or unit.id,
                        int(child.required),
                        child.input_ref,
                        child.max_attempts,
                        _json_dump(child.metadata),
                    ],
                )

            downstream = await self._db.fetch_all(
                "SELECT unit_id, dependencies_json FROM ai_agent_long_task_units "
                "WHERE task_id = ? AND unit_id <> ?",
                [task.id, unit.id],
            )
            for row in downstream:
                dependencies = tuple(_json_load(row.get("dependencies_json"), []))
                if unit.id not in dependencies:
                    continue
                if not split.replacement_dependency_ids:
                    raise ValueError(
                        "split must replace dependencies on the expanded parent"
                    )
                rewritten: list[str] = []
                for dependency in dependencies:
                    if dependency == unit.id:
                        rewritten.extend(split.replacement_dependency_ids)
                    else:
                        rewritten.append(str(dependency))
                await self._db.execute(
                    "UPDATE ai_agent_long_task_units SET dependencies_json = ?, "
                    "update_time = CURRENT_TIMESTAMP WHERE task_id = ? AND unit_id = ?",
                    [
                        json.dumps(list(_unique_ordered(rewritten)), separators=(",", ":")),
                        task.id,
                        str(row["unit_id"]),
                    ],
                )

            manifest_rows = await self._db.fetch_all(
                "SELECT unit_id, dependencies_json FROM ai_agent_long_task_units "
                "WHERE task_id = ?",
                [task.id],
            )
            _require_acyclic_dependencies({
                str(row["unit_id"]): tuple(
                    _json_load(row.get("dependencies_json"), [])
                )
                for row in manifest_rows
            })

            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'needs_split', "
                "error_code = ?, failure_json = ?, disposition = 'split_part', "
                "update_time = CURRENT_TIMESTAMP WHERE task_id = ? AND unit_id = ?",
                [
                    decision.code[:240],
                    _json_dump(_failure_payload(decision)),
                    task.id,
                    unit.id,
                ],
            )
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'expanded', "
                "required = 0, worker_id = NULL, lease_expires_at_ms = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE task_id = ? AND unit_id = ?",
                [task.id, unit.id],
            )
            await self._refresh_task_totals(task.id)
            return await self._require(task.id)

    async def interrupt_unit(
        self,
        task_id: str,
        unit_id: str,
        *,
        worker_id: str,
        reason_code: str,
    ) -> LongTaskRecord:
        """Checkpoint an in-flight unit without consuming its retry budget."""

        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            unit = await self._require_unit(task.id, unit_id)
            if task.cancellation_requested_at_ms is not None:
                return await self._cancel_in_transaction(task)
            if task.status in {LongTaskStatus.PAUSED, LongTaskStatus.CANCELED}:
                return task
            if task.status is not LongTaskStatus.RUNNING:
                raise ValueError("long task is not running")
            _require_worker(unit, worker_id)
            if unit.status not in {
                LongTaskUnitStatus.CLAIMED,
                LongTaskUnitStatus.RUNNING,
            }:
                raise ValueError("long task unit is not active")
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'pending', "
                "max_attempts = max_attempts + 1, worker_id = NULL, "
                "lease_expires_at_ms = NULL, error_code = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE task_id = ? AND unit_id = ?",
                [
                    str(reason_code or "execution_interrupted")[:240],
                    unit.task_id,
                    unit.id,
                ],
            )
            await self._update_task_status(task, LongTaskStatus.PAUSED)
            return await self._require(task.id)

    async def recover_after_restart(
        self,
        *,
        reason_code: str = "execution_recovery_after_restart",
    ) -> tuple[str, ...]:
        """Pause every process-owned task and release its active unit."""

        async with self._db.transaction(cancellation_linearizable=True):
            rows = await self._db.fetch_all(
                "SELECT id FROM ai_agent_long_tasks "
                "WHERE status = 'running' ORDER BY create_time ASC"
            )
            task_ids = tuple(str(row["id"]) for row in rows)
            for task_id in task_ids:
                task = await self._require(task_id)
                if task.cancellation_requested_at_ms is not None:
                    await self._cancel_in_transaction(task)
                    continue
                await self._db.execute(
                    "UPDATE ai_agent_long_task_units SET status = 'pending', "
                    "max_attempts = max_attempts + 1, worker_id = NULL, "
                    "lease_expires_at_ms = NULL, error_code = ?, "
                    "update_time = CURRENT_TIMESTAMP WHERE task_id = ? "
                    "AND status IN ('claimed', 'running')",
                    [str(reason_code or "execution_recovery_after_restart")[:240], task_id],
                )
                await self._db.execute(
                    "UPDATE ai_agent_long_tasks SET status = 'paused', "
                    "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND status = 'running'",
                    [task_id],
                )
            return task_ids

    async def pause(self, task_id: str) -> LongTaskRecord:
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            if task.cancellation_requested_at_ms is not None:
                return await self._cancel_in_transaction(task)
            if task.status is LongTaskStatus.PAUSED:
                return task
            if task.status not in {LongTaskStatus.PENDING, LongTaskStatus.RUNNING}:
                raise ValueError(
                    f"long task cannot transition from {task.status.value}"
                )
            await self._update_task_status(task, LongTaskStatus.PAUSED)
            # A pause is a checkpoint boundary, not a five-minute lease wait.
            # Any in-flight child Run is signaled by the application composition;
            # releasing its unit here makes an immediate resume deterministic.
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'pending', "
                "max_attempts = max_attempts + 1, worker_id = NULL, "
                "lease_expires_at_ms = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE task_id = ? "
                "AND status IN ('claimed', 'running')",
                [task.id],
            )
            return await self._require(task.id)

    async def resume(
        self,
        task_id: str,
        *,
        additional_attempts: int = 0,
    ) -> LongTaskRecord:
        extra_attempts = int(additional_attempts)
        if extra_attempts < 0:
            raise ValueError("additional long task attempts cannot be negative")
        async with self._mutation_transaction():
            task = await self._require(task_id)
            if task.cancellation_requested_at_ms is not None:
                return await self._cancel_in_transaction(task)
            if task.status is LongTaskStatus.RUNNING:
                return task
            if task.status is LongTaskStatus.FAILED:
                if extra_attempts == 0:
                    raise ValueError(
                        "failed long task retry requires additional attempts"
                    )
                await self._db.execute(
                    "UPDATE ai_agent_long_task_units SET status = 'pending', "
                    "max_attempts = max_attempts + ?, worker_id = NULL, "
                    "lease_expires_at_ms = NULL, update_time = CURRENT_TIMESTAMP "
                    "WHERE task_id = ? AND status IN "
                    "('failed', 'canceled', 'claimed', 'running')",
                    [extra_attempts, task.id],
                )
                await self._db.execute(
                    "UPDATE ai_agent_long_tasks SET status = 'running', "
                    "failed_units = 0, revision = revision + 1, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                    [task.id],
                )
                return await self._require(task.id)
            if task.status is not LongTaskStatus.PAUSED:
                raise ValueError(f"long task cannot transition from {task.status.value}")
            blocked = await self._db.fetch_one(
                "SELECT COUNT(*) AS count FROM ai_agent_long_task_units "
                "WHERE task_id = ? AND status = 'blocked'",
                [task.id],
            )
            blocked_count = int((blocked or {}).get("count") or 0)
            if blocked_count and extra_attempts == 0:
                raise ValueError(
                    "blocked long task resume requires additional attempts"
                )
            if blocked_count:
                await self._db.execute(
                    "UPDATE ai_agent_long_task_units SET status = 'pending', "
                    "max_attempts = max_attempts + ?, worker_id = NULL, "
                    "lease_expires_at_ms = NULL, update_time = CURRENT_TIMESTAMP "
                    "WHERE task_id = ? AND status = 'blocked'",
                    [extra_attempts, task.id],
                )
            elif extra_attempts:
                raise ValueError(
                    "paused long task has no blocked unit requiring attempts"
                )
            await self._update_task_status(task, LongTaskStatus.RUNNING)
            return await self._require(task.id)

    async def cancel(self, task_id: str) -> LongTaskRecord:
        async with self._mutation_transaction():
            task = await self._require(task_id)
            if task.status is LongTaskStatus.CANCELED:
                return task
            if task.status.terminal:
                raise ValueError("terminal long task cannot be canceled")
            return await self._cancel_in_transaction(task)

    async def _cancel_in_transaction(
        self,
        task: LongTaskRecord,
    ) -> LongTaskRecord:
        await self._update_task_status(task, LongTaskStatus.CANCELED)
        await self._db.execute(
            "UPDATE ai_agent_long_task_units SET status = 'canceled', "
            "worker_id = NULL, lease_expires_at_ms = NULL, "
            "update_time = CURRENT_TIMESTAMP WHERE task_id = ? "
            "AND status IN ('pending', 'waiting_retry', 'claimed', "
            "'running', 'needs_split', 'blocked')",
            [task.id],
        )
        return await self._require(task.id)

    async def request_cancel(
        self,
        task_id: str,
        *,
        requested_at_ms: int | None = None,
    ) -> LongTaskRecord:
        requested_at = (
            int(time.time() * 1000)
            if requested_at_ms is None
            else int(requested_at_ms)
        )
        async with self._mutation_transaction():
            task = await self._require(task_id)
            if task.status.terminal:
                return task
            await self._db.execute(
                "UPDATE ai_agent_long_tasks SET cancel_requested_at_ms = "
                "COALESCE(cancel_requested_at_ms, ?), revision = revision + 1, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ? "
                "AND cancel_requested_at_ms IS NULL",
                [requested_at, task.id],
            )
            return await self._require(task.id)

    async def finalize_if_complete(self, task_id: str) -> LongTaskRecord:
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            if task.cancellation_requested_at_ms is not None:
                return await self._cancel_in_transaction(task)
            if task.status is not LongTaskStatus.RUNNING:
                return task
            counts = await self._db.fetch_one(
                "SELECT SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS done, "
                "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed, "
                "SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked, "
                "SUM(CASE WHEN status IN ('claimed', 'running') THEN 1 ELSE 0 END) "
                "AS active FROM ai_agent_long_task_units "
                "WHERE task_id = ? AND required = 1",
                [task.id],
            )
            done = int((counts or {}).get("done") or 0)
            failed = int((counts or {}).get("failed") or 0)
            blocked = int((counts or {}).get("blocked") or 0)
            active = int((counts or {}).get("active") or 0)
            if failed:
                await self._update_task_status(task, LongTaskStatus.FAILED)
            elif done == task.total_units:
                await self._update_task_status(task, LongTaskStatus.COMPLETED)
            elif blocked and not active:
                await self._update_task_status(task, LongTaskStatus.PAUSED)
            return await self._require(task.id)

    async def _simple_transition(self, task_id, allowed, target):
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            if task.status is target:
                return task
            if task.status not in allowed:
                raise ValueError(f"long task cannot transition from {task.status.value}")
            await self._update_task_status(task, target)
            return await self._require(task.id)

    async def _require(self, task_id: str) -> LongTaskRecord:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_long_tasks WHERE id = ?",
            [str(task_id or "").strip()],
        )
        if row is None:
            raise LookupError("long task does not exist")
        return _task(row)

    async def _require_unit(self, task_id: str, unit_id: str) -> LongTaskUnitRecord:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_long_task_units WHERE task_id = ? AND unit_id = ?",
            [str(task_id or "").strip(), str(unit_id or "").strip()],
        )
        if row is None:
            raise LookupError("long task unit does not exist")
        return _unit(row)

    async def _update_task_status(self, task, target):
        await self._db.execute(
            "UPDATE ai_agent_long_tasks SET status = ?, revision = revision + 1, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [target.value, task.id],
        )

    async def _touch_task(self, task):
        await self._db.execute(
            "UPDATE ai_agent_long_tasks SET revision = revision + 1, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [task.id],
        )

    async def _refresh_task_totals(self, task_id: str) -> None:
        await self._db.execute(
            "UPDATE ai_agent_long_tasks SET "
            "total_units = (SELECT COUNT(*) FROM ai_agent_long_task_units "
            "WHERE task_id = ? AND required = 1), "
            "completed_units = (SELECT COUNT(*) FROM ai_agent_long_task_units "
            "WHERE task_id = ? AND required = 1 AND status = 'completed'), "
            "failed_units = (SELECT COUNT(*) FROM ai_agent_long_task_units "
            "WHERE task_id = ? AND required = 1 AND status = 'failed'), "
            "revision = revision + 1, update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [task_id, task_id, task_id, task_id],
        )

    async def _pause_if_no_runnable_work(self, task: LongTaskRecord) -> None:
        work = await self._db.fetch_one(
            "SELECT "
            "SUM(CASE WHEN u.status IN ('claimed', 'running') THEN 1 ELSE 0 END) "
            "AS active, "
            "SUM(CASE WHEN u.attempt < u.max_attempts "
            "AND u.status IN ('pending', 'waiting_retry') AND NOT EXISTS ("
            "SELECT 1 FROM json_each(u.dependencies_json) AS dep "
            "LEFT JOIN ai_agent_long_task_units AS prerequisite "
            "ON prerequisite.task_id = u.task_id "
            "AND prerequisite.unit_id = dep.value "
            "WHERE prerequisite.status IS NULL "
            "OR prerequisite.status <> 'completed') THEN 1 ELSE 0 END) AS ready "
            "FROM ai_agent_long_task_units AS u WHERE u.task_id = ?",
            [task.id],
        )
        active = int((work or {}).get("active") or 0)
        ready = int((work or {}).get("ready") or 0)
        if active or ready:
            await self._touch_task(task)
            return
        await self._update_task_status(task, LongTaskStatus.PAUSED)


def _task(row: dict[str, Any] | None) -> LongTaskRecord:
    if row is None:
        raise LookupError("long task does not exist")
    return LongTaskRecord(
        id=str(row["id"]),
        namespace=str(row["namespace"]),
        kind=str(row["kind"]),
        owner_id=str(row["owner_id"]),
        work_item_id=str(row["work_item_id"]),
        created_by_run_id=str(row["created_by_run_id"]),
        status=str(row["status"]),
        revision=int(row["revision"]),
        total_units=int(row["total_units"]),
        completed_units=int(row["completed_units"]),
        failed_units=int(row["failed_units"]),
        max_parallelism=int(row["max_parallelism"]),
        cancellation_requested_at_ms=row.get("cancel_requested_at_ms"),
        usage=_usage_mapping(_json_load(row.get("usage_json"), {})),
        metadata=_json_load(row.get("metadata_json"), {}),
        create_time=row.get("create_time"),
        update_time=row.get("update_time"),
    )


def _unit(row: dict[str, Any] | None) -> LongTaskUnitRecord:
    if row is None:
        raise LookupError("long task unit does not exist")
    return LongTaskUnitRecord(
        task_id=str(row["task_id"]),
        id=str(row["unit_id"]),
        position=int(row["position"]),
        status=str(row["status"]),
        semantic_key=row.get("semantic_key") or row["unit_id"],
        dependencies=tuple(_json_load(row.get("dependencies_json"), [])),
        parent_unit_id=row.get("parent_unit_id"),
        required=bool(row.get("required", 1)),
        attempt=int(row.get("attempt") or 0),
        max_attempts=int(row.get("max_attempts") or 3),
        worker_id=row.get("worker_id"),
        lease_expires_at_ms=row.get("lease_expires_at_ms"),
        run_id=row.get("run_id"),
        input_ref=row.get("input_ref"),
        output_ref=row.get("output_ref"),
        artifact_digest=row.get("artifact_digest"),
        validation_receipt=_json_load(row.get("validation_receipt_json"), {}),
        failure=_json_load(row.get("failure_json"), {}),
        disposition=row.get("disposition"),
        error_code=row.get("error_code"),
        metadata=_json_load(row.get("metadata_json"), {}),
        create_time=row.get("create_time"),
        update_time=row.get("update_time"),
    )


def _require_worker(unit: LongTaskUnitRecord, worker_id: str) -> None:
    if unit.worker_id != _required(worker_id, "long task worker id"):
        raise ValueError("long task unit is owned by another worker")


def _required(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _json_dump(value) -> str:
    return json.dumps(
        thaw_json_mapping(value),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_load(value: object, default):
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _usage_mapping(value: Mapping[str, Any]) -> LongTaskUsage:
    return LongTaskUsage(
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


def _usage_row(row: Mapping[str, Any]) -> LongTaskUsage:
    return LongTaskUsage(
        invocation_count=int(row.get("invocation_count") or 0),
        input_tokens=int(row.get("input_tokens") or 0),
        output_tokens=int(row.get("output_tokens") or 0),
        reasoning_tokens=(
            None
            if row.get("reasoning_tokens") is None
            else int(row["reasoning_tokens"])
        ),
    )


def _aggregate_usage(row: Mapping[str, Any] | None) -> LongTaskUsage:
    value = row or {}
    return LongTaskUsage(
        invocation_count=int(value.get("invocation_count") or 0),
        input_tokens=int(value.get("input_tokens") or 0),
        output_tokens=int(value.get("output_tokens") or 0),
        reasoning_tokens=(
            None
            if int(value.get("unknown_reasoning") or 0) > 0
            else int(value.get("reasoning_tokens") or 0)
        ),
    )


def _metadata_session_id(value: object) -> SessionId | None:
    metadata = thaw_json_mapping(value)
    raw = metadata.get("sessionId")
    if raw is None:
        return None
    session_id = str(raw).strip()
    return session_id or None


def _matches_create(task, units, command: LongTaskCreateCommand) -> bool:
    if (
        task.namespace != command.namespace
        or task.kind != command.kind
        or task.owner_id != command.owner_id
        or task.work_item_id != command.work_item_id
        or task.created_by_run_id != command.created_by_run_id
        or task.max_parallelism != command.max_parallelism
        or thaw_json_mapping(task.metadata) != thaw_json_mapping(command.metadata)
        or len(units) != len(command.units)
    ):
        return False
    return all(
        persisted.id == requested.id
        and persisted.semantic_key == requested.semantic_key
        and persisted.position == requested.position
        and persisted.dependencies == requested.dependencies
        and persisted.parent_unit_id == requested.parent_unit_id
        and persisted.required == requested.required
        and persisted.input_ref == requested.input_ref
        and persisted.max_attempts == requested.max_attempts
        and thaw_json_mapping(persisted.metadata)
        == thaw_json_mapping(requested.metadata)
        for persisted, requested in zip(units, command.units)
    )


def _failure_payload(decision: FailureDecision) -> dict[str, object]:
    return {
        "category": decision.category.value,
        "code": decision.code,
        "scope": decision.scope.value,
        "effectState": decision.effect_state.value,
        "checkpointAvailable": decision.checkpoint_available,
        "partSplittable": decision.part_splittable,
    }


def _unique_ordered(values) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)


def _require_acyclic_dependencies(graph: Mapping[str, tuple[str, ...]]) -> None:
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as error:
        raise ValueError("expanded long task dependencies contain a cycle") from error


__all__ = ["SqliteLongTaskRepository"]
