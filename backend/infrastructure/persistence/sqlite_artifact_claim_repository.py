"""Atomic exclusive writer claims for Work Item-scoped Artifacts."""

from __future__ import annotations

from collections.abc import Callable
from time import time
from typing import Any
from uuid import uuid4

from purra.artifacts.continuity import (
    ArtifactClaimLeaseCommand,
    ArtifactWriteClaim,
    ArtifactWriteClaimCommand,
)
from purra.artifacts.errors import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    ArtifactStateError,
)


def _now_ms() -> int:
    return int(time() * 1_000)


class SqliteArtifactClaimRepository:
    """One durable, expiring writer lease per Work Item Artifact."""

    def __init__(
        self,
        db,
        *,
        clock: Callable[[], int] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._db = db
        self._clock = clock or _now_ms
        self._token_factory = token_factory or (
            lambda: f"artifact_claim_{uuid4().hex}"
        )

    async def acquire(
        self,
        command: ArtifactWriteClaimCommand,
    ) -> ArtifactWriteClaim:
        async with self._db.transaction(cancellation_linearizable=True):
            # Read time only after this transaction owns SQLite's write slot.
            # A caller may have waited behind another writer long enough for
            # the previously active claim to expire.
            now = int(self._clock())
            expires_at = now + command.lease_duration_ms
            artifact = await self._require_writable_artifact(
                artifact_id=command.artifact_id,
                work_item_id=command.work_item_id,
                run_id=command.run_id,
            )
            actual_revision = int(artifact.get("revision") or 0)
            if actual_revision != command.expected_revision:
                raise ArtifactConflictError(
                    "artifact revision does not match",
                    code="artifact_revision_conflict",
                    details={
                        "expectedRevision": command.expected_revision,
                        "actualRevision": actual_revision,
                    },
                )
            current = await self._db.fetch_one(
                "SELECT * FROM ai_agent_artifact_claims WHERE artifact_id = ?",
                [command.artifact_id],
            )
            if current is not None and int(current["expires_at_ms"]) > now:
                if str(current["run_id"]) != command.run_id:
                    raise ArtifactConflictError(
                        "artifact already has an active writer",
                        code="artifact_claim_conflict",
                        details={
                            "artifactId": command.artifact_id,
                            "workItemId": command.work_item_id,
                            "holderRunId": str(current["run_id"]),
                            "expiresAtMs": int(current["expires_at_ms"]),
                        },
                    )
                expires_at = max(expires_at, int(current["expires_at_ms"]))
                await self._db.execute(
                    "UPDATE ai_agent_artifact_claims SET acquired_revision = ?, "
                    "expires_at_ms = ?, update_time = CURRENT_TIMESTAMP "
                    "WHERE artifact_id = ? AND run_id = ? AND claim_token = ?",
                    [
                        actual_revision,
                        expires_at,
                        command.artifact_id,
                        command.run_id,
                        str(current["claim_token"]),
                    ],
                )
                refreshed = await self._db.fetch_one(
                    "SELECT * FROM ai_agent_artifact_claims WHERE artifact_id = ?",
                    [command.artifact_id],
                )
                return _require_claim(refreshed, command.artifact_id)

            token = str(self._token_factory() or "").strip()
            if not token:
                raise RuntimeError("artifact claim token factory returned empty token")
            await self._db.execute(
                "INSERT INTO ai_agent_artifact_claims "
                "(artifact_id, work_item_id, run_id, claim_token, "
                "acquired_revision, expires_at_ms) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(artifact_id) DO UPDATE SET "
                "work_item_id = excluded.work_item_id, "
                "run_id = excluded.run_id, claim_token = excluded.claim_token, "
                "acquired_revision = excluded.acquired_revision, "
                "expires_at_ms = excluded.expires_at_ms, "
                "create_time = CURRENT_TIMESTAMP, "
                "update_time = CURRENT_TIMESTAMP",
                [
                    command.artifact_id,
                    command.work_item_id,
                    command.run_id,
                    token,
                    actual_revision,
                    expires_at,
                ],
            )
            persisted = await self._db.fetch_one(
                "SELECT * FROM ai_agent_artifact_claims WHERE artifact_id = ?",
                [command.artifact_id],
            )
            return _require_claim(persisted, command.artifact_id)

    async def load_active(
        self,
        artifact_id: str,
    ) -> ArtifactWriteClaim | None:
        normalized_id = _required_text(artifact_id, "artifact id")
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_artifact_claims "
            "WHERE artifact_id = ? AND expires_at_ms > ?",
            [normalized_id, int(self._clock())],
        )
        return _claim(row) if row is not None else None

    async def renew(
        self,
        command: ArtifactClaimLeaseCommand,
    ) -> ArtifactWriteClaim:
        if command.lease_duration_ms is None:
            raise ValueError("renew requires lease_duration_ms")
        async with self._db.transaction(cancellation_linearizable=True):
            now = int(self._clock())
            current = await self._db.fetch_one(
                "SELECT * FROM ai_agent_artifact_claims WHERE artifact_id = ?",
                [command.artifact_id],
            )
            self._require_live_owner(current, command, now=now)
            assert current is not None
            artifact = await self._require_writable_artifact(
                artifact_id=command.artifact_id,
                work_item_id=str(current["work_item_id"]),
                run_id=command.run_id,
            )
            revision = int(artifact.get("revision") or 0)
            expires_at = max(
                now + command.lease_duration_ms,
                int(current["expires_at_ms"]),
            )
            await self._db.execute(
                "UPDATE ai_agent_artifact_claims SET acquired_revision = ?, "
                "expires_at_ms = ?, update_time = CURRENT_TIMESTAMP "
                "WHERE artifact_id = ? AND run_id = ? AND claim_token = ? "
                "AND expires_at_ms > ?",
                [
                    revision,
                    expires_at,
                    command.artifact_id,
                    command.run_id,
                    command.claim_token,
                    now,
                ],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            if int((changed or {}).get("count") or 0) != 1:
                raise ArtifactConflictError(
                    "artifact claim changed during renewal",
                    code="artifact_claim_conflict",
                    details={"artifactId": command.artifact_id},
                )
            renewed = await self._db.fetch_one(
                "SELECT * FROM ai_agent_artifact_claims WHERE artifact_id = ?",
                [command.artifact_id],
            )
            return _require_claim(renewed, command.artifact_id)

    async def release(self, command: ArtifactClaimLeaseCommand) -> bool:
        if command.lease_duration_ms is not None:
            raise ValueError("release cannot include lease_duration_ms")
        async with self._db.transaction(cancellation_linearizable=True):
            await self._db.execute(
                "DELETE FROM ai_agent_artifact_claims WHERE artifact_id = ? "
                "AND run_id = ? AND claim_token = ?",
                [command.artifact_id, command.run_id, command.claim_token],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            return int((changed or {}).get("count") or 0) == 1

    async def release_for_run(self, run_id: str) -> int:
        normalized_run = _required_text(run_id, "run id")
        async with self._db.transaction(cancellation_linearizable=True):
            await self._db.execute(
                "DELETE FROM ai_agent_artifact_claims WHERE run_id = ?",
                [normalized_run],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            return int((changed or {}).get("count") or 0)

    async def _require_writable_artifact(
        self,
        *,
        artifact_id: str,
        work_item_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        artifact = await self._db.fetch_one(
            "SELECT * FROM ai_agent_artifacts WHERE id = ?",
            [artifact_id],
        )
        if artifact is None:
            raise ArtifactNotFoundError(
                "artifact does not exist",
                code="artifact_not_found",
                details={"artifactId": artifact_id},
            )
        if (
            str(artifact.get("artifact_scope") or "run") != "work_item"
            or str(artifact.get("work_item_id") or "") != work_item_id
        ):
            raise ArtifactStateError(
                "artifact is not owned by this Work Item",
                code="artifact_work_item_scope_mismatch",
                details={
                    "artifactId": artifact_id,
                    "workItemId": work_item_id,
                },
            )
        if str(artifact.get("status") or "") != "open":
            raise ArtifactStateError(
                "artifact is not open",
                code="artifact_not_open",
                details={"status": str(artifact.get("status") or "")},
            )
        work_item = await self._db.fetch_one(
            "SELECT namespace, owner_id, status FROM ai_agent_work_items "
            "WHERE id = ?",
            [work_item_id],
        )
        if work_item is None:
            raise ArtifactStateError(
                "artifact Work Item does not exist",
                code="artifact_work_item_not_found",
                details={"workItemId": work_item_id},
            )
        if (
            str(work_item.get("namespace") or "")
            != str(artifact.get("namespace") or "")
            or str(work_item.get("owner_id") or "")
            != str(artifact.get("owner_id") or "")
        ):
            raise ArtifactStateError(
                "artifact and Work Item ownership do not match",
                code="artifact_work_item_owner_mismatch",
                details={"workItemId": work_item_id},
            )
        if str(work_item.get("status") or "") != "open":
            raise ArtifactStateError(
                "artifact Work Item is not open",
                code="artifact_work_item_not_open",
                details={"status": str(work_item.get("status") or "")},
            )
        link = await self._db.fetch_one(
            "SELECT relation FROM ai_agent_work_item_runs "
            "WHERE work_item_id = ? AND run_id = ?",
            [work_item_id, run_id],
        )
        if str((link or {}).get("relation") or "") not in {
            "created",
            "continuation",
        }:
            raise ArtifactStateError(
                "Run cannot write this Work Item",
                code="artifact_claim_run_not_linked",
                details={"workItemId": work_item_id, "runId": run_id},
            )
        return artifact

    @staticmethod
    def _require_live_owner(
        row: dict[str, Any] | None,
        command: ArtifactClaimLeaseCommand,
        *,
        now: int,
    ) -> None:
        if row is None:
            raise ArtifactConflictError(
                "artifact claim does not exist",
                code="artifact_claim_not_found",
                details={"artifactId": command.artifact_id},
            )
        if (
            str(row.get("run_id") or "") != command.run_id
            or str(row.get("claim_token") or "") != command.claim_token
        ):
            raise ArtifactConflictError(
                "artifact claim is owned by another lease",
                code="artifact_claim_owner_mismatch",
                details={"artifactId": command.artifact_id},
            )
        if int(row.get("expires_at_ms") or 0) <= now:
            raise ArtifactConflictError(
                "artifact claim has expired",
                code="artifact_claim_expired",
                details={"artifactId": command.artifact_id},
            )


def _claim(row: dict[str, Any]) -> ArtifactWriteClaim:
    return ArtifactWriteClaim(
        artifact_id=str(row["artifact_id"]),
        work_item_id=str(row["work_item_id"]),
        run_id=str(row["run_id"]),
        claim_token=str(row["claim_token"]),
        acquired_revision=int(row["acquired_revision"]),
        expires_at_ms=int(row["expires_at_ms"]),
    )


def _require_claim(
    row: dict[str, Any] | None,
    artifact_id: str,
) -> ArtifactWriteClaim:
    if row is None:
        raise RuntimeError(f"artifact claim {artifact_id} was not persisted")
    return _claim(row)


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


__all__ = ["SqliteArtifactClaimRepository"]
