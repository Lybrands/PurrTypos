"""Atomic creation boundary for one durable Work Item Artifact."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import time
from typing import Any
from uuid import uuid4

from purra.artifacts import (
    ArtifactCreateCommand,
    ArtifactRecord,
    ArtifactScope,
    ArtifactStatus,
    ArtifactWriteClaim,
)
from purra.artifacts.errors import (
    ArtifactConflictError,
    ArtifactStateError,
)
from purra.json_values import thaw_json_mapping
from purra.work_items import WorkItemCreateCommand, WorkItemRecord
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from infrastructure.persistence.sqlite_work_item_repository import (
    SqliteWorkItemRepository,
)


@dataclass(frozen=True, slots=True)
class WorkItemArtifactStart:
    work_item: WorkItemRecord
    artifact: ArtifactRecord
    write_claim: ArtifactWriteClaim | None


class SqliteWorkItemArtifactLifecycle:
    """Create identity, provenance, Artifact and first claim in one commit.

    The IDs are deterministic for ``(namespace, owner, kind, creating Run)`` so
    concurrent/replayed begin calls converge even before a tool receipt exists.
    """

    def __init__(
        self,
        db,
        *,
        clock: Callable[[], int] | None = None,
        token_factory: Callable[[], str] | None = None,
        write_lease_duration_ms: int = 300_000,
    ) -> None:
        self._db = db
        self._clock = clock or (lambda: int(time() * 1_000))
        self._token_factory = token_factory or (
            lambda: f"artifact_claim_{uuid4().hex}"
        )
        self._write_lease_duration_ms = int(write_lease_duration_ms)
        if self._write_lease_duration_ms <= 0:
            raise ValueError("write claim duration must be positive")
        self._artifacts = SqliteArtifactRepository(db, clock=self._clock)
        self._work_items = SqliteWorkItemRepository(db)

    async def begin(
        self,
        command: ArtifactCreateCommand,
        *,
        work_item_metadata: Mapping[str, Any] | None = None,
    ) -> WorkItemArtifactStart:
        run_id = str(command.created_by_run_id or command.run_id or "").strip()
        if not run_id:
            raise ValueError("Work Item Artifact creation requires a Run id")
        if command.scope is ArtifactScope.WORK_ITEM:
            raise ValueError(
                "atomic Work Item Artifact creation accepts an unbound command"
            )
        work_item_id = _stable_id(
            "work_item",
            command.namespace,
            command.owner_id,
            command.kind,
            run_id,
        )
        artifact_id = _stable_id(
            "artifact",
            command.namespace,
            command.owner_id,
            command.kind,
            run_id,
        )
        work_item_command = WorkItemCreateCommand(
            namespace=command.namespace,
            kind=command.kind,
            owner_id=command.owner_id,
            created_by_run_id=run_id,
            metadata=work_item_metadata or {
                "artifactKind": command.kind,
                "scopeVersion": 1,
            },
        )
        artifact_command = ArtifactCreateCommand(
            namespace=command.namespace,
            kind=command.kind,
            owner_id=command.owner_id,
            run_id=run_id,
            schema_version=command.schema_version,
            expected_item_count=command.expected_item_count,
            metadata=command.metadata,
            scope=ArtifactScope.WORK_ITEM,
            work_item_id=work_item_id,
            created_by_run_id=run_id,
        )

        async with self._db.transaction(cancellation_linearizable=True):
            existing = await self._artifacts.find_linked_for_run(
                namespace=command.namespace,
                kind=command.kind,
                owner_id=command.owner_id,
                run_id=run_id,
            )
            if existing is not None:
                return await self._resume_existing(existing, run_id=run_id)

            await self._db.execute(
                "INSERT INTO ai_agent_work_items "
                "(id, namespace, kind, owner_id, created_by_run_id, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    work_item_id,
                    work_item_command.namespace,
                    work_item_command.kind,
                    work_item_command.owner_id,
                    run_id,
                    _json(work_item_command.metadata),
                ],
            )
            await self._db.execute(
                "INSERT INTO ai_agent_work_item_runs "
                "(work_item_id, run_id, relation, work_item_revision) "
                "VALUES (?, ?, 'created', 1)",
                [work_item_id, run_id],
            )
            await self._db.execute(
                "INSERT INTO ai_agent_artifacts "
                "(id, namespace, kind, owner_id, run_id, artifact_scope, "
                "work_item_id, created_by_run_id, schema_version, "
                "expected_item_count, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, 'work_item', ?, ?, ?, ?, ?)",
                [
                    artifact_id,
                    artifact_command.namespace,
                    artifact_command.kind,
                    artifact_command.owner_id,
                    run_id,
                    work_item_id,
                    run_id,
                    artifact_command.schema_version,
                    artifact_command.expected_item_count,
                    _json(artifact_command.metadata),
                ],
            )
            claim = await self._insert_claim(
                artifact_id=artifact_id,
                work_item_id=work_item_id,
                run_id=run_id,
                acquired_revision=1,
            )
            work_item = await self._work_items.load(work_item_id)
            artifact = await self._artifacts.load(artifact_id)
            if work_item is None or artifact is None:
                raise RuntimeError("Work Item Artifact creation was not persisted")
            return WorkItemArtifactStart(work_item, artifact, claim)

    async def _resume_existing(
        self,
        artifact: ArtifactRecord,
        *,
        run_id: str,
    ) -> WorkItemArtifactStart:
        if artifact.work_item_id is None:
            raise ArtifactStateError(
                "linked Artifact is missing its Work Item",
                code="artifact_work_item_scope_mismatch",
                details={"artifactId": artifact.id},
            )
        work_item = await self._work_items.load(artifact.work_item_id)
        if work_item is None:
            raise ArtifactStateError(
                "artifact Work Item does not exist",
                code="artifact_work_item_not_found",
                details={"workItemId": artifact.work_item_id},
            )
        if artifact.status is not ArtifactStatus.OPEN:
            return WorkItemArtifactStart(work_item, artifact, None)
        now = int(self._clock())
        current = await self._db.fetch_one(
            "SELECT * FROM ai_agent_artifact_claims WHERE artifact_id = ?",
            [artifact.id],
        )
        if current is not None and int(current["expires_at_ms"]) > now:
            if str(current["run_id"]) != run_id:
                raise ArtifactConflictError(
                    "artifact already has an active writer",
                    code="artifact_claim_conflict",
                    details={
                        "artifactId": artifact.id,
                        "holderRunId": str(current["run_id"]),
                        "expiresAtMs": int(current["expires_at_ms"]),
                    },
                )
            expires_at = max(
                int(current["expires_at_ms"]),
                now + self._write_lease_duration_ms,
            )
            await self._db.execute(
                "UPDATE ai_agent_artifact_claims SET acquired_revision = ?, "
                "expires_at_ms = ?, update_time = CURRENT_TIMESTAMP "
                "WHERE artifact_id = ? AND run_id = ? AND claim_token = ?",
                [
                    artifact.revision,
                    expires_at,
                    artifact.id,
                    run_id,
                    str(current["claim_token"]),
                ],
            )
            claim = ArtifactWriteClaim(
                artifact_id=artifact.id,
                work_item_id=artifact.work_item_id,
                run_id=run_id,
                claim_token=str(current["claim_token"]),
                acquired_revision=artifact.revision,
                expires_at_ms=expires_at,
            )
        else:
            claim = await self._insert_claim(
                artifact_id=artifact.id,
                work_item_id=artifact.work_item_id,
                run_id=run_id,
                acquired_revision=artifact.revision,
            )
        return WorkItemArtifactStart(work_item, artifact, claim)

    async def _insert_claim(
        self,
        *,
        artifact_id: str,
        work_item_id: str,
        run_id: str,
        acquired_revision: int,
    ) -> ArtifactWriteClaim:
        token = str(self._token_factory() or "").strip()
        if not token:
            raise RuntimeError("artifact claim token factory returned empty token")
        expires_at = int(self._clock()) + self._write_lease_duration_ms
        await self._db.execute(
            "INSERT INTO ai_agent_artifact_claims "
            "(artifact_id, work_item_id, run_id, claim_token, "
            "acquired_revision, expires_at_ms) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(artifact_id) DO UPDATE SET "
            "work_item_id = excluded.work_item_id, run_id = excluded.run_id, "
            "claim_token = excluded.claim_token, "
            "acquired_revision = excluded.acquired_revision, "
            "expires_at_ms = excluded.expires_at_ms, "
            "create_time = CURRENT_TIMESTAMP, update_time = CURRENT_TIMESTAMP",
            [
                artifact_id,
                work_item_id,
                run_id,
                token,
                acquired_revision,
                expires_at,
            ],
        )
        return ArtifactWriteClaim(
            artifact_id=artifact_id,
            work_item_id=work_item_id,
            run_id=run_id,
            claim_token=token,
            acquired_revision=acquired_revision,
            expires_at_ms=expires_at,
        )


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:40]}"


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        thaw_json_mapping(value),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


__all__ = [
    "SqliteWorkItemArtifactLifecycle",
    "WorkItemArtifactStart",
]
