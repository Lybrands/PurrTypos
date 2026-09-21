"""Attempt-scoped host-captured outputs for Screenplay replacement Parts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from agents.screenplay.contracts import ScreenplayPartKind, ScreenplayPartOperationScope
from infrastructure.persistence.sqlite_artifact_claim_repository import SqliteArtifactClaimRepository
from infrastructure.persistence.sqlite_artifact_repository import SqliteArtifactRepository
from purra.artifacts import (
    ArtifactAppendCommand,
    ArtifactCreateCommand,
    ArtifactFinalizeCommand,
    ArtifactLifecycle,
    ArtifactMutationLease,
    ArtifactOwnerRef,
    ArtifactStatus,
    ArtifactWriteClaimCommand,
)
from purra.json_values import canonical_json_digest, thaw_json_mapping


SCREENPLAY_CAPTURE_NAMESPACE = "purrtypos.screenplay.v1"
SCREENPLAY_CAPTURE_KIND = "host_capture_attempt"
SCREENPLAY_CAPTURE_REF_PREFIX = "screenplay-capture-v1://"


def validate_screenplay_host_capture(scope, value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Screenplay host capture must be an object")
    if scope.part_kind is ScreenplayPartKind.DRAFT_SCENE:
        if set(value) != {"sceneId", "sceneText"}:
            raise ValueError("Screenplay scene capture shape is invalid")
        scene_id = str(value.get("sceneId") or "").strip()
        scene_text = str(value.get("sceneText") or "").strip()
        if scene_id != scope.scene_id or not scene_text:
            raise ValueError("Screenplay scene capture scope is invalid")
        result = {"sceneId": scene_id, "sceneText": scene_text}
    elif scope.part_kind is ScreenplayPartKind.EPISODE_METADATA:
        if set(value) != {"episodeNumber", "title", "continuitySummary"}:
            raise ValueError("Screenplay episode metadata capture shape is invalid")
        number = value.get("episodeNumber")
        title = str(value.get("title") or "").strip()
        continuity = str(value.get("continuitySummary") or "").strip()
        if type(number) is not int or number != scope.episode_number or not title or not continuity:
            raise ValueError("Screenplay episode metadata capture scope is invalid")
        result = {"episodeNumber": number, "title": title, "continuitySummary": continuity}
    else:
        raise ValueError("Screenplay Part is not host-captured")
    if len(str(result)) > 160_000:
        raise ValueError("Screenplay host capture exceeds host limit")
    canonical_json_digest(result)
    return result


class ScreenplayCaptureArtifactStore:
    def __init__(self, db) -> None:
        self._repository = SqliteArtifactRepository(db)
        self._claims = SqliteArtifactClaimRepository(db)
        self._lifecycle = ArtifactLifecycle(self._repository)

    async def commit(self, *, scope, run_id: str, payload: Mapping[str, Any]):
        normalized = validate_screenplay_host_capture(scope, payload)
        owner = ArtifactOwnerRef("operation", scope.operation_scope_id)
        artifact = await self._repository.find_for_owner(
            namespace=SCREENPLAY_CAPTURE_NAMESPACE,
            kind=SCREENPLAY_CAPTURE_KIND,
            owner_id=scope.project_id,
            owner_ref=owner,
        )
        digest = canonical_json_digest(normalized)
        if artifact is None:
            artifact = await self._repository.create(
                "screenplay_capture_" + hashlib.sha256(scope.operation_scope_id.encode()).hexdigest()[:32],
                ArtifactCreateCommand(
                    namespace=SCREENPLAY_CAPTURE_NAMESPACE,
                    kind=SCREENPLAY_CAPTURE_KIND,
                    owner_id=scope.project_id,
                    owner_ref=owner,
                    created_by_run_id=run_id,
                    schema_version=1,
                    expected_item_count=1,
                    metadata={
                        "taskId": scope.task_id,
                        "unitId": scope.unit_id,
                        "attempt": scope.attempt,
                        "operationScopeId": scope.operation_scope_id,
                        "partKind": scope.part_kind.value,
                        "payloadDigest": digest,
                    },
                ),
            )
        self._require_identity(artifact, scope, digest)
        if artifact.status is ArtifactStatus.FINALIZED:
            await self._require_payload(artifact.id, normalized)
            return artifact.id, str(artifact.resource_ref), True
        claim = await self._claims.acquire(ArtifactWriteClaimCommand(
            artifact_id=artifact.id,
            run_id=run_id,
            expected_revision=artifact.revision,
            lease_duration_ms=300_000,
        ))
        lease = ArtifactMutationLease(run_id=run_id, claim_token=claim.claim_token)
        replayed = artifact.committed_item_count == 1
        if not replayed:
            appended = await self._lifecycle.append(ArtifactAppendCommand(
                artifact_id=artifact.id,
                expected_revision=artifact.revision,
                sequence=1,
                batch_id="capture",
                idempotency_key=scope.operation_scope_id,
                items=(normalized,),
                coverage_keys=(scope.part_key,),
                write_lease=lease,
            ))
            if appended.accepted_count != 1:
                raise RuntimeError("Screenplay host capture append was incomplete")
            artifact = await self._lifecycle.get(artifact.id)
        else:
            await self._require_payload(artifact.id, normalized)
        artifact = await self._lifecycle.finalize(ArtifactFinalizeCommand(
            artifact_id=artifact.id,
            expected_revision=artifact.revision,
            expected_item_count=1,
            expected_coverage_keys=(scope.part_key,),
            resource_ref=SCREENPLAY_CAPTURE_REF_PREFIX + artifact.id,
            write_lease=lease,
        ))
        return artifact.id, str(artifact.resource_ref), replayed

    async def try_load(self, scope):
        artifact = await self._repository.find_for_owner(
            namespace=SCREENPLAY_CAPTURE_NAMESPACE,
            kind=SCREENPLAY_CAPTURE_KIND,
            owner_id=scope.project_id,
            owner_ref=ArtifactOwnerRef("operation", scope.operation_scope_id),
        )
        if artifact is None or artifact.status is not ArtifactStatus.FINALIZED:
            return None
        self._require_identity(artifact, scope, str(artifact.metadata.get("payloadDigest") or ""))
        batches = await self._repository.list_batches(artifact.id)
        if len(batches) != 1 or len(batches[0].items) != 1:
            raise ValueError("Screenplay capture Artifact payload is invalid")
        payload = thaw_json_mapping(batches[0].items[0])
        if canonical_json_digest(payload) != artifact.metadata.get("payloadDigest"):
            raise ValueError("Screenplay capture Artifact digest conflicts")
        return artifact.id, str(artifact.resource_ref), payload

    async def load_dependency(self, *, project_id, task_id, output_ref):
        if not str(output_ref).startswith(SCREENPLAY_CAPTURE_REF_PREFIX):
            raise ValueError("Screenplay dependency is not a host capture")
        artifact = await self._repository.load(
            str(output_ref).removeprefix(SCREENPLAY_CAPTURE_REF_PREFIX)
        )
        if (
            artifact is None
            or artifact.status is not ArtifactStatus.FINALIZED
            or artifact.namespace != SCREENPLAY_CAPTURE_NAMESPACE
            or artifact.kind != SCREENPLAY_CAPTURE_KIND
            or artifact.owner_id != project_id
            or artifact.metadata.get("taskId") != task_id
            or artifact.resource_ref != output_ref
        ):
            raise ValueError("Screenplay dependency host capture scope conflicts")
        batches = await self._repository.list_batches(artifact.id)
        if len(batches) != 1 or len(batches[0].items) != 1:
            raise ValueError("Screenplay dependency host capture is incomplete")
        payload = thaw_json_mapping(batches[0].items[0])
        if canonical_json_digest(payload) != artifact.metadata.get("payloadDigest"):
            raise ValueError("Screenplay dependency host capture digest conflicts")
        return payload

    def _require_identity(self, artifact, scope, digest):
        if (
            artifact.owner_ref.id != scope.operation_scope_id
            or artifact.metadata.get("operationScopeId") != scope.operation_scope_id
            or artifact.metadata.get("payloadDigest") != digest
        ):
            raise ValueError("Screenplay capture attempt identity conflict")

    async def _require_payload(self, artifact_id, payload):
        batches = await self._repository.list_batches(artifact_id)
        if len(batches) != 1 or len(batches[0].items) != 1:
            raise ValueError("Screenplay capture attempt payload is invalid")
        if canonical_json_digest(thaw_json_mapping(batches[0].items[0])) != canonical_json_digest(payload):
            raise ValueError("Screenplay capture attempt payload conflict")


__all__ = [
    "SCREENPLAY_CAPTURE_REF_PREFIX",
    "ScreenplayCaptureArtifactStore",
    "validate_screenplay_host_capture",
]
