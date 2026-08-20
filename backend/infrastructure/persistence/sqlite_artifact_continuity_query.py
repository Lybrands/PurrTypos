"""SQLite candidate discovery and host authorization for Artifact continuity."""

from __future__ import annotations

from application.artifact_continuity import ArtifactContinuityRecord
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)


class SqliteArtifactContinuityQuery:
    def __init__(self, db) -> None:
        self._db = db
        self._artifacts = SqliteArtifactRepository(db)

    async def list_open_candidates(
        self,
        *,
        namespace: str,
        owner_id: str,
        session_id: str | int,
        limit: int,
    ) -> tuple[ArtifactContinuityRecord, ...]:
        rows = await self._db.fetch_all(
            "SELECT a.id FROM ai_agent_artifacts AS a "
            "JOIN ai_agent_runs AS creator ON creator.id = a.created_by_run_id "
            "WHERE a.namespace = ? AND a.owner_id = ? "
            "AND a.status IN ('open', 'finalized') "
            "AND creator.session_id = ? "
            "AND NOT EXISTS (SELECT 1 FROM ai_agent_artifact_projections AS p "
            "WHERE p.artifact_id = a.id) "
            "ORDER BY a.update_time DESC, a.id ASC LIMIT ?",
            [
                str(namespace or "").strip(),
                str(owner_id or "").strip(),
                session_id,
                max(1, min(int(limit), 32)),
            ],
        )
        records: list[ArtifactContinuityRecord] = []
        for row in rows:
            artifact = await self._artifacts.load(str(row["id"]))
            if artifact is not None:
                records.append(ArtifactContinuityRecord(artifact=artifact))
        return tuple(records)

    async def list_batches(self, artifact_id: str):
        return await self._artifacts.list_batches(artifact_id)


class SqliteSessionArtifactAuthorizer:
    """Allow cross-Run access only inside one persisted host session."""

    def __init__(self, db) -> None:
        self._db = db

    async def authorize(self, candidate, request) -> bool:
        row = await self._db.fetch_one(
            "SELECT source.session_id AS source_session, "
            "target.session_id AS target_session "
            "FROM ai_agent_runs AS source JOIN ai_agent_runs AS target "
            "WHERE source.id = ? AND target.id = ?",
            [candidate.created_by_run_id, request.run_id],
        )
        return bool(
            row is not None
            and row.get("source_session") is not None
            and row.get("source_session") == row.get("target_session")
        )


__all__ = ["SqliteArtifactContinuityQuery", "SqliteSessionArtifactAuthorizer"]
