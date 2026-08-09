"""SQLite candidate discovery for application Artifact continuity routing."""

from __future__ import annotations

from application.artifact_continuity import ArtifactContinuityRecord
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from infrastructure.persistence.sqlite_work_item_repository import (
    SqliteWorkItemRepository,
)


class SqliteArtifactContinuityQuery:
    def __init__(self, db) -> None:
        self._db = db
        self._artifacts = SqliteArtifactRepository(db)
        self._work_items = SqliteWorkItemRepository(db)

    async def list_open_candidates(
        self,
        *,
        namespace: str,
        owner_id: str,
        session_id: str | int,
        limit: int,
    ) -> tuple[ArtifactContinuityRecord, ...]:
        normalized_namespace = str(namespace or "").strip()
        normalized_owner = str(owner_id or "").strip()
        normalized_limit = max(1, min(int(limit), 32))
        if not normalized_namespace or not normalized_owner:
            return ()
        rows = await self._db.fetch_all(
            "SELECT a.id AS artifact_id, w.id AS work_item_id "
            "FROM ai_agent_artifacts AS a "
            "JOIN ai_agent_work_items AS w ON w.id = a.work_item_id "
            "WHERE a.artifact_scope = 'work_item' "
            "AND a.namespace = ? AND a.owner_id = ? "
            "AND ((a.status = 'open' AND w.status = 'open') "
            "OR (a.status = 'finalized' AND w.status = 'completed')) "
            "AND NOT EXISTS (SELECT 1 FROM ai_agent_artifact_projections AS p "
            "WHERE p.artifact_id = a.id) "
            "AND w.namespace = a.namespace AND w.owner_id = a.owner_id "
            "AND EXISTS ("
            "SELECT 1 FROM ai_agent_work_item_runs AS wr "
            "JOIN ai_agent_runs AS r ON r.id = wr.run_id "
            "WHERE wr.work_item_id = w.id AND r.session_id = ?"
            ") "
            "ORDER BY a.update_time DESC, a.id ASC LIMIT ?",
            [
                normalized_namespace,
                normalized_owner,
                session_id,
                normalized_limit,
            ],
        )
        records: list[ArtifactContinuityRecord] = []
        for row in rows:
            artifact = await self._artifacts.load(str(row["artifact_id"]))
            work_item = await self._work_items.load(str(row["work_item_id"]))
            if artifact is None or work_item is None:
                continue
            records.append(ArtifactContinuityRecord(
                artifact=artifact,
                work_item=work_item,
            ))
        return tuple(records)

    async def list_batches(self, artifact_id: str):
        return await self._artifacts.list_batches(artifact_id)


__all__ = ["SqliteArtifactContinuityQuery"]
