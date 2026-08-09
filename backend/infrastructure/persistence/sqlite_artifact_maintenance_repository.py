"""Atomic SQLite maintenance for Artifact leases and retained content."""

from __future__ import annotations

from time import time

from agent_core.artifacts.maintenance import (
    ArtifactMaintenancePolicy,
    ArtifactMaintenanceReport,
    ArtifactMaintenanceSnapshot,
)


def _now_ms() -> int:
    return int(time() * 1_000)


class SqliteArtifactMaintenanceRepository:
    """Repair disposable lease state without guessing task semantics.

    Every sweep is one cancellation-linearizable write transaction. This keeps
    claim cleanup, eligibility checks and optional retention deletion from
    racing with Artifact append/finalize transactions on the same database.
    """

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
        checked_at = _now_ms() if timestamp_ms is None else int(timestamp_ms)
        if checked_at < 0:
            raise ValueError(
                "artifact maintenance timestamp must be non-negative"
            )

        async with self._db.transaction(cancellation_linearizable=True):
            expired = await self._delete_and_count(
                "DELETE FROM ai_agent_artifact_claims WHERE expires_at_ms <= ?",
                [checked_at],
            )
            unavailable = await self._delete_and_count(
                "DELETE FROM ai_agent_artifact_claims AS c WHERE "
                "NOT EXISTS (SELECT 1 FROM ai_agent_runs AS r "
                "WHERE r.id = c.run_id AND r.status = 'running')"
            )
            invalid = await self._delete_and_count(
                "DELETE FROM ai_agent_artifact_claims AS c WHERE "
                "NOT EXISTS ("
                "SELECT 1 FROM ai_agent_artifacts AS a "
                "JOIN ai_agent_work_items AS w ON w.id = a.work_item_id "
                "JOIN ai_agent_work_item_runs AS wir "
                "ON wir.work_item_id = w.id AND wir.run_id = c.run_id "
                "WHERE a.id = c.artifact_id "
                "AND a.artifact_scope = 'work_item' "
                "AND a.work_item_id = c.work_item_id "
                "AND a.namespace = w.namespace "
                "AND a.owner_id = w.owner_id "
                "AND a.status = 'open' AND w.status = 'open' "
                "AND a.revision = c.acquired_revision "
                "AND wir.relation IN ('created', 'continuation'))"
            )

            purged_work_items = 0
            purged_artifacts = 0
            if policy.terminal_retention_ms is not None:
                cutoff = checked_at - policy.terminal_retention_ms
                item_count, item_artifacts = await self._purge_work_items(
                    cutoff_ms=cutoff,
                    limit=policy.max_purge_work_items,
                )
                run_artifacts = await self._purge_run_artifacts(
                    cutoff_ms=cutoff,
                    limit=policy.max_purge_run_artifacts,
                )
                purged_work_items = item_count
                purged_artifacts = item_artifacts + run_artifacts

            issues = await self._count_consistency_issues()

        return ArtifactMaintenanceReport(
            expired_claims_released=expired,
            unavailable_run_claims_released=unavailable,
            invalid_target_claims_released=invalid,
            purged_work_items=purged_work_items,
            purged_artifacts=purged_artifacts,
            consistency_issues=issues,
        )

    async def inspect(
        self,
        *,
        run_id: str | None = None,
        timestamp_ms: int | None = None,
    ) -> ArtifactMaintenanceSnapshot:
        checked_at = _now_ms() if timestamp_ms is None else int(timestamp_ms)
        if checked_at < 0:
            raise ValueError(
                "artifact maintenance timestamp must be non-negative"
            )
        normalized_run = None
        if run_id is not None:
            normalized_run = str(run_id or "").strip()
            if not normalized_run:
                raise ValueError("artifact maintenance Run id is required")

        artifact_filter, artifact_params = _artifact_scope_filter(
            "a",
            normalized_run,
        )
        work_item_filter, work_item_params = _work_item_scope_filter(
            "w",
            normalized_run,
        )
        claim_filter, claim_params = _claim_scope_filter(normalized_run)
        async with self._db.transaction(write=False):
            work_items = await self._status_counts(
                "SELECT w.status AS status, COUNT(*) AS count "
                "FROM ai_agent_work_items AS w "
                f"WHERE {work_item_filter} GROUP BY w.status",
                work_item_params,
            )
            artifacts = await self._status_counts(
                "SELECT a.status AS status, COUNT(*) AS count "
                "FROM ai_agent_artifacts AS a "
                f"WHERE {artifact_filter} GROUP BY a.status",
                artifact_params,
            )
            claim_rows = await self._db.fetch_all(
                "SELECT claim_state, COUNT(*) AS count FROM ("
                "SELECT CASE "
                "WHEN c.expires_at_ms <= ? THEN 'expired' "
                "WHEN NOT EXISTS (SELECT 1 FROM ai_agent_runs AS r "
                "WHERE r.id = c.run_id AND r.status = 'running') "
                "THEN 'unavailable_run' "
                "WHEN a.id IS NULL OR a.artifact_scope <> 'work_item' "
                "OR NOT EXISTS ("
                "SELECT 1 FROM ai_agent_work_items AS w "
                "JOIN ai_agent_work_item_runs AS wir "
                "ON wir.work_item_id = w.id AND wir.run_id = c.run_id "
                "WHERE w.id = a.work_item_id "
                "AND a.work_item_id = c.work_item_id "
                "AND a.namespace = w.namespace AND a.owner_id = w.owner_id "
                "AND a.status = 'open' AND w.status = 'open' "
                "AND a.revision = c.acquired_revision "
                "AND wir.relation IN ('created', 'continuation')) "
                "THEN 'invalid_target' ELSE 'active' END AS claim_state "
                "FROM ai_agent_artifact_claims AS c "
                "LEFT JOIN ai_agent_artifacts AS a ON a.id = c.artifact_id "
                f"WHERE {claim_filter}) GROUP BY claim_state",
                [checked_at, *claim_params],
            )
            claims = {
                str(row.get("claim_state") or ""): int(
                    row.get("count") or 0
                )
                for row in claim_rows
            }
            issues = await self._count_consistency_issues(
                run_id=normalized_run,
            )

        return ArtifactMaintenanceSnapshot(
            checked_at_ms=checked_at,
            scope_run_id=normalized_run,
            open_work_items=work_items.get("open", 0),
            completed_work_items=work_items.get("completed", 0),
            canceled_work_items=work_items.get("canceled", 0),
            unknown_work_items=sum(
                count
                for status, count in work_items.items()
                if status not in {"open", "completed", "canceled"}
            ),
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

    async def _purge_work_items(
        self,
        *,
        cutoff_ms: int,
        limit: int,
    ) -> tuple[int, int]:
        rows = await self._db.fetch_all(
            "SELECT w.id FROM ai_agent_work_items AS w "
            "WHERE w.status IN ('completed', 'canceled') "
            "AND CAST(strftime('%s', w.update_time) AS INTEGER) * 1000 <= ? "
            "AND EXISTS (SELECT 1 FROM ai_agent_artifacts AS a "
            "WHERE a.artifact_scope = 'work_item' AND a.work_item_id = w.id) "
            "AND NOT EXISTS (SELECT 1 FROM ai_agent_artifacts AS a "
            "WHERE a.work_item_id = w.id AND ("
            "a.artifact_scope <> 'work_item' OR a.status = 'open')) "
            "AND NOT EXISTS (SELECT 1 FROM ai_agent_artifact_claims AS c "
            "WHERE c.work_item_id = w.id) "
            "AND NOT EXISTS (SELECT 1 FROM ai_agent_work_item_runs AS wir "
            "JOIN ai_agent_runs AS r ON r.id = wir.run_id "
            "WHERE wir.work_item_id = w.id AND r.status = 'running') "
            "ORDER BY w.update_time ASC, w.id ASC LIMIT ?",
            [cutoff_ms, limit],
        )
        work_item_ids = tuple(str(row["id"]) for row in rows)
        if not work_item_ids:
            return 0, 0
        placeholders = ",".join("?" for _ in work_item_ids)
        count = await self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_artifacts "
            f"WHERE artifact_scope = 'work_item' AND work_item_id IN ({placeholders})",
            list(work_item_ids),
        )
        artifact_count = int((count or {}).get("count") or 0)
        await self._db.execute(
            "DELETE FROM ai_agent_artifact_projections WHERE artifact_id IN ("
            "SELECT id FROM ai_agent_artifacts WHERE work_item_id IN "
            f"({placeholders}))",
            list(work_item_ids),
        )
        await self._db.execute(
            "DELETE FROM ai_agent_artifact_claims WHERE work_item_id IN "
            f"({placeholders})",
            list(work_item_ids),
        )
        await self._db.execute(
            "DELETE FROM ai_agent_artifact_batches WHERE artifact_id IN ("
            "SELECT id FROM ai_agent_artifacts WHERE artifact_scope = 'work_item' "
            f"AND work_item_id IN ({placeholders}))",
            list(work_item_ids),
        )
        await self._db.execute(
            "DELETE FROM ai_agent_artifacts WHERE artifact_scope = 'work_item' "
            f"AND work_item_id IN ({placeholders})",
            list(work_item_ids),
        )
        await self._db.execute(
            "DELETE FROM ai_agent_long_task_units WHERE task_id IN ("
            "SELECT id FROM ai_agent_long_tasks WHERE work_item_id IN "
            f"({placeholders}))",
            list(work_item_ids),
        )
        await self._db.execute(
            "DELETE FROM ai_agent_long_tasks WHERE work_item_id IN "
            f"({placeholders})",
            list(work_item_ids),
        )
        await self._db.execute(
            "DELETE FROM ai_agent_work_item_runs WHERE work_item_id IN "
            f"({placeholders})",
            list(work_item_ids),
        )
        await self._db.execute(
            f"DELETE FROM ai_agent_work_items WHERE id IN ({placeholders})",
            list(work_item_ids),
        )
        return len(work_item_ids), artifact_count

    async def _purge_run_artifacts(
        self,
        *,
        cutoff_ms: int,
        limit: int,
    ) -> int:
        rows = await self._db.fetch_all(
            "SELECT a.id FROM ai_agent_artifacts AS a "
            "LEFT JOIN ai_agent_runs AS r ON r.id = a.run_id "
            "WHERE a.artifact_scope = 'run' "
            "AND a.status IN ('finalized', 'aborted') "
            "AND CAST(strftime('%s', a.update_time) AS INTEGER) * 1000 <= ? "
            "AND (r.id IS NULL OR r.status <> 'running') "
            "ORDER BY a.update_time ASC, a.id ASC LIMIT ?",
            [cutoff_ms, limit],
        )
        artifact_ids = tuple(str(row["id"]) for row in rows)
        if not artifact_ids:
            return 0
        placeholders = ",".join("?" for _ in artifact_ids)
        await self._db.execute(
            "DELETE FROM ai_agent_artifact_projections WHERE artifact_id IN "
            f"({placeholders})",
            list(artifact_ids),
        )
        await self._db.execute(
            "DELETE FROM ai_agent_artifact_claims WHERE artifact_id IN "
            f"({placeholders})",
            list(artifact_ids),
        )
        await self._db.execute(
            "DELETE FROM ai_agent_artifact_batches WHERE artifact_id IN "
            f"({placeholders})",
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
        artifact_filter, artifact_params = _artifact_scope_filter(
            "a",
            run_id,
        )
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM ai_agent_artifacts AS a "
            "LEFT JOIN ai_agent_work_items AS w ON w.id = a.work_item_id "
            "WHERE ((a.status NOT IN ('open', 'finalized', 'aborted')) "
            "OR (a.artifact_scope = 'work_item' AND ("
            "a.work_item_id IS NULL OR w.id IS NULL "
            "OR a.run_id IS NULL OR a.created_by_run_id IS NULL "
            "OR a.run_id <> a.created_by_run_id "
            "OR a.namespace <> w.namespace OR a.owner_id <> w.owner_id "
            "OR w.status NOT IN ('open', 'completed', 'canceled') "
            "OR (a.status = 'open' AND w.status <> 'open'))) "
            "OR (a.artifact_scope = 'run' AND ("
            "a.run_id IS NULL OR a.created_by_run_id IS NULL "
            "OR a.run_id <> a.created_by_run_id "
            "OR a.work_item_id IS NOT NULL)) "
            "OR a.artifact_scope NOT IN ('run', 'work_item')) "
            f"AND {artifact_filter}",
            artifact_params,
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


def _artifact_scope_filter(
    alias: str,
    run_id: str | None,
) -> tuple[str, list[str]]:
    if run_id is None:
        return "1 = 1", []
    return (
        f"({alias}.run_id = ? OR EXISTS ("
        "SELECT 1 FROM ai_agent_work_item_runs AS scope_wir "
        f"WHERE scope_wir.work_item_id = {alias}.work_item_id "
        "AND scope_wir.run_id = ?))",
        [run_id, run_id],
    )


def _work_item_scope_filter(
    alias: str,
    run_id: str | None,
) -> tuple[str, list[str]]:
    if run_id is None:
        return "1 = 1", []
    return (
        "EXISTS (SELECT 1 FROM ai_agent_work_item_runs AS scope_wir "
        f"WHERE scope_wir.work_item_id = {alias}.id "
        "AND scope_wir.run_id = ?)",
        [run_id],
    )


def _claim_scope_filter(run_id: str | None) -> tuple[str, list[str]]:
    if run_id is None:
        return "1 = 1", []
    artifact_filter, artifact_params = _artifact_scope_filter("a", run_id)
    return f"(c.run_id = ? OR {artifact_filter})", [run_id, *artifact_params]


__all__ = ["SqliteArtifactMaintenanceRepository"]
