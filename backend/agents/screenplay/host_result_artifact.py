"""Auditable host-only results for Screenplay replacement Parts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from agents.screenplay.contracts import ScreenplayPartOperationScope
from infrastructure.persistence.sqlite_artifact_claim_repository import SqliteArtifactClaimRepository
from infrastructure.persistence.sqlite_artifact_repository import SqliteArtifactRepository
from purra.artifacts import (
    ArtifactAppendCommand, ArtifactCreateCommand, ArtifactFinalizeCommand,
    ArtifactLifecycle, ArtifactMutationLease, ArtifactOwnerRef, ArtifactStatus,
    ArtifactWriteClaimCommand,
)
from purra.json_values import canonical_json_digest, thaw_json_mapping


SCREENPLAY_HOST_RESULT_NAMESPACE = "purrtypos.screenplay.v1"
SCREENPLAY_HOST_RESULT_KIND = "host_result_attempt"
SCREENPLAY_HOST_RESULT_REF_PREFIX = "screenplay-host-result-v1://"


class ScreenplayHostResultArtifactStore:
    def __init__(self, db) -> None:
        self._repository = SqliteArtifactRepository(db)
        self._claims = SqliteArtifactClaimRepository(db)
        self._lifecycle = ArtifactLifecycle(self._repository)

    async def commit(self, *, scope: ScreenplayPartOperationScope, run_id: str, result, dependencies):
        if not isinstance(result, Mapping) or not result:
            raise ValueError("Screenplay host result is required")
        payload = {
            "schemaVersion": 1,
            "partKind": scope.part_kind.value,
            "partKey": scope.part_key,
            "targetRole": scope.target_role,
            "dependencies": list(dependencies),
            "result": dict(result),
        }
        digest = canonical_json_digest(payload)
        owner = ArtifactOwnerRef("operation", scope.operation_scope_id)
        artifact = await self._repository.find_for_owner(
            namespace=SCREENPLAY_HOST_RESULT_NAMESPACE,
            kind=SCREENPLAY_HOST_RESULT_KIND,
            owner_id=scope.project_id,
            owner_ref=owner,
        )
        if artifact is None:
            artifact = await self._repository.create(
                "screenplay_host_" + hashlib.sha256(scope.operation_scope_id.encode()).hexdigest()[:32],
                ArtifactCreateCommand(
                    namespace=SCREENPLAY_HOST_RESULT_NAMESPACE,
                    kind=SCREENPLAY_HOST_RESULT_KIND,
                    owner_id=scope.project_id,
                    owner_ref=owner,
                    created_by_run_id=run_id,
                    schema_version=1,
                    expected_item_count=1,
                    metadata={
                        "taskId": scope.task_id, "unitId": scope.unit_id,
                        "attempt": scope.attempt, "operationScopeId": scope.operation_scope_id,
                        "partKind": scope.part_kind.value, "payloadDigest": digest,
                    },
                ),
            )
        if artifact.metadata.get("payloadDigest") != digest:
            raise ValueError("Screenplay host result attempt identity conflict")
        if artifact.status is ArtifactStatus.FINALIZED:
            await self._require_payload(artifact.id, payload)
            return artifact.id, str(artifact.resource_ref), True
        claim = await self._claims.acquire(ArtifactWriteClaimCommand(
            artifact_id=artifact.id, run_id=run_id,
            expected_revision=artifact.revision, lease_duration_ms=300_000,
        ))
        lease = ArtifactMutationLease(run_id=run_id, claim_token=claim.claim_token)
        replayed = artifact.committed_item_count == 1
        if not replayed:
            appended = await self._lifecycle.append(ArtifactAppendCommand(
                artifact_id=artifact.id, expected_revision=artifact.revision,
                sequence=1, batch_id="host-result",
                idempotency_key=scope.operation_scope_id, items=(payload,),
                coverage_keys=(scope.part_key,), write_lease=lease,
            ))
            if appended.accepted_count != 1:
                raise RuntimeError("Screenplay host result append was incomplete")
            artifact = await self._lifecycle.get(artifact.id)
        else:
            await self._require_payload(artifact.id, payload)
        artifact = await self._lifecycle.finalize(ArtifactFinalizeCommand(
            artifact_id=artifact.id, expected_revision=artifact.revision,
            expected_item_count=1, expected_coverage_keys=(scope.part_key,),
            resource_ref=SCREENPLAY_HOST_RESULT_REF_PREFIX + artifact.id,
            write_lease=lease,
        ))
        return artifact.id, str(artifact.resource_ref), replayed

    async def load_dependency(self, *, project_id, task_id, output_ref):
        if not str(output_ref).startswith(SCREENPLAY_HOST_RESULT_REF_PREFIX):
            raise ValueError("Screenplay dependency is not a host result")
        artifact = await self._repository.load(
            str(output_ref).removeprefix(SCREENPLAY_HOST_RESULT_REF_PREFIX)
        )
        if (
            artifact is None or artifact.status is not ArtifactStatus.FINALIZED
            or artifact.namespace != SCREENPLAY_HOST_RESULT_NAMESPACE
            or artifact.kind != SCREENPLAY_HOST_RESULT_KIND
            or artifact.owner_id != project_id
            or artifact.owner_ref.kind != "operation"
            or artifact.metadata.get("taskId") != task_id
            or artifact.resource_ref != output_ref
        ):
            raise ValueError("Screenplay dependency host result scope conflicts")
        payload = await self._load(artifact.id)
        if canonical_json_digest(payload) != artifact.metadata.get("payloadDigest"):
            raise ValueError("Screenplay dependency host result digest conflicts")
        return payload

    async def _load(self, artifact_id):
        batches = await self._repository.list_batches(artifact_id)
        if len(batches) != 1 or len(batches[0].items) != 1:
            raise ValueError("Screenplay host result Artifact is incomplete")
        return thaw_json_mapping(batches[0].items[0])

    async def _require_payload(self, artifact_id, payload):
        existing = await self._load(artifact_id)
        if canonical_json_digest(existing) != canonical_json_digest(payload):
            raise ValueError("Screenplay host result attempt payload conflict")


__all__ = ["SCREENPLAY_HOST_RESULT_REF_PREFIX", "ScreenplayHostResultArtifactStore"]
