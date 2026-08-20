"""Host orchestration for model-selected Artifact continuity."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from purra.artifacts import (
    ArtifactAccessController,
    ArtifactAccessMode,
    ArtifactAccessRequest,
    ArtifactBatch,
    ArtifactClaimLeaseCommand,
    ArtifactRecord,
    ArtifactResumeCandidate,
    ArtifactStatus,
)
from purra.artifacts.continuity import ArtifactWriteClaim
from purra.artifacts.ports import (
    ArtifactAccessAuthorizer,
    ArtifactClaimRepository,
)
from purra.errors import AgentCoreError


class ArtifactContinuityAction(StrEnum):
    IGNORE = "ignore"
    REFERENCE = "reference"
    CONTINUE = "continue"


class ArtifactContinuityUnavailableError(AgentCoreError):
    def __init__(
        self,
        message: str = "selected Artifact continuity candidate is unavailable",
        *,
        code: str = "artifact_continuity_candidate_unavailable",
    ) -> None:
        super().__init__(message)
        self.code = str(code or "artifact_continuity_candidate_unavailable")


@dataclass(frozen=True, slots=True)
class ArtifactContinuityRecord:
    artifact: ArtifactRecord

    def planning_view(self) -> dict[str, Any]:
        if self.artifact.status is ArtifactStatus.FINALIZED:
            next_action = "replay_finalization"
        elif (
            self.artifact.expected_item_count is not None
            and self.artifact.committed_item_count
            == self.artifact.expected_item_count
        ):
            next_action = "finalize"
        else:
            next_action = "append_batch"
        return {
            key: value
            for key, value in {
                "artifactId": self.artifact.id,
                "kind": self.artifact.kind,
                "ownerRef": {
                    "kind": self.artifact.owner_ref.kind,
                    "id": self.artifact.owner_ref.id,
                },
                "artifactStatus": self.artifact.status.value,
                "artifactRevision": self.artifact.revision,
                "committedItemCount": self.artifact.committed_item_count,
                "expectedItemCount": self.artifact.expected_item_count,
                "nextAction": next_action,
            }.items()
            if value not in (None, "")
        }


@dataclass(frozen=True, slots=True)
class ArtifactContinuityResolution:
    action: ArtifactContinuityAction
    record: ArtifactContinuityRecord
    batches: tuple[ArtifactBatch, ...]
    write_claim: ArtifactWriteClaim | None = None


@runtime_checkable
class ArtifactContinuityQuery(Protocol):
    async def list_open_candidates(
        self,
        *,
        namespace: str,
        owner_id: str,
        session_id: str | int,
        limit: int,
    ) -> Sequence[ArtifactContinuityRecord]: ...

    async def list_batches(self, artifact_id: str) -> Sequence[ArtifactBatch]: ...


class ArtifactContinuityCoordinator:
    """Revalidate a host candidate and delegate lifecycle checks to PurrA."""

    def __init__(
        self,
        *,
        query: ArtifactContinuityQuery,
        claims: ArtifactClaimRepository,
        authorizer: ArtifactAccessAuthorizer,
        write_lease_duration_ms: int = 300_000,
        candidate_limit: int = 8,
    ) -> None:
        self._query = query
        self._access = ArtifactAccessController(claims, authorizer=authorizer)
        self._write_lease_duration_ms = int(write_lease_duration_ms)
        self._candidate_limit = int(candidate_limit)
        if self._write_lease_duration_ms <= 0 or self._candidate_limit <= 0:
            raise ValueError("Artifact continuity limits must be positive")

    async def discover(
        self,
        *,
        namespace: str,
        owner_id: str,
        session_id: str | int | None,
        allowed_artifact_kinds: frozenset[str] | None = None,
    ) -> tuple[ArtifactContinuityRecord, ...]:
        if session_id is None:
            return ()
        records = tuple(await self._query.list_open_candidates(
            namespace=str(namespace or "").strip(),
            owner_id=str(owner_id or "").strip(),
            session_id=session_id,
            limit=self._candidate_limit,
        ))
        if allowed_artifact_kinds is None:
            return records
        return tuple(
            record
            for record in records
            if record.artifact.kind in allowed_artifact_kinds
        )

    async def resolve(
        self,
        selection: Mapping[str, Any] | None,
        *,
        namespace: str,
        owner_id: str,
        session_id: str | int | None,
        run_id: str | None,
        allowed_artifact_kinds: frozenset[str] | None = None,
    ) -> ArtifactContinuityResolution | None:
        preview = await self.preview(
            selection,
            namespace=namespace,
            owner_id=owner_id,
            session_id=session_id,
            allowed_artifact_kinds=allowed_artifact_kinds,
        )
        if preview is None:
            return None
        normalized_run = str(run_id or "").strip()
        if not normalized_run:
            raise ValueError("Artifact continuity selection requires a Run id")
        artifact = preview.record.artifact
        grant = await self._access.authorize(
            _resume_candidate(artifact),
            ArtifactAccessRequest(
                artifact_id=artifact.id,
                run_id=normalized_run,
                mode=(
                    ArtifactAccessMode.WRITE
                    if preview.action is ArtifactContinuityAction.CONTINUE
                    else ArtifactAccessMode.READ
                ),
                expected_revision=artifact.revision,
            ),
            lease_duration_ms=(
                self._write_lease_duration_ms
                if preview.action is ArtifactContinuityAction.CONTINUE
                else None
            ),
        )
        return ArtifactContinuityResolution(
            action=preview.action,
            record=preview.record,
            batches=preview.batches,
            write_claim=grant.write_claim,
        )

    async def preview(
        self,
        selection: Mapping[str, Any] | None,
        *,
        namespace: str,
        owner_id: str,
        session_id: str | int | None,
        allowed_artifact_kinds: frozenset[str] | None = None,
    ) -> ArtifactContinuityResolution | None:
        action, artifact_id = _parse_selection(selection)
        if action is ArtifactContinuityAction.IGNORE:
            return None
        candidates = await self.discover(
            namespace=namespace,
            owner_id=owner_id,
            session_id=session_id,
            allowed_artifact_kinds=allowed_artifact_kinds,
        )
        record = next(
            (item for item in candidates if item.artifact.id == artifact_id),
            None,
        )
        if record is None:
            raise ArtifactContinuityUnavailableError()
        return ArtifactContinuityResolution(
            action=action,
            record=record,
            batches=tuple(await self._query.list_batches(record.artifact.id)),
        )

    async def release_resolution(
        self,
        resolution: ArtifactContinuityResolution,
    ) -> bool:
        claim = resolution.write_claim
        if claim is None:
            return False
        return await self._access.release(ArtifactClaimLeaseCommand(
            artifact_id=claim.artifact_id,
            run_id=claim.run_id,
            claim_token=claim.claim_token,
        ))


def continuity_selection_from_target(
    target: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    value = target.get("artifactContinuity")
    return value if isinstance(value, Mapping) else None


def _parse_selection(
    value: Mapping[str, Any] | None,
) -> tuple[ArtifactContinuityAction, str]:
    if value is None:
        return ArtifactContinuityAction.IGNORE, ""
    try:
        action = ArtifactContinuityAction(str(value.get("action") or "ignore"))
    except ValueError as error:
        raise ValueError("unsupported Artifact continuity action") from error
    if action is ArtifactContinuityAction.IGNORE:
        return action, ""
    artifact_id = str(value.get("artifactId") or "").strip()
    if not artifact_id:
        raise ValueError("Artifact continuity selection requires artifactId")
    return action, artifact_id


def _resume_candidate(artifact: ArtifactRecord) -> ArtifactResumeCandidate:
    return ArtifactResumeCandidate(
        artifact_id=artifact.id,
        namespace=artifact.namespace,
        kind=artifact.kind,
        owner_id=artifact.owner_id,
        owner_ref=artifact.owner_ref,
        created_by_run_id=artifact.created_by_run_id,
        status=artifact.status,
        revision=artifact.revision,
        committed_item_count=artifact.committed_item_count,
        expected_item_count=artifact.expected_item_count,
    )


__all__ = [
    "ArtifactContinuityAction",
    "ArtifactContinuityCoordinator",
    "ArtifactContinuityQuery",
    "ArtifactContinuityRecord",
    "ArtifactContinuityResolution",
    "ArtifactContinuityUnavailableError",
    "continuity_selection_from_target",
]
