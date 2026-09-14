"""Attempt-scoped Candidate Artifacts for Screenplay replacement Parts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from agents.screenplay.contracts import ScreenplayPartOperationScope
from infrastructure.persistence.sqlite_artifact_claim_repository import (
    SqliteArtifactClaimRepository,
)
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
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


SCREENPLAY_CANDIDATE_NAMESPACE = "purrtypos.screenplay.v1"
SCREENPLAY_CANDIDATE_KIND = "candidate_part_attempt"
SCREENPLAY_CANDIDATE_SCHEMA_VERSION = 1
SCREENPLAY_CANDIDATE_REF_PREFIX = "screenplay-candidate-v1://"


@dataclass(frozen=True, slots=True)
class ScreenplayCandidateReceipt:
    artifact_id: str
    resource_ref: str
    replayed: bool


def validate_candidate_payload(
    scope: ScreenplayPartOperationScope,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "schemaVersion", "partKind", "partKey", "targetRole", "payload",
    }:
        raise ValueError("Screenplay Candidate payload shape is invalid")
    if value["schemaVersion"] != SCREENPLAY_CANDIDATE_SCHEMA_VERSION:
        raise ValueError("Screenplay Candidate schema version is invalid")
    if (
        value["partKind"] != scope.part_kind.value
        or value["partKey"] != scope.part_key
        or value["targetRole"] != scope.target_role
    ):
        raise ValueError("Screenplay Candidate payload scope conflicts")
    payload = value["payload"]
    if not isinstance(payload, Mapping) or not payload:
        raise ValueError("Screenplay Candidate content is required")
    _validate_role_payload(scope, payload)
    normalized = {
        "schemaVersion": SCREENPLAY_CANDIDATE_SCHEMA_VERSION,
        "partKind": scope.part_kind.value,
        "partKey": scope.part_key,
        "targetRole": scope.target_role,
        "payload": dict(payload),
    }
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(encoded) > 300_000:
        raise ValueError("Screenplay Candidate payload exceeds host limit")
    canonical_json_digest(normalized)
    return normalized


def _validate_role_payload(
    scope: ScreenplayPartOperationScope,
    payload: Mapping[str, Any],
) -> None:
    if scope.target_role == "review" and scope.part_kind.value == "review_dimension":
        _validate_review_payload(payload)
        return
    if scope.target_role != "sceneList" or scope.part_kind.value != "document_section":
        return
    if set(payload) != {"episodeNumber", "title", "scenes"}:
        raise ValueError("Screenplay scene-list episode shape is invalid")
    if payload.get("episodeNumber") != scope.episode_number:
        raise ValueError("Screenplay scene-list episode scope conflicts")
    title = str(payload.get("title") or "").strip()
    scenes = payload.get("scenes")
    if not title or not isinstance(scenes, list) or not scenes:
        raise ValueError("Screenplay scene-list episode content is incomplete")
    scene_ids = []
    for scene in scenes:
        if not isinstance(scene, Mapping):
            raise ValueError("Screenplay scene-list scene shape is invalid")
        scene_id = str(scene.get("id") or "").strip()
        if not scene_id:
            raise ValueError("Screenplay scene-list scene id is required")
        scene_ids.append(scene_id)
    if len(scene_ids) != len(set(scene_ids)):
        raise ValueError("Screenplay scene-list scene ids must be unique")


def _validate_review_payload(payload: Mapping[str, Any]) -> None:
    if set(payload) != {"verdict", "issues"}:
        raise ValueError("Screenplay review shape is invalid")
    verdict = str(payload.get("verdict") or "").strip()
    if verdict not in {"ready", "revise", "major_rework"}:
        raise ValueError("Screenplay review verdict is invalid")
    issues = payload.get("issues")
    if not isinstance(issues, list):
        raise ValueError("Screenplay review issues are invalid")
    issue_ids = []
    for issue in issues:
        if (
            not isinstance(issue, Mapping)
            or not {"id", "description", "sceneIds"}.issubset(issue)
            or not set(issue).issubset({
                "id", "severity", "description", "sceneIds",
            })
        ):
            raise ValueError("Screenplay review issue shape is invalid")
        issue_id = str(issue.get("id") or "").strip()
        description = str(issue.get("description") or "").strip()
        severity = str(issue.get("severity") or "minor").strip()
        scene_ids = issue.get("sceneIds")
        if not issue_id or not description:
            raise ValueError("Screenplay review issue content is incomplete")
        if severity not in {"minor", "major", "critical"}:
            raise ValueError("Screenplay review issue severity is invalid")
        if (
            not isinstance(scene_ids, list)
            or any(not str(scene_id).strip() for scene_id in scene_ids)
        ):
            raise ValueError("Screenplay review issue scene ids are invalid")
        issue_ids.append(issue_id)
    if len(issue_ids) != len(set(issue_ids)):
        raise ValueError("Screenplay review issue ids must be unique")
    if verdict == "ready" and issues:
        raise ValueError("Screenplay ready review cannot contain issues")
    if verdict != "ready" and not issues:
        raise ValueError("Screenplay revision review requires issues")


class ScreenplayCandidateArtifactStore:
    def __init__(self, db, *, lifecycle=None, claims=None) -> None:
        self._repository = SqliteArtifactRepository(db)
        self._claims = claims or SqliteArtifactClaimRepository(db)
        self._lifecycle = lifecycle or ArtifactLifecycle(self._repository)

    async def commit(
        self,
        *,
        scope: ScreenplayPartOperationScope,
        run_id: str,
        payload: Mapping[str, Any],
    ) -> ScreenplayCandidateReceipt:
        normalized = validate_candidate_payload(scope, payload)
        operation_id = scope.operation_scope_id
        owner_ref = ArtifactOwnerRef("operation", operation_id)
        artifact = await self._repository.find_for_owner(
            namespace=SCREENPLAY_CANDIDATE_NAMESPACE,
            kind=SCREENPLAY_CANDIDATE_KIND,
            owner_id=scope.project_id,
            owner_ref=owner_ref,
        )
        if artifact is None:
            artifact = await self._repository.create(
                "screenplay_candidate_" + hashlib.sha256(
                    operation_id.encode("utf-8")
                ).hexdigest()[:32],
                ArtifactCreateCommand(
                    namespace=SCREENPLAY_CANDIDATE_NAMESPACE,
                    kind=SCREENPLAY_CANDIDATE_KIND,
                    owner_id=scope.project_id,
                    owner_ref=owner_ref,
                    created_by_run_id=str(run_id or "").strip(),
                    schema_version=SCREENPLAY_CANDIDATE_SCHEMA_VERSION,
                    expected_item_count=1,
                    metadata={
                        "taskId": scope.task_id,
                        "unitId": scope.unit_id,
                        "attempt": scope.attempt,
                        "operationScopeId": operation_id,
                        "partKind": scope.part_kind.value,
                        "partKey": scope.part_key,
                        "payloadDigest": canonical_json_digest(normalized),
                    },
                ),
            )
        self._require_identity(artifact, scope, normalized)
        if artifact.status is ArtifactStatus.FINALIZED:
            await self._require_payload(artifact.id, normalized)
            return _receipt(artifact.id, replayed=True)
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
                batch_id="candidate",
                idempotency_key=operation_id,
                items=(normalized,),
                coverage_keys=(scope.part_key,),
                write_lease=lease,
            ))
            if appended.accepted_count != 1:
                raise RuntimeError("Screenplay Candidate append was incomplete")
            artifact = await self._lifecycle.get(artifact.id)
        else:
            await self._require_payload(artifact.id, normalized)
        finalized = await self._lifecycle.finalize(ArtifactFinalizeCommand(
            artifact_id=artifact.id,
            expected_revision=artifact.revision,
            expected_item_count=1,
            expected_coverage_keys=(scope.part_key,),
            resource_ref=SCREENPLAY_CANDIDATE_REF_PREFIX + artifact.id,
            write_lease=lease,
        ))
        return _receipt(finalized.id, replayed=replayed)

    async def load(self, artifact_id: str) -> dict[str, Any]:
        artifact = await self._lifecycle.get(artifact_id)
        if artifact.status is not ArtifactStatus.FINALIZED:
            raise ValueError("Screenplay Candidate Artifact is not finalized")
        batches = await self._repository.list_batches(artifact.id)
        if len(batches) != 1 or len(batches[0].items) != 1:
            raise ValueError("Screenplay Candidate Artifact payload is invalid")
        return thaw_json_mapping(batches[0].items[0])

    async def load_dependency(
        self,
        *,
        project_id: str,
        task_id: str,
        output_ref: str,
    ) -> dict[str, Any]:
        if not output_ref.startswith(SCREENPLAY_CANDIDATE_REF_PREFIX):
            raise ValueError("Screenplay dependency is not a replacement Candidate")
        artifact_id = output_ref.removeprefix(SCREENPLAY_CANDIDATE_REF_PREFIX)
        artifact = await self._repository.load(artifact_id)
        if (
            artifact is None
            or artifact.status is not ArtifactStatus.FINALIZED
            or artifact.namespace != SCREENPLAY_CANDIDATE_NAMESPACE
            or artifact.kind != SCREENPLAY_CANDIDATE_KIND
            or artifact.owner_id != project_id
            or artifact.owner_ref.kind != "operation"
            or artifact.metadata.get("taskId") != task_id
            or artifact.resource_ref != output_ref
        ):
            raise ValueError("Screenplay dependency Candidate scope conflicts")
        return await self.load(artifact_id)

    async def require_scope_artifact(
        self,
        scope: ScreenplayPartOperationScope,
        artifact_id: str,
    ) -> tuple[dict[str, Any], str]:
        artifact = await self._repository.load(str(artifact_id or "").strip())
        if (
            artifact is None
            or artifact.status is not ArtifactStatus.FINALIZED
            or artifact.namespace != SCREENPLAY_CANDIDATE_NAMESPACE
            or artifact.kind != SCREENPLAY_CANDIDATE_KIND
            or artifact.owner_id != scope.project_id
            or artifact.owner_ref.kind != "operation"
            or artifact.owner_ref.id != scope.operation_scope_id
            or artifact.metadata.get("taskId") != scope.task_id
            or artifact.metadata.get("unitId") != scope.unit_id
            or artifact.metadata.get("attempt") != scope.attempt
        ):
            raise ValueError("Screenplay Candidate Artifact scope conflicts")
        payload = await self.load(artifact.id)
        self._require_identity(artifact, scope, payload)
        return payload, str(artifact.resource_ref or "")

    async def try_load_scope_payload(
        self,
        scope: ScreenplayPartOperationScope,
    ) -> dict[str, Any] | None:
        artifact = await self._repository.find_for_owner(
            namespace=SCREENPLAY_CANDIDATE_NAMESPACE,
            kind=SCREENPLAY_CANDIDATE_KIND,
            owner_id=scope.project_id,
            owner_ref=ArtifactOwnerRef("operation", scope.operation_scope_id),
        )
        if artifact is None or artifact.status is not ArtifactStatus.FINALIZED:
            return None
        payload, _ = await self.require_scope_artifact(scope, artifact.id)
        return payload

    async def require_scope_receipt(
        self,
        scope: ScreenplayPartOperationScope,
    ) -> ScreenplayCandidateReceipt:
        artifact = await self._repository.find_for_owner(
            namespace=SCREENPLAY_CANDIDATE_NAMESPACE,
            kind=SCREENPLAY_CANDIDATE_KIND,
            owner_id=scope.project_id,
            owner_ref=ArtifactOwnerRef("operation", scope.operation_scope_id),
        )
        if artifact is None:
            raise ValueError("Screenplay Candidate was not submitted")
        await self.require_scope_artifact(scope, artifact.id)
        return _receipt(artifact.id, replayed=True)

    def _require_identity(self, artifact, scope, payload) -> None:
        metadata = artifact.metadata
        if (
            metadata.get("operationScopeId") != scope.operation_scope_id
            or metadata.get("payloadDigest") != canonical_json_digest(payload)
        ):
            raise ValueError("Screenplay Candidate attempt identity conflict")

    async def _require_payload(self, artifact_id, payload) -> None:
        batches = await self._repository.list_batches(artifact_id)
        if len(batches) != 1 or len(batches[0].items) != 1:
            raise ValueError("Screenplay Candidate attempt payload is invalid")
        existing = thaw_json_mapping(batches[0].items[0])
        if canonical_json_digest(existing) != canonical_json_digest(payload):
            raise ValueError("Screenplay Candidate attempt payload conflict")


def _receipt(artifact_id: str, *, replayed: bool) -> ScreenplayCandidateReceipt:
    return ScreenplayCandidateReceipt(
        artifact_id=artifact_id,
        resource_ref=SCREENPLAY_CANDIDATE_REF_PREFIX + artifact_id,
        replayed=replayed,
    )


__all__ = [
    "SCREENPLAY_CANDIDATE_REF_PREFIX",
    "ScreenplayCandidateArtifactStore",
    "ScreenplayCandidateReceipt",
    "validate_candidate_payload",
]
