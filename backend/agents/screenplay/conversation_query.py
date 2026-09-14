"""Read models for replacement Screenplay conversation transport."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agents.screenplay.conversation_projection import (
    SCREENPLAY_REPLACEMENT_ROOT_BINDING,
)
from application.agent_event_stream import projection_version
from application.sse_mapping import canonical_output_to_sse_chunk
from exceptions import NotFoundError


class ScreenplayReplacementConversationQuery:
    def __init__(self, db, *, output_repository) -> None:
        self._db = db
        self._output = output_repository

    async def snapshot(self, *, project_id: str, session_id: int) -> dict[str, Any]:
        await self._require_session(project_id, session_id)
        rows = await self._db.fetch_all(
            "SELECT t.*, t.planner_run_id AS root_run_id, "
            "o.status AS operation_status, "
            "o.revision AS operation_revision, "
            "COALESCE(o.long_task_id, (SELECT task.id "
            "FROM ai_agent_long_tasks AS task "
            "WHERE task.created_by_run_id = t.planner_run_id "
            "AND task.namespace = 'purrtypos.screenplay' "
            "ORDER BY task.create_time, task.id LIMIT 1)) AS long_task_id, "
            "o.target_role AS operation_target_role, "
            "o.result_revision_id AS operation_result_revision_id, "
            "o.finalization_receipt_id, o.cancel_receipt_id, "
            "o.cancel_requested_at_ms AS operation_cancel_requested_at_ms, "
            "o.usage_json AS operation_usage_json, "
            "o.error_json AS operation_error_json, "
            "o.create_time AS operation_create_time, "
            "o.update_time AS operation_update_time "
            "FROM screenplay_agent_turns AS t "
            "LEFT JOIN screenplay_agent_operations AS o ON o.turn_id = t.id "
            "WHERE t.project_id = ? AND t.session_id = ? ORDER BY t.rowid",
            [project_id, session_id],
        )
        tasks = []
        operations = []
        for row in rows:
            task = await self._task(row) if row.get("long_task_id") else None
            if task is not None:
                tasks.append(task)
            if row.get("operation_id"):
                operations.append(self._operation(row, task))
        cursor = await self._db.fetch_one(
            "SELECT COALESCE(MAX(e.id), 0) AS cursor "
            "FROM ai_agent_run_events AS e JOIN ai_agent_runs AS r "
            "ON r.id = e.run_id WHERE r.session_id = ? "
            "AND e.event_id IS NOT NULL AND e.visibility = 'public'",
            [session_id],
        )
        return {
            "projectId": project_id,
            "sessionId": session_id,
            "cursor": int((cursor or {}).get("cursor") or 0),
            "turns": [_turn(row) for row in rows],
            "tasks": tasks,
            "operations": operations,
        }

    async def list_chunks(
        self,
        *,
        project_id: str,
        session_id: int,
        after: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        await self._require_session(project_id, session_id)
        rows = await self._output.list_session_events(
            session_id=session_id,
            after_cursor=max(0, int(after)),
            limit=int(limit) + 1,
        )
        page = rows[:limit]
        turn_ids = tuple(dict.fromkeys(
            event.turn_id for _cursor, event in page if event.turn_id
        ))
        turns = await self._turns(turn_ids)
        chunks = []
        for cursor, event in page:
            if event.turn_id is None:
                continue
            chunk = canonical_output_to_sse_chunk(event)
            if chunk is None:
                continue
            turn = turns.get(str(event.turn_id), {})
            runtime = _object(turn.get("runtime_profile_json"))
            chunks.append({
                "cursor": int(cursor),
                "turnId": event.turn_id,
                "taskId": str(turn.get("task_id") or "") or None,
                "runId": event.run_id,
                "runRole": (
                    "root"
                    if turn.get("root_run_id") == event.run_id
                    else "related"
                ),
                "userContent": str(turn.get("user_content") or ""),
                "model": str(runtime.get("model") or "") or None,
                "turnCreatedAt": turn.get("create_time"),
                "chunk": chunk,
                "createdAt": event.emitted_at.isoformat(),
            })
        return {
            "chunks": chunks,
            "nextCursor": int(page[-1][0]) if page else max(0, int(after)),
            "hasMore": len(rows) > limit,
            "projectionVersion": await self.projection_version(session_id),
        }

    async def projection_version(self, session_id: int) -> str:
        rows = await self._db.fetch_all(
            "SELECT t.id, t.status, t.attempt, t.error_json, t.operation_id, "
            "t.task_id, t.result_revision_id, "
            "t.planner_run_id AS root_run_id, "
            "o.status AS operation_status, o.revision AS operation_revision, "
            "task.revision AS task_revision "
            "FROM screenplay_agent_turns AS t "
            "LEFT JOIN screenplay_agent_operations AS o ON o.turn_id = t.id "
            "LEFT JOIN ai_agent_long_tasks AS task ON task.id = o.long_task_id "
            "WHERE t.session_id = ? ORDER BY t.rowid",
            [session_id],
        )
        return projection_version(rows)

    async def _task(self, row):
        task = await self._db.fetch_one(
            "SELECT * FROM ai_agent_long_tasks WHERE id = ?",
            [row["long_task_id"]],
        )
        if task is None:
            return None
        units = await self._db.fetch_all(
            "SELECT * FROM ai_agent_long_task_units "
            "WHERE task_id = ? ORDER BY position",
            [task["id"]],
        )
        revision = await self._revision(row.get("operation_result_revision_id"))
        return {
            "id": str(task["id"]),
            "projectId": str(row["project_id"]),
            "sessionId": int(row["session_id"]),
            "turnId": str(row["id"]),
            "status": _task_status(row.get("operation_status")),
            "targetRole": str(row.get("operation_target_role") or ""),
            "intent": _object(row.get("intent_json")),
            "rootRunId": str(row.get("root_run_id") or "") or None,
            "totalUnits": int(task.get("total_units") or 0),
            "completedUnits": int(task.get("completed_units") or 0),
            "usage": _usage(task.get("usage_json")),
            "resultRevisionId": str(
                row.get("operation_result_revision_id") or ""
            ) or None,
            "resultRevision": revision,
            "error": _object(row.get("operation_error_json")) or None,
            "units": [_unit(unit) for unit in units],
            "createdAt": task.get("create_time"),
            "updatedAt": task.get("update_time"),
        }

    def _operation(self, row, task):
        return {
            "id": str(row["operation_id"]),
            "turnId": str(row["id"]),
            "taskId": str(row.get("long_task_id") or "") or None,
            "status": str(row.get("operation_status") or "queued"),
            "revision": int(row.get("operation_revision") or 1),
            "targetRole": str(row.get("operation_target_role") or ""),
            "parts": list((task or {}).get("units") or ()),
            "resultRevisionId": str(
                row.get("operation_result_revision_id") or ""
            ) or None,
            "finalizationReceiptId": str(
                row.get("finalization_receipt_id") or ""
            ) or None,
            "cancelReceiptId": str(row.get("cancel_receipt_id") or "") or None,
            "cancelRequestedAt": (
                str(row["operation_cancel_requested_at_ms"])
                if row.get("operation_cancel_requested_at_ms") is not None
                else None
            ),
            "error": _object(row.get("operation_error_json")) or None,
            "usage": _usage(row.get("operation_usage_json")),
            "resultRevision": (task or {}).get("resultRevision"),
            "createdAt": row.get("operation_create_time"),
            "updatedAt": row.get("operation_update_time"),
        }

    async def _revision(self, revision_id):
        identity = str(revision_id or "").strip()
        if not identity:
            return None
        row = await self._db.fetch_one(
            "SELECT r.*, d.role, "
            "CASE WHEN h.revision_id IS NOT NULL THEN 'current' "
            "WHEN a.id IS NOT NULL THEN 'historical' ELSE 'candidate' END AS status "
            "FROM screenplay_revisions AS r "
            "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
            "LEFT JOIN screenplay_project_heads AS h ON h.project_id = r.project_id "
            "AND h.deliverable_id = r.deliverable_id AND h.revision_id = r.id "
            "LEFT JOIN screenplay_acceptance_events AS a ON a.project_id = r.project_id "
            "AND a.revision_id = r.id WHERE r.id = ? LIMIT 1",
            [identity],
        )
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "deliverableId": str(row["deliverable_id"]),
            "role": str(row["role"]),
            "revisionNo": int(row.get("revision_no") or 1),
            "parentRevisionId": row.get("parent_revision_id"),
            "contentDigest": str(row.get("content_digest") or ""),
            "summary": _object(row.get("summary_json")),
            "agentTaskId": row.get("agent_task_id"),
            "rootRunId": row.get("root_run_id"),
            "finalizingRunId": row.get("finalizing_run_id"),
            "createdAt": row.get("create_time"),
            "projectId": str(row["project_id"]),
            "schemaVersion": int(row.get("schema_version") or 1),
            "createdBy": (
                "agent"
                if row.get("created_by") in {"agent", "screenplay_agent_task"}
                else "user"
            ),
            "applicability": "current",
            "status": str(row["status"]),
        }

    async def _turns(self, turn_ids):
        if not turn_ids:
            return {}
        marks = ",".join("?" for _ in turn_ids)
        rows = await self._db.fetch_all(
            "SELECT *, planner_run_id AS root_run_id "
            "FROM screenplay_agent_turns "
            f"WHERE id IN ({marks})",
            list(turn_ids),
        )
        return {str(row["id"]): row for row in rows}

    async def _require_session(self, project_id, session_id):
        row = await self._db.fetch_one(
            "SELECT id FROM ai_sessions WHERE id = ? "
            "AND screenplay_project_id = ? AND scope = 'screenplay'",
            [int(session_id), str(project_id)],
        )
        if row is None:
            raise NotFoundError("剧本对话不存在")


def _turn(row):
    return {
        "id": str(row["id"]),
        "commandId": str(row.get("command_id") or ""),
        "projectId": str(row["project_id"]),
        "sessionId": int(row["session_id"]),
        "status": str(row["status"]),
        "userContent": str(row.get("user_content") or ""),
        "stageCommand": _object(row.get("stage_command_json")) or None,
        "assistantContent": str(row.get("assistant_content") or ""),
        "runtimeProfile": _object(row.get("runtime_profile_json")),
        "intent": _object(row.get("intent_json")) or None,
        "rootRunId": str(row.get("root_run_id") or "") or None,
        "taskId": str(row.get("task_id") or "") or None,
        "cancelReceiptId": str(row.get("cancel_receipt_id") or "") or None,
        "cancelRequestedAt": (
            str(row["cancel_requested_at_ms"])
            if row.get("cancel_requested_at_ms") is not None
            else None
        ),
        "error": _object(row.get("error_json")) or None,
        "createdAt": row.get("create_time"),
        "updatedAt": row.get("update_time"),
    }


def _unit(row):
    return {
        "id": str(row["unit_id"]),
        "semanticKey": str(row.get("semantic_key") or ""),
        "position": int(row.get("position") or 0),
        "kind": str(_object(row.get("metadata_json")).get("partKind") or ""),
        "status": str(row.get("status") or "pending"),
        "input": dict(_object(row.get("metadata_json")).get("input") or {}),
        "outputRef": str(row.get("output_ref") or "") or None,
        "artifactDigest": str(row.get("artifact_digest") or "") or None,
        "validationReceipt": _object(row.get("validation_receipt_json")),
        "error": (
            {"code": str(row.get("error_code") or "")}
            if row.get("error_code") else None
        ),
        "attempt": int(row.get("attempt") or 0),
    }


def _task_status(operation_status):
    return {
        "queued": "pending",
        "running": "running",
        "paused": "paused",
        "succeeded": "completed",
        "failed": "failed",
        "canceled": "canceled",
    }.get(str(operation_status or ""), "pending")


def _usage(value):
    raw = _object(value)
    return {
        "invocationCount": int(raw.get("invocationCount") or 0),
        "inputTokens": int(raw.get("inputTokens") or 0),
        "generationTokens": int(raw.get("generationTokens") or 0),
        "reasoningTokens": (
            int(raw["reasoningTokens"])
            if raw.get("reasoningTokens") is not None else None
        ),
    }


def _object(value):
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


__all__ = ["ScreenplayReplacementConversationQuery"]
