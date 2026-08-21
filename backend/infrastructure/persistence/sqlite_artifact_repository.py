"""SQLite adapter for recoverable, replay-safe Agent artifacts."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from time import time
from typing import Any

from purra.artifacts import (
    ArtifactAppendCommand,
    ArtifactBatch,
    ArtifactBatchReceipt,
    ArtifactCreateCommand,
    ArtifactFinalizeCommand,
    ArtifactMutationLease,
    ArtifactRecord,
    ArtifactStatus,
)
from purra.artifacts import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    ArtifactStateError,
)
from purra.artifacts import ArtifactOwnerRef
from purra.json_values import thaw_json_mapping


class SqliteArtifactRepository:
    def __init__(
        self,
        db,
        *,
        clock: Callable[[], int] | None = None,
        join_ambient_transaction: bool = False,
    ) -> None:
        self._db = db
        self._clock = clock or (lambda: int(time() * 1_000))
        self._join_ambient_transaction = bool(join_ambient_transaction)

    def _mutation_transaction(self):
        if self._join_ambient_transaction:
            if not self._db.current_task_owns_transaction():
                raise RuntimeError(
                    "ambient artifact repository requires an owning transaction"
                )
            return self._db.transaction()
        return self._db.transaction(cancellation_linearizable=True)

    async def create(
        self,
        artifact_id: str,
        command: ArtifactCreateCommand,
    ) -> ArtifactRecord:
        normalized_id = _required(artifact_id, "artifact id")
        try:
            async with self._mutation_transaction():
                await self._db.execute(
                    "INSERT INTO ai_agent_artifacts "
                    "(id, namespace, kind, owner_id, owner_ref_kind, owner_ref_id, "
                    "created_by_run_id, schema_version, expected_item_count, metadata_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        normalized_id,
                        command.namespace,
                        command.kind,
                        command.owner_id,
                        command.owner_ref.kind,
                        command.owner_ref.id,
                        command.created_by_run_id,
                        command.schema_version,
                        command.expected_item_count,
                        _json_dump(command.metadata),
                    ],
                )
                return _require_artifact(
                    await self._db.fetch_one(
                        "SELECT * FROM ai_agent_artifacts WHERE id = ?",
                        [normalized_id],
                    ),
                    normalized_id,
                )
        except Exception as error:
            if "unique" not in str(error).lower():
                raise
            existing = await self.find_for_owner(
                namespace=command.namespace,
                kind=command.kind,
                owner_id=command.owner_id,
                owner_ref=command.owner_ref,
            )
            if existing is not None and _matches_create(existing, command):
                return existing
            raise ArtifactConflictError(
                "artifact identity already exists",
                code="artifact_id_conflict",
                details={"artifactId": normalized_id},
            ) from error

    async def load(self, artifact_id: str) -> ArtifactRecord | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_artifacts WHERE id = ?",
            [str(artifact_id or "").strip()],
        )
        return _artifact_record(row) if row is not None else None

    async def find_for_owner(
        self,
        *,
        namespace: str,
        kind: str,
        owner_id: str,
        owner_ref: ArtifactOwnerRef,
    ) -> ArtifactRecord | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_artifacts WHERE namespace = ? AND kind = ? "
            "AND owner_id = ? AND owner_ref_kind = ? AND owner_ref_id = ? "
            "ORDER BY create_time DESC LIMIT 1",
            [namespace, kind, owner_id, owner_ref.kind, owner_ref.id],
        )
        return _artifact_record(row) if row is not None else None

    async def replay_receipt(
        self,
        command: ArtifactAppendCommand,
    ) -> ArtifactBatchReceipt | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_artifact_batches "
            "WHERE artifact_id = ? AND idempotency_key = ?",
            [command.artifact_id, command.idempotency_key],
        )
        return _replay_receipt(row, command) if row is not None else None

    async def append(
        self,
        command: ArtifactAppendCommand,
    ) -> ArtifactBatchReceipt:
        async with self._mutation_transaction():
            existing = await self._db.fetch_one(
                "SELECT * FROM ai_agent_artifact_batches "
                "WHERE artifact_id = ? AND idempotency_key = ?",
                [command.artifact_id, command.idempotency_key],
            )
            if existing is not None:
                return _replay_receipt(existing, command)
            artifact = _require_artifact(
                await self._db.fetch_one(
                    "SELECT * FROM ai_agent_artifacts WHERE id = ?",
                    [command.artifact_id],
                ),
                command.artifact_id,
            )
            _require_open(artifact)
            _require_revision(artifact, command.expected_revision)
            claim = await self._require_mutation_lease(
                artifact,
                command.write_lease,
                now=int(self._clock()),
            )
            if artifact.next_sequence != command.sequence:
                raise ArtifactConflictError(
                    "artifact batch sequence is not next",
                    code="artifact_sequence_conflict",
                    details={
                        "expectedSequence": artifact.next_sequence,
                        "actualSequence": command.sequence,
                    },
                )
            revision = artifact.revision + 1
            next_sequence = command.sequence + 1
            await self._db.execute(
                "INSERT INTO ai_agent_artifact_batches "
                "(artifact_id, batch_id, idempotency_key, sequence, "
                "committed_revision, next_sequence, item_count, items_json, "
                "coverage_keys_json, content_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    artifact.id,
                    command.batch_id,
                    command.idempotency_key,
                    command.sequence,
                    revision,
                    next_sequence,
                    len(command.items),
                    json.dumps(
                        [thaw_json_mapping(item) for item in command.items],
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                    json.dumps(list(command.coverage_keys), separators=(",", ":")),
                    command.content_digest,
                ],
            )
            await self._db.execute(
                "UPDATE ai_agent_artifacts SET revision = ?, next_sequence = ?, "
                "committed_item_count = committed_item_count + ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ? AND revision = ?",
                [revision, next_sequence, len(command.items), artifact.id, artifact.revision],
            )
            await self._require_one_change("artifact changed during append")
            await self._renew_mutation_lease(
                artifact,
                command.write_lease,
                acquired_revision=revision,
                current_expires_at_ms=int(claim["expires_at_ms"]),
                now=int(self._clock()),
            )
            return ArtifactBatchReceipt(
                artifact_id=artifact.id,
                batch_id=command.batch_id,
                sequence=command.sequence,
                committed_revision=revision,
                next_sequence=next_sequence,
                accepted_count=len(command.items),
            )

    async def list_batches(self, artifact_id: str) -> tuple[ArtifactBatch, ...]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ai_agent_artifact_batches WHERE artifact_id = ? "
            "ORDER BY sequence ASC",
            [str(artifact_id or "").strip()],
        )
        return tuple(_artifact_batch(row) for row in rows)

    async def finalize(
        self,
        command: ArtifactFinalizeCommand,
        *,
        coverage_digest: str,
    ) -> ArtifactRecord:
        async with self._mutation_transaction():
            now = int(self._clock())
            artifact = _require_artifact(
                await self._db.fetch_one(
                    "SELECT * FROM ai_agent_artifacts WHERE id = ?",
                    [command.artifact_id],
                ),
                command.artifact_id,
            )
            _require_open(artifact)
            _require_revision(artifact, command.expected_revision)
            await self._require_mutation_lease(artifact, command.write_lease, now=now)
            expected_count = (
                command.expected_item_count
                if command.expected_item_count is not None
                else artifact.expected_item_count
            )
            await self._db.execute(
                "UPDATE ai_agent_artifacts SET status = 'finalized', revision = ?, "
                "expected_item_count = ?, resource_ref = ?, coverage_digest = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ? AND revision = ?",
                [
                    artifact.revision + 1,
                    expected_count,
                    command.resource_ref,
                    str(coverage_digest or ""),
                    artifact.id,
                    artifact.revision,
                ],
            )
            await self._require_one_change("artifact changed during finalization")
            await self._release_mutation_lease(artifact.id, command.write_lease, now=now)
            return _require_artifact(
                await self._db.fetch_one(
                    "SELECT * FROM ai_agent_artifacts WHERE id = ?", [artifact.id]
                ),
                artifact.id,
            )

    async def abort(
        self,
        artifact_id: str,
        *,
        expected_revision: int,
        write_lease: ArtifactMutationLease,
    ) -> ArtifactRecord:
        async with self._mutation_transaction():
            now = int(self._clock())
            artifact = _require_artifact(
                await self._db.fetch_one(
                    "SELECT * FROM ai_agent_artifacts WHERE id = ?", [artifact_id]
                ),
                artifact_id,
            )
            _require_open(artifact)
            _require_revision(artifact, expected_revision)
            await self._require_mutation_lease(artifact, write_lease, now=now)
            await self._db.execute(
                "UPDATE ai_agent_artifacts SET status = 'aborted', revision = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ? AND revision = ?",
                [artifact.revision + 1, artifact.id, artifact.revision],
            )
            await self._require_one_change("artifact changed during abort")
            await self._release_mutation_lease(artifact.id, write_lease, now=now)
            return _require_artifact(
                await self._db.fetch_one(
                    "SELECT * FROM ai_agent_artifacts WHERE id = ?", [artifact.id]
                ),
                artifact.id,
            )

    async def _require_mutation_lease(
        self,
        artifact: ArtifactRecord,
        lease: ArtifactMutationLease,
        *,
        now: int,
    ) -> dict[str, Any]:
        claim = await self._db.fetch_one(
            "SELECT * FROM ai_agent_artifact_claims WHERE artifact_id = ?",
            [artifact.id],
        )
        if claim is None:
            raise ArtifactConflictError(
                "artifact claim does not exist",
                code="artifact_claim_not_found",
                details={"artifactId": artifact.id},
            )
        if (
            str(claim.get("run_id") or "") != lease.run_id
            or str(claim.get("claim_token") or "") != lease.claim_token
        ):
            raise ArtifactConflictError(
                "artifact claim is owned by another lease",
                code="artifact_claim_owner_mismatch",
                details={"artifactId": artifact.id},
            )
        if int(claim.get("expires_at_ms") or 0) <= now:
            raise ArtifactConflictError(
                "artifact claim has expired",
                code="artifact_claim_expired",
                details={"artifactId": artifact.id},
            )
        if int(claim.get("acquired_revision") or 0) != artifact.revision:
            raise ArtifactConflictError(
                "artifact claim revision is stale",
                code="artifact_claim_revision_conflict",
                details={"artifactId": artifact.id},
            )
        return claim

    async def _renew_mutation_lease(
        self,
        artifact: ArtifactRecord,
        lease: ArtifactMutationLease,
        *,
        acquired_revision: int,
        current_expires_at_ms: int,
        now: int,
    ) -> None:
        await self._db.execute(
            "UPDATE ai_agent_artifact_claims SET acquired_revision = ?, "
            "expires_at_ms = ?, update_time = CURRENT_TIMESTAMP "
            "WHERE artifact_id = ? AND run_id = ? AND claim_token = ? "
            "AND expires_at_ms > ?",
            [
                acquired_revision,
                max(current_expires_at_ms, now + lease.lease_duration_ms),
                artifact.id,
                lease.run_id,
                lease.claim_token,
                now,
            ],
        )
        await self._require_one_change("artifact claim changed during append")

    async def _release_mutation_lease(
        self,
        artifact_id: str,
        lease: ArtifactMutationLease,
        *,
        now: int,
    ) -> None:
        await self._db.execute(
            "DELETE FROM ai_agent_artifact_claims WHERE artifact_id = ? "
            "AND run_id = ? AND claim_token = ? AND expires_at_ms > ?",
            [artifact_id, lease.run_id, lease.claim_token, now],
        )
        await self._require_one_change("artifact claim changed during terminal mutation")

    async def _require_one_change(self, message: str) -> None:
        changed = await self._db.fetch_one("SELECT changes() AS count")
        if int((changed or {}).get("count") or 0) != 1:
            raise ArtifactConflictError(message, code="artifact_revision_conflict")


async def get_run_artifact_metrics(db, run_id: str) -> dict[str, Any]:
    """Return content-free lifecycle metrics for one persisted Agent Run."""

    normalized_run_id = _required(run_id, "run id")
    rows = await db.fetch_all(
        "SELECT a.id, a.namespace, a.kind, a.status, a.revision, "
        "a.owner_ref_kind, a.owner_ref_id, a.committed_item_count, "
        "a.expected_item_count, COUNT(b.batch_id) AS batch_count, "
        "COALESCE(SUM(b.item_count), 0) AS batch_item_count "
        "FROM ai_agent_artifacts AS a "
        "LEFT JOIN ai_agent_artifact_claims AS c ON c.artifact_id = a.id "
        "LEFT JOIN ai_agent_artifact_batches AS b ON b.artifact_id = a.id "
        "WHERE a.created_by_run_id = ? OR c.run_id = ? "
        "GROUP BY a.id ORDER BY a.create_time ASC, a.id ASC",
        [normalized_run_id, normalized_run_id],
    )
    artifacts = [
        {
            "artifactId": str(row.get("id") or ""),
            "namespace": str(row.get("namespace") or ""),
            "kind": str(row.get("kind") or ""),
            "status": str(row.get("status") or ""),
            "ownerRef": {
                "kind": str(row.get("owner_ref_kind") or ""),
                "id": str(row.get("owner_ref_id") or ""),
            },
            "revision": int(row.get("revision") or 0),
            "committedItemCount": int(row.get("committed_item_count") or 0),
            "expectedItemCount": (
                int(row["expected_item_count"])
                if row.get("expected_item_count") is not None
                else None
            ),
            "batchCount": int(row.get("batch_count") or 0),
            "batchItemCount": int(row.get("batch_item_count") or 0),
        }
        for row in rows
    ]
    statuses = Counter(str(item["status"] or "unknown") for item in artifacts)
    expected_items = sum(
        int(item["expectedItemCount"] or 0)
        for item in artifacts
        if item["expectedItemCount"] is not None
    )
    committed_items = sum(int(item["committedItemCount"]) for item in artifacts)
    return {
        "artifactCount": len(artifacts),
        "openArtifacts": statuses.get(ArtifactStatus.OPEN.value, 0),
        "finalizedArtifacts": statuses.get(ArtifactStatus.FINALIZED.value, 0),
        "abortedArtifacts": statuses.get(ArtifactStatus.ABORTED.value, 0),
        "batchCount": sum(int(item["batchCount"]) for item in artifacts),
        "committedItemCount": committed_items,
        "expectedItemCount": expected_items,
        "completionRate": (
            round(committed_items / expected_items, 4) if expected_items else None
        ),
        "artifacts": artifacts,
    }


def _replay_receipt(
    row: dict[str, Any],
    command: ArtifactAppendCommand,
) -> ArtifactBatchReceipt:
    if str(row.get("content_digest") or "") != command.content_digest:
        raise ArtifactConflictError(
            "artifact idempotency key was reused with different content",
            code="artifact_idempotency_conflict",
            details={"idempotencyKey": command.idempotency_key},
        )
    return ArtifactBatchReceipt(
        artifact_id=str(row["artifact_id"]),
        batch_id=str(row["batch_id"]),
        sequence=int(row["sequence"]),
        committed_revision=int(row["committed_revision"]),
        next_sequence=int(row["next_sequence"]),
        accepted_count=int(row["item_count"]),
        replayed=True,
    )


def _artifact_record(row: dict[str, Any]) -> ArtifactRecord:
    return ArtifactRecord(
        id=str(row["id"]),
        namespace=str(row["namespace"]),
        kind=str(row["kind"]),
        owner_id=str(row["owner_id"]),
        owner_ref=ArtifactOwnerRef(
            kind=str(row["owner_ref_kind"]),
            id=str(row["owner_ref_id"]),
        ),
        created_by_run_id=str(row["created_by_run_id"]),
        schema_version=int(row.get("schema_version") or 1),
        status=str(row.get("status") or ArtifactStatus.OPEN.value),
        revision=int(row.get("revision") or 1),
        next_sequence=int(row.get("next_sequence") or 1),
        committed_item_count=int(row.get("committed_item_count") or 0),
        expected_item_count=(
            int(row["expected_item_count"])
            if row.get("expected_item_count") is not None
            else None
        ),
        metadata=_json_mapping(row.get("metadata_json")),
        resource_ref=row.get("resource_ref"),
        coverage_digest=row.get("coverage_digest"),
    )


def _matches_create(
    artifact: ArtifactRecord,
    command: ArtifactCreateCommand,
) -> bool:
    return bool(
        artifact.namespace == command.namespace
        and artifact.kind == command.kind
        and artifact.owner_id == command.owner_id
        and artifact.owner_ref == command.owner_ref
        and artifact.created_by_run_id == command.created_by_run_id
        and artifact.schema_version == command.schema_version
        and artifact.expected_item_count == command.expected_item_count
        and thaw_json_mapping(artifact.metadata) == thaw_json_mapping(command.metadata)
    )


def _artifact_batch(row: dict[str, Any]) -> ArtifactBatch:
    raw_items = _json_value(row.get("items_json"), [])
    raw_coverage = _json_value(row.get("coverage_keys_json"), [])
    return ArtifactBatch(
        artifact_id=str(row["artifact_id"]),
        batch_id=str(row["batch_id"]),
        idempotency_key=str(row["idempotency_key"]),
        sequence=int(row["sequence"]),
        committed_revision=int(row["committed_revision"]),
        items=tuple(item for item in raw_items if isinstance(item, dict)),
        coverage_keys=tuple(str(item) for item in raw_coverage if str(item).strip()),
        content_digest=str(row.get("content_digest") or ""),
    )


def _require_artifact(
    row: dict[str, Any] | None,
    artifact_id: str,
) -> ArtifactRecord:
    if row is None:
        raise ArtifactNotFoundError(
            "artifact does not exist",
            code="artifact_not_found",
            details={"artifactId": artifact_id},
        )
    return _artifact_record(row)


def _require_open(artifact: ArtifactRecord) -> None:
    if artifact.status is not ArtifactStatus.OPEN:
        raise ArtifactStateError(
            "artifact is not open",
            code="artifact_not_open",
            details={"status": artifact.status.value},
        )


def _require_revision(artifact: ArtifactRecord, expected_revision: int) -> None:
    if artifact.revision != int(expected_revision):
        raise ArtifactConflictError(
            "artifact revision does not match",
            code="artifact_revision_conflict",
            details={
                "expectedRevision": int(expected_revision),
                "actualRevision": artifact.revision,
            },
        )


def _json_dump(value: Any) -> str:
    return json.dumps(
        thaw_json_mapping(value),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_value(raw: Any, fallback: Any) -> Any:
    if raw is None:
        return fallback
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError):
        return fallback


def _json_mapping(raw: Any) -> dict[str, Any]:
    value = _json_value(raw, {})
    return value if isinstance(value, dict) else {}


def _required(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


__all__ = ["SqliteArtifactRepository", "get_run_artifact_metrics"]
