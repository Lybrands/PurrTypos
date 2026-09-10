"""Single-item finalized Artifacts for recoverable analysis units and review."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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
from purra.json_values import thaw_json_mapping

from domains.novel_analysis import (
    NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX,
    canonical_digest,
)
from infrastructure.persistence.sqlite_artifact_claim_repository import (
    SqliteArtifactClaimRepository,
)
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)


class NovelAnalysisArtifactStore:
    def __init__(self, db, *, join_ambient_transaction: bool = False) -> None:
        self._repository = SqliteArtifactRepository(db, join_ambient_transaction=join_ambient_transaction)
        self._lifecycle = ArtifactLifecycle(self._repository)
        self._claims = SqliteArtifactClaimRepository(db, join_ambient_transaction=join_ambient_transaction)

    async def write(
        self,
        *,
        namespace: str,
        kind: str,
        owner_id: str,
        owner_ref_kind: str,
        owner_ref_id: str,
        run_id: str,
        semantic_key: str,
        payload: Mapping[str, Any],
        metadata: Mapping[str, Any] | None = None,
    ) -> dict:
        owner_ref = ArtifactOwnerRef(kind=owner_ref_kind, id=owner_ref_id)
        artifact = await self._repository.find_for_owner(
            namespace=namespace,
            kind=kind,
            owner_id=owner_id,
            owner_ref=owner_ref,
        )
        item = {"payload": dict(payload)}
        digest = canonical_digest(item)
        if artifact is None:
            artifact = await self._lifecycle.begin(ArtifactCreateCommand(
                namespace=namespace,
                kind=kind,
                owner_id=owner_id,
                owner_ref=owner_ref,
                created_by_run_id=run_id,
                expected_item_count=1,
                metadata={
                    "semanticKey": semantic_key,
                    **dict(metadata or {}),
                },
            ))
        batches = tuple(await self._repository.list_batches(artifact.id))
        if batches:
            stored = thaw_json_mapping(batches[0].items[0])
            if canonical_digest(stored) != digest:
                raise RuntimeError("novel analysis Artifact content conflicts")
        elif artifact.status is ArtifactStatus.OPEN:
            lease = await self._lease(artifact.id, run_id, artifact.revision)
            await self._lifecycle.append(ArtifactAppendCommand(
                artifact_id=artifact.id,
                expected_revision=artifact.revision,
                sequence=artifact.next_sequence,
                batch_id=semantic_key,
                idempotency_key=f"{owner_ref_id}:{semantic_key}",
                items=(item,),
                write_lease=lease,
                coverage_keys=(semantic_key,),
            ))
            artifact = await self._repository.load(artifact.id)
            if artifact is None:
                raise RuntimeError("novel analysis Artifact disappeared")
        if artifact.status is ArtifactStatus.OPEN:
            lease = await self._lease(artifact.id, run_id, artifact.revision)
            artifact = await self._lifecycle.finalize(ArtifactFinalizeCommand(
                artifact_id=artifact.id,
                expected_revision=artifact.revision,
                write_lease=lease,
                expected_item_count=1,
                expected_coverage_keys=(semantic_key,),
                resource_ref=f"{NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX}{artifact.id}",
            ))
        return await self.require(f"{NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX}{artifact.id}")

    async def require(self, reference: str) -> dict:
        artifact_id = self.artifact_id(reference)
        artifact = await self._repository.load(artifact_id)
        if artifact is None or artifact.status is not ArtifactStatus.FINALIZED:
            raise RuntimeError("novel analysis Artifact is not finalized")
        batches = tuple(await self._repository.list_batches(artifact.id))
        if len(batches) != 1 or len(batches[0].items) != 1:
            raise RuntimeError("novel analysis Artifact is incomplete")
        item = thaw_json_mapping(batches[0].items[0])
        payload = item.get("payload")
        if not isinstance(payload, Mapping):
            raise RuntimeError("novel analysis Artifact payload is invalid")
        return {
            **dict(payload),
            "artifactId": artifact.id,
            "artifactKind": artifact.kind,
            "artifactRevision": artifact.revision,
            "artifactDigest": batches[0].content_digest,
            "createdByRunId": artifact.created_by_run_id,
            "metadata": thaw_json_mapping(artifact.metadata),
        }

    @staticmethod
    def artifact_id(reference: str) -> str:
        value = str(reference or "").strip()
        if not value.startswith(NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX):
            raise ValueError("invalid novel analysis Artifact reference")
        artifact_id = value.removeprefix(NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX)
        if not artifact_id:
            raise ValueError("novel analysis Artifact id is required")
        return artifact_id

    async def _lease(self, artifact_id: str, run_id: str, revision: int):
        claim = await self._claims.acquire(ArtifactWriteClaimCommand(
            artifact_id=artifact_id,
            run_id=run_id,
            expected_revision=revision,
            lease_duration_ms=300_000,
        ))
        return ArtifactMutationLease(
            run_id=claim.run_id,
            claim_token=claim.claim_token,
        )


__all__ = ["NovelAnalysisArtifactStore"]
