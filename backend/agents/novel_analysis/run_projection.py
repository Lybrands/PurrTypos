"""Read-only conversation projection for replacement Novel Analysis Runs."""

from __future__ import annotations

from collections.abc import Mapping

from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from infrastructure.persistence.sqlite_run_tree_repository import (
    SqliteRunTreeRepository,
)
from purra.errors import ContractViolationError
from purra.json_values import thaw_json_mapping

from agents.novel_analysis.review_projection import (
    NOVEL_ANALYSIS_REVIEW_REF_PREFIX,
    NovelAnalysisReviewProjection,
    NovelAnalysisReviewProjectionError,
)


NOVEL_ANALYSIS_REPLACEMENT_RUN_NAMESPACE = "purrtypos.novel_analysis"


class NovelAnalysisReplacementRunProjection:
    """Project replacement persistence into the existing read-only UI DTO."""

    def __init__(self, db, *, long_tasks) -> None:
        self._db = db
        self._tasks = long_tasks
        self._runs = SqliteRunRepository(db)
        self._run_tree = SqliteRunTreeRepository(db)

    async def list_for_revision(self, source_revision_id: str) -> list[dict]:
        rows = await self._db.fetch_all(
            "SELECT r.id AS run_id, r.status AS run_status, "
            "r.binding_command_id, r.binding_attributes_json, r.prompt, "
            "r.final_response, r.error, r.provider_output_events, "
            "r.create_time, r.update_time, ltr.task_id, ltr.relation, "
            "t.status AS task_status, t.revision AS task_revision, "
            "t.state_reason_code, t.state_reason_scope, t.total_units, "
            "t.completed_units, t.failed_units, t.metadata_json, "
            "COALESCE(sc.session_id, ("
            "SELECT owner_sc.session_id FROM ai_agent_long_task_runs owner_ltr "
            "JOIN ai_agent_runs owner_run ON owner_run.id = owner_ltr.run_id "
            "JOIN novel_analysis_session_commands owner_sc "
            "ON owner_sc.command_id = owner_run.binding_command_id "
            "WHERE owner_ltr.task_id = ltr.task_id "
            "AND owner_ltr.relation = 'created' "
            "ORDER BY owner_run.rowid LIMIT 1"
            "), 'replacement:' || r.binding_aggregate_id) "
            "AS conversation_id "
            "FROM ai_agent_runs r "
            "LEFT JOIN ai_agent_long_task_runs ltr ON ltr.run_id = r.id "
            "LEFT JOIN ai_agent_long_tasks t ON t.id = ltr.task_id "
            "LEFT JOIN novel_analysis_session_commands sc "
            "ON sc.command_id = r.binding_command_id "
            "WHERE r.binding_namespace = ? AND r.binding_aggregate_id = ? "
            "AND NOT EXISTS (SELECT 1 FROM novel_analysis_superseded_runs s "
            "WHERE s.run_id = r.id) "
            "ORDER BY r.create_time DESC, r.rowid DESC",
            [NOVEL_ANALYSIS_REPLACEMENT_RUN_NAMESPACE, source_revision_id],
        )
        latest_by_task: dict[str, str] = {}
        origin_by_task: dict[str, dict] = {}
        for row in rows:
            task_id = str(row.get("task_id") or "")
            if task_id and task_id not in latest_by_task:
                latest_by_task[task_id] = str(row["run_id"])
            if task_id and str(row.get("relation") or "") == "created":
                origin_by_task[task_id] = row

        projected = []
        for row in rows:
            run_id = str(row["run_id"])
            task_id = str(row.get("task_id") or "")
            is_latest = bool(task_id and latest_by_task.get(task_id) == run_id)
            # A durable continuation is another execution Root for the same
            # logical user turn. Project only its latest Root; otherwise an
            # automatic recovery appears as a fabricated second user message.
            if task_id and not is_latest:
                continue
            origin = origin_by_task.get(task_id, row)
            metadata = _mapping(row.get("metadata_json"))
            attributes = _mapping(origin.get("binding_attributes_json"))
            units = await self._tasks.list_units(task_id) if is_latest else ()
            snapshot = await self._runs.get(run_id)
            artifact_ref, published_id, artifact_summary = await self._result_refs(
                task_id
            )
            related_runs = await self._related_runs(run_id, task_id=task_id)
            workflow = _workflow(row, units, metadata) if is_latest else _empty_workflow()
            interaction_kind = str(
                attributes.get("interactionKind") or "analysis"
            )
            analysis_artifact_id = str(
                attributes.get("analysisArtifactId") or ""
            )
            projected.append({
                "runId": run_id,
                "runStatus": str(row.get("run_status") or ""),
                "conversationStatus": (
                    "streaming"
                    if str(row.get("run_status") or "") in {"pending", "running"}
                    else "finalized"
                ),
                "commandId": str(origin.get("binding_command_id") or ""),
                "conversationId": origin.get("conversation_id"),
                "interactionKind": interaction_kind,
                "automaticRecovery": False,
                "analysisArtifactRef": (
                    NOVEL_ANALYSIS_REVIEW_REF_PREFIX + analysis_artifact_id
                    if interaction_kind == "follow_up" and analysis_artifact_id
                    else None
                ),
                "prompt": str(origin.get("prompt") or ""),
                "finalResponse": (
                    str(row.get("final_response") or "").strip()
                    or artifact_summary
                ),
                "partialCompletion": False,
                "taskId": task_id or None,
                "taskStatus": (
                    str(row.get("task_status") or "") or None
                ) if is_latest else None,
                "workflowStatus": workflow["status"],
                "workflowPauseKind": workflow["pauseKind"],
                "workflowReasonCode": workflow["reasonCode"],
                "workflowResumable": workflow["resumable"],
                "workflowAutoResumeAtMs": workflow["autoResumeAtMs"],
                "workflowAutoRecoveryEligible": workflow["autoRecoveryEligible"],
                "taskRevision": row.get("task_revision") if is_latest else None,
                "totalUnits": int(row.get("total_units") or 0) if is_latest else 0,
                "completedUnits": int(row.get("completed_units") or 0) if is_latest else 0,
                "failedUnits": int(row.get("failed_units") or 0) if is_latest else 0,
                "error": row.get("error"),
                "artifactRef": artifact_ref,
                "publishedAnalysisId": published_id,
                "providerOutputEvents": int(row.get("provider_output_events") or 0),
                # Scalable Units execute as real Agent-tree Child Runs. Expose
                # their identities so diagnostics can render their tool calls;
                # public conversation projection still filters private output.
                "relatedRuns": related_runs,
                "analysisPlan": _plan(snapshot),
                "units": [_unit(item) for item in units],
                "createTime": origin.get("create_time"),
                "updateTime": row.get("update_time"),
            })
        return projected

    async def _related_runs(
        self, root_run_id: str, *, task_id: str = ""
    ) -> list[dict[str, object]]:
        previous_roots = []
        if task_id:
            previous_roots = await self._db.fetch_all(
                "SELECT r.id, r.status FROM ai_agent_runs r "
                "JOIN ai_agent_long_task_runs binding ON binding.run_id = r.id "
                "WHERE binding.task_id = ? AND r.id <> ? "
                "ORDER BY r.rowid",
                [task_id, root_run_id],
            )
            rows = await self._db.fetch_all(
                "SELECT child.id, child.status, child.agent_id, "
                "child.create_time, (SELECT event.occurred_at "
                "FROM ai_agent_run_events event WHERE event.run_id = child.id "
                "AND event.event_type = 'run.lifecycle' "
                "ORDER BY event.id LIMIT 1) AS precise_start_time "
                "FROM ai_agent_runs child "
                "JOIN ai_agent_long_task_runs binding "
                "ON binding.run_id = child.root_run_id "
                "WHERE binding.task_id = ? "
                "AND child.parent_run_id = child.root_run_id "
                "ORDER BY child.rowid",
                [task_id],
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT child.id, child.status, child.agent_id, child.create_time, "
                "(SELECT event.occurred_at FROM ai_agent_run_events event "
                "WHERE event.run_id = child.id "
                "AND event.event_type = 'run.lifecycle' "
                "ORDER BY event.id LIMIT 1) AS precise_start_time "
                "FROM ai_agent_runs child "
                "WHERE child.root_run_id = ? "
                "AND child.parent_run_id = ? ORDER BY child.rowid",
                [root_run_id, root_run_id],
            )
        result: list[dict[str, object]] = [{
            "runId": str(row["id"]),
            "status": str(row["status"]),
            "role": "previous_root",
        } for row in previous_roots]
        for row in rows:
            item: dict[str, object] = {
                "runId": str(row["id"]),
                "status": str(row["status"]),
                "role": "child",
                "createTime": row.get("precise_start_time") or row.get("create_time"),
            }
            try:
                tree_run = await self._run_tree.get_run(str(row["id"]))
                agent = await self._run_tree.get_agent(tree_run.agent_id)
            except ContractViolationError:
                pass
            else:
                item.update({
                    "agentId": agent.agent_id,
                    "agentName": agent.name,
                    "agentTitle": agent.title,
                    "objective": tree_run.objective,
                    "previousRunId": tree_run.previous_run_id,
                    "unitId": tree_run.input_payload.get("unitId"),
                    "attempt": tree_run.input_payload.get("attempt"),
                })
            result.append(item)
        return result

    async def _result_refs(
        self, task_id: str
    ) -> tuple[str | None, str | None, str]:
        if not task_id:
            return None, None, ""
        row = await self._db.fetch_one(
            "SELECT u.output_ref FROM ai_agent_long_task_units u "
            "JOIN ai_agent_artifacts a "
            "ON u.output_ref = ? || a.id "
            "WHERE u.task_id = ? AND u.unit_id = 'review:artifact' "
            "AND u.status = 'completed'",
            [NOVEL_ANALYSIS_REVIEW_REF_PREFIX, task_id],
        )
        source_ref = str((row or {}).get("output_ref") or "") or None
        if source_ref is None:
            return None, None, ""
        try:
            review = await NovelAnalysisReviewProjection(self._db).load(source_ref)
        except NovelAnalysisReviewProjectionError:
            # Retired review contracts are neither recovered nor exposed. They
            # must not prevent a fresh canonical analysis from being created.
            return None, None, ""
        overview = _mapping(review.get("storyOverview"))
        summary = str(overview.get("summaryMarkdown") or "").strip()
        task = await self._db.fetch_one(
            "SELECT owner_id FROM ai_agent_long_tasks WHERE id = ?",
            [task_id],
        )
        published = None if task is None else await self._db.fetch_one(
            "SELECT id, json_extract(summary_json, '$.artifactId') "
            "AS artifact_id FROM novel_source_analyses "
            "WHERE source_revision_id = ? "
            "AND json_extract(summary_json, '$.sourceArtifactRef') = ? "
            "ORDER BY version_no DESC LIMIT 1",
            [str(task["owner_id"]), source_ref],
        )
        if published is None:
            return source_ref, None, summary
        reviewed_id = str(published.get("artifact_id") or "")
        return (
            NOVEL_ANALYSIS_REVIEW_REF_PREFIX + reviewed_id
            if reviewed_id else source_ref,
            str(published["id"]),
            summary,
        )


def _mapping(value) -> dict:
    if isinstance(value, Mapping):
        return thaw_json_mapping(value)
    if not value:
        return {}
    import json

    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _plan(snapshot) -> dict | None:
    if not snapshot.steps:
        return None
    task_spec = snapshot.task_spec
    return {
        "title": snapshot.title,
        "goal": snapshot.goal,
        "taskSpec": ({
            "goal": task_spec.goal,
            "operation": task_spec.operation,
            "instruction": task_spec.instruction,
            "deliverable": task_spec.deliverable,
            "constraints": list(task_spec.constraints),
        } if task_spec is not None else None),
        "steps": [{
            "id": step.id,
            "title": step.title,
            "type": step.type.value,
            "executor": step.executor.value,
            "dependsOn": list(step.depends_on),
            **({"description": step.description} if step.description else {}),
        } for step in snapshot.steps],
    }


def _unit(unit) -> dict:
    retry_at = unit.metadata.get("retryNotBeforeMs")
    return {
        "unitId": unit.id,
        "title": str(unit.metadata.get("displayTitle") or unit.id),
        "kind": str(unit.metadata.get("unitKind") or ""),
        **(
            {"plannerStepId": str(unit.metadata["plannerStepId"])}
            if unit.metadata.get("plannerStepId") else {}
        ),
        "status": unit.status.value,
        "attempt": unit.attempt,
        "maxAttempts": unit.max_attempts,
        "errorCode": unit.error_code,
        "nextRetryAtMs": retry_at if isinstance(retry_at, int) else None,
        "updateTime": unit.update_time,
    }


def _empty_workflow() -> dict:
    return {
        "status": None,
        "pauseKind": None,
        "reasonCode": None,
        "resumable": False,
        "autoResumeAtMs": None,
        "autoRecoveryEligible": False,
    }


def _workflow(row, units, metadata: Mapping[str, object]) -> dict:
    status = {
        "pending": "queued",
        "running": "running",
        "paused": "paused",
        "completed": "completed",
        "failed": "failed",
        "canceled": "canceled",
    }.get(str(row.get("task_status") or ""))
    reason = str(row.get("state_reason_code") or "").strip() or next(
        (str(unit.error_code) for unit in units if unit.error_code),
        None,
    )
    raw_scope = str(row.get("state_reason_scope") or "").strip()
    pause_kind = raw_scope if raw_scope in {"system", "user", "budget"} else None
    if status == "paused" and pause_kind is None:
        pause_kind = "user" if reason and reason.startswith("user_") else "unknown"
    raw_due = metadata.get("autoResumeNotBeforeMs")
    due = raw_due if isinstance(raw_due, int) and raw_due > 0 else None
    auto = bool(
        status == "paused"
        and pause_kind == "system"
        and due is not None
        and isinstance(metadata.get("runtimeBinding"), Mapping)
        and not metadata.get("autoRecoveryBudgetExceeded")
    )
    return {
        "status": status,
        "pauseKind": pause_kind,
        "reasonCode": reason,
        "resumable": status == "paused",
        "autoResumeAtMs": due,
        "autoRecoveryEligible": auto,
    }


__all__ = [
    "NOVEL_ANALYSIS_REPLACEMENT_RUN_NAMESPACE",
    "NovelAnalysisReplacementRunProjection",
]
