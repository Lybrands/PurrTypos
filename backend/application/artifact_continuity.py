"""Application orchestration for model-selected Artifact continuity."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from agent_core.artifacts import (
    ArtifactAccessController,
    ArtifactAccessMode,
    ArtifactAccessRequest,
    ArtifactBatch,
    ArtifactClaimLeaseCommand,
    ArtifactRecord,
    ArtifactResumeCandidate,
    ArtifactScope,
    ArtifactScopeBinding,
    ArtifactStatus,
)
from agent_core.artifacts.continuity import ArtifactWriteClaim
from agent_core.artifacts.ports import ArtifactClaimRepository
from agent_core.errors import AgentCoreError
from agent_core.work_items import (
    WorkItemRecord,
    WorkItemRunLinkCommand,
    WorkItemRunRelation,
    WorkItemStatus,
)
from agent_core.work_items.ports import WorkItemRepository


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
    work_item: WorkItemRecord

    def __post_init__(self) -> None:
        if self.artifact.scope is not ArtifactScope.WORK_ITEM:
            raise ValueError("continuity record requires Work Item scope")
        if self.artifact.work_item_id != self.work_item.id:
            raise ValueError("artifact and Work Item identity do not match")
        if (
            self.artifact.namespace != self.work_item.namespace
            or self.artifact.owner_id != self.work_item.owner_id
        ):
            raise ValueError("artifact and Work Item ownership do not match")

    def planning_view(self) -> dict[str, Any]:
        if (
            self.artifact.status is ArtifactStatus.FINALIZED
            and self.work_item.status is WorkItemStatus.COMPLETED
        ):
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
                "workItemId": self.work_item.id,
                "kind": self.artifact.kind,
                "artifactStatus": self.artifact.status.value,
                "workItemStatus": self.work_item.status.value,
                "artifactRevision": self.artifact.revision,
                "workItemRevision": self.work_item.revision,
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
    relation: WorkItemRunRelation
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

    async def list_batches(
        self,
        artifact_id: str,
    ) -> Sequence[ArtifactBatch]: ...


class ArtifactContinuityCoordinator:
    """Turn a planner choice into durable, Core-authorized access."""

    def __init__(
        self,
        *,
        query: ArtifactContinuityQuery,
        work_items: WorkItemRepository,
        claims: ArtifactClaimRepository,
        write_lease_duration_ms: int = 300_000,
        candidate_limit: int = 8,
    ) -> None:
        self._query = query
        self._work_items = work_items
        self._access = ArtifactAccessController(claims)
        self._write_lease_duration_ms = int(write_lease_duration_ms)
        self._candidate_limit = int(candidate_limit)
        if self._write_lease_duration_ms <= 0:
            raise ValueError("write claim duration must be positive")
        if self._candidate_limit <= 0:
            raise ValueError("candidate limit must be positive")

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
        allowed = frozenset(
            str(kind or "").strip()
            for kind in allowed_artifact_kinds
            if str(kind or "").strip()
        )
        return tuple(
            record for record in records
            if record.artifact.kind in allowed
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
        normalized_run = str(run_id or "").strip()
        preview = await self.preview(
            selection,
            namespace=namespace,
            owner_id=owner_id,
            session_id=session_id,
            allowed_artifact_kinds=allowed_artifact_kinds,
        )
        if preview is None:
            return None
        if not normalized_run:
            raise ValueError("Artifact continuity selection requires a Run id")
        record = preview.record
        relation = preview.relation
        await self._work_items.link_run(WorkItemRunLinkCommand(
            work_item_id=record.work_item.id,
            run_id=normalized_run,
            relation=relation,
            expected_revision=record.work_item.revision,
        ))
        grant = await self._access.authorize(
            _resume_candidate(record),
            ArtifactAccessRequest(
                artifact_id=record.artifact.id,
                run_id=normalized_run,
                mode=(
                    ArtifactAccessMode.WRITE
                    if preview.action is ArtifactContinuityAction.CONTINUE
                    else ArtifactAccessMode.READ
                ),
                expected_revision=record.artifact.revision,
                work_item_id=record.work_item.id,
                work_item_run_relation=relation,
            ),
            lease_duration_ms=(
                self._write_lease_duration_ms
                if preview.action is ArtifactContinuityAction.CONTINUE
                else None
            ),
        )
        return ArtifactContinuityResolution(
            action=preview.action,
            record=record,
            relation=relation,
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
        """Revalidate a semantic selection without creating links or claims."""

        action, artifact_id, work_item_id = _parse_selection(selection)
        if action is ArtifactContinuityAction.IGNORE:
            return None
        candidates = await self.discover(
            namespace=namespace,
            owner_id=owner_id,
            session_id=session_id,
            allowed_artifact_kinds=allowed_artifact_kinds,
        )
        record = next(
            (
                item for item in candidates
                if item.artifact.id == artifact_id
                and item.work_item.id == work_item_id
            ),
            None,
        )
        if record is None:
            raise ArtifactContinuityUnavailableError(
                "selected Artifact continuity candidate is unavailable",
            )
        relation = (
            WorkItemRunRelation.CONTINUATION
            if action is ArtifactContinuityAction.CONTINUE
            else WorkItemRunRelation.REFERENCE
        )
        return ArtifactContinuityResolution(
            action=action,
            record=record,
            relation=relation,
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
) -> tuple[ArtifactContinuityAction, str, str]:
    if value is None:
        return ArtifactContinuityAction.IGNORE, "", ""
    try:
        action = ArtifactContinuityAction(str(value.get("action") or "ignore"))
    except ValueError as error:
        raise ValueError("unsupported Artifact continuity action") from error
    if action is ArtifactContinuityAction.IGNORE:
        return action, "", ""
    artifact_id = str(value.get("artifactId") or "").strip()
    work_item_id = str(value.get("workItemId") or "").strip()
    if not artifact_id or not work_item_id:
        raise ValueError(
            "Artifact continuity selection requires artifactId and workItemId"
        )
    return action, artifact_id, work_item_id


def _resume_candidate(
    record: ArtifactContinuityRecord,
) -> ArtifactResumeCandidate:
    artifact = record.artifact
    created_by_run_id = str(artifact.created_by_run_id or "").strip()
    if not created_by_run_id:
        raise ValueError(
            "Work Item-scoped Artifact is missing creating Run provenance"
        )
    return ArtifactResumeCandidate(
        artifact_id=artifact.id,
        namespace=artifact.namespace,
        kind=artifact.kind,
        owner_id=artifact.owner_id,
        binding=ArtifactScopeBinding(
            scope=artifact.scope,
            scope_id=record.work_item.id,
            created_by_run_id=created_by_run_id,
        ),
        status=artifact.status,
        revision=artifact.revision,
        committed_item_count=artifact.committed_item_count,
        expected_item_count=artifact.expected_item_count,
        work_item_status=record.work_item.status,
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
