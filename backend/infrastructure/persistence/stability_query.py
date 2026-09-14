"""Bounded, content-free evidence queries for Agent Run stability trends."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
import json
import time
from typing import Any

from application.provider_capacity_policy import (
    SETTING_KEY as PROVIDER_CAPACITY_POLICY_SETTING_KEY,
    configured_capacity_limit,
)
from application.run_provenance import digest_model_endpoint
from infrastructure.persistence.provider_health_repository import ProviderHealthScope
from purra.observability import TRACE_EVENT_TYPE
from purra.events import CoreEventType


_TREND_STATUSES = ("done", "blocked", "failed")


class SqliteStabilityEvidenceGateway:
    def __init__(self, db):
        self._db = db

    async def get_reference_scope(
        self,
        run_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT r.id AS run_id, r.session_id, s.book_id, "
            "s.screenplay_project_id "
            "FROM ai_agent_runs AS r "
            "LEFT JOIN ai_sessions AS s ON s.id = r.session_id "
            "WHERE r.id = ?",
            [str(run_id or "").strip()],
        )
        if row is None:
            return None
        return {
            "runId": str(row["run_id"]),
            "sessionId": row.get("session_id"),
            "bookId": row.get("book_id"),
            "screenplayProjectId": row.get("screenplay_project_id"),
        }

    async def list_recent(
        self,
        *,
        session_id: int | None = None,
        book_id: str | None = None,
        screenplay_project_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return await list_recent_run_stability_evidence(
            self._db,
            session_id=session_id,
            book_id=book_id,
            screenplay_project_id=screenplay_project_id,
            limit=limit,
        )

    async def get_novel_analysis_reliability(
        self,
        *,
        session_id: int | None = None,
        book_id: str | None = None,
        screenplay_project_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return await get_novel_analysis_reliability_snapshot(
            self._db,
            session_id=session_id,
            book_id=book_id,
            screenplay_project_id=screenplay_project_id,
            limit=limit,
        )

    async def list_novel_analysis_reliability_baselines(
        self,
        *,
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        return await list_novel_analysis_reliability_baselines(
            self._db,
            limit=limit,
        )


async def list_recent_run_stability_evidence(
    db,
    *,
    session_id: int | None = None,
    book_id: str | None = None,
    screenplay_project_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return recent root Runs plus a strict diagnostic event projection."""

    normalized_limit = int(limit)
    if normalized_limit < 1 or normalized_limit > 100:
        raise ValueError("stability trend limit must be between 1 and 100")
    scopes = [
        session_id is not None,
        bool(str(book_id or "").strip()),
        bool(str(screenplay_project_id or "").strip()),
    ]
    if sum(scopes) > 1:
        raise ValueError("only one stability trend scope may be selected")

    status_placeholders = ", ".join("?" for _ in _TREND_STATUSES)
    clauses = [
        f"r.status IN ({status_placeholders})",
    ]
    params: list[Any] = list(_TREND_STATUSES)
    if session_id is not None:
        normalized_session_id = int(session_id)
        if normalized_session_id < 1:
            raise ValueError("session id must be positive")
        clauses.append("r.session_id = ?")
        params.append(normalized_session_id)
    elif str(book_id or "").strip():
        clauses.append("s.book_id = ?")
        params.append(str(book_id).strip())
    elif str(screenplay_project_id or "").strip():
        clauses.append("s.screenplay_project_id = ?")
        params.append(str(screenplay_project_id).strip())
    params.append(normalized_limit)
    rows = await db.fetch_all(
        "SELECT r.id AS run_id, r.session_id, r.status AS run_status, "
        "r.model_provider, r.model_name, r.create_time, r.update_time "
        "FROM ai_agent_runs AS r "
        "LEFT JOIN ai_sessions AS s ON s.id = r.session_id "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY r.update_time DESC, r.rowid DESC LIMIT ?",
        params,
    )
    if not rows:
        return []

    run_ids = [str(row["run_id"]) for row in rows]
    events = await _read_projected_events(db, run_ids)
    return [
        {
            "runId": str(row["run_id"]),
            "sessionId": row.get("session_id"),
            "runStatus": str(row.get("run_status") or "unknown"),
            "modelProvider": row.get("model_provider"),
            "modelName": row.get("model_name"),
            "createTime": row.get("create_time"),
            "updateTime": row.get("update_time"),
            "events": events.get(str(row["run_id"]), []),
        }
        for row in rows
    ]


async def get_novel_analysis_reliability_snapshot(
    db,
    *,
    session_id: int | None = None,
    book_id: str | None = None,
    screenplay_project_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Aggregate content-free task and Provider facts for one bounded scope.

    Provider health is intentionally shared by endpoint across tasks.  The
    returned Provider portion therefore means "health history for identities
    used by these tasks", not a causal attribution of every Provider event to
    a particular novel-analysis task.
    """

    normalized_limit = int(limit)
    if normalized_limit < 1 or normalized_limit > 100:
        raise ValueError("novel analysis reliability limit must be between 1 and 100")
    scopes = [
        session_id is not None,
        bool(str(book_id or "").strip()),
        bool(str(screenplay_project_id or "").strip()),
    ]
    if sum(scopes) > 1:
        raise ValueError("only one novel analysis reliability scope may be selected")
    clauses = ["t.namespace = ?"]
    params: list[Any] = ["purrtypos.novel_analysis"]
    if session_id is not None:
        normalized_session_id = int(session_id)
        if normalized_session_id < 1:
            raise ValueError("session id must be positive")
        clauses.append("r.session_id = ?")
        params.append(normalized_session_id)
    elif str(book_id or "").strip():
        clauses.append("s.book_id = ?")
        params.append(str(book_id).strip())
    elif str(screenplay_project_id or "").strip():
        clauses.append("s.screenplay_project_id = ?")
        params.append(str(screenplay_project_id).strip())
    params.append(normalized_limit)
    safe_metadata = (
        "CASE WHEN json_valid(t.metadata_json) THEN t.metadata_json ELSE '{}' END"
    )
    tasks = await db.fetch_all(
        "SELECT t.id, t.status, t.total_units, t.completed_units, t.failed_units, "
        f"json_extract({safe_metadata}, '$.runtimeBinding.provider') AS provider, "
        f"json_extract({safe_metadata}, '$.runtimeBinding.model') AS model, "
        f"json_extract({safe_metadata}, '$.runtimeBinding.endpoint') AS endpoint "
        "FROM ai_agent_long_tasks AS t "
        "JOIN ai_agent_runs AS r ON r.id = t.created_by_run_id "
        "LEFT JOIN ai_sessions AS s ON s.id = r.session_id "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY t.update_time DESC, t.id DESC LIMIT ?",
        params,
    )
    task_ids = [str(row["id"]) for row in tasks]
    status_counts = _count_rows(tasks, "status")
    unit_totals = {
        "total": sum(int(row.get("total_units") or 0) for row in tasks),
        "completed": sum(int(row.get("completed_units") or 0) for row in tasks),
        "failed": sum(int(row.get("failed_units") or 0) for row in tasks),
    }
    event_groups: list[dict[str, Any]] = []
    task_events: list[dict[str, Any]] = []
    stage_groups: list[dict[str, Any]] = []
    unit_attempts = {
        "eligibleUnits": 0,
        "claimAttempts": 0,
        "permanentlyFailedUnits": 0,
    }
    if task_ids:
        placeholders = ", ".join("?" for _ in task_ids)
        event_groups = await db.fetch_all(
            "SELECT event_type, reason_code, reason_scope, COUNT(*) AS count "
            "FROM ai_agent_long_task_events "
            f"WHERE task_id IN ({placeholders}) "
            "GROUP BY event_type, reason_code, reason_scope "
            "ORDER BY count DESC, event_type ASC, reason_code ASC",
            task_ids,
        )
        safe_task_payload = (
            "CASE WHEN json_valid(payload_json) THEN payload_json ELSE '{}' END"
        )
        task_events = await db.fetch_all(
            "SELECT task_id, id, event_type, reason_code, reason_scope, "
            "CAST(strftime('%s', create_time) AS INTEGER) * 1000 AS occurred_at_ms, "
            f"json_extract({safe_task_payload}, '$.recoverySource') AS recovery_source "
            "FROM ai_agent_long_task_events "
            f"WHERE task_id IN ({placeholders}) ORDER BY task_id ASC, id ASC",
            task_ids,
        )
        unit_attempt_row = await db.fetch_one(
            "SELECT COUNT(*) AS eligible_units, "
            "COALESCE(SUM(attempt), 0) AS claim_attempts, "
            "COALESCE(SUM(CASE WHEN disposition = 'fail_permanent' "
            "THEN 1 ELSE 0 END), 0) AS permanently_failed_units "
            "FROM ai_agent_long_task_units "
            f"WHERE task_id IN ({placeholders}) "
            "AND required = 1 AND status <> 'expanded'",
            task_ids,
        )
        if unit_attempt_row:
            unit_attempts = {
                "eligibleUnits": int(unit_attempt_row.get("eligible_units") or 0),
                "claimAttempts": int(unit_attempt_row.get("claim_attempts") or 0),
                "permanentlyFailedUnits": int(
                    unit_attempt_row.get("permanently_failed_units") or 0,
                ),
            }
        safe_payload = (
            "CASE WHEN json_valid(e.payload_json) THEN e.payload_json ELSE '{}' END"
        )
        stage_groups = await db.fetch_all(
            "SELECT COALESCE(json_extract(" + safe_payload + ", '$.stage'), "
            "'unknown') AS stage, COUNT(*) AS count "
            "FROM ai_agent_run_events AS e "
            "JOIN ai_agent_long_task_units AS u ON u.run_id = e.run_id "
            f"WHERE u.task_id IN ({placeholders}) AND e.event_type = ? "
            "GROUP BY stage ORDER BY count DESC, stage ASC",
            [*task_ids, TRACE_EVENT_TYPE],
        )
    event_type_counts = _count_rows(event_groups, "event_type", count_key="count")
    reason_code_counts = _count_rows(
        [row for row in event_groups if row.get("reason_code")],
        "reason_code",
        count_key="count",
    )
    provider_scopes = {
        ProviderHealthScope(
            provider=str(row.get("provider") or "").strip(),
            model=str(row.get("model") or "").strip(),
            endpoint_digest=digest_model_endpoint(str(row.get("endpoint") or "")),
        )
        for row in tasks
        if str(row.get("provider") or "").strip()
        and str(row.get("model") or "").strip()
    }
    provider_snapshot, provider_event_counts, capacity_policy_events = await _provider_reliability(
        db,
        provider_scopes,
    )
    task_count = len(tasks)
    eligible_units = unit_attempts["eligibleUnits"]
    claim_attempts = unit_attempts["claimAttempts"]
    permanently_failed_units = unit_attempts["permanentlyFailedUnits"]
    return {
        "sample": {
            "taskCount": len(tasks),
            "taskLimit": normalized_limit,
            "workflowStatuses": status_counts,
            "units": unit_totals,
        },
        "taskEvents": {
            "eventTypes": event_type_counts,
            "reasonCodes": reason_code_counts,
            "stages": _count_rows(stage_groups, "stage", count_key="count"),
            "recovery": {
                "scheduled": event_type_counts.get("recovery_scheduled", 0),
                "dispatched": event_type_counts.get(
                    "automatic_recovery_dispatched", 0,
                ),
                "bindingUnavailable": event_type_counts.get(
                    "automatic_recovery_binding_unavailable", 0,
                ),
                "dispatchRejected": event_type_counts.get(
                    "automatic_recovery_rejected", 0,
                ),
                "dispatchFailed": event_type_counts.get(
                    "automatic_recovery_dispatch_failed", 0,
                ),
                "outcomes": _automatic_recovery_outcomes(task_events),
            },
        },
        "outcomes": {
            "terminalTaskFailure": {
                "failedTasks": int(status_counts.get("failed", 0)),
                "taskCount": task_count,
                "rate": _rate(int(status_counts.get("failed", 0)), task_count),
            },
            "permanentUnitFailure": {
                "failedUnits": permanently_failed_units,
                "eligibleUnits": eligible_units,
                "rate": _rate(permanently_failed_units, eligible_units),
            },
            "retryAmplification": {
                "claimAttempts": claim_attempts,
                "eligibleUnits": eligible_units,
                "additionalClaims": max(0, claim_attempts - eligible_units),
                "factor": _rate(claim_attempts, eligible_units),
            },
            "pauses": _pause_duration_metrics(
                task_events,
                now_ms=int(time.time() * 1000),
            ),
        },
        "providers": {
            "scope": "shared_provider_identity",
            "current": provider_snapshot,
            "events": provider_event_counts,
            "capacityPolicyEvents": capacity_policy_events,
        },
    }


async def list_novel_analysis_reliability_baselines(
    db,
    *,
    limit: int = 24,
) -> list[dict[str, Any]]:
    """Read bounded global baseline snapshots without exposing task content."""

    normalized_limit = int(limit)
    if normalized_limit < 1 or normalized_limit > 48:
        raise ValueError("novel analysis reliability baseline limit must be between 1 and 48")
    rows = await db.fetch_all(
        "SELECT bucket_started_at_ms, window_limit, metrics_json "
        "FROM ai_novel_analysis_reliability_snapshots "
        "ORDER BY bucket_started_at_ms DESC LIMIT ?",
        [normalized_limit],
    )
    return [
        _baseline_projection(row)
        for row in rows
    ]


def _baseline_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    try:
        raw_metrics = json.loads(str(row.get("metrics_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        raw_metrics = {}
    metrics = raw_metrics if isinstance(raw_metrics, Mapping) else {}
    sample = metrics.get("sample")
    task_events = metrics.get("taskEvents")
    providers = metrics.get("providers")
    outcomes = metrics.get("outcomes")
    sample = sample if isinstance(sample, Mapping) else {}
    task_events = task_events if isinstance(task_events, Mapping) else {}
    providers = providers if isinstance(providers, Mapping) else {}
    outcomes = outcomes if isinstance(outcomes, Mapping) else {}
    recovery = task_events.get("recovery")
    recovery = recovery if isinstance(recovery, Mapping) else {}
    reason_codes = task_events.get("reasonCodes")
    reason_codes = reason_codes if isinstance(reason_codes, Mapping) else {}
    provider_rows = providers.get("current")
    capacity_policy_events = providers.get("capacityPolicyEvents")
    provider_rows = provider_rows if isinstance(provider_rows, list) else []
    capacity_policy_events = (
        capacity_policy_events if isinstance(capacity_policy_events, list) else []
    )
    return {
        "bucketStartedAtMs": int(row.get("bucket_started_at_ms") or 0),
        "windowLimit": int(row.get("window_limit") or 0),
        "sample": {
            "taskCount": int(sample.get("taskCount") or 0),
            "workflowStatuses": _mapping_copy(sample.get("workflowStatuses")),
            "units": _mapping_copy(sample.get("units")),
        },
        "recovery": {
            "scheduled": int(recovery.get("scheduled") or 0),
            "dispatched": int(recovery.get("dispatched") or 0),
            "outcomes": _mapping_copy(recovery.get("outcomes")),
        },
        "reasonCodes": _mapping_copy(reason_codes),
        "outcomes": {
            "terminalTaskFailure": _mapping_copy(
                outcomes.get("terminalTaskFailure"),
            ),
            "permanentUnitFailure": _mapping_copy(
                outcomes.get("permanentUnitFailure"),
            ),
            "retryAmplification": _mapping_copy(
                outcomes.get("retryAmplification"),
            ),
            "pauses": _mapping_copy(outcomes.get("pauses")),
        },
        "providerStates": [
            {
                "provider": item.get("provider"),
                "model": item.get("model"),
                "state": item.get("state"),
                "failureCount": int(item.get("failureCount") or 0),
                "lastFailureCode": item.get("lastFailureCode"),
                "rampUntilMs": item.get("rampUntilMs"),
                "configuredCapacity": (
                    int(item["configuredCapacity"])
                    if isinstance(item.get("configuredCapacity"), int)
                    else None
                ),
            }
            for item in provider_rows
            if isinstance(item, Mapping)
        ],
        "capacityPolicyEvents": [
            {
                "provider": item.get("provider"),
                "eventType": item.get("eventType"),
                "count": int(item.get("count") or 0),
            }
            for item in capacity_policy_events
            if isinstance(item, Mapping)
        ],
    }


def _mapping_copy(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rate(numerator: int, denominator: int) -> float | None:
    """Return an explicit undefined value for an empty observation window."""

    if denominator < 1:
        return None
    return numerator / denominator


def _pause_duration_metrics(
    events: list[dict[str, Any]],
    *,
    now_ms: int,
) -> dict[str, int | float]:
    """Measure observable workflow pauses without inventing unavailable history.

    Events are append-only, but older databases have second-resolution timestamps.
    Durations consequently represent lower-precision operational evidence, not an
    SLA clock.  A repeated pause while already paused leaves the original start
    intact, so duplicate state evidence cannot shorten the observed pause.
    """

    pause_starts = {"task_paused", "budget_paused", "recovery_after_restart"}
    pause_ends = {
        "task_resumed",
        "task_completed",
        "task_failed",
        "task_canceled",
    }
    active_by_task: dict[str, int] = {}
    closed_durations: list[int] = []
    for event in events:
        task_id = str(event.get("task_id") or "")
        event_type = str(event.get("event_type") or "")
        occurred_at_ms = int(event.get("occurred_at_ms") or 0)
        if not task_id or occurred_at_ms < 1:
            continue
        if event_type in pause_starts:
            active_by_task.setdefault(task_id, occurred_at_ms)
            continue
        if event_type not in pause_ends:
            continue
        pause_started_at_ms = active_by_task.pop(task_id, None)
        if pause_started_at_ms is not None:
            closed_durations.append(max(0, occurred_at_ms - pause_started_at_ms))
    open_durations = [
        max(0, now_ms - pause_started_at_ms)
        for pause_started_at_ms in active_by_task.values()
    ]
    closed_total_ms = sum(closed_durations)
    return {
        "closedCount": len(closed_durations),
        "closedTotalMs": closed_total_ms,
        "closedAverageMs": (
            closed_total_ms / len(closed_durations) if closed_durations else 0.0
        ),
        "openCount": len(open_durations),
        "openTotalMs": sum(open_durations),
    }


async def _provider_reliability(
    db,
    scopes: set[ProviderHealthScope],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not scopes:
        return [], [], []
    keys = sorted(scope.key for scope in scopes)
    placeholders = ", ".join("?" for _ in keys)
    capacity_policy = await _read_provider_capacity_policy(db)
    current_rows = await db.fetch_all(
        "SELECT provider, model, endpoint_digest, state, failure_count, last_failure_code, open_until_ms, "
        "ramp_until_ms "
        f"FROM ai_provider_health WHERE scope_key IN ({placeholders}) "
        "ORDER BY provider ASC, model ASC",
        keys,
    )
    event_rows = await db.fetch_all(
        "SELECT h.provider, h.model, e.event_type, e.reason_code, "
        "COUNT(*) AS count FROM ai_provider_health_events AS e "
        "JOIN ai_provider_health AS h ON h.scope_key = e.scope_key "
        f"WHERE e.scope_key IN ({placeholders}) "
        "GROUP BY h.provider, h.model, e.event_type, e.reason_code "
        "ORDER BY count DESC, h.provider ASC, h.model ASC, e.event_type ASC",
        keys,
    )
    capacity_conditions = " OR ".join(
        "(provider = ? AND endpoint_digest = ?)" for _ in scopes
    )
    capacity_params = [
        value
        for scope in sorted(scopes, key=lambda item: (item.provider, item.endpoint_digest))
        for value in (scope.provider, scope.endpoint_digest)
    ]
    capacity_event_rows = await db.fetch_all(
        "SELECT provider, event_type, COUNT(*) AS count "
        "FROM ai_provider_capacity_policy_events "
        f"WHERE {capacity_conditions} "
        "GROUP BY provider, event_type ORDER BY count DESC, provider ASC, event_type ASC",
        capacity_params,
    )
    return [
        {
            "provider": row.get("provider"),
            "model": row.get("model"),
            "state": row.get("state"),
            "failureCount": int(row.get("failure_count") or 0),
            "lastFailureCode": row.get("last_failure_code"),
            "openUntilMs": row.get("open_until_ms"),
            "rampUntilMs": row.get("ramp_until_ms"),
            "configuredCapacity": configured_capacity_limit(
                capacity_policy,
                provider=str(row.get("provider") or ""),
                endpoint_digest=str(row.get("endpoint_digest") or ""),
            ),
        }
        for row in current_rows
    ], [
        {
            "provider": row.get("provider"),
            "model": row.get("model"),
            "eventType": row.get("event_type"),
            "reasonCode": row.get("reason_code"),
            "count": int(row.get("count") or 0),
        }
        for row in event_rows
    ], [
        {
            "provider": row.get("provider"),
            "eventType": row.get("event_type"),
            "count": int(row.get("count") or 0),
        }
        for row in capacity_event_rows
    ]


async def _read_provider_capacity_policy(db) -> object:
    row = await db.fetch_one(
        "SELECT value FROM settings WHERE key = ?",
        [PROVIDER_CAPACITY_POLICY_SETTING_KEY],
    )
    if not row:
        return None
    try:
        return json.loads(str(row.get("value") or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _count_rows(
    rows: list[dict[str, Any]],
    field: str,
    *,
    count_key: str | None = None,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(field) or "").strip()
        if not value:
            continue
        increment = int(row.get(count_key) or 0) if count_key else 1
        counts[value] = counts.get(value, 0) + increment
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _automatic_recovery_outcomes(
    events: list[dict[str, Any]],
) -> dict[str, int]:
    """Classify only observable outcomes following an automatic dispatch.

    A new manual resume ends the automatic attribution chain rather than being
    counted as an automatic success.  Events without a later terminal/pause
    transition remain pending; the dashboard must not turn them into success.
    """

    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_task[str(event["task_id"])].append(event)
    outcomes: dict[str, int] = {}
    for task_events in by_task.values():
        dispatched = False
        for event in task_events:
            event_type = str(event.get("event_type") or "")
            if event_type == "automatic_recovery_dispatched":
                if dispatched:
                    outcomes["pending"] = outcomes.get("pending", 0) + 1
                dispatched = True
                continue
            if not dispatched:
                continue
            if (
                event_type == "task_resumed"
                and str(event.get("recovery_source") or "") == "user"
            ):
                outcomes["supersededByUser"] = outcomes.get("supersededByUser", 0) + 1
                dispatched = False
                continue
            if event_type == "task_completed":
                outcome = "completed"
            elif event_type == "task_failed":
                outcome = "failed"
            elif event_type == "task_canceled":
                outcome = "canceled"
            elif event_type == "task_paused":
                outcome = (
                    "pausedByUser"
                    if str(event.get("reason_scope") or "") == "user"
                    else "pausedAgain"
                )
            else:
                continue
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            dispatched = False
        if dispatched:
            outcomes["pending"] = outcomes.get("pending", 0) + 1
    return dict(sorted(outcomes.items(), key=lambda item: (-item[1], item[0])))


async def _read_projected_events(
    db,
    run_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    placeholders = ", ".join("?" for _ in run_ids)
    safe_json = (
        "CASE WHEN json_valid(e.payload_json) "
        "THEN e.payload_json ELSE '{}' END"
    )
    safe_item_json = (
        "CASE WHEN json_valid(j.value) THEN j.value ELSE '{}' END"
    )
    projected: dict[str, list[tuple[int, int, dict[str, Any]]]] = defaultdict(list)

    trace_rows = await db.fetch_all(
        "SELECT e.run_id, e.id, "
        f"json_extract({safe_json}, '$.stage') AS stage, "
        f"json_extract({safe_json}, '$.outcome') AS outcome, "
        f"json_extract({safe_json}, '$.details.compactedTurnCount') "
        "AS compacted_turn_count, "
        f"json_extract({safe_json}, '$.details.droppedMessages') "
        "AS dropped_messages, "
        f"json_extract({safe_json}, '$.details.providerAttemptTerminal') "
        "AS provider_attempt_terminal "
        "FROM ai_agent_run_events AS e "
        f"WHERE e.run_id IN ({placeholders}) AND e.event_type = ? "
        "ORDER BY e.id ASC",
        [*run_ids, TRACE_EVENT_TYPE],
    )
    for row in trace_rows:
        details: dict[str, Any] = {}
        if row.get("compacted_turn_count") is not None:
            details["compactedTurnCount"] = row["compacted_turn_count"]
        if row.get("dropped_messages") is not None:
            details["droppedMessages"] = row["dropped_messages"]
        if row.get("provider_attempt_terminal") is not None:
            details["providerAttemptTerminal"] = bool(
                row["provider_attempt_terminal"]
            )
        payload: dict[str, Any] = {
            "stage": str(row.get("stage") or ""),
            "outcome": str(row.get("outcome") or ""),
        }
        if details:
            payload["details"] = details
        _append_projected(projected, row, TRACE_EVENT_TYPE, payload)

    completed_rows = await db.fetch_all(
        "SELECT e.run_id, e.id, "
        f"json_extract({safe_json}, '$.toolCallId') AS tool_call_id, "
        f"json_extract({safe_json}, '$.tool_call_id') AS legacy_tool_call_id, "
        f"json_extract({safe_json}, '$.toolName') AS tool_name, "
        f"json_extract({safe_json}, '$.tool_name') AS legacy_tool_name, "
        f"json_extract({safe_json}, '$.outcome') AS outcome, "
        f"json_extract({safe_json}, '$.errorCode') AS error_code, "
        f"json_extract({safe_json}, '$.error_code') AS legacy_error_code "
        "FROM ai_agent_run_events AS e "
        f"WHERE e.run_id IN ({placeholders}) AND e.event_type = ? "
        "ORDER BY e.id ASC",
        [*run_ids, CoreEventType.TOOL_CALL_COMPLETED.value],
    )
    for row in completed_rows:
        _append_projected(
            projected,
            row,
            CoreEventType.TOOL_CALL_COMPLETED.value,
            {
                "toolCallId": row.get("tool_call_id")
                or row.get("legacy_tool_call_id"),
                "toolName": row.get("tool_name") or row.get("legacy_tool_name"),
                "outcome": row.get("outcome"),
                "errorCode": row.get("error_code")
                or row.get("legacy_error_code"),
            },
        )

    started_rows = await db.fetch_all(
        "SELECT e.run_id, e.id, CAST(j.key AS INTEGER) AS item_index, "
        f"json_extract({safe_item_json}, '$.id') AS tool_call_id, "
        f"json_extract({safe_item_json}, '$.name') AS tool_name "
        "FROM ai_agent_run_events AS e "
        f"CROSS JOIN json_each({safe_json}, '$.calls') AS j "
        f"WHERE e.run_id IN ({placeholders}) AND e.event_type = ? "
        "ORDER BY e.id ASC, item_index ASC",
        [*run_ids, CoreEventType.TOOL_CALLS_STARTED.value],
    )
    for row in started_rows:
        _append_projected(
            projected,
            row,
            CoreEventType.TOOL_CALLS_STARTED.value,
            {"calls": [{
                "id": row.get("tool_call_id"),
                "name": row.get("tool_name"),
            }]},
            suborder=int(row.get("item_index") or 0),
        )

    result_rows = await db.fetch_all(
        "SELECT e.run_id, e.id, CAST(j.key AS INTEGER) AS item_index, "
        f"json_extract({safe_item_json}, '$.tool_call_id') AS tool_call_id, "
        f"json_extract({safe_item_json}, '$.tool_name') AS tool_name, "
        f"json_extract({safe_item_json}, '$.error') AS error_code "
        "FROM ai_agent_run_events AS e "
        f"CROSS JOIN json_each({safe_json}, '$.results') AS j "
        f"WHERE e.run_id IN ({placeholders}) AND e.event_type = ? "
        "ORDER BY e.id ASC, item_index ASC",
        [*run_ids, CoreEventType.TOOL_RESULTS.value],
    )
    for row in result_rows:
        _append_projected(
            projected,
            row,
            CoreEventType.TOOL_RESULTS.value,
            {"results": [{
                "tool_call_id": row.get("tool_call_id"),
                "tool_name": row.get("tool_name"),
                "error": row.get("error_code"),
            }]},
            suborder=int(row.get("item_index") or 0),
        )

    return {
        run_id: [
            event
            for _, _, event in sorted(
                items,
                key=lambda item: (item[0], item[1]),
            )
        ]
        for run_id, items in projected.items()
    }


def _append_projected(
    projected: dict[str, list[tuple[int, int, dict[str, Any]]]],
    row: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
    *,
    suborder: int = 0,
) -> None:
    projected[str(row["run_id"])].append((
        int(row.get("id") or 0),
        suborder,
        {"eventType": event_type, "payload": payload},
    ))


__all__ = [
    "SqliteStabilityEvidenceGateway",
    "get_novel_analysis_reliability_snapshot",
    "list_novel_analysis_reliability_baselines",
    "list_recent_run_stability_evidence",
]
