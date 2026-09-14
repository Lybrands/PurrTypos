"""Attempt-scoped immutable Artifacts for replacement analysis Units."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

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


NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE = "purrtypos.novel_analysis.v1"
NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_KIND = "unit_attempt_result"
NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class AnalysisAttemptArtifactReceipt:
    artifact_id: str
    resource_ref: str
    replayed: bool


class NovelAnalysisAttemptArtifactStore:
    """Commit one payload per Operation; retries always get another Artifact."""

    def __init__(self, db, *, lifecycle=None, claims=None) -> None:
        self._repository = SqliteArtifactRepository(db)
        self._claims = claims or SqliteArtifactClaimRepository(db)
        self._lifecycle = lifecycle or ArtifactLifecycle(self._repository)

    async def commit(
        self,
        *,
        task_id: str,
        unit_id: str,
        attempt: int,
        operation_id: str,
        run_id: str,
        payload: dict[str, object],
    ) -> AnalysisAttemptArtifactReceipt:
        expected_operation_id = f"{task_id}:{unit_id}:{attempt}"
        if operation_id != expected_operation_id or type(attempt) is not int or attempt < 0:
            raise ValueError("analysis attempt operation identity is invalid")
        owner_ref = ArtifactOwnerRef("operation", operation_id)
        artifact = await self._repository.find_for_owner(
            namespace=NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE,
            kind=NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_KIND,
            owner_id=task_id,
            owner_ref=owner_ref,
        )
        if artifact is None:
            artifact = await self._repository.create(
                "analysis_attempt_" + hashlib.sha256(
                    operation_id.encode("utf-8")
                ).hexdigest()[:32],
                ArtifactCreateCommand(
                    namespace=NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE,
                    kind=NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_KIND,
                    owner_id=task_id,
                    owner_ref=owner_ref,
                    created_by_run_id=run_id,
                    schema_version=NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_SCHEMA_VERSION,
                    expected_item_count=1,
                    metadata={
                        "taskId": task_id,
                        "unitId": unit_id,
                        "attempt": attempt,
                        "operationId": operation_id,
                        "payloadDigest": canonical_json_digest(payload),
                    },
                ),
            )
        self._require_same_attempt(artifact, operation_id, payload)
        if artifact.status is ArtifactStatus.FINALIZED:
            await self._require_same_payload(artifact.id, payload)
            return _receipt(artifact.id, replayed=True)
        claim = await self._claims.acquire(ArtifactWriteClaimCommand(
            artifact_id=artifact.id,
            run_id=run_id,
            expected_revision=artifact.revision,
            lease_duration_ms=300_000,
        ))
        lease = ArtifactMutationLease(
            run_id=run_id,
            claim_token=claim.claim_token,
        )
        replayed = artifact.committed_item_count == 1
        if artifact.committed_item_count == 0:
            appended = await self._lifecycle.append(ArtifactAppendCommand(
                artifact_id=artifact.id,
                expected_revision=artifact.revision,
                sequence=1,
                batch_id="result",
                idempotency_key=operation_id,
                items=(payload,),
                coverage_keys=(unit_id,),
                write_lease=lease,
            ))
            artifact = await self._lifecycle.get(artifact.id)
            if appended.accepted_count != 1:
                raise RuntimeError("analysis attempt Artifact append was incomplete")
        else:
            await self._require_same_payload(artifact.id, payload)
        finalized = await self._lifecycle.finalize(ArtifactFinalizeCommand(
            artifact_id=artifact.id,
            expected_revision=artifact.revision,
            expected_item_count=1,
            expected_coverage_keys=(unit_id,),
            resource_ref=f"novel-analysis-v1://{artifact.id}",
            write_lease=lease,
        ))
        return _receipt(finalized.id, replayed=replayed)

    async def load_payload(self, artifact_id: str) -> dict[str, object]:
        artifact = await self._lifecycle.get(artifact_id)
        if artifact.status is not ArtifactStatus.FINALIZED:
            raise ValueError("analysis attempt Artifact is not finalized")
        batches = await self._repository.list_batches(artifact.id)
        if len(batches) != 1 or len(batches[0].items) != 1:
            raise ValueError("analysis attempt Artifact payload shape is invalid")
        return thaw_json_mapping(batches[0].items[0])

    async def load_operation_payload(
        self,
        *,
        task_id: str,
        operation_id: str,
    ) -> dict[str, object]:
        """Read the finalized result submitted by one durable Operation."""

        artifact = await self._repository.find_for_owner(
            namespace=NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE,
            kind=NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_KIND,
            owner_id=task_id,
            owner_ref=ArtifactOwnerRef("operation", operation_id),
        )
        if artifact is None:
            raise ValueError("analysis Operation did not submit a result")
        return await self.load_payload(artifact.id)

    async def try_load_operation_payload(
        self,
        *,
        task_id: str,
        operation_id: str,
    ) -> dict[str, object] | None:
        artifact = await self._repository.find_for_owner(
            namespace=NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE,
            kind=NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_KIND,
            owner_id=task_id,
            owner_ref=ArtifactOwnerRef("operation", operation_id),
        )
        if artifact is None or artifact.status is not ArtifactStatus.FINALIZED:
            return None
        return await self.load_payload(artifact.id)

    def _require_same_attempt(self, artifact, operation_id, payload) -> None:
        metadata = artifact.metadata
        if (
            metadata.get("operationId") != operation_id
            or metadata.get("payloadDigest") != canonical_json_digest(payload)
        ):
            raise ValueError("analysis attempt Artifact identity conflict")

    async def _require_same_payload(self, artifact_id, payload) -> None:
        batches = await self._repository.list_batches(artifact_id)
        if (
            len(batches) != 1
            or len(batches[0].items) != 1
            or canonical_json_digest(thaw_json_mapping(batches[0].items[0]))
            != canonical_json_digest(payload)
        ):
            raise ValueError("analysis attempt Artifact payload conflict")


def _receipt(artifact_id: str, *, replayed: bool) -> AnalysisAttemptArtifactReceipt:
    return AnalysisAttemptArtifactReceipt(
        artifact_id=artifact_id,
        resource_ref=f"novel-analysis-v1://{artifact_id}",
        replayed=replayed,
    )


__all__ = [
    "AnalysisAttemptArtifactReceipt",
    "NovelAnalysisAttemptArtifactStore",
]
