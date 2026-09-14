"""Version-aware SSE query spanning frozen and replacement analysis Runs."""

from __future__ import annotations

import json

from agents.novel_analysis.run_projection import (
    NOVEL_ANALYSIS_REPLACEMENT_RUN_NAMESPACE,
    NovelAnalysisReplacementRunProjection,
)
from application.agent_event_stream import projection_version
from application.sse_mapping import canonical_output_to_sse_chunk
from exceptions import NotFoundError
from purra.output import OutputChannel, OutputVisibility


_LEGACY_NAMESPACES = ("novel_source_analysis", "novel_source_analysis.unit")
_EVENT_NAMESPACES = (*_LEGACY_NAMESPACES, NOVEL_ANALYSIS_REPLACEMENT_RUN_NAMESPACE)


class VersionedNovelAnalysisStreamQuery:
    """Use one event cursor while projecting both implementation versions."""

    def __init__(self, db, *, output_repository, historical_analysis, long_tasks):
        self._db = db
        self._output = output_repository
        self._historical = historical_analysis
        self._replacement = NovelAnalysisReplacementRunProjection(
            db,
            long_tasks=long_tasks,
        )
        self._version = None
        self._runs = []

    async def read_page(self, revision_id, *, after=0, limit=500):
        if await self._db.fetch_one(
            "SELECT id FROM novel_source_revisions WHERE id = ?",
            [revision_id],
        ) is None:
            raise NotFoundError("来源版本不存在")
        state = await self._db.fetch_all(
            "SELECT r.id, r.status, r.binding_namespace, "
            "sr.replacement_command_id, t.id AS task_id, "
            "t.status AS task_status, t.revision AS task_revision, "
            "t.state_reason_code, t.state_reason_scope, t.total_units, "
            "t.completed_units, t.failed_units, t.metadata_json "
            "FROM ai_agent_runs r "
            "LEFT JOIN novel_analysis_superseded_runs sr ON sr.run_id = r.id "
            "LEFT JOIN ai_agent_long_task_runs tr ON tr.run_id = r.id "
            "LEFT JOIN ai_agent_long_tasks t ON t.id = tr.task_id "
            "WHERE r.binding_aggregate_id = ? AND r.binding_namespace IN (?,?,?) "
            "ORDER BY r.rowid",
            [revision_id, *_EVENT_NAMESPACES],
        )
        units = await self._db.fetch_all(
            "SELECT u.task_id, u.unit_id, u.status, u.attempt, u.run_id, "
            "u.output_ref, u.metadata_json FROM ai_agent_long_task_units u "
            "WHERE u.task_id IN ("
            "SELECT tr.task_id FROM ai_agent_long_task_runs tr "
            "JOIN ai_agent_runs r ON r.id = tr.run_id "
            "WHERE r.binding_aggregate_id = ? "
            "AND r.binding_namespace IN (?,?)) "
            "ORDER BY u.task_id, u.position",
            [
                revision_id,
                "novel_source_analysis",
                NOVEL_ANALYSIS_REPLACEMENT_RUN_NAMESPACE,
            ],
        )
        published = await self._db.fetch_one(
            "SELECT id FROM novel_source_analyses WHERE source_revision_id = ? "
            "ORDER BY version_no DESC LIMIT 1",
            [revision_id],
        )
        version = projection_version([state, units, published])
        changed = version != self._version
        if changed:
            legacy = await self._historical.list_for_revision(revision_id)
            replacement = await self._replacement.list_for_revision(revision_id)
            self._runs = sorted(
                [*legacy, *replacement],
                key=lambda item: (
                    str(item.get("createTime") or ""),
                    str(item.get("runId") or ""),
                ),
                reverse=True,
            )
            self._version = version
        rows = await self._output.list_bound_events(
            aggregate_id=revision_id,
            namespaces=_EVENT_NAMESPACES,
            after_cursor=after,
            limit=limit + 1,
        )
        page = rows[:limit]
        visible_ids = {
            identity
            for run in self._runs
            for identity in (
                run["runId"],
                *(item["runId"] for item in run.get("relatedRuns", [])),
            )
        }
        projected_ids = {str(row["id"]) for row in state}
        for unit in units:
            if unit.get("run_id"):
                projected_ids.add(str(unit["run_id"]))
            try:
                metadata = json.loads(unit.get("metadata_json") or "{}")
            except (TypeError, ValueError):
                metadata = {}
            if isinstance(metadata, dict):
                projected_ids.update(
                    str(item["runId"])
                    for item in metadata.get("runHistory", [])
                    if isinstance(item, dict) and item.get("runId")
                )
        archived_ids = projected_ids - visible_ids
        archived_ids.update(
            str(row["id"])
            for row in state
            if row.get("replacement_command_id")
        )
        chunks = []
        for cursor, event in page:
            tree_child = (
                getattr(event, "parent_run_id", None) is not None
                or getattr(event, "root_run_id", event.run_id)
                not in (None, event.run_id)
            )
            if event.run_id in archived_ids or (
                tree_child
                and not (
                    event.visibility is OutputVisibility.PUBLIC
                    and event.channel is OutputChannel.OPERATION
                )
            ):
                continue
            chunk = canonical_output_to_sse_chunk(event)
            if chunk is not None:
                chunks.append({
                    "cursor": cursor,
                    "runId": event.run_id,
                    "chunk": chunk,
                    "createdAt": event.emitted_at.isoformat(),
                })
        return {
            "kind": "analysis_events",
            "chunks": chunks,
            "nextCursor": page[-1][0] if page else after,
            "hasMore": len(rows) > limit,
            "projectionVersion": version,
            **(
                {
                    "runs": self._runs,
                    "publishedId": (published or {}).get("id"),
                }
                if changed else {}
            ),
        }


__all__ = ["VersionedNovelAnalysisStreamQuery"]
