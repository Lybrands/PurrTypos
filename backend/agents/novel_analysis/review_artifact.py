"""Immutable user-reviewed Artifacts for replacement Novel Analysis."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from agents.novel_analysis.review_projection import NOVEL_ANALYSIS_REVIEW_REF_PREFIX
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


NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE = (
    "purrtypos.novel_analysis.review.v2"
)
NOVEL_ANALYSIS_REVIEWED_ARTIFACT_KIND = "user_reviewed_result"


@dataclass(frozen=True, slots=True)
class ReviewedArtifactReceipt:
    artifact_id: str
    resource_ref: str
    replayed: bool


class NovelAnalysisReviewedArtifactStore:
    def __init__(self, db) -> None:
        self._repository = SqliteArtifactRepository(db)
        self._claims = SqliteArtifactClaimRepository(db)
        self._lifecycle = ArtifactLifecycle(self._repository)

    async def commit(
        self,
        *,
        source_revision_id: str,
        source_artifact_id: str,
        task_id: str,
        command_id: str,
        run_id: str,
        payload: dict[str, object],
    ) -> ReviewedArtifactReceipt:
        command = _required(command_id, "review command id")
        revision = _required(source_revision_id, "source revision id")
        source_id = _required(source_artifact_id, "source Artifact id")
        owner_ref = ArtifactOwnerRef("analysis_review_command", command)
        artifact = await self._repository.find_for_owner(
            namespace=NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE,
            kind=NOVEL_ANALYSIS_REVIEWED_ARTIFACT_KIND,
            owner_id=revision,
            owner_ref=owner_ref,
        )
        digest = canonical_json_digest(payload)
        if artifact is None:
            artifact = await self._repository.create(
                "analysis_review_" + hashlib.sha256(
                    f"{revision}:{command}".encode("utf-8")
                ).hexdigest()[:32],
                ArtifactCreateCommand(
                    namespace=NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE,
                    kind=NOVEL_ANALYSIS_REVIEWED_ARTIFACT_KIND,
                    owner_id=revision,
                    owner_ref=owner_ref,
                    created_by_run_id=_required(run_id, "source Run id"),
                    schema_version=2,
                    expected_item_count=1,
                    metadata={
                        "sourceArtifactId": source_id,
                        "taskId": _required(task_id, "analysis task id"),
                        "reviewCommandId": command,
                        "payloadDigest": digest,
                    },
                ),
            )
        if (
            artifact.metadata.get("sourceArtifactId") != source_id
            or artifact.metadata.get("reviewCommandId") != command
            or artifact.metadata.get("payloadDigest") != digest
        ):
            raise ValueError("analysis review command identity conflicts")
        if artifact.status is ArtifactStatus.FINALIZED:
            await self._require_payload(artifact.id, payload)
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
            await self._lifecycle.append(ArtifactAppendCommand(
                artifact_id=artifact.id,
                expected_revision=artifact.revision,
                sequence=1,
                batch_id="reviewed-result",
                idempotency_key=command,
                items=(payload,),
                coverage_keys=(source_id,),
                write_lease=lease,
            ))
            artifact = await self._lifecycle.get(artifact.id)
        else:
            await self._require_payload(artifact.id, payload)
        finalized = await self._lifecycle.finalize(ArtifactFinalizeCommand(
            artifact_id=artifact.id,
            expected_revision=artifact.revision,
            expected_item_count=1,
            expected_coverage_keys=(source_id,),
            resource_ref=NOVEL_ANALYSIS_REVIEW_REF_PREFIX + artifact.id,
            write_lease=lease,
        ))
        return _receipt(finalized.id, replayed=replayed)

    async def load(self, artifact_id: str):
        artifact = await self._repository.load(artifact_id)
        if (
            artifact is None
            or artifact.status is not ArtifactStatus.FINALIZED
            or artifact.namespace != NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE
            or artifact.kind != NOVEL_ANALYSIS_REVIEWED_ARTIFACT_KIND
            or artifact.schema_version != 2
        ):
            raise ValueError("reviewed analysis Artifact is unavailable")
        batches = await self._repository.list_batches(artifact.id)
        if len(batches) != 1 or len(batches[0].items) != 1:
            raise ValueError("reviewed analysis Artifact is incomplete")
        payload = thaw_json_mapping(batches[0].items[0])
        if canonical_json_digest(payload) != artifact.metadata.get("payloadDigest"):
            raise ValueError("reviewed analysis Artifact digest changed")
        return artifact, payload

    async def _require_payload(self, artifact_id: str, payload) -> None:
        batches = await self._repository.list_batches(artifact_id)
        if (
            len(batches) != 1
            or len(batches[0].items) != 1
            or canonical_json_digest(
                thaw_json_mapping(batches[0].items[0])
            ) != canonical_json_digest(payload)
        ):
            raise ValueError("reviewed analysis Artifact payload conflicts")


def _required(value, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _receipt(artifact_id: str, *, replayed: bool) -> ReviewedArtifactReceipt:
    return ReviewedArtifactReceipt(
        artifact_id=artifact_id,
        resource_ref=NOVEL_ANALYSIS_REVIEW_REF_PREFIX + artifact_id,
        replayed=replayed,
    )


__all__ = [
    "NOVEL_ANALYSIS_REVIEWED_ARTIFACT_KIND",
    "NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE",
    "NovelAnalysisReviewedArtifactStore",
    "ReviewedArtifactReceipt",
]
