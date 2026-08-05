"""Bounded, content-free evidence queries for Agent Run stability trends."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from agent_core.evaluation import TRACE_EVENT_TYPE
from agent_core.events import CoreEventType


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
        "(r.parent_run_id IS NULL OR r.parent_run_id = '')",
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
    "list_recent_run_stability_evidence",
]
