from __future__ import annotations

import pytest

from agent_core.artifacts import (
    ArtifactAccessController,
    ArtifactAccessMode,
    ArtifactAccessReason,
    ArtifactAccessRequest,
    ArtifactClaimLeaseCommand,
    ArtifactResumeCandidate,
    ArtifactScope,
    ArtifactScopeBinding,
    ArtifactStatus,
    ArtifactWriteClaim,
    ArtifactWriteClaimCommand,
)
from agent_core.artifacts.errors import ArtifactAccessDeniedError
from agent_core.artifacts.ports import ArtifactClaimRepository
from agent_core.work_items import WorkItemRunRelation, WorkItemStatus


class _MemoryArtifactClaimRepository:
    def __init__(self) -> None:
        self.active: dict[str, ArtifactWriteClaim] = {}
        self.acquire_count = 0

    async def acquire(
        self,
        command: ArtifactWriteClaimCommand,
    ) -> ArtifactWriteClaim:
        self.acquire_count += 1
        current = self.active.get(command.artifact_id)
        if current is not None and current.run_id != command.run_id:
            raise RuntimeError("exclusive claim already held")
        claim = ArtifactWriteClaim(
            artifact_id=command.artifact_id,
            work_item_id=command.work_item_id,
            run_id=command.run_id,
            claim_token=f"claim-{command.artifact_id}-{command.run_id}",
            acquired_revision=command.expected_revision,
            expires_at_ms=1_000_000 + command.lease_duration_ms,
        )
        self.active[claim.artifact_id] = claim
        return claim

    async def load_active(
        self,
        artifact_id: str,
    ) -> ArtifactWriteClaim | None:
        return self.active.get(artifact_id)

    async def renew(
        self,
        command: ArtifactClaimLeaseCommand,
    ) -> ArtifactWriteClaim:
        claim = self.active[command.artifact_id]
        renewed = ArtifactWriteClaim(
            artifact_id=claim.artifact_id,
            work_item_id=claim.work_item_id,
            run_id=claim.run_id,
            claim_token=claim.claim_token,
            acquired_revision=claim.acquired_revision,
            expires_at_ms=claim.expires_at_ms + int(command.lease_duration_ms or 0),
        )
        self.active[renewed.artifact_id] = renewed
        return renewed

    async def release(self, command: ArtifactClaimLeaseCommand) -> bool:
        claim = self.active.get(command.artifact_id)
        if claim is None or (
            claim.run_id != command.run_id
            or claim.claim_token != command.claim_token
        ):
            return False
        del self.active[command.artifact_id]
        return True

    async def release_for_run(self, run_id: str) -> int:
        artifact_ids = [
            artifact_id
            for artifact_id, claim in self.active.items()
            if claim.run_id == run_id
        ]
        for artifact_id in artifact_ids:
            del self.active[artifact_id]
        return len(artifact_ids)


class _MismatchedArtifactClaimRepository(_MemoryArtifactClaimRepository):
    async def acquire(
        self,
        command: ArtifactWriteClaimCommand,
    ) -> ArtifactWriteClaim:
        claim = await super().acquire(command)
        return ArtifactWriteClaim(
            artifact_id=claim.artifact_id,
            work_item_id="wrong-work-item",
            run_id=claim.run_id,
            claim_token=claim.claim_token,
            acquired_revision=claim.acquired_revision,
            expires_at_ms=claim.expires_at_ms,
        )


def _run_candidate(*, status: ArtifactStatus = ArtifactStatus.OPEN):
    return ArtifactResumeCandidate(
        artifact_id="artifact-run",
        namespace="screenplay",
        kind="scene_list",
        owner_id="project-1",
        binding=ArtifactScopeBinding(
            scope=ArtifactScope.RUN,
            scope_id="run-a",
            created_by_run_id="run-a",
        ),
        status=status,
        revision=3,
        committed_item_count=2,
        expected_item_count=5,
    )


def _work_item_candidate(
    *,
    artifact_status: ArtifactStatus = ArtifactStatus.OPEN,
    work_item_status: WorkItemStatus = WorkItemStatus.OPEN,
):
    return ArtifactResumeCandidate(
        artifact_id="artifact-work-item",
        namespace="screenplay",
        kind="source_analysis",
        owner_id="project-1",
        binding=ArtifactScopeBinding(
            scope=ArtifactScope.WORK_ITEM,
            scope_id="work-item-1",
            created_by_run_id="run-a",
        ),
        status=artifact_status,
        revision=8,
        committed_item_count=30,
        expected_item_count=100,
        work_item_status=work_item_status,
    )


@pytest.mark.asyncio
async def test_run_scoped_artifact_stays_private_to_its_run() -> None:
    repository = _MemoryArtifactClaimRepository()
    controller = ArtifactAccessController(repository)
    candidate = _run_candidate()

    grant = await controller.authorize(candidate, ArtifactAccessRequest(
        artifact_id=candidate.artifact_id,
        run_id="run-a",
        mode=ArtifactAccessMode.WRITE,
        expected_revision=3,
    ))
    assert isinstance(repository, ArtifactClaimRepository)
    assert grant.write_claim is None
    assert repository.acquire_count == 0

    with pytest.raises(ArtifactAccessDeniedError) as denied:
        await controller.authorize(candidate, ArtifactAccessRequest(
            artifact_id=candidate.artifact_id,
            run_id="run-b",
            mode=ArtifactAccessMode.READ,
            expected_revision=3,
        ))
    assert denied.value.code == ArtifactAccessReason.RUN_SCOPE_MISMATCH.value


@pytest.mark.asyncio
async def test_work_item_artifact_supports_read_or_exclusive_write() -> None:
    repository = _MemoryArtifactClaimRepository()
    controller = ArtifactAccessController(repository)
    candidate = _work_item_candidate()

    read_grant = await controller.authorize(candidate, ArtifactAccessRequest(
        artifact_id=candidate.artifact_id,
        run_id="run-b",
        mode=ArtifactAccessMode.READ,
        expected_revision=8,
        work_item_id="work-item-1",
        work_item_run_relation=WorkItemRunRelation.REFERENCE,
    ))
    assert read_grant.write_claim is None
    assert repository.acquire_count == 0

    write_grant = await controller.authorize(
        candidate,
        ArtifactAccessRequest(
            artifact_id=candidate.artifact_id,
            run_id="run-b",
            mode=ArtifactAccessMode.WRITE,
            expected_revision=8,
            work_item_id="work-item-1",
            work_item_run_relation=WorkItemRunRelation.CONTINUATION,
        ),
        lease_duration_ms=30_000,
    )
    assert write_grant.write_claim is not None
    assert write_grant.write_claim.run_id == "run-b"
    assert repository.acquire_count == 1

    renewed = await controller.renew(ArtifactClaimLeaseCommand(
        artifact_id=candidate.artifact_id,
        run_id="run-b",
        claim_token=write_grant.write_claim.claim_token,
        lease_duration_ms=30_000,
    ))
    assert renewed.expires_at_ms > write_grant.write_claim.expires_at_ms
    assert await controller.release(ArtifactClaimLeaseCommand(
        artifact_id=candidate.artifact_id,
        run_id="run-b",
        claim_token=write_grant.write_claim.claim_token,
    )) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("candidate", "access_request", "reason"),
    [
        (
            _work_item_candidate(),
            ArtifactAccessRequest(
                artifact_id="artifact-work-item",
                run_id="run-b",
                mode=ArtifactAccessMode.WRITE,
                expected_revision=7,
                work_item_id="work-item-1",
                work_item_run_relation=WorkItemRunRelation.CONTINUATION,
            ),
            ArtifactAccessReason.ARTIFACT_REVISION_CONFLICT,
        ),
        (
            _work_item_candidate(),
            ArtifactAccessRequest(
                artifact_id="artifact-work-item",
                run_id="run-b",
                mode=ArtifactAccessMode.WRITE,
                expected_revision=8,
                work_item_id="other-work-item",
                work_item_run_relation=WorkItemRunRelation.CONTINUATION,
            ),
            ArtifactAccessReason.WORK_ITEM_SCOPE_MISMATCH,
        ),
        (
            _work_item_candidate(work_item_status=WorkItemStatus.COMPLETED),
            ArtifactAccessRequest(
                artifact_id="artifact-work-item",
                run_id="run-b",
                mode=ArtifactAccessMode.WRITE,
                expected_revision=8,
                work_item_id="work-item-1",
                work_item_run_relation=WorkItemRunRelation.CONTINUATION,
            ),
            ArtifactAccessReason.WORK_ITEM_NOT_OPEN,
        ),
        (
            _work_item_candidate(artifact_status=ArtifactStatus.FINALIZED),
            ArtifactAccessRequest(
                artifact_id="artifact-work-item",
                run_id="run-b",
                mode=ArtifactAccessMode.WRITE,
                expected_revision=8,
                work_item_id="work-item-1",
                work_item_run_relation=WorkItemRunRelation.CONTINUATION,
            ),
            ArtifactAccessReason.ARTIFACT_FINALIZED,
        ),
        (
            _work_item_candidate(),
            ArtifactAccessRequest(
                artifact_id="artifact-work-item",
                run_id="run-unlinked",
                mode=ArtifactAccessMode.READ,
                expected_revision=8,
                work_item_id="work-item-1",
            ),
            ArtifactAccessReason.WORK_ITEM_RUN_NOT_LINKED,
        ),
        (
            _work_item_candidate(),
            ArtifactAccessRequest(
                artifact_id="artifact-work-item",
                run_id="run-reader",
                mode=ArtifactAccessMode.WRITE,
                expected_revision=8,
                work_item_id="work-item-1",
                work_item_run_relation=WorkItemRunRelation.REFERENCE,
            ),
            ArtifactAccessReason.WORK_ITEM_RUN_READ_ONLY,
        ),
    ],
)
async def test_work_item_access_fails_closed(
    candidate: ArtifactResumeCandidate,
    access_request: ArtifactAccessRequest,
    reason: ArtifactAccessReason,
) -> None:
    controller = ArtifactAccessController(_MemoryArtifactClaimRepository())

    with pytest.raises(ArtifactAccessDeniedError) as denied:
        await controller.authorize(
            candidate,
            access_request,
            lease_duration_ms=30_000,
        )
    assert denied.value.code == reason.value


@pytest.mark.asyncio
async def test_finalized_work_item_artifact_remains_readable() -> None:
    candidate = _work_item_candidate(
        artifact_status=ArtifactStatus.FINALIZED,
        work_item_status=WorkItemStatus.COMPLETED,
    )
    grant = await ArtifactAccessController(
        _MemoryArtifactClaimRepository()
    ).authorize(candidate, ArtifactAccessRequest(
        artifact_id=candidate.artifact_id,
        run_id="run-c",
        mode=ArtifactAccessMode.READ,
        expected_revision=8,
        work_item_id="work-item-1",
        work_item_run_relation=WorkItemRunRelation.REFERENCE,
    ))

    assert grant.decision.allowed is True
    assert grant.write_claim is None


@pytest.mark.asyncio
async def test_claim_repository_result_is_verified_by_core() -> None:
    candidate = _work_item_candidate()
    controller = ArtifactAccessController(
        _MismatchedArtifactClaimRepository()
    )

    with pytest.raises(TypeError, match="mismatched claim"):
        await controller.authorize(
            candidate,
            ArtifactAccessRequest(
                artifact_id=candidate.artifact_id,
                run_id="run-b",
                mode=ArtifactAccessMode.WRITE,
                expected_revision=8,
                work_item_id="work-item-1",
                work_item_run_relation=WorkItemRunRelation.CONTINUATION,
            ),
            lease_duration_ms=30_000,
        )


def test_scope_binding_rejects_cross_run_ownership_for_run_scope() -> None:
    with pytest.raises(ValueError, match="creating Run"):
        ArtifactScopeBinding(
            scope=ArtifactScope.RUN,
            scope_id="run-a",
            created_by_run_id="run-b",
        )
