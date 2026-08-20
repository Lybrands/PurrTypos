"""Atomic SQLite maintenance for Artifact leases and retained content."""

from __future__ import annotations

from time import time

from purra.artifacts.maintenance import (
    ArtifactMaintenancePolicy,
    ArtifactMaintenanceReport,
    ArtifactMaintenanceSnapshot,
)


def _now_ms() -> int:
    return int(time() * 1_000)


class SqliteArtifactMaintenanceRepository:
    def __init__(self, db) -> None:
        self._db = db

    async def maintain(
        self,
        policy: ArtifactMaintenancePolicy,
        *,
        timestamp_ms: int | None = None,
    ) -> ArtifactMaintenanceReport:
        if not isinstance(policy, ArtifactMaintenancePolicy):
            raise TypeError("artifact maintenance policy is invalid")
        checked_at = _timestamp(timestamp_ms)
        async with self._db.transaction(cancellation_linearizable=True):
            expired = await self._delete_and_count(
                "DELETE FROM ai_agent_artifact_claims WHERE expires_at_ms <= ?",
                [checked_at],
            )
            unavailable = await self._delete_and_count(
                "DELETE FROM ai_agent_artifact_claims AS c WHERE NOT EXISTS ("
                "SELECT 1 FROM ai_agent_runs AS r WHERE r.id = c.run_id "
                "AND r.status = 'running')"
            )
            invalid = await self._delete_and_count(
                "DELETE FROM ai_agent_artifact_claims AS c WHERE NOT EXISTS ("
                "SELECT 1 FROM ai_agent_artifacts AS a "
                "WHERE a.id = c.artifact_id AND a.status = 'open' "
                "AND a.revision = c.acquired_revision)"
            )
            purged = 0
            if policy.terminal_retention_ms is not None:
                purged = await self._purge_artifacts(
                    cutoff_ms=checked_at - policy.terminal_retention_ms,
                    limit=policy.max_purge_artifacts,
                )
            issues = await self._count_consistency_issues()
        return ArtifactMaintenanceReport(
            expired_claims_released=expired,
            unavailable_run_claims_released=unavailable,
            invalid_target_claims_released=invalid,
            purged_artifacts=purged,
            consistency_issues=issues,
        )

    async def inspect(
        self,
        *,
        run_id: str | None = None,
        timestamp_ms: int | None = None,
    ) -> ArtifactMaintenanceSnapshot:
        checked_at = _timestamp(timestamp_ms)
        normalized_run = str(run_id or "").strip() or None
        artifact_filter = "1 = 1"
        artifact_params: list[str] = []
        claim_filter = "1 = 1"
        claim_params: list[str] = []
        if normalized_run is not None:
            artifact_filter = (
                "(a.created_by_run_id = ? OR EXISTS ("
                "SELECT 1 FROM ai_agent_artifact_claims AS linked "
                "WHERE linked.artifact_id = a.id AND linked.run_id = ?))"
            )
            artifact_params = [normalized_run, normalized_run]
            claim_filter = "c.run_id = ?"
            claim_params = [normalized_run]
        async with self._db.transaction(write=False):
            artifacts = await self._status_counts(
                "SELECT a.status AS status, COUNT(*) AS count "
                "FROM ai_agent_artifacts AS a WHERE "
                f"{artifact_filter} GROUP BY a.status",
                artifact_params,
            )
            claim_rows = await self._db.fetch_all(
                "SELECT claim_state, COUNT(*) AS count FROM (SELECT CASE "
                "WHEN c.expires_at_ms <= ? THEN 'expired' "
                "WHEN NOT EXISTS (SELECT 1 FROM ai_agent_runs AS r "
                "WHERE r.id = c.run_id AND r.status = 'running') "
                "THEN 'unavailable_run' "
                "WHEN a.id IS NULL OR a.status <> 'open' "
                "OR a.revision <> c.acquired_revision "
                "THEN 'invalid_target' ELSE 'active' END AS claim_state "
                "FROM ai_agent_artifact_claims AS c "
                "LEFT JOIN ai_agent_artifacts AS a ON a.id = c.artifact_id "
                f"WHERE {claim_filter}) GROUP BY claim_state",
                [checked_at, *claim_params],
            )
            claims = {
                str(row.get("claim_state") or ""): int(row.get("count") or 0)
                for row in claim_rows
            }
            issues = await self._count_consistency_issues(run_id=normalized_run)
        return ArtifactMaintenanceSnapshot(
            checked_at_ms=checked_at,
            scope_run_id=normalized_run,
            open_artifacts=artifacts.get("open", 0),
            finalized_artifacts=artifacts.get("finalized", 0),
            aborted_artifacts=artifacts.get("aborted", 0),
            unknown_artifacts=sum(
                count
                for status, count in artifacts.items()
                if status not in {"open", "finalized", "aborted"}
            ),
            active_claims=claims.get("active", 0),
            expired_claims=claims.get("expired", 0),
            unavailable_run_claims=claims.get("unavailable_run", 0),
            invalid_target_claims=claims.get("invalid_target", 0),
            consistency_issues=issues,
        )

    async def _purge_artifacts(self, *, cutoff_ms: int, limit: int) -> int:
        rows = await self._db.fetch_all(
            "SELECT a.id FROM ai_agent_artifacts AS a "
            "WHERE a.status IN ('finalized', 'aborted') "
            "AND CAST(strftime('%s', a.update_time) AS INTEGER) * 1000 <= ? "
            "AND NOT EXISTS (SELECT 1 FROM ai_agent_artifact_claims AS c "
            "WHERE c.artifact_id = a.id) "
            "ORDER BY a.update_time ASC, a.id ASC LIMIT ?",
            [cutoff_ms, limit],
        )
        artifact_ids = tuple(str(row["id"]) for row in rows)
        if not artifact_ids:
            return 0
        placeholders = ",".join("?" for _ in artifact_ids)
        for table in (
            "ai_agent_artifact_projections",
            "ai_agent_artifact_batches",
            "ai_agent_artifact_claims",
        ):
            await self._db.execute(
                f"DELETE FROM {table} WHERE artifact_id IN ({placeholders})",
                list(artifact_ids),
            )
        await self._db.execute(
            f"DELETE FROM ai_agent_artifacts WHERE id IN ({placeholders})",
            list(artifact_ids),
        )
        return len(artifact_ids)

    async def _count_consistency_issues(
        self,
        *,
        run_id: str | None = None,
    ) -> int:
        run_filter = ""
        params: list[str] = []
        if run_id is not None:
            run_filter = "AND a.created_by_run_id = ?"
            params.append(run_id)
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_artifacts AS a "
            "WHERE (a.status NOT IN ('open', 'finalized', 'aborted') "
            "OR TRIM(a.owner_ref_kind) = '' OR TRIM(a.owner_ref_id) = '' "
            "OR TRIM(a.created_by_run_id) = '') "
            f"{run_filter}",
            params,
        )
        return int((row or {}).get("count") or 0)

    async def _status_counts(
        self,
        sql: str,
        params: list[str],
    ) -> dict[str, int]:
        rows = await self._db.fetch_all(sql, params)
        return {
            str(row.get("status") or ""): int(row.get("count") or 0)
            for row in rows
        }

    async def _delete_and_count(
        self,
        sql: str,
        params: list[int] | None = None,
    ) -> int:
        await self._db.execute(sql, params or [])
        row = await self._db.fetch_one("SELECT changes() AS count")
        return int((row or {}).get("count") or 0)


def _timestamp(value: int | None) -> int:
    checked_at = _now_ms() if value is None else int(value)
    if checked_at < 0:
        raise ValueError("artifact maintenance timestamp must be non-negative")
    return checked_at


__all__ = ["SqliteArtifactMaintenanceRepository"]
