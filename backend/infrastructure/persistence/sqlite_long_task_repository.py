"""SQLite adapter for durable multi-Run task execution."""

from __future__ import annotations

import json
import sqlite3
import time
from hashlib import blake2s
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from graphlib import CycleError, TopologicalSorter
from typing import Any

from purra.contracts import SessionId
from purra.errors import ContractViolationError
from purra.json_values import thaw_json_mapping
from purra.long_tasks import (
    BudgetExhaustionDisposition,
    LongTaskCreateCommand,
    LongTaskBudgetLimits,
    LongTaskRecord,
    LongTaskRunBinding,
    LongTaskRunRelation,
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


LongTaskClaimGuard = Callable[[LongTaskRecord, LongTaskUnitRecord], Awaitable[bool]]


async def append_long_task_event(
    db,
    *,
    task_id: str,
    event_type: str,
    source_key: str,
    reason_code: str | None = None,
    reason_scope: str | None = None,
    payload: Mapping[str, object] | None = None,
) -> None:
    """Write an idempotent, non-content task recovery fact."""

    await db.execute(
        "INSERT OR IGNORE INTO ai_agent_long_task_events "
        "(task_id, event_type, reason_code, reason_scope, payload_json, source_key) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            str(task_id),
            str(event_type)[:80],
            str(reason_code)[:240] if reason_code else None,
            str(reason_scope)[:32] if reason_scope else None,
            _json_dump(dict(payload or {})),
            str(source_key)[:300],
        ],
    )


class SqliteLongTaskRepository:
    def __init__(self, db, *, claim_guard: LongTaskClaimGuard | None = None) -> None:
        self._db = db
        self._claim_guard = claim_guard

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
        metadata = {
            **thaw_json_mapping(command.metadata),
            "budgetExhaustionDisposition": (
                command.budget_exhaustion_disposition.value
            ),
        }
        try:
            async with self._mutation_transaction():
                await self._db.execute(
                    "INSERT INTO ai_agent_long_tasks "
                    "(id, namespace, kind, owner_id, "
                    "created_by_run_id, total_units, max_parallelism, "
                    "deadline_at_ms, budget_limits_json, metadata_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        normalized_id,
                        command.namespace,
                        command.kind,
                        command.owner_id,
                        command.created_by_run_id,
                        sum(1 for unit in command.units if unit.required),
                        command.max_parallelism,
                        command.deadline_at_ms,
                        _json_dump(command.budget_limits.to_mapping()),
                        _json_dump(metadata),
                    ],
                )
                await self._db.execute(
                    "INSERT INTO ai_agent_long_task_runs "
                    "(task_id, run_id, relation) VALUES (?, ?, 'created')",
                    [normalized_id, command.created_by_run_id],
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

    async def bind_run(
        self,
        task_id: str,
        run_id: str,
        *,
        relation: LongTaskRunRelation,
    ) -> LongTaskRunBinding:
        normalized_task = _required(task_id, "long task id")
        normalized_run = _required(run_id, "long task Run id")
        normalized_relation = LongTaskRunRelation(relation)
        async with self._mutation_transaction():
            await self._require(normalized_task)
            existing = await self._db.fetch_one(
                "SELECT relation FROM ai_agent_long_task_runs "
                "WHERE task_id = ? AND run_id = ?",
                [normalized_task, normalized_run],
            )
            if existing is not None:
                persisted = LongTaskRunRelation(str(existing["relation"]))
                if persisted is not normalized_relation:
                    raise ValueError("long task Run binding relation conflicts")
            else:
                await self._db.execute(
                    "INSERT INTO ai_agent_long_task_runs "
                    "(task_id, run_id, relation) VALUES (?, ?, ?)",
                    [normalized_task, normalized_run, normalized_relation.value],
                )
        return LongTaskRunBinding(
            task_id=normalized_task,
            run_id=normalized_run,
            relation=normalized_relation,
        )

    async def list_run_bindings(
        self,
        task_id: str,
    ) -> tuple[LongTaskRunBinding, ...]:
        normalized_task = _required(task_id, "long task id")
        rows = await self._db.fetch_all(
            "SELECT task_id, run_id, relation FROM ai_agent_long_task_runs "
            "WHERE task_id = ? ORDER BY create_time ASC, run_id ASC",
            [normalized_task],
        )
        return tuple(
            LongTaskRunBinding(
                task_id=str(row["task_id"]),
                run_id=str(row["run_id"]),
                relation=str(row["relation"]),
            )
            for row in rows
        )

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

    async def find_by_idempotency_key(
        self,
        namespace: str,
        idempotency_key: str,
    ) -> LongTaskRecord | None:
        normalized_namespace = _required(namespace, "long task namespace")
        normalized_key = _required(
            idempotency_key,
            "long task idempotency key",
        )
        rows = await self._db.fetch_all(
            "SELECT * FROM ai_agent_long_tasks WHERE namespace = ? "
            "AND CAST(json_extract(metadata_json, '$.idempotencyKey') AS TEXT) = ? "
            "ORDER BY update_time DESC LIMIT 2",
            [normalized_namespace, normalized_key],
        )
        if len(rows) > 1:
            raise ValueError("long task idempotency key conflicts")
        return _task(rows[0]) if rows else None

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
                "(task_id, run_id, invocation_count, "
                "unreported_usage_attempts, input_tokens, "
                "output_tokens, reasoning_tokens) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    task.id,
                    normalized_run_id,
                    usage.invocation_count,
                    usage.unreported_usage_attempts,
                    usage.input_tokens,
                    usage.generation_tokens,
                    usage.reasoning_tokens,
                ],
            )
            aggregate = await self._db.fetch_one(
                "SELECT SUM(invocation_count) AS invocation_count, "
                "SUM(unreported_usage_attempts) AS unreported_usage_attempts, "
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
            updated = await self._require(task.id)
            budget_kind = _task_budget_exhaustion(updated, exceeded_only=True)
            if budget_kind is not None:
                return await self._fail_budget(updated, budget_kind)
            return updated

    async def start(
        self,
        task_id: str,
        *,
        expected_revision: int,
    ) -> LongTaskRecord:
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            if _deadline_elapsed(task):
                return await self._expire_deadline_in_transaction(task)
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
        return await self._claim_unit(task_id, worker_id=worker_id,
                                      lease_duration_ms=lease_duration_ms)

    async def claim_unit(
        self, task_id: str, unit_id: str, *, worker_id: str,
        lease_duration_ms: int,
    ) -> LongTaskUnitRecord | None:
        return await self._claim_unit(
            task_id, unit_id=_required(unit_id, "long task unit id"),
            worker_id=worker_id, lease_duration_ms=lease_duration_ms,
        )

    async def _claim_unit(
        self, task_id: str, *, worker_id: str, lease_duration_ms: int,
        unit_id: str | None = None,
    ) -> LongTaskUnitRecord | None:
        normalized_worker = _required(worker_id, "long task worker id")
        if int(lease_duration_ms) <= 0:
            raise ValueError("long task lease duration must be positive")
        now_ms = int(time.time() * 1000)
        lease_expires = now_ms + int(lease_duration_ms)
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            if _deadline_elapsed(task, now_ms=now_ms):
                await self._expire_deadline_in_transaction(task)
                return None
            budget_kind = _task_budget_exhaustion(task)
            if budget_kind is not None:
                await self._fail_budget(task, budget_kind)
                return None
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
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'failed', "
                "worker_id = NULL, lease_expires_at_ms = NULL, "
                "error_code = 'lease_expired_attempts_exhausted', "
                "disposition = 'fail_permanent', "
                "failure_json = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE task_id = ? "
                "AND status IN ('claimed', 'running') "
                "AND COALESCE(lease_expires_at_ms, 0) <= ? "
                "AND attempt >= max_attempts",
                [
                    _json_dump({
                        "category": "permanent_external",
                        "code": "lease_expired_attempts_exhausted",
                        "scope": "systemic",
                        "effectState": "unknown",
                        "checkpointAvailable": False,
                        "partSplittable": False,
                    }),
                    task.id,
                    now_ms,
                ],
            )
            exhausted = await self._db.fetch_one(
                "SELECT unit_id FROM ai_agent_long_task_units "
                "WHERE task_id = ? AND required = 1 AND status = 'failed' "
                "AND error_code = 'lease_expired_attempts_exhausted' "
                "ORDER BY position ASC LIMIT 1",
                [task.id],
            )
            if exhausted is not None:
                await self._db.execute(
                    "UPDATE ai_agent_long_task_units SET status = 'canceled', "
                    "worker_id = NULL, lease_expires_at_ms = NULL, "
                    "error_code = COALESCE(error_code, 'task_failed_dependency'), "
                    "update_time = CURRENT_TIMESTAMP WHERE task_id = ? "
                    "AND unit_id <> ? AND status NOT IN "
                    "('completed', 'expanded', 'failed', 'canceled')",
                    [task.id, str(exhausted["unit_id"])],
                )
                await self._refresh_task_totals(task.id)
                await self._update_task_status(
                    await self._require(task.id),
                    LongTaskStatus.FAILED,
                )
                return None
            row = await self._db.fetch_one(
                "SELECT u.* FROM ai_agent_long_task_units AS u "
                "WHERE u.task_id = ? AND (? IS NULL OR u.unit_id = ?) "
                "AND u.attempt < u.max_attempts AND ("
                "u.status = 'pending' OR "
                "(u.status = 'waiting_retry' AND COALESCE(CAST("
                "json_extract(u.metadata_json, '$.retryNotBeforeMs') AS INTEGER), 0) <= ?) OR "
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
                [task.id, unit_id, unit_id, now_ms, now_ms],
            )
            if row is None:
                return None
            if self._claim_guard is not None and not await self._claim_guard(
                task, _unit(row),
            ):
                return None
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'claimed', "
                "attempt = attempt + 1, worker_id = ?, lease_expires_at_ms = ?, "
                "lease_epoch = lease_epoch + 1, settled_by_worker_id = NULL, "
                "run_id = NULL, metadata_json = json_remove(metadata_json, "
                "'$.retryNotBeforeMs', '$.retryBackoffMs'), update_time = CURRENT_TIMESTAMP "
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

    async def expire_deadline(self, task_id: str) -> LongTaskRecord:
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            if task.status.terminal:
                return task
            if not _deadline_elapsed(task):
                raise ContractViolationError(
                    "long task deadline has not elapsed",
                    code="long_task_deadline_not_elapsed",
                )
            return await self._expire_deadline_in_transaction(task)

    async def bind_unit_run(
        self,
        task_id: str,
        unit_id: str,
        *,
        worker_id: str,
        lease_epoch: int,
        run_id: str,
    ) -> LongTaskUnitRecord:
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            unit = await self._require_unit(task_id, unit_id)
            if task.cancellation_requested_at_ms is not None:
                await self._cancel_in_transaction(task)
                return await self._require_unit(task.id, unit.id)
            await self._require_active_unit(
                task,
                unit,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
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
            if unit.run_id is not None:
                if unit.run_id != normalized_run_id:
                    raise ContractViolationError("A Unit attempt cannot change its Run", code="long_task_unit_run_conflict")
                return unit
            duplicate = await self._db.fetch_one(
                "SELECT unit_id FROM ai_agent_long_task_units WHERE task_id = ? AND run_id = ? AND unit_id <> ? LIMIT 1",
                [task.id, normalized_run_id, unit.id],
            )
            if duplicate is not None:
                raise ContractViolationError("A Run cannot belong to two task Units", code="long_task_unit_run_conflict")
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

    async def renew_unit_lease(
        self,
        task_id: str,
        unit_id: str,
        *,
        worker_id: str,
        lease_epoch: int,
        lease_duration_ms: int,
    ) -> LongTaskUnitRecord:
        duration = int(lease_duration_ms)
        if duration <= 0:
            raise ValueError("long task lease duration must be positive")
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            unit = await self._require_unit(task.id, unit_id)
            await self._require_active_unit(
                task,
                unit,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET lease_expires_at_ms = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE task_id = ? AND unit_id = ?",
                [int(time.time() * 1000) + duration, task.id, unit.id],
            )
            await self._touch_task(task)
            return await self._require_unit(task.id, unit.id)

    async def update_unit_progress(
        self,
        task_id: str,
        unit_id: str,
        *,
        worker_id: str,
        lease_epoch: int,
        metadata,
    ) -> LongTaskUnitRecord:
        """Persist bounded live progress without completing the unit."""

        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            unit = await self._require_unit(task.id, unit_id)
            if task.cancellation_requested_at_ms is not None:
                await self._cancel_in_transaction(task)
                return await self._require_unit(task.id, unit.id)
            await self._require_active_unit(
                task,
                unit,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
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
        lease_epoch: int,
        result: LongTaskUnitResult,
    ) -> LongTaskRecord:
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            unit = await self._require_unit(task.id, unit_id)
            if unit.status is LongTaskUnitStatus.COMPLETED:
                if (
                    unit.lease_epoch == int(lease_epoch)
                    and unit.settled_by_worker_id
                    == _required(worker_id, "long task worker id")
                    and
                    unit.output_ref == result.output_ref
                    and (result.run_id is None or unit.run_id == result.run_id)
                    and unit.artifact_digest == result.artifact_digest
                    and thaw_json_mapping(unit.validation_receipt)
                    == thaw_json_mapping(result.validation_receipt)
                ):
                    return task
                self._raise_lease_lost(unit)
            if task.cancellation_requested_at_ms is not None:
                return await self._cancel_in_transaction(task)
            await self._require_active_unit(
                task,
                unit,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
            if unit.run_id is not None and result.run_id is not None and result.run_id != unit.run_id:
                raise ContractViolationError("Unit result belongs to a different Run", code="long_task_unit_run_conflict")
            selected_run = result.run_id or unit.run_id
            if selected_run is not None:
                duplicate = await self._db.fetch_one(
                    "SELECT unit_id FROM ai_agent_long_task_units WHERE task_id = ? AND run_id = ? AND unit_id <> ? LIMIT 1",
                    [task.id, selected_run, unit.id],
                )
                if duplicate is not None:
                    raise ContractViolationError("A Run cannot belong to two task Units", code="long_task_unit_run_conflict")
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'completed', "
                "output_ref = ?, run_id = COALESCE(?, run_id), worker_id = NULL, "
                "settled_by_worker_id = ?, "
                "artifact_digest = ?, validation_receipt_json = ?, "
                "lease_expires_at_ms = NULL, error_code = NULL, failure_json = '{}', "
                "disposition = NULL, "
                "metadata_json = ?, update_time = CURRENT_TIMESTAMP "
                "WHERE task_id = ? AND unit_id = ?",
                [
                    result.output_ref,
                    result.run_id,
                    _required(worker_id, "long task worker id"),
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
        lease_epoch: int,
        decision: FailureDecision,
    ) -> LongTaskRecord:
        if not isinstance(decision, FailureDecision):
            raise TypeError("long task failure settlement requires a FailureDecision")
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            unit = await self._require_unit(task.id, unit_id)
            if task.cancellation_requested_at_ms is not None:
                return await self._cancel_in_transaction(task)
            await self._require_active_unit(
                task,
                unit,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
            disposition = decision.disposition
            if disposition in {
                FailureDisposition.RETRY_ATTEMPT,
                FailureDisposition.RESUME_CHECKPOINT,
            }:
                target = LongTaskUnitStatus.WAITING_RETRY
            elif disposition is FailureDisposition.PAUSE_RECOVERABLE:
                target = LongTaskUnitStatus.BLOCKED
            elif disposition is FailureDisposition.SPLIT_PART:
                target = LongTaskUnitStatus.NEEDS_SPLIT
            elif disposition is FailureDisposition.CANCEL:
                target = LongTaskUnitStatus.CANCELED
            else:
                target = LongTaskUnitStatus.FAILED
            now_ms = int(time.time() * 1000)
            unit_metadata = dict(thaw_json_mapping(unit.metadata))
            retry_wait_spent_ms = _metadata_non_negative_int(
                unit_metadata.get("retryWaitSpentMs"),
            )
            retry_not_before_ms = (
                _retry_not_before_ms(
                    task.id,
                    unit.id,
                    attempt=unit.attempt,
                    now_ms=now_ms,
                )
                if target is LongTaskUnitStatus.WAITING_RETRY
                else None
            )
            retry_backoff_ms = (
                retry_not_before_ms - now_ms
                if retry_not_before_ms is not None
                else None
            )
            retry_wait_budget_exhausted = bool(
                retry_backoff_ms is not None
                and retry_wait_spent_ms + retry_backoff_ms
                > _retry_wait_budget_ms(task)
            )
            if retry_wait_budget_exhausted:
                target = LongTaskUnitStatus.BLOCKED
                retry_not_before_ms = None
                retry_backoff_ms = None
                unit_metadata["retryWaitBudgetExceeded"] = True
            elif retry_not_before_ms is not None and retry_backoff_ms is not None:
                unit_metadata["retryNotBeforeMs"] = retry_not_before_ms
                unit_metadata["retryBackoffMs"] = retry_backoff_ms
                unit_metadata["retryWaitSpentMs"] = (
                    retry_wait_spent_ms + retry_backoff_ms
                )
                unit_metadata.pop("retryWaitBudgetExceeded", None)
            else:
                unit_metadata.pop("retryNotBeforeMs", None)
                unit_metadata.pop("retryBackoffMs", None)
                unit_metadata.pop("retryWaitSpentMs", None)
            stored_disposition = (
                FailureDisposition.PAUSE_RECOVERABLE
                if retry_wait_budget_exhausted
                else disposition
            )
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = ?, worker_id = NULL, "
                "lease_expires_at_ms = NULL, error_code = ?, "
                "failure_json = ?, disposition = ?, "
                "max_attempts = max_attempts + ?, "
                "metadata_json = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE task_id = ? AND unit_id = ?",
                [
                    target.value,
                    decision.code[:240],
                    _json_dump(_failure_payload(decision)),
                    stored_disposition.value,
                    int(
                        disposition is FailureDisposition.RESUME_CHECKPOINT
                        and unit.attempt >= unit.max_attempts
                    ),
                    _json_dump(unit_metadata),
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
                    await self._set_task_state_reason(
                        task,
                        code=decision.code,
                        scope="system",
                    )
                    await self._schedule_system_recovery(
                        task,
                        reason_code=decision.code,
                        now_ms=now_ms,
                    )
                    await self._update_task_status(task, LongTaskStatus.PAUSED)
                    await self._db.execute(
                        "UPDATE ai_agent_long_task_units SET status = 'pending', "
                        "max_attempts = max_attempts + 1, worker_id = NULL, "
                        "lease_expires_at_ms = NULL, error_code = COALESCE(error_code, ?), "
                        "update_time = CURRENT_TIMESTAMP WHERE task_id = ? "
                        "AND unit_id <> ? AND status IN ('claimed', 'running')",
                        [decision.code[:240], task.id, unit.id],
                    )
                    updated = await self._require(task.id)
                    await append_long_task_event(
                        self._db,
                        task_id=updated.id,
                        event_type="task_paused",
                        reason_code=decision.code,
                        reason_scope="system",
                        source_key=f"{updated.id}:paused:{updated.revision}",
                        payload={
                            "blockedUnitId": unit.id,
                            "automaticRecoveryAttempt": _metadata_non_negative_int(
                                updated.metadata.get("automaticRecoveryAttempt"),
                            ),
                            "autoResumeNotBeforeMs": _metadata_non_negative_int(
                                updated.metadata.get("autoResumeNotBeforeMs"),
                            ),
                        },
                    )
                else:
                    await self._touch_task(task)
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
        lease_epoch: int,
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
                if (
                    unit.lease_epoch == int(lease_epoch)
                    and unit.settled_by_worker_id
                    == _required(worker_id, "long task worker id")
                ):
                    return task
                self._raise_lease_lost(unit)
            await self._require_active_unit(
                task,
                unit,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )

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
                "settled_by_worker_id = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE task_id = ? AND unit_id = ?",
                [_required(worker_id, "long task worker id"), task.id, unit.id],
            )
            await self._refresh_task_totals(task.id)
            return await self._require(task.id)

    async def interrupt_unit(
        self,
        task_id: str,
        unit_id: str,
        *,
        worker_id: str,
        lease_epoch: int,
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
            await self._require_active_unit(
                task,
                unit,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
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
                now_ms = int(time.time() * 1000)
                await self._db.execute(
                    "UPDATE ai_agent_long_task_units SET status = 'pending', "
                    "max_attempts = max_attempts + 1, worker_id = NULL, "
                    "lease_expires_at_ms = NULL, error_code = ?, "
                    "update_time = CURRENT_TIMESTAMP WHERE task_id = ? "
                    "AND status IN ('claimed', 'running')",
                    [str(reason_code or "execution_recovery_after_restart")[:240], task_id],
                )
                await self._set_task_state_reason(
                    task,
                    code=reason_code,
                    scope="system",
                )
                await self._schedule_system_recovery(
                    task,
                    reason_code=reason_code,
                    now_ms=now_ms,
                )
                await self._db.execute(
                    "UPDATE ai_agent_long_tasks SET status = 'paused', "
                    "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND status = 'running'",
                    [task_id],
                )
                updated = await self._require(task_id)
                await append_long_task_event(
                    self._db,
                    task_id=updated.id,
                    event_type="recovery_after_restart",
                    reason_code=reason_code,
                    reason_scope="system",
                    source_key=f"{updated.id}:recovery-after-restart:{updated.revision}",
                    payload={
                        "automaticRecoveryAttempt": _metadata_non_negative_int(
                            updated.metadata.get("automaticRecoveryAttempt"),
                        ),
                        "autoResumeNotBeforeMs": _metadata_non_negative_int(
                            updated.metadata.get("autoResumeNotBeforeMs"),
                        ),
                    },
                )
            return task_ids

    async def pause(
        self,
        task_id: str,
        *,
        expected_revision: int | None = None,
        reason_code: str | None = None,
    ) -> LongTaskRecord:
        async with self._db.transaction(cancellation_linearizable=True):
            task = await self._require(task_id)
            if (
                expected_revision is not None
                and task.revision != int(expected_revision)
            ):
                raise ValueError("long task revision conflict")
            if task.cancellation_requested_at_ms is not None:
                return await self._cancel_in_transaction(task)
            if task.status is LongTaskStatus.PAUSED:
                return task
            if task.status not in {LongTaskStatus.PENDING, LongTaskStatus.RUNNING}:
                raise ValueError(
                    f"long task cannot transition from {task.status.value}"
                )
            pause_scope = (
                "user"
                if str(reason_code or "").startswith("user_")
                else "system"
            )
            await self._set_task_state_reason(
                task,
                code=reason_code or "manual_pause",
                scope=pause_scope,
            )
            if pause_scope == "system":
                await self._schedule_system_recovery(
                    task,
                    reason_code=reason_code or "manual_pause",
                    now_ms=int(time.time() * 1000),
                )
            await self._update_task_status(task, LongTaskStatus.PAUSED)
            # A pause is a checkpoint boundary, not a five-minute lease wait.
            # Any in-flight executor is signaled by the application composition;
            # releasing its unit here makes an immediate resume deterministic.
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'pending', "
                "max_attempts = max_attempts + 1, worker_id = NULL, "
                "lease_expires_at_ms = NULL, error_code = COALESCE(?, error_code), "
                "update_time = CURRENT_TIMESTAMP WHERE task_id = ? "
                "AND status IN ('claimed', 'running')",
                [str(reason_code or "").strip()[:240] or None, task.id],
            )
            updated = await self._require(task.id)
            await append_long_task_event(
                self._db,
                task_id=updated.id,
                event_type="task_paused",
                reason_code=reason_code or "manual_pause",
                reason_scope=pause_scope,
                source_key=f"{updated.id}:paused:{updated.revision}",
                payload={
                    "automaticRecoveryAttempt": _metadata_non_negative_int(
                        updated.metadata.get("automaticRecoveryAttempt"),
                    ),
                    "autoResumeNotBeforeMs": _metadata_non_negative_int(
                        updated.metadata.get("autoResumeNotBeforeMs"),
                    ),
                },
            )
            return updated

    async def resume(
        self,
        task_id: str,
        *,
        additional_attempts: int = 0,
        recovery_source: str = "user",
    ) -> LongTaskRecord:
        extra_attempts = int(additional_attempts)
        if extra_attempts < 0:
            raise ValueError("additional long task attempts cannot be negative")
        normalized_recovery_source = str(recovery_source or "").strip()
        if normalized_recovery_source not in {"user", "automatic"}:
            raise ValueError("long task recovery source is invalid")
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
                await self._clear_task_state_reason(task)
                updated = await self._require(task.id)
                await append_long_task_event(
                    self._db,
                    task_id=updated.id,
                    event_type="task_resumed",
                    source_key=f"{updated.id}:resumed:{updated.revision}",
                    payload={
                        "additionalAttempts": extra_attempts,
                        "recoverySource": normalized_recovery_source,
                    },
                )
                return updated
            if task.status is not LongTaskStatus.PAUSED:
                raise ValueError(f"long task cannot transition from {task.status.value}")
            if extra_attempts:
                raise ValueError("paused long task does not accept retry attempts")
            # A blocked transient Unit exhausted only its automatic attempts.
            # A user-requested resume grants it one new attempt without
            # invalidating completed sibling outputs.
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'pending', "
                "max_attempts = max_attempts + 1, worker_id = NULL, "
                "lease_expires_at_ms = NULL, metadata_json = json_remove(metadata_json, "
                "'$.retryNotBeforeMs', '$.retryBackoffMs', '$.retryWaitSpentMs', "
                "'$.retryWaitBudgetExceeded'), update_time = CURRENT_TIMESTAMP "
                "WHERE task_id = ? AND status = 'blocked'",
                [task.id],
            )
            await self._clear_task_state_reason(task)
            await self._update_task_status(task, LongTaskStatus.RUNNING)
            updated = await self._require(task.id)
            await append_long_task_event(
                self._db,
                task_id=updated.id,
                event_type="task_resumed",
                source_key=f"{updated.id}:resumed:{updated.revision}",
                payload={
                    "additionalAttempts": 0,
                    "recoverySource": normalized_recovery_source,
                },
            )
            return updated

    async def cancel(self, task_id: str) -> LongTaskRecord:
        async with self._mutation_transaction():
            task = await self._require(task_id)
            if task.status is LongTaskStatus.CANCELED:
                return task
            if task.status.terminal:
                raise ValueError("terminal long task cannot be canceled")
            return await self._cancel_in_transaction(task)

    async def fail(
        self,
        task_id: str,
        *,
        decision: FailureDecision,
    ) -> LongTaskRecord:
        """Close nonterminal work after a systemic permanent host failure."""

        if not isinstance(decision, FailureDecision):
            raise TypeError("long task failure requires a FailureDecision")
        if (
            decision.disposition is not FailureDisposition.FAIL_PERMANENT
            or decision.scope is not FailureScope.SYSTEMIC
        ):
            raise ValueError(
                "long task host failure must be systemic and permanent"
            )
        async with self._mutation_transaction():
            task = await self._require(task_id)
            if task.status is LongTaskStatus.FAILED:
                return task
            if task.status.terminal:
                raise ValueError("terminal long task cannot be failed")
            active = await self._db.fetch_one(
                "SELECT COUNT(*) AS count FROM ai_agent_long_task_units "
                "WHERE task_id = ? AND required = 1 "
                "AND status IN ('claimed', 'running')",
                [task.id],
            )
            if int((active or {}).get("count") or 0) == 0:
                candidate = await self._db.fetch_one(
                    "SELECT unit_id FROM ai_agent_long_task_units "
                    "WHERE task_id = ? AND required = 1 AND status NOT IN "
                    "('completed', 'expanded', 'failed', 'canceled') "
                    "ORDER BY position ASC LIMIT 1",
                    [task.id],
                )
                if candidate is not None:
                    await self._db.execute(
                        "UPDATE ai_agent_long_task_units SET status = 'failed', "
                        "worker_id = NULL, lease_expires_at_ms = NULL, "
                        "error_code = ?, failure_json = ?, disposition = ?, "
                        "update_time = CURRENT_TIMESTAMP WHERE task_id = ? "
                        "AND unit_id = ?",
                        [
                            decision.code[:240],
                            _json_dump(_failure_payload(decision)),
                            decision.disposition.value,
                            task.id,
                            str(candidate["unit_id"]),
                        ],
                    )
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'failed', "
                "worker_id = NULL, lease_expires_at_ms = NULL, "
                "error_code = ?, failure_json = ?, disposition = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE task_id = ? "
                "AND status IN ('claimed', 'running')",
                [
                    decision.code[:240],
                    _json_dump(_failure_payload(decision)),
                    decision.disposition.value,
                    task.id,
                ],
            )
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'canceled', "
                "worker_id = NULL, lease_expires_at_ms = NULL, "
                "error_code = COALESCE(error_code, "
                "'task_failed_dependency'), update_time = CURRENT_TIMESTAMP "
                "WHERE task_id = ? AND status IN ('pending', 'waiting_retry', "
                "'needs_split', 'blocked')",
                [task.id],
            )
            await self._refresh_task_totals(task.id)
            await self._update_task_status(
                await self._require(task.id),
                LongTaskStatus.FAILED,
            )
            return await self._require(task.id)

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
                # A retryable Unit is a durable checkpoint.  Keep its failure
                # record and completed siblings for an explicit resume rather
                # than turning a transient outage into a failed task.
                await self._update_task_status(task, LongTaskStatus.PAUSED)
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

    async def _require_active_unit(
        self,
        task: LongTaskRecord,
        unit: LongTaskUnitRecord,
        *,
        worker_id: str,
        lease_epoch: int,
    ) -> None:
        if _deadline_elapsed(task):
            raise ContractViolationError(
                "long task deadline was exceeded",
                code="long_task_deadline_exceeded",
                details={"taskId": task.id},
            )
        worker = _required(worker_id, "long task worker id")
        if (
            task.status is not LongTaskStatus.RUNNING
            or unit.status not in {
                LongTaskUnitStatus.CLAIMED,
                LongTaskUnitStatus.RUNNING,
            }
            or unit.worker_id != worker
            or unit.lease_epoch != int(lease_epoch)
            or unit.lease_expires_at_ms is None
            or unit.lease_expires_at_ms <= int(time.time() * 1000)
        ):
            self._raise_lease_lost(unit)

    @staticmethod
    def _raise_lease_lost(unit: LongTaskUnitRecord) -> None:
        raise ContractViolationError(
            "long task unit lease authority was lost",
            code="long_task_unit_lease_lost",
            details={"taskId": unit.task_id, "unitId": unit.id},
        )

    async def _expire_deadline_in_transaction(
        self,
        task: LongTaskRecord,
    ) -> LongTaskRecord:
        await self._db.execute(
            "UPDATE ai_agent_long_task_units SET status = 'failed', "
            "worker_id = NULL, lease_expires_at_ms = NULL, "
            "error_code = 'long_task_deadline_exceeded', "
            "update_time = CURRENT_TIMESTAMP WHERE task_id = ? "
            "AND status NOT IN ('completed', 'expanded', 'failed', 'canceled')",
            [task.id],
        )
        await self._refresh_task_totals(task.id)
        current = await self._require(task.id)
        if not current.status.terminal:
            await self._update_task_status(current, LongTaskStatus.FAILED)
        return await self._require(task.id)

    async def _fail_budget(
        self,
        task: LongTaskRecord,
        budget_kind: str,
    ) -> LongTaskRecord:
        if (
            task.budget_exhaustion_disposition
            is BudgetExhaustionDisposition.PAUSE_RECOVERABLE
        ):
            # A task profile may opt into an explicit budget-recovery path.
            # Preserve successful Unit artifacts; leave the rest blocked until
            # a new user-authorized Root grants its next bounded allocation.
            failure = _json_dump({
                "category": "runtime_budget",
                "code": "runtime_budget_exceeded",
                "scope": "budget",
                "budgetKind": budget_kind,
            })
            await self._db.execute(
                "UPDATE ai_agent_long_task_units SET status = 'blocked', "
                "worker_id = NULL, lease_expires_at_ms = NULL, "
                "error_code = 'runtime_budget_exceeded', failure_json = ?, "
                "disposition = 'pause_recoverable', "
                "metadata_json = json_set(COALESCE(metadata_json, '{}'), "
                "'$.budgetKind', ?), update_time = CURRENT_TIMESTAMP "
                "WHERE task_id = ? AND status NOT IN "
                "('completed', 'expanded', 'failed', 'canceled')",
                [failure, budget_kind, task.id],
            )
            await self._refresh_task_totals(task.id)
            current = await self._require(task.id)
            if (
                not current.status.terminal
                and current.status is not LongTaskStatus.PAUSED
            ):
                await self._set_task_state_reason(
                    current,
                    code="runtime_budget_exceeded",
                    scope="budget",
                )
                await self._update_task_status(current, LongTaskStatus.PAUSED)
            updated = await self._require(task.id)
            if (
                updated.status is LongTaskStatus.PAUSED
                and updated.revision != current.revision
            ):
                await append_long_task_event(
                    self._db,
                    task_id=updated.id,
                    event_type="budget_paused",
                    reason_code="runtime_budget_exceeded",
                    reason_scope="budget",
                    source_key=f"{updated.id}:budget-paused:{updated.revision}",
                    payload={"budgetKind": str(budget_kind)[:80]},
                )
            return updated
        await self._db.execute(
            "UPDATE ai_agent_long_task_units SET status = 'failed', "
            "worker_id = NULL, lease_expires_at_ms = NULL, "
            "error_code = 'runtime_budget_exceeded', "
            "metadata_json = json_set(COALESCE(metadata_json, '{}'), "
            "'$.budgetKind', ?), update_time = CURRENT_TIMESTAMP "
            "WHERE task_id = ? AND status NOT IN "
            "('completed', 'expanded', 'failed', 'canceled')",
            [budget_kind, task.id],
        )
        await self._refresh_task_totals(task.id)
        current = await self._require(task.id)
        if not current.status.terminal:
            await self._update_task_status(current, LongTaskStatus.FAILED)
        return await self._require(task.id)

    async def _update_task_status(self, task, target):
        await self._db.execute(
            "UPDATE ai_agent_long_tasks SET status = ?, revision = revision + 1, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [target.value, task.id],
        )
        if target not in {
            LongTaskStatus.COMPLETED,
            LongTaskStatus.FAILED,
            LongTaskStatus.CANCELED,
        }:
            return
        updated = await self._require(task.id)
        await append_long_task_event(
            self._db,
            task_id=updated.id,
            event_type=f"task_{target.value}",
            reason_code=(
                "task_canceled"
                if target is LongTaskStatus.CANCELED
                else "task_terminal"
            ),
            reason_scope=("user" if target is LongTaskStatus.CANCELED else "unknown"),
            source_key=f"{updated.id}:terminal:{target.value}:{updated.revision}",
            payload={"previousStatus": task.status.value},
        )

    async def _set_task_state_reason(self, task, *, code: str, scope: str) -> None:
        normalized_code = str(code or "").strip()[:240]
        normalized_scope = str(scope or "").strip()
        if normalized_scope not in {"system", "user", "budget", "unknown"}:
            raise ValueError("long task state reason scope is invalid")
        await self._db.execute(
            "UPDATE ai_agent_long_tasks SET state_reason_code = ?, "
            "state_reason_scope = ?, update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [normalized_code or None, normalized_scope, task.id],
        )

    async def _clear_task_state_reason(self, task) -> None:
        await self._db.execute(
            "UPDATE ai_agent_long_tasks SET state_reason_code = NULL, "
            "state_reason_scope = NULL, metadata_json = json_remove(metadata_json, "
            "'$.autoResumeNotBeforeMs', '$.automaticRecoveryAttempt', "
            "'$.automaticRecoveryWaitSpentMs', '$.autoRecoveryBudgetExceeded', "
            "'$.autoRecoveryReasonCode'), "
            "update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [task.id],
        )

    async def _schedule_system_recovery(
        self,
        task: LongTaskRecord,
        *,
        reason_code: str,
        now_ms: int,
    ) -> None:
        """Persist bounded task-level recovery timing before pausing its Root."""

        metadata = dict(thaw_json_mapping(task.metadata))
        attempt = _metadata_non_negative_int(metadata.get("automaticRecoveryAttempt")) + 1
        spent_ms = _metadata_non_negative_int(
            metadata.get("automaticRecoveryWaitSpentMs"),
        )
        due_ms = _retry_not_before_ms(
            task.id,
            "system-recovery",
            attempt=attempt,
            now_ms=now_ms,
        )
        delay_ms = due_ms - now_ms
        if spent_ms + delay_ms > _retry_wait_budget_ms(task):
            metadata.pop("autoResumeNotBeforeMs", None)
            metadata["automaticRecoveryAttempt"] = attempt
            metadata["automaticRecoveryWaitSpentMs"] = spent_ms
            metadata["autoRecoveryBudgetExceeded"] = True
        else:
            metadata["autoResumeNotBeforeMs"] = due_ms
            metadata["automaticRecoveryAttempt"] = attempt
            metadata["automaticRecoveryWaitSpentMs"] = spent_ms + delay_ms
            metadata["autoRecoveryReasonCode"] = str(reason_code or "")[:240]
            metadata.pop("autoRecoveryBudgetExceeded", None)
        await self._db.execute(
            "UPDATE ai_agent_long_tasks SET metadata_json = ?, "
            "update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [_json_dump(metadata), task.id],
        )
        await append_long_task_event(
            self._db,
            task_id=task.id,
            event_type="recovery_scheduled",
            reason_code=reason_code,
            reason_scope="system",
            source_key=f"{task.id}:recovery-scheduled:{attempt}",
            payload={
                "automaticRecoveryAttempt": attempt,
                "autoResumeNotBeforeMs": _metadata_non_negative_int(
                    metadata.get("autoResumeNotBeforeMs"),
                ),
                "automaticRecoveryWaitSpentMs": _metadata_non_negative_int(
                    metadata.get("automaticRecoveryWaitSpentMs"),
                ),
                "autoRecoveryBudgetExceeded": bool(
                    metadata.get("autoRecoveryBudgetExceeded"),
                ),
            },
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

def _task(row: dict[str, Any] | None) -> LongTaskRecord:
    if row is None:
        raise LookupError("long task does not exist")
    return LongTaskRecord(
        id=str(row["id"]),
        namespace=str(row["namespace"]),
        kind=str(row["kind"]),
        owner_id=str(row["owner_id"]),
        created_by_run_id=str(row["created_by_run_id"]),
        status=str(row["status"]),
        revision=int(row["revision"]),
        total_units=int(row["total_units"]),
        completed_units=int(row["completed_units"]),
        failed_units=int(row["failed_units"]),
        max_parallelism=int(row["max_parallelism"]),
        deadline_at_ms=row.get("deadline_at_ms"),
        budget_limits=_budget_limits(
            _json_load(row.get("budget_limits_json"), {})
        ),
        budget_exhaustion_disposition=BudgetExhaustionDisposition(
            _json_load(row.get("metadata_json"), {}).get(
                "budgetExhaustionDisposition",
                BudgetExhaustionDisposition.PAUSE_RECOVERABLE.value,
            )
        ),
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
        lease_epoch=int(row.get("lease_epoch") or 0),
        lease_expires_at_ms=row.get("lease_expires_at_ms"),
        settled_by_worker_id=row.get("settled_by_worker_id"),
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
        unreported_usage_attempts=int(
            value.get("unreportedUsageAttempts") or 0
        ),
        input_tokens=int(value.get("inputTokens") or 0),
        generation_tokens=int(value.get("generationTokens") or 0),
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
        unreported_usage_attempts=int(
            row.get("unreported_usage_attempts") or 0
        ),
        input_tokens=int(row.get("input_tokens") or 0),
        generation_tokens=int(row.get("output_tokens") or 0),
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
        unreported_usage_attempts=int(
            value.get("unreported_usage_attempts") or 0
        ),
        input_tokens=int(value.get("input_tokens") or 0),
        generation_tokens=int(value.get("output_tokens") or 0),
        reasoning_tokens=(
            None
            if int(value.get("unknown_reasoning") or 0) > 0
            else int(value.get("reasoning_tokens") or 0)
        ),
    )


def _budget_limits(value: Mapping[str, Any]) -> LongTaskBudgetLimits:
    fields = {
        "maxInvocationAttempts": "max_invocation_attempts",
        "maxInputTokens": "max_input_tokens",
        "maxRunGenerationTokens": "max_run_generation_tokens",
        "maxReasoningTokens": "max_reasoning_tokens",
    }
    if set(value) - fields.keys():
        raise ContractViolationError(
            "Unknown long task budget fields",
            code="runtime_limits_invalid",
        )
    try:
        return LongTaskBudgetLimits(**{fields[key]: limit for key, limit in value.items()})
    except (TypeError, ValueError) as error:
        raise ContractViolationError(
            "Invalid long task budget limits", code="runtime_limits_invalid",
        ) from error


def _deadline_elapsed(
    task: LongTaskRecord,
    *,
    now_ms: int | None = None,
) -> bool:
    return task.deadline_at_ms is not None and task.deadline_at_ms <= (
        int(time.time() * 1000) if now_ms is None else int(now_ms)
    )


def _task_budget_exhaustion(
    task: LongTaskRecord,
    *,
    exceeded_only: bool = False,
) -> str | None:
    usage = task.usage
    limits = task.budget_limits
    if usage.unreported_usage_attempts and any(
        limit is not None
        for limit in (
            limits.max_input_tokens,
            limits.max_run_generation_tokens,
            limits.max_reasoning_tokens,
        )
    ):
        return "provider_usage_unreported"
    if limits.max_reasoning_tokens is not None and usage.reasoning_tokens is None:
        return "reasoning_tokens_unreported"
    for kind, value, limit in (
        ("model_attempts", usage.invocation_count, limits.max_invocation_attempts),
        ("input_tokens", usage.input_tokens, limits.max_input_tokens),
        (
            "generation_tokens",
            usage.generation_tokens,
            limits.max_run_generation_tokens,
        ),
        ("reasoning_tokens", usage.reasoning_tokens, limits.max_reasoning_tokens),
    ):
        if limit is not None and value is not None and (
            value > limit or (not exceeded_only and value >= limit)
        ):
            return kind
    return None


def _metadata_session_id(value: object) -> SessionId | None:
    metadata = thaw_json_mapping(value)
    raw = metadata.get("sessionId")
    if raw is None:
        return None
    session_id = str(raw).strip()
    return session_id or None


def _matches_create(task, units, command: LongTaskCreateCommand) -> bool:
    requested_metadata = {
        **thaw_json_mapping(command.metadata),
        "budgetExhaustionDisposition": command.budget_exhaustion_disposition.value,
    }
    if (
        task.namespace != command.namespace
        or task.kind != command.kind
        or task.owner_id != command.owner_id
        or task.created_by_run_id != command.created_by_run_id
        or task.max_parallelism != command.max_parallelism
        or task.deadline_at_ms != command.deadline_at_ms
        or task.budget_limits != command.budget_limits
        or task.budget_exhaustion_disposition
        != command.budget_exhaustion_disposition
        or thaw_json_mapping(task.metadata) != requested_metadata
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


def _retry_not_before_ms(
    task_id: str,
    unit_id: str,
    *,
    attempt: int,
    now_ms: int,
) -> int:
    """Return a durable exponential retry deadline with stable jitter.

    The jitter is derived from the task/unit/attempt identity rather than a
    process-local random source, so a restart neither loses the schedule nor
    turns a batch of retries into a synchronized provider burst.
    """

    exponent = min(max(0, int(attempt) - 1), 6)
    base_ms = min(60_000, 1_000 * (2 ** exponent))
    digest = blake2s(
        f"{task_id}:{unit_id}:{attempt}".encode("utf-8"),
        digest_size=4,
    ).digest()
    jitter_ms = int.from_bytes(digest, "big") % (max(1, base_ms // 4) + 1)
    return int(now_ms) + base_ms + jitter_ms


def _retry_wait_budget_ms(task: LongTaskRecord) -> int:
    policy = thaw_json_mapping(task.metadata).get("retryPolicy")
    if isinstance(policy, Mapping):
        configured = _metadata_non_negative_int(policy.get("maxAutomaticWaitMs"))
        if configured > 0:
            return min(configured, 900_000)
    return 120_000


def _metadata_non_negative_int(value: object) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, numeric)


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


__all__ = ["LongTaskClaimGuard", "SqliteLongTaskRepository"]
