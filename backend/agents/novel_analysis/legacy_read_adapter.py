"""Read-only projection for frozen Novel Analysis history."""

from __future__ import annotations

import json
from collections.abc import Mapping

from agents.novel_analysis.legacy_contracts import (
    LEGACY_NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX,
    LEGACY_NOVEL_ANALYSIS_DOMAIN_NAMESPACE,
)
from purra.artifacts import ArtifactStatus
from purra.json_values import thaw_json_mapping

from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)


_PARTIAL_COMPLETION_FAILURE_CODES = frozenset({
    "runtime_budget_exceeded",
    "provider_circuit_open",
    "provider_capacity_limited",
    "upstream_stream_interrupted",
    "provider_rate_limited",
    "provider_unavailable",
    "model_activity_deadline_exceeded",
    "model_progress_deadline_exceeded",
    "model_invocation_deadline_exceeded",
})

_CONVERSATION_STREAMING_RUN_STATUSES = frozenset({
    "pending",
    "running",
    "claimed",
})

_WORKFLOW_STATUS_BY_TASK_STATUS = {
    "pending": "queued",
    "running": "running",
    "paused": "paused",
    "completed": "completed",
    "failed": "failed",
    "canceled": "canceled",
}


class NovelAnalysisLegacyReadAdapter:
    """Read frozen history without exposing execution or mutation methods."""

    def __init__(self, db, *, long_tasks=None) -> None:
        self._db = db
        self._long_tasks = long_tasks or SqliteLongTaskRepository(db)
        self._artifacts = SqliteArtifactRepository(db)

    async def list_for_revision(self, source_revision_id: str) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT r.id AS run_id, r.status AS run_status, "
            "r.binding_command_id, r.binding_attributes_json, r.prompt, "
            "r.final_response, r.error, "
            "COALESCE(sc.session_id, 'legacy:' || r.binding_aggregate_id) "
            "AS conversation_id, r.create_time, r.update_time, "
            "r.provider_output_events, t.id AS task_id, "
            "t.status AS task_status, t.revision AS task_revision, "
            "t.state_reason_code AS task_state_reason_code, "
            "t.state_reason_scope AS task_state_reason_scope, "
            "t.total_units, t.completed_units, t.failed_units, "
            "t.metadata_json AS task_metadata_json "
            "FROM ai_agent_runs AS r "
            "LEFT JOIN novel_analysis_session_commands AS sc "
            "ON sc.command_id = r.binding_command_id "
            "LEFT JOIN ai_agent_long_task_runs AS ltr ON ltr.run_id = r.id "
            "LEFT JOIN ai_agent_long_tasks AS t ON t.id = ltr.task_id "
            "WHERE r.binding_namespace = 'novel_source_analysis' "
            "AND r.binding_aggregate_id = ? "
            "AND ((r.agent_kind = 'novel_analysis' "
            "AND r.implementation_id = 'legacy-frozen-2026-09-12') "
            "OR (r.agent_kind IS NULL AND r.implementation_id IS NULL)) "
            "AND NOT EXISTS (SELECT 1 FROM novel_analysis_superseded_runs "
            "WHERE run_id = r.id) "
            "ORDER BY r.create_time DESC, r.rowid DESC",
            [source_revision_id],
        )
        results: list[dict] = []
        seen_tasks: set[str] = set()
        for row in rows:
            binding_attributes = _mapping(row.get("binding_attributes_json"))
            task_id = str(row.get("task_id") or "")
            latest_task_turn = task_id not in seen_tasks
            if task_id:
                seen_tasks.add(task_id)

            artifact_ref = None
            published_id = None
            units = ()
            analysis_plan = None
            related_runs = []
            partial_completion = False
            workflow = _empty_workflow()
            provider_output_events = int(row.get("provider_output_events") or 0)

            if task_id:
                task_metadata = _mapping(row.get("task_metadata_json"))
                raw_plan = task_metadata.get("analysisPlan")
                if isinstance(raw_plan, Mapping):
                    analysis_plan = thaw_json_mapping(raw_plan)
                units = await self._long_tasks.list_units(task_id)
                task_runs = await self._list_task_runs(task_id)
                owner = None
                owned_runs = []
                for item in task_runs:
                    if item["binding_namespace"] == "novel_source_analysis":
                        owner = item["id"]
                    if owner == row["run_id"]:
                        owned_runs.append(item)
                related_runs = [
                    {"runId": item["id"], "status": item["status"]}
                    for item in owned_runs
                    if item["id"] != row["run_id"]
                ]
                if not latest_task_turn:
                    units = ()
                provider_output_events = sum(
                    int(item.get("provider_output_events") or 0)
                    for item in owned_runs
                )
                unit = await self._db.fetch_one(
                    "SELECT u.output_ref FROM ai_agent_long_task_units u "
                    "JOIN ai_agent_artifacts a ON u.output_ref = "
                    "'novel-analysis-artifact://' || a.id "
                    "WHERE u.task_id = ? AND u.unit_id = 'artifact:review' "
                    "AND u.status = 'completed' AND a.created_by_run_id = ?",
                    [task_id, row["run_id"]],
                )
                artifact_ref = str((unit or {}).get("output_ref") or "") or None
                partial_completion = (
                    str(row.get("run_status") or "") == "done"
                    and str(row.get("task_status") or "") in {"failed", "paused"}
                    and any(
                        item.error_code in _PARTIAL_COMPLETION_FAILURE_CODES
                        for item in units
                    )
                )
                workflow = _workflow_projection(
                    row,
                    units,
                    metadata=task_metadata,
                )
                if artifact_ref:
                    published = await self._db.fetch_one(
                        "SELECT p.id, "
                        "json_extract(p.summary_json, '$.artifactId') "
                        "AS artifact_id FROM novel_source_analyses p "
                        "JOIN ai_agent_artifacts a "
                        "ON a.id = json_extract(p.summary_json, '$.artifactId') "
                        "WHERE p.source_revision_id = ? "
                        "AND a.created_by_run_id = ? "
                        "ORDER BY p.version_no DESC LIMIT 1",
                        [source_revision_id, row["run_id"]],
                    )
                    if published:
                        published_id = published["id"]
                        artifact_ref = (
                            LEGACY_NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX
                            + published["artifact_id"]
                        )

            unit_views = []
            for unit in units:
                retry_not_before_ms = unit.metadata.get("retryNotBeforeMs")
                unit_views.append({
                    "unitId": unit.id,
                    "title": str(unit.metadata.get("displayTitle") or unit.id),
                    "kind": str(unit.metadata.get("unitKind") or ""),
                    **(
                        {"plannerStepId": str(unit.metadata["plannerStepId"])}
                        if unit.metadata.get("plannerStepId")
                        else {}
                    ),
                    "status": unit.status.value,
                    "attempt": unit.attempt,
                    "maxAttempts": unit.max_attempts,
                    "errorCode": unit.error_code,
                    "nextRetryAtMs": (
                        int(retry_not_before_ms)
                        if isinstance(retry_not_before_ms, int)
                        else None
                    ),
                    "updateTime": unit.update_time,
                })

            results.append({
                "runId": str(row["run_id"]),
                "runStatus": str(row["run_status"]),
                "commandId": str(row.get("binding_command_id") or ""),
                "conversationId": row.get("conversation_id"),
                "interactionKind": str(
                    binding_attributes.get("interactionKind") or "analysis"
                ),
                "automaticRecovery": bool(
                    binding_attributes.get("automaticRecovery")
                ),
                "analysisArtifactRef": (
                    str(binding_attributes.get("analysisArtifactRef") or "")
                    or None
                ),
                "prompt": str(row.get("prompt") or ""),
                "finalResponse": str(row.get("final_response") or ""),
                "conversationStatus": _conversation_status(
                    row.get("run_status")
                ),
                "partialCompletion": (
                    partial_completion if latest_task_turn else False
                ),
                "taskId": task_id or None,
                "taskStatus": (
                    str(row.get("task_status") or "") or None
                ) if latest_task_turn else None,
                "workflowStatus": (
                    workflow["status"] if latest_task_turn else None
                ),
                "workflowPauseKind": (
                    workflow["pauseKind"] if latest_task_turn else None
                ),
                "workflowReasonCode": (
                    workflow["reasonCode"] if latest_task_turn else None
                ),
                "workflowResumable": (
                    bool(workflow["resumable"]) if latest_task_turn else False
                ),
                "workflowAutoResumeAtMs": (
                    workflow["autoResumeAtMs"] if latest_task_turn else None
                ),
                "workflowAutoRecoveryEligible": (
                    bool(workflow["autoRecoveryEligible"])
                    if latest_task_turn
                    else False
                ),
                "taskRevision": row.get("task_revision"),
                "totalUnits": int(row.get("total_units") or 0),
                "completedUnits": int(row.get("completed_units") or 0),
                "failedUnits": int(row.get("failed_units") or 0),
                "error": row.get("error"),
                "artifactRef": artifact_ref,
                "publishedAnalysisId": published_id,
                "providerOutputEvents": provider_output_events,
                "relatedRuns": related_runs,
                "analysisPlan": analysis_plan,
                "units": unit_views,
                "createTime": row.get("create_time"),
                "updateTime": row.get("update_time"),
            })
        return results

    async def get_artifact(self, reference_or_id: str) -> dict:
        artifact_id = _artifact_id(reference_or_id)
        artifact = await self._artifacts.load(artifact_id)
        if (
            artifact is None
            or artifact.namespace != LEGACY_NOVEL_ANALYSIS_DOMAIN_NAMESPACE
            or artifact.status is not ArtifactStatus.FINALIZED
        ):
            raise RuntimeError("legacy novel analysis Artifact is not finalized")
        batches = await self._artifacts.list_batches(artifact.id)
        if len(batches) != 1 or len(batches[0].items) != 1:
            raise RuntimeError("legacy novel analysis Artifact is incomplete")
        item = thaw_json_mapping(batches[0].items[0])
        payload = item.get("payload")
        if not isinstance(payload, Mapping):
            raise RuntimeError("legacy novel analysis Artifact payload is invalid")
        return {
            **dict(payload),
            "artifactId": artifact.id,
            "artifactKind": artifact.kind,
            "artifactRevision": artifact.revision,
            "artifactDigest": batches[0].content_digest,
            "createdByRunId": artifact.created_by_run_id,
            "metadata": thaw_json_mapping(artifact.metadata),
        }

    async def _list_task_runs(self, task_id: str) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT r.id, r.status, r.binding_namespace, "
            "r.provider_output_events FROM ai_agent_runs AS r WHERE r.id IN ("
            "SELECT run_id FROM ai_agent_long_task_runs WHERE task_id = ? "
            "UNION SELECT run_id FROM ai_agent_long_task_units "
            "WHERE task_id = ? "
            "UNION SELECT json_extract(history.value, '$.runId') "
            "FROM ai_agent_long_task_units AS unit, "
            "json_each(unit.metadata_json, '$.runHistory') AS history "
            "WHERE unit.task_id = ? "
            "UNION SELECT id FROM ai_agent_runs WHERE "
            "json_extract(binding_attributes_json, '$.taskId') = ?) "
            "AND r.binding_namespace IN "
            "('novel_source_analysis', 'novel_source_analysis.unit') "
            "ORDER BY r.rowid",
            [task_id, task_id, task_id, task_id],
        )


def _artifact_id(reference_or_id: str) -> str:
    value = str(reference_or_id or "").strip()
    if value.startswith(LEGACY_NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX):
        value = value.removeprefix(LEGACY_NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX)
    if not value:
        raise ValueError("legacy novel analysis Artifact id is required")
    return value


def _mapping(value: object) -> dict:
    if isinstance(value, Mapping):
        return thaw_json_mapping(value)
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _conversation_status(run_status: object) -> str:
    return (
        "streaming"
        if str(run_status or "") in _CONVERSATION_STREAMING_RUN_STATUSES
        else "finalized"
    )


def _empty_workflow() -> dict[str, object]:
    return {
        "status": None,
        "pauseKind": None,
        "reasonCode": None,
        "resumable": False,
        "autoResumeAtMs": None,
        "autoRecoveryEligible": False,
    }


def _workflow_projection(
    row: Mapping[str, object],
    units,
    *,
    metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    task_status = str(row.get("task_status") or "")
    status = _WORKFLOW_STATUS_BY_TASK_STATUS.get(task_status)
    task_reason = str(row.get("task_state_reason_code") or "").strip() or None
    unit_reason = next(
        (
            str(unit.error_code or "").strip()
            for unit in units
            if str(unit.error_code or "").strip()
        ),
        None,
    )
    reason_code = task_reason or unit_reason
    raw_scope = str(row.get("task_state_reason_scope") or "").strip()
    pause_kind = raw_scope if raw_scope in {"system", "user", "budget"} else None
    if status == "paused" and pause_kind is None:
        if reason_code in _PARTIAL_COMPLETION_FAILURE_CODES:
            pause_kind = "system"
        elif reason_code and reason_code.startswith("user_"):
            pause_kind = "user"
        else:
            pause_kind = "unknown"
    metadata = metadata or {}
    raw_due = metadata.get("autoResumeNotBeforeMs")
    auto_resume_at_ms = (
        int(raw_due) if isinstance(raw_due, int) and raw_due > 0 else None
    )
    auto_recovery_eligible = bool(
        status == "paused"
        and pause_kind == "system"
        and auto_resume_at_ms is not None
        and isinstance(metadata.get("runtimeBinding"), Mapping)
        and not bool(metadata.get("autoRecoveryBudgetExceeded"))
    )
    return {
        "status": status,
        "pauseKind": pause_kind,
        "reasonCode": reason_code,
        "resumable": status in {"paused", "failed"},
        "autoResumeAtMs": auto_resume_at_ms,
        "autoRecoveryEligible": auto_recovery_eligible,
    }


__all__ = ["NovelAnalysisLegacyReadAdapter"]
