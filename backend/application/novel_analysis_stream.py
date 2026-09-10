"""Revision-scoped analysis output: root, dynamic units and resumed Runs."""

from application.agent_event_stream import projection_version
from application.sse_mapping import canonical_output_to_sse_chunk
from exceptions import NotFoundError


class NovelAnalysisStreamQuery:
    def __init__(self, db, *, output_repository, analysis_service):
        self._db = db
        self._output = output_repository
        self._analysis = analysis_service
        self._version = None
        self._runs = []

    async def read_page(self, revision_id, *, after=0, limit=500):
        if await self._db.fetch_one(
            "SELECT id FROM novel_source_revisions WHERE id = ?", [revision_id],
        ) is None:
            raise NotFoundError("来源版本不存在")
        state = await self._db.fetch_all(
            "SELECT r.id, r.status, r.binding_command_id, sr.replacement_command_id, t.id AS task_id, "
            "t.status AS task_status, t.revision AS task_revision "
            "FROM ai_agent_runs r "
            "LEFT JOIN novel_analysis_superseded_runs sr ON sr.run_id=r.id "
            "LEFT JOIN ai_agent_long_task_runs tr ON tr.run_id = r.id "
            "LEFT JOIN ai_agent_long_tasks t ON t.id = tr.task_id "
            "WHERE r.binding_aggregate_id = ? AND r.binding_namespace "
            "IN ('novel_source_analysis', 'novel_source_analysis.unit') ORDER BY r.rowid",
            [revision_id],
        )
        units = await self._db.fetch_all(
            "SELECT u.task_id, u.unit_id, u.status, u.attempt, u.run_id, u.output_ref "
            "FROM ai_agent_long_task_units u WHERE u.task_id IN ("
            "SELECT tr.task_id FROM ai_agent_long_task_runs tr "
            "JOIN ai_agent_runs r ON r.id = tr.run_id "
            "WHERE r.binding_namespace = 'novel_source_analysis' AND r.binding_aggregate_id = ?) "
            "ORDER BY u.task_id, u.position", [revision_id],
        )
        published = await self._db.fetch_one(
            "SELECT id FROM novel_source_analyses WHERE source_revision_id = ? "
            "ORDER BY version_no DESC LIMIT 1", [revision_id],
        )
        version = projection_version([state, units, published])
        changed = version != self._version
        if changed:
            self._runs = await self._analysis.list_for_revision(revision_id)
            self._version = version
        rows = await self._output.list_bound_events(
            aggregate_id=revision_id,
            namespaces=("novel_source_analysis", "novel_source_analysis.unit"),
            after_cursor=after, limit=limit + 1,
        )
        page = rows[:limit]
        chunks = []
        known_ids = {row['id'] for row in state}
        visible_ids = {identity for run in self._runs
                       for identity in [run['runId'], *(item['runId'] for item in run.get('relatedRuns', []))]}
        for cursor, event in page:
            # Advance the cursor over archived outputs without replaying them.
            # An event created after the state read remains available for the
            # client's dynamic-membership catch-up.
            if event.run_id in known_ids and event.run_id not in visible_ids:
                continue
            chunk = canonical_output_to_sse_chunk(event)
            if chunk is not None:
                chunks.append({"cursor": cursor, "runId": event.run_id,
                               "chunk": chunk, "createdAt": event.emitted_at.isoformat()})
        return {
            "kind": "analysis_events", "chunks": chunks,
            "nextCursor": page[-1][0] if page else after,
            "hasMore": len(rows) > limit, "projectionVersion": version,
            **({"runs": self._runs, "publishedId": (published or {}).get("id")}
               if changed else {}),
        }
