"""Atomic exclusive writer claims for durable Artifacts."""

from __future__ import annotations

from collections.abc import Callable
from time import time
from uuid import uuid4

from purra.artifacts import (
    ArtifactClaimLeaseCommand,
    ArtifactWriteClaim,
    ArtifactWriteClaimCommand,
)
from purra.artifacts import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    ArtifactStateError,
)


def _now_ms() -> int:
    return int(time() * 1_000)


class SqliteArtifactClaimRepository:
    """One durable, expiring writer lease per Artifact.

    Cross-Run authorization belongs to the host authorizer used by PurrA's
    access controller. This repository only linearizes lease ownership and
    optimistic revision checks.
    """

    def __init__(
        self,
        db,
        *,
        clock: Callable[[], int] | None = None,
        token_factory: Callable[[], str] | None = None,
        join_ambient_transaction: bool = False,
    ) -> None:
        self._db = db
        self._clock = clock or _now_ms
        self._token_factory = token_factory or (
            lambda: f"artifact_claim_{uuid4().hex}"
        )
        self._join_ambient_transaction = bool(join_ambient_transaction)

    def _mutation_transaction(self):
        if self._join_ambient_transaction:
            if not self._db.current_task_owns_transaction():
                raise RuntimeError(
                    "ambient artifact claim repository requires an owning transaction"
                )
            return self._db.transaction()
        return self._db.transaction(cancellation_linearizable=True)

    async def acquire(
        self,
        command: ArtifactWriteClaimCommand,
    ) -> ArtifactWriteClaim:
        async with self._mutation_transaction():
            now = int(self._clock())
            artifact = await self._require_open_artifact(command.artifact_id)
            revision = int(artifact.get("revision") or 0)
            if revision != command.expected_revision:
                raise ArtifactConflictError(
                    "artifact revision does not match",
                    code="artifact_revision_conflict",
                    details={
                        "expectedRevision": command.expected_revision,
                        "actualRevision": revision,
                    },
                )
            current = await self._db.fetch_one(
                "SELECT * FROM ai_agent_artifact_claims WHERE artifact_id = ?",
                [command.artifact_id],
            )
            expires_at = now + command.lease_duration_ms
            if current is not None and int(current["expires_at_ms"]) > now:
                if str(current["run_id"]) != command.run_id:
                    raise ArtifactConflictError(
                        "artifact already has an active writer",
                        code="artifact_claim_conflict",
                        details={
                            "artifactId": command.artifact_id,
                            "holderRunId": str(current["run_id"]),
                            "expiresAtMs": int(current["expires_at_ms"]),
                        },
                    )
                if int(current["acquired_revision"]) != revision:
                    raise ArtifactConflictError(
                        "artifact claim revision is stale",
                        code="artifact_claim_revision_conflict",
                        details={"artifactId": command.artifact_id},
                    )
                return _claim(current)

            token = str(self._token_factory() or "").strip()
            if not token:
                raise RuntimeError("artifact claim token factory returned empty token")
            await self._db.execute(
                "INSERT INTO ai_agent_artifact_claims "
                "(artifact_id, run_id, claim_token, acquired_revision, expires_at_ms) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(artifact_id) DO UPDATE SET "
                "run_id = excluded.run_id, claim_token = excluded.claim_token, "
                "acquired_revision = excluded.acquired_revision, "
                "expires_at_ms = excluded.expires_at_ms, "
                "create_time = CURRENT_TIMESTAMP, update_time = CURRENT_TIMESTAMP",
                [command.artifact_id, command.run_id, token, revision, expires_at],
            )
            return _require_claim(
                await self._db.fetch_one(
                    "SELECT * FROM ai_agent_artifact_claims WHERE artifact_id = ?",
                    [command.artifact_id],
                ),
                command.artifact_id,
            )

    async def load_active(self, artifact_id: str) -> ArtifactWriteClaim | None:
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
        async with self._mutation_transaction():
            now = int(self._clock())
            current = await self._db.fetch_one(
                "SELECT * FROM ai_agent_artifact_claims WHERE artifact_id = ?",
                [command.artifact_id],
            )
            self._require_live_owner(current, command, now=now)
            artifact = await self._require_open_artifact(command.artifact_id)
            assert current is not None
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
                    int(artifact.get("revision") or 0),
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
            return _require_claim(
                await self._db.fetch_one(
                    "SELECT * FROM ai_agent_artifact_claims WHERE artifact_id = ?",
                    [command.artifact_id],
                ),
                command.artifact_id,
            )

    async def release(self, command: ArtifactClaimLeaseCommand) -> bool:
        if command.lease_duration_ms is not None:
            raise ValueError("release cannot include lease_duration_ms")
        async with self._mutation_transaction():
            await self._db.execute(
                "DELETE FROM ai_agent_artifact_claims WHERE artifact_id = ? "
                "AND run_id = ? AND claim_token = ?",
                [command.artifact_id, command.run_id, command.claim_token],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            return int((changed or {}).get("count") or 0) == 1

    async def release_for_run(self, run_id: str) -> int:
        normalized_run = _required_text(run_id, "run id")
        async with self._mutation_transaction():
            await self._db.execute(
                "DELETE FROM ai_agent_artifact_claims WHERE run_id = ?",
                [normalized_run],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            return int((changed or {}).get("count") or 0)

    async def _require_open_artifact(self, artifact_id: str) -> dict:
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
        if str(artifact.get("status") or "") != "open":
            raise ArtifactStateError(
                "artifact is not open",
                code="artifact_not_open",
                details={"status": str(artifact.get("status") or "")},
            )
        return artifact

    @staticmethod
    def _require_live_owner(
        row: dict | None,
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


def _claim(row: dict) -> ArtifactWriteClaim:
    return ArtifactWriteClaim(
        artifact_id=str(row["artifact_id"]),
        run_id=str(row["run_id"]),
        claim_token=str(row["claim_token"]),
        acquired_revision=int(row["acquired_revision"]),
        expires_at_ms=int(row["expires_at_ms"]),
    )


def _require_claim(row: dict | None, artifact_id: str) -> ArtifactWriteClaim:
    if row is None:
        raise RuntimeError(f"artifact claim {artifact_id} was not persisted")
    return _claim(row)


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


__all__ = ["SqliteArtifactClaimRepository"]
