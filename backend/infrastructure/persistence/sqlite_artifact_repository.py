"""SQLite adapter for atomic, replay-safe Agent artifact batches."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from time import time
from typing import Any

from purra.artifacts.contracts import (
    ArtifactAppendCommand,
    ArtifactBatch,
    ArtifactBatchReceipt,
    ArtifactCreateCommand,
    ArtifactFinalizeCommand,
    ArtifactMutationLease,
    ArtifactRecord,
    ArtifactStatus,
)
from purra.artifacts.errors import (
    ArtifactConflictError,
    ArtifactNotFoundError,
    ArtifactStateError,
)
from purra.artifacts.scope import ArtifactScope
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
        normalized_id = str(artifact_id or "").strip()
        if not normalized_id:
            raise ValueError("artifact id is required")
        try:
            async with self._mutation_transaction():
                if command.scope is ArtifactScope.WORK_ITEM:
                    await self._require_work_item_scope(command)
                await self._db.execute(
                    "INSERT INTO ai_agent_artifacts "
                    "(id, namespace, kind, owner_id, run_id, artifact_scope, "
                    "work_item_id, created_by_run_id, schema_version, "
                    "expected_item_count, metadata_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        normalized_id,
                        command.namespace,
                        command.kind,
                        command.owner_id,
                        command.run_id,
                        command.scope.value,
                        command.work_item_id,
                        command.created_by_run_id,
                        command.schema_version,
                        command.expected_item_count,
                        json.dumps(
                            thaw_json_mapping(command.metadata),
                            ensure_ascii=False,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                    ],
                )
                row = await self._db.fetch_one(
                    "SELECT * FROM ai_agent_artifacts WHERE id = ?",
                    [normalized_id],
                )
                artifact = _require_artifact(row, normalized_id)
        except Exception as error:
            if "unique" in str(error).lower():
                if command.scope is ArtifactScope.WORK_ITEM:
                    existing = await self.find_for_work_item(
                        namespace=command.namespace,
                        kind=command.kind,
                        owner_id=command.owner_id,
                        work_item_id=str(command.work_item_id),
                    )
                else:
                    existing = (
                        await self.find_for_run(
                            namespace=command.namespace,
                            kind=command.kind,
                            owner_id=command.owner_id,
                            run_id=command.run_id,
                        )
                        if command.run_id
                        else None
                    )
                if existing is not None and _matches_create(
                    existing,
                    command,
                ):
                    return existing
                raise ArtifactConflictError(
                    "artifact id already exists",
                    code="artifact_id_conflict",
                    details={"artifactId": normalized_id},
                ) from error
            raise
        return artifact

    async def _require_work_item_scope(
        self,
        command: ArtifactCreateCommand,
    ) -> None:
        item = await self._db.fetch_one(
            "SELECT namespace, owner_id, status FROM ai_agent_work_items "
            "WHERE id = ?",
            [command.work_item_id],
        )
        if item is None:
            raise ArtifactStateError(
                "artifact Work Item does not exist",
                code="artifact_work_item_not_found",
                details={"workItemId": command.work_item_id},
            )
        if (
            str(item.get("namespace") or "") != command.namespace
            or str(item.get("owner_id") or "") != command.owner_id
        ):
            raise ArtifactStateError(
                "artifact and Work Item ownership do not match",
                code="artifact_work_item_owner_mismatch",
                details={"workItemId": command.work_item_id},
            )
        if str(item.get("status") or "") != "open":
            raise ArtifactStateError(
                "artifact Work Item is not open",
                code="artifact_work_item_not_open",
                details={
                    "workItemId": command.work_item_id,
                    "status": str(item.get("status") or ""),
                },
            )
        creator_link = await self._db.fetch_one(
            "SELECT relation FROM ai_agent_work_item_runs "
            "WHERE work_item_id = ? AND run_id = ?",
            [command.work_item_id, command.created_by_run_id],
        )
        if str((creator_link or {}).get("relation") or "") not in {
            "created",
            "continuation",
        }:
            raise ArtifactStateError(
                "artifact creator Run cannot write this Work Item",
                code="artifact_creator_run_not_linked",
                details={
                    "workItemId": command.work_item_id,
                    "runId": command.created_by_run_id,
                },
            )

    async def load(self, artifact_id: str) -> ArtifactRecord | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_artifacts WHERE id = ?",
            [str(artifact_id or "").strip()],
        )
        return _artifact_record(row) if row is not None else None

    async def find_for_run(
        self,
        *,
        namespace: str,
        kind: str,
        owner_id: str,
        run_id: str,
    ) -> ArtifactRecord | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_artifacts "
            "WHERE namespace = ? AND kind = ? AND owner_id = ? AND run_id = ? "
            "AND artifact_scope = 'run' "
            "ORDER BY create_time DESC LIMIT 1",
            [
                str(namespace or "").strip(),
                str(kind or "").strip(),
                str(owner_id or "").strip(),
                str(run_id or "").strip(),
            ],
        )
        return _artifact_record(row) if row is not None else None

    async def find_for_work_item(
        self,
        *,
        namespace: str,
        kind: str,
        owner_id: str,
        work_item_id: str,
    ) -> ArtifactRecord | None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_artifacts "
            "WHERE namespace = ? AND kind = ? AND owner_id = ? "
            "AND work_item_id = ? AND artifact_scope = 'work_item' "
            "ORDER BY create_time DESC LIMIT 1",
            [
                str(namespace or "").strip(),
                str(kind or "").strip(),
                str(owner_id or "").strip(),
                str(work_item_id or "").strip(),
            ],
        )
        return _artifact_record(row) if row is not None else None

    async def find_linked_for_run(
        self,
        *,
        namespace: str,
        kind: str,
        owner_id: str,
        run_id: str,
        writable: bool = True,
    ) -> ArtifactRecord | None:
        relations = ("created", "continuation") if writable else (
            "created",
            "continuation",
            "reference",
        )
        placeholders = ",".join("?" for _ in relations)
        row = await self._db.fetch_one(
            "SELECT a.* FROM ai_agent_artifacts AS a "
            "JOIN ai_agent_work_item_runs AS wir "
            "ON wir.work_item_id = a.work_item_id "
            "WHERE a.namespace = ? AND a.kind = ? AND a.owner_id = ? "
            "AND a.artifact_scope = 'work_item' AND wir.run_id = ? "
            f"AND wir.relation IN ({placeholders}) "
            "ORDER BY wir.create_time DESC, a.create_time DESC LIMIT 1",
            [
                str(namespace or "").strip(),
                str(kind or "").strip(),
                str(owner_id or "").strip(),
                str(run_id or "").strip(),
                *relations,
            ],
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
        if row is None:
            return None
        artifact_row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_artifacts WHERE id = ?",
            [command.artifact_id],
        )
        artifact = _require_artifact(artifact_row, command.artifact_id)
        if artifact.scope is ArtifactScope.WORK_ITEM:
            await self._require_mutation_lease(
                artifact,
                command.write_lease,
                now=int(self._clock()),
            )
        return _replay_receipt(row, command)

    async def append(
        self,
        command: ArtifactAppendCommand,
    ) -> ArtifactBatchReceipt:
        async with self._mutation_transaction():
            now = int(self._clock())
            row = await self._db.fetch_one(
                "SELECT * FROM ai_agent_artifacts WHERE id = ?",
                [command.artifact_id],
            )
            artifact = _require_artifact(row, command.artifact_id)
            claim = await self._require_mutation_lease(
                artifact,
                command.write_lease,
                now=now,
            )
            existing = await self._db.fetch_one(
                "SELECT * FROM ai_agent_artifact_batches "
                "WHERE artifact_id = ? AND idempotency_key = ?",
                [command.artifact_id, command.idempotency_key],
            )
            if existing is not None:
                return _replay_receipt(existing, command)
            _require_open(artifact)
            if artifact.revision != command.expected_revision:
                raise ArtifactConflictError(
                    "artifact revision does not match",
                    code="artifact_revision_conflict",
                    details={
                        "expectedRevision": command.expected_revision,
                        "actualRevision": artifact.revision,
                    },
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
            duplicate_batch = await self._db.fetch_one(
                "SELECT idempotency_key FROM ai_agent_artifact_batches "
                "WHERE artifact_id = ? AND batch_id = ?",
                [command.artifact_id, command.batch_id],
            )
            if duplicate_batch is not None:
                raise ArtifactConflictError(
                    "artifact batch id was reused",
                    code="artifact_batch_id_conflict",
                    details={"batchId": command.batch_id},
                )
            committed_revision = artifact.revision + 1
            next_sequence = artifact.next_sequence + 1
            await self._db.execute(
                "INSERT INTO ai_agent_artifact_batches "
                "(artifact_id, batch_id, idempotency_key, sequence, "
                "committed_revision, next_sequence, item_count, items_json, "
                "coverage_keys_json, content_digest) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    command.artifact_id,
                    command.batch_id,
                    command.idempotency_key,
                    command.sequence,
                    committed_revision,
                    next_sequence,
                    len(command.items),
                    json.dumps(
                        [thaw_json_mapping(item) for item in command.items],
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                    json.dumps(
                        list(command.coverage_keys),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    command.content_digest,
                ],
            )
            await self._db.execute(
                "UPDATE ai_agent_artifacts SET revision = ?, next_sequence = ?, "
                "committed_item_count = committed_item_count + ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ? AND revision = ?",
                [
                    committed_revision,
                    next_sequence,
                    len(command.items),
                    command.artifact_id,
                    artifact.revision,
                ],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            if int((changed or {}).get("count") or 0) != 1:
                raise ArtifactConflictError(
                    "artifact changed during append",
                    code="artifact_revision_conflict",
                    details={"expectedRevision": artifact.revision},
                )
            if claim is not None:
                await self._renew_mutation_lease(
                    artifact,
                    command.write_lease,
                    acquired_revision=committed_revision,
                    current_expires_at_ms=int(claim["expires_at_ms"]),
                    now=now,
                )
            return ArtifactBatchReceipt(
                artifact_id=command.artifact_id,
                batch_id=command.batch_id,
                sequence=command.sequence,
                committed_revision=committed_revision,
                next_sequence=next_sequence,
                accepted_count=len(command.items),
            )

    async def list_batches(self, artifact_id: str) -> tuple[ArtifactBatch, ...]:
        rows = await self._db.fetch_all(
            "SELECT * FROM ai_agent_artifact_batches "
            "WHERE artifact_id = ? ORDER BY sequence ASC",
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
            row = await self._db.fetch_one(
                "SELECT * FROM ai_agent_artifacts WHERE id = ?",
                [command.artifact_id],
            )
            artifact = _require_artifact(row, command.artifact_id)
            claim = await self._require_mutation_lease(
                artifact,
                command.write_lease,
                now=now,
            )
            _require_open(artifact)
            if artifact.revision != command.expected_revision:
                raise ArtifactConflictError(
                    "artifact revision does not match",
                    code="artifact_revision_conflict",
                    details={
                        "expectedRevision": command.expected_revision,
                        "actualRevision": artifact.revision,
                    },
                )
            final_revision = artifact.revision + 1
            expected_count = (
                command.expected_item_count
                if command.expected_item_count is not None
                else artifact.expected_item_count
            )
            await self._db.execute(
                "UPDATE ai_agent_artifacts SET status = ?, revision = ?, "
                "expected_item_count = ?, resource_ref = ?, coverage_digest = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ? AND revision = ?",
                [
                    ArtifactStatus.FINALIZED.value,
                    final_revision,
                    expected_count,
                    command.resource_ref,
                    str(coverage_digest or ""),
                    artifact.id,
                    artifact.revision,
                ],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            if int((changed or {}).get("count") or 0) != 1:
                raise ArtifactConflictError(
                    "artifact changed during finalization",
                    code="artifact_revision_conflict",
                    details={"expectedRevision": artifact.revision},
                )
            if artifact.scope is ArtifactScope.WORK_ITEM:
                if command.complete_work_item:
                    await self._complete_work_item(artifact)
                assert claim is not None
                assert command.write_lease is not None
                await self._db.execute(
                    "DELETE FROM ai_agent_artifact_claims WHERE artifact_id = ? "
                    "AND run_id = ? AND claim_token = ? AND expires_at_ms > ?",
                    [
                        artifact.id,
                        command.write_lease.run_id,
                        command.write_lease.claim_token,
                        now,
                    ],
                )
                changed = await self._db.fetch_one("SELECT changes() AS count")
                if int((changed or {}).get("count") or 0) != 1:
                    raise ArtifactConflictError(
                        "artifact claim changed during finalization",
                        code="artifact_claim_conflict",
                        details={"artifactId": artifact.id},
                    )
            elif command.complete_work_item:
                raise ArtifactStateError(
                    "Run-scoped artifact cannot complete a Work Item",
                    code="artifact_work_item_scope_mismatch",
                    details={"artifactId": artifact.id},
                )
            finalized = await self._db.fetch_one(
                "SELECT * FROM ai_agent_artifacts WHERE id = ?",
                [artifact.id],
            )
            return _require_artifact(finalized, artifact.id)

    async def abort(
        self,
        artifact_id: str,
        *,
        expected_revision: int,
        write_lease: ArtifactMutationLease | None = None,
    ) -> ArtifactRecord:
        normalized_id = str(artifact_id or "").strip()
        async with self._mutation_transaction():
            now = int(self._clock())
            row = await self._db.fetch_one(
                "SELECT * FROM ai_agent_artifacts WHERE id = ?",
                [normalized_id],
            )
            artifact = _require_artifact(row, normalized_id)
            claim = await self._require_mutation_lease(
                artifact,
                write_lease,
                now=now,
            )
            _require_open(artifact)
            if artifact.revision != int(expected_revision):
                raise ArtifactConflictError(
                    "artifact revision does not match",
                    code="artifact_revision_conflict",
                    details={
                        "expectedRevision": int(expected_revision),
                        "actualRevision": artifact.revision,
                    },
                )
            await self._db.execute(
                "UPDATE ai_agent_artifacts SET status = ?, revision = ?, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ? AND revision = ?",
                [
                    ArtifactStatus.ABORTED.value,
                    artifact.revision + 1,
                    artifact.id,
                    artifact.revision,
                ],
            )
            changed = await self._db.fetch_one("SELECT changes() AS count")
            if int((changed or {}).get("count") or 0) != 1:
                raise ArtifactConflictError(
                    "artifact changed during abort",
                    code="artifact_revision_conflict",
                    details={"expectedRevision": artifact.revision},
                )
            if artifact.scope is ArtifactScope.WORK_ITEM:
                assert claim is not None
                assert write_lease is not None
                await self._cancel_work_item(artifact)
                await self._db.execute(
                    "DELETE FROM ai_agent_artifact_claims WHERE artifact_id = ? "
                    "AND run_id = ? AND claim_token = ? AND expires_at_ms > ?",
                    [
                        artifact.id,
                        write_lease.run_id,
                        write_lease.claim_token,
                        now,
                    ],
                )
                changed = await self._db.fetch_one("SELECT changes() AS count")
                if int((changed or {}).get("count") or 0) != 1:
                    raise ArtifactConflictError(
                        "artifact claim changed during abort",
                        code="artifact_claim_conflict",
                        details={"artifactId": artifact.id},
                    )
            aborted = await self._db.fetch_one(
                "SELECT * FROM ai_agent_artifacts WHERE id = ?",
                [artifact.id],
            )
            return _require_artifact(aborted, artifact.id)

    async def _require_mutation_lease(
        self,
        artifact: ArtifactRecord,
        lease: ArtifactMutationLease | None,
        *,
        now: int,
    ) -> dict[str, Any] | None:
        if artifact.scope is ArtifactScope.RUN:
            if lease is not None:
                raise ArtifactStateError(
                    "Run-scoped artifact does not accept a Work Item claim",
                    code="artifact_claim_scope_mismatch",
                    details={"artifactId": artifact.id},
                )
            return None
        if lease is None:
            raise ArtifactConflictError(
                "Work Item-scoped artifact requires an active writer claim",
                code="artifact_claim_required",
                details={"artifactId": artifact.id},
            )
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
            str(claim.get("work_item_id") or "") != artifact.work_item_id
            or str(claim.get("run_id") or "") != lease.run_id
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
                details={
                    "artifactId": artifact.id,
                    "claimRevision": int(
                        claim.get("acquired_revision") or 0
                    ),
                    "artifactRevision": artifact.revision,
                },
            )
        link = await self._db.fetch_one(
            "SELECT relation FROM ai_agent_work_item_runs "
            "WHERE work_item_id = ? AND run_id = ?",
            [artifact.work_item_id, lease.run_id],
        )
        if str((link or {}).get("relation") or "") not in {
            "created",
            "continuation",
        }:
            raise ArtifactStateError(
                "Run cannot write this Work Item",
                code="artifact_claim_run_not_linked",
                details={
                    "artifactId": artifact.id,
                    "workItemId": artifact.work_item_id,
                    "runId": lease.run_id,
                },
            )
        work_item = await self._db.fetch_one(
            "SELECT namespace, owner_id, status FROM ai_agent_work_items "
            "WHERE id = ?",
            [artifact.work_item_id],
        )
        if work_item is None:
            raise ArtifactStateError(
                "artifact Work Item does not exist",
                code="artifact_work_item_not_found",
                details={"workItemId": artifact.work_item_id},
            )
        if (
            str(work_item.get("namespace") or "") != artifact.namespace
            or str(work_item.get("owner_id") or "") != artifact.owner_id
        ):
            raise ArtifactStateError(
                "artifact and Work Item ownership do not match",
                code="artifact_work_item_owner_mismatch",
                details={"workItemId": artifact.work_item_id},
            )
        if str(work_item.get("status") or "") != "open":
            raise ArtifactStateError(
                "artifact Work Item is not open",
                code="artifact_work_item_not_open",
                details={"status": str(work_item.get("status") or "")},
            )
        return claim

    async def _renew_mutation_lease(
        self,
        artifact: ArtifactRecord,
        lease: ArtifactMutationLease | None,
        *,
        acquired_revision: int,
        current_expires_at_ms: int,
        now: int,
    ) -> None:
        assert lease is not None
        expires_at = max(
            current_expires_at_ms,
            now + lease.lease_duration_ms,
        )
        await self._db.execute(
            "UPDATE ai_agent_artifact_claims SET acquired_revision = ?, "
            "expires_at_ms = ?, update_time = CURRENT_TIMESTAMP "
            "WHERE artifact_id = ? AND run_id = ? AND claim_token = ? "
            "AND expires_at_ms > ?",
            [
                acquired_revision,
                expires_at,
                artifact.id,
                lease.run_id,
                lease.claim_token,
                now,
            ],
        )
        changed = await self._db.fetch_one("SELECT changes() AS count")
        if int((changed or {}).get("count") or 0) != 1:
            raise ArtifactConflictError(
                "artifact claim changed during append",
                code="artifact_claim_conflict",
                details={"artifactId": artifact.id},
            )

    async def _complete_work_item(self, artifact: ArtifactRecord) -> None:
        await self._transition_work_item(artifact, target="completed")

    async def _cancel_work_item(self, artifact: ArtifactRecord) -> None:
        await self._transition_work_item(artifact, target="canceled")

    async def _transition_work_item(
        self,
        artifact: ArtifactRecord,
        *,
        target: str,
    ) -> None:
        row = await self._db.fetch_one(
            "SELECT * FROM ai_agent_work_items WHERE id = ?",
            [artifact.work_item_id],
        )
        if row is None:
            raise ArtifactStateError(
                "artifact Work Item does not exist",
                code="artifact_work_item_not_found",
                details={"workItemId": artifact.work_item_id},
            )
        revision = int(row.get("revision") or 0)
        if str(row.get("status") or "") != "open":
            raise ArtifactStateError(
                "artifact Work Item is not open",
                code="artifact_work_item_not_open",
                details={"status": str(row.get("status") or "")},
            )
        await self._db.execute(
            "UPDATE ai_agent_work_items SET status = ?, "
            "revision = ?, update_time = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'open' AND revision = ?",
            [target, revision + 1, artifact.work_item_id, revision],
        )
        changed = await self._db.fetch_one("SELECT changes() AS count")
        if int((changed or {}).get("count") or 0) != 1:
            raise ArtifactConflictError(
                "Work Item changed during Artifact finalization",
                code="work_item_revision_conflict",
                details={"expectedRevision": revision},
            )


async def get_run_artifact_metrics(db, run_id: str) -> dict[str, Any]:
    """Return content-free lifecycle metrics for one persisted Agent Run."""

    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        raise ValueError("run id is required")
    rows = await db.fetch_all(
        "SELECT a.id, a.namespace, a.kind, a.status, a.revision, "
        "a.artifact_scope, a.work_item_id, "
        "w.status AS work_item_status, wir.relation AS run_relation, "
        "a.committed_item_count, a.expected_item_count, "
        "COUNT(b.batch_id) AS batch_count, "
        "COALESCE(SUM(b.item_count), 0) AS batch_item_count "
        "FROM ai_agent_artifacts AS a "
        "LEFT JOIN ai_agent_work_items AS w ON w.id = a.work_item_id "
        "LEFT JOIN ai_agent_work_item_runs AS wir "
        "ON wir.work_item_id = a.work_item_id AND wir.run_id = ? "
        "LEFT JOIN ai_agent_artifact_batches AS b ON b.artifact_id = a.id "
        "WHERE (a.artifact_scope = 'run' AND a.run_id = ?) OR ("
        "a.artifact_scope = 'work_item' AND EXISTS ("
        "SELECT 1 FROM ai_agent_work_item_runs AS wir "
        "WHERE wir.work_item_id = a.work_item_id AND wir.run_id = ?)) "
        "GROUP BY a.id, a.namespace, a.kind, a.status, a.revision, "
        "a.artifact_scope, a.work_item_id, w.status, wir.relation, "
        "a.committed_item_count, a.expected_item_count "
        "ORDER BY a.create_time ASC, a.id ASC",
        [normalized_run_id, normalized_run_id, normalized_run_id],
    )
    artifacts = [
        {
            "artifactId": str(row.get("id") or ""),
            "namespace": str(row.get("namespace") or ""),
            "kind": str(row.get("kind") or ""),
            "status": str(row.get("status") or ""),
            "scope": str(row.get("artifact_scope") or "run"),
            "workItemId": row.get("work_item_id"),
            "workItemStatus": row.get("work_item_status"),
            "runRelation": row.get("run_relation"),
            "revision": int(row.get("revision") or 0),
            "committedItemCount": int(
                row.get("committed_item_count") or 0
            ),
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
    statuses = Counter(
        str(item["status"] or "unknown") for item in artifacts
    )
    expected_items = sum(
        int(item["expectedItemCount"] or 0)
        for item in artifacts
        if item["expectedItemCount"] is not None
    )
    committed_items = sum(
        int(item["committedItemCount"]) for item in artifacts
    )
    return {
        "artifactCount": len(artifacts),
        "openArtifacts": statuses.get(ArtifactStatus.OPEN.value, 0),
        "finalizedArtifacts": statuses.get(
            ArtifactStatus.FINALIZED.value,
            0,
        ),
        "abortedArtifacts": statuses.get(ArtifactStatus.ABORTED.value, 0),
        "batchCount": sum(int(item["batchCount"]) for item in artifacts),
        "committedItemCount": committed_items,
        "expectedItemCount": expected_items,
        "completionRate": (
            round(committed_items / expected_items, 4)
            if expected_items
            else None
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
        run_id=row.get("run_id"),
        schema_version=int(row.get("schema_version") or 1),
        scope=str(row.get("artifact_scope") or ArtifactScope.RUN.value),
        work_item_id=row.get("work_item_id"),
        created_by_run_id=row.get("created_by_run_id"),
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
        and artifact.run_id == command.run_id
        and artifact.scope is command.scope
        and artifact.work_item_id == command.work_item_id
        and artifact.created_by_run_id == command.created_by_run_id
        and artifact.schema_version == command.schema_version
        and artifact.expected_item_count == command.expected_item_count
        and thaw_json_mapping(artifact.metadata)
        == thaw_json_mapping(command.metadata)
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
        items=tuple(
            item for item in raw_items
            if isinstance(item, dict)
        ) if isinstance(raw_items, list) else (),
        coverage_keys=tuple(
            str(item) for item in raw_coverage
            if str(item).strip()
        ) if isinstance(raw_coverage, list) else (),
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


def _json_mapping(value: Any) -> dict[str, Any]:
    parsed = _json_value(value, {})
    return parsed if isinstance(parsed, dict) else {}


def _json_value(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


__all__ = ["SqliteArtifactRepository", "get_run_artifact_metrics"]
