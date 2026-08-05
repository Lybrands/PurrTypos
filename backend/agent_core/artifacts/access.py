"""Core coordinator for Artifact authorization and exclusive write claims."""

from __future__ import annotations

from agent_core.artifacts.continuity import (
    ArtifactAccessGrant,
    ArtifactAccessMode,
    ArtifactAccessPolicy,
    ArtifactAccessRequest,
    ArtifactClaimLeaseCommand,
    ArtifactResumeCandidate,
    ArtifactWriteClaim,
    ArtifactWriteClaimCommand,
)
from agent_core.artifacts.errors import ArtifactAccessDeniedError
from agent_core.artifacts.ports import ArtifactClaimRepository


class ArtifactAccessController:
    """Authorize access and atomically claim Work Item-scoped writers."""

    def __init__(
        self,
        claim_repository: ArtifactClaimRepository,
        *,
        policy: ArtifactAccessPolicy | None = None,
    ) -> None:
        self._claim_repository = claim_repository
        self._policy = policy or ArtifactAccessPolicy()

    async def authorize(
        self,
        candidate: ArtifactResumeCandidate,
        request: ArtifactAccessRequest,
        *,
        lease_duration_ms: int | None = None,
    ) -> ArtifactAccessGrant:
        decision = self._policy.decide(candidate, request)
        if not decision.allowed:
            raise ArtifactAccessDeniedError(
                "artifact access was denied",
                code=decision.reason.value,
                details={
                    "artifactId": decision.artifact_id,
                    "runId": decision.run_id,
                    "mode": decision.mode.value,
                    "artifactRevision": decision.artifact_revision,
                },
            )
        claim: ArtifactWriteClaim | None = None
        if decision.requires_write_claim:
            if lease_duration_ms is None:
                raise ValueError(
                    "Work Item-scoped write access requires lease_duration_ms"
                )
            work_item_id = candidate.binding.work_item_id
            if work_item_id is None:
                raise RuntimeError(
                    "Work Item-scoped candidate lost its Work Item identity"
                )
            claim = await self._claim_repository.acquire(ArtifactWriteClaimCommand(
                artifact_id=candidate.artifact_id,
                work_item_id=work_item_id,
                run_id=request.run_id,
                expected_revision=request.expected_revision,
                lease_duration_ms=lease_duration_ms,
            ))
            if (
                claim.artifact_id != candidate.artifact_id
                or claim.work_item_id != work_item_id
                or claim.run_id != request.run_id
                or claim.acquired_revision != request.expected_revision
            ):
                raise TypeError(
                    "artifact claim repository returned a mismatched claim"
                )
        elif lease_duration_ms is not None:
            raise ValueError("this artifact access does not require a write lease")
        return ArtifactAccessGrant(decision=decision, write_claim=claim)

    async def renew(
        self,
        command: ArtifactClaimLeaseCommand,
    ) -> ArtifactWriteClaim:
        if command.lease_duration_ms is None:
            raise ValueError("renew requires lease_duration_ms")
        return await self._claim_repository.renew(command)

    async def release(self, command: ArtifactClaimLeaseCommand) -> bool:
        if command.lease_duration_ms is not None:
            raise ValueError("release cannot include lease_duration_ms")
        return await self._claim_repository.release(command)


__all__ = ["ArtifactAccessController"]
