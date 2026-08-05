"""Contracts and policy for Artifact access across Run boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent_core.artifacts.contracts import ArtifactStatus
from agent_core.artifacts.scope import ArtifactScope
from agent_core.work_items.contracts import WorkItemRunRelation, WorkItemStatus


class ArtifactAccessMode(StrEnum):
    READ = "read"
    WRITE = "write"


class ArtifactAccessReason(StrEnum):
    ALLOWED = "allowed"
    ARTIFACT_ID_MISMATCH = "artifact_id_mismatch"
    ARTIFACT_ABORTED = "artifact_aborted"
    ARTIFACT_FINALIZED = "artifact_finalized"
    ARTIFACT_REVISION_CONFLICT = "artifact_revision_conflict"
    RUN_SCOPE_MISMATCH = "run_scope_mismatch"
    WORK_ITEM_SCOPE_MISMATCH = "work_item_scope_mismatch"
    WORK_ITEM_RUN_NOT_LINKED = "work_item_run_not_linked"
    WORK_ITEM_RUN_READ_ONLY = "work_item_run_read_only"
    WORK_ITEM_NOT_OPEN = "work_item_not_open"


@dataclass(frozen=True, slots=True)
class ArtifactScopeBinding:
    """Stable owner of an Artifact's execution lifetime.

    ``scope_id`` is a Run ID for Run scope and a Work Item ID for Work Item
    scope. ``created_by_run_id`` remains immutable provenance; it is not the
    current writer of a Work Item-scoped Artifact.
    """

    scope: ArtifactScope
    scope_id: str
    created_by_run_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", ArtifactScope(self.scope))
        for name in ("scope_id", "created_by_run_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"artifact scope binding {name} is required")
            object.__setattr__(self, name, value)
        if (
            self.scope is ArtifactScope.RUN
            and self.scope_id != self.created_by_run_id
        ):
            raise ValueError(
                "Run-scoped artifact must be owned by its creating Run"
            )

    @property
    def run_id(self) -> str | None:
        return self.scope_id if self.scope is ArtifactScope.RUN else None

    @property
    def work_item_id(self) -> str | None:
        return self.scope_id if self.scope is ArtifactScope.WORK_ITEM else None


@dataclass(frozen=True, slots=True)
class ArtifactResumeCandidate:
    """Content-free Core view used to authorize a possible continuation."""

    artifact_id: str
    namespace: str
    kind: str
    owner_id: str
    binding: ArtifactScopeBinding
    status: ArtifactStatus
    revision: int
    committed_item_count: int = 0
    expected_item_count: int | None = None
    work_item_status: WorkItemStatus | None = None

    def __post_init__(self) -> None:
        for name in ("artifact_id", "namespace", "kind", "owner_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"artifact candidate {name} is required")
            object.__setattr__(self, name, value)
        if not isinstance(self.binding, ArtifactScopeBinding):
            raise TypeError("artifact candidate binding is invalid")
        object.__setattr__(self, "status", ArtifactStatus(self.status))
        revision = int(self.revision)
        if revision <= 0:
            raise ValueError("artifact candidate revision must be positive")
        object.__setattr__(self, "revision", revision)
        committed = int(self.committed_item_count)
        if committed < 0:
            raise ValueError("committed_item_count must be non-negative")
        object.__setattr__(self, "committed_item_count", committed)
        if self.expected_item_count is not None:
            expected = int(self.expected_item_count)
            if expected < 0:
                raise ValueError("expected_item_count must be non-negative")
            if committed > expected:
                raise ValueError(
                    "committed_item_count cannot exceed expected_item_count"
                )
            object.__setattr__(self, "expected_item_count", expected)
        if self.binding.scope is ArtifactScope.WORK_ITEM:
            if self.work_item_status is None:
                raise ValueError(
                    "Work Item-scoped candidate requires work_item_status"
                )
            object.__setattr__(
                self,
                "work_item_status",
                WorkItemStatus(self.work_item_status),
            )
        elif self.work_item_status is not None:
            raise ValueError(
                "Run-scoped candidate cannot carry work_item_status"
            )


@dataclass(frozen=True, slots=True)
class ArtifactAccessRequest:
    artifact_id: str
    run_id: str
    mode: ArtifactAccessMode
    expected_revision: int
    work_item_id: str | None = None
    work_item_run_relation: WorkItemRunRelation | None = None

    def __post_init__(self) -> None:
        for name in ("artifact_id", "run_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"artifact access {name} is required")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "mode", ArtifactAccessMode(self.mode))
        revision = int(self.expected_revision)
        if revision <= 0:
            raise ValueError("expected_revision must be positive")
        object.__setattr__(self, "expected_revision", revision)
        object.__setattr__(
            self,
            "work_item_id",
            str(self.work_item_id or "").strip() or None,
        )
        if self.work_item_run_relation is not None:
            object.__setattr__(
                self,
                "work_item_run_relation",
                WorkItemRunRelation(self.work_item_run_relation),
            )


@dataclass(frozen=True, slots=True)
class ArtifactAccessDecision:
    artifact_id: str
    run_id: str
    mode: ArtifactAccessMode
    allowed: bool
    reason: ArtifactAccessReason
    artifact_revision: int
    requires_write_claim: bool = False

    def __post_init__(self) -> None:
        for name in ("artifact_id", "run_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"artifact decision {name} is required")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "mode", ArtifactAccessMode(self.mode))
        object.__setattr__(self, "allowed", bool(self.allowed))
        object.__setattr__(self, "reason", ArtifactAccessReason(self.reason))
        if self.allowed != (self.reason is ArtifactAccessReason.ALLOWED):
            raise ValueError(
                "artifact access decision reason does not match its verdict"
            )
        revision = int(self.artifact_revision)
        if revision <= 0:
            raise ValueError("artifact_revision must be positive")
        object.__setattr__(self, "artifact_revision", revision)
        requires_claim = bool(self.requires_write_claim)
        if requires_claim and (
            not self.allowed or self.mode is not ArtifactAccessMode.WRITE
        ):
            raise ValueError(
                "only allowed write decisions may require a write claim"
            )
        object.__setattr__(self, "requires_write_claim", requires_claim)


@dataclass(frozen=True, slots=True)
class ArtifactWriteClaimCommand:
    artifact_id: str
    work_item_id: str
    run_id: str
    expected_revision: int
    lease_duration_ms: int

    def __post_init__(self) -> None:
        for name in ("artifact_id", "work_item_id", "run_id"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"artifact claim {name} is required")
            object.__setattr__(self, name, value)
        revision = int(self.expected_revision)
        if revision <= 0:
            raise ValueError("expected_revision must be positive")
        object.__setattr__(self, "expected_revision", revision)
        duration = int(self.lease_duration_ms)
        if duration <= 0:
            raise ValueError("lease_duration_ms must be positive")
        object.__setattr__(self, "lease_duration_ms", duration)


@dataclass(frozen=True, slots=True)
class ArtifactWriteClaim:
    artifact_id: str
    work_item_id: str
    run_id: str
    claim_token: str
    acquired_revision: int
    expires_at_ms: int

    def __post_init__(self) -> None:
        for name in (
            "artifact_id",
            "work_item_id",
            "run_id",
            "claim_token",
        ):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"artifact write claim {name} is required")
            object.__setattr__(self, name, value)
        revision = int(self.acquired_revision)
        if revision <= 0:
            raise ValueError("acquired_revision must be positive")
        object.__setattr__(self, "acquired_revision", revision)
        expires_at = int(self.expires_at_ms)
        if expires_at <= 0:
            raise ValueError("expires_at_ms must be positive")
        object.__setattr__(self, "expires_at_ms", expires_at)


@dataclass(frozen=True, slots=True)
class ArtifactClaimLeaseCommand:
    artifact_id: str
    run_id: str
    claim_token: str
    lease_duration_ms: int | None = None

    def __post_init__(self) -> None:
        for name in ("artifact_id", "run_id", "claim_token"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"artifact claim lease {name} is required")
            object.__setattr__(self, name, value)
        if self.lease_duration_ms is not None:
            duration = int(self.lease_duration_ms)
            if duration <= 0:
                raise ValueError("lease_duration_ms must be positive")
            object.__setattr__(self, "lease_duration_ms", duration)


@dataclass(frozen=True, slots=True)
class ArtifactAccessGrant:
    decision: ArtifactAccessDecision
    write_claim: ArtifactWriteClaim | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, ArtifactAccessDecision):
            raise TypeError("artifact access grant decision is invalid")
        if not self.decision.allowed:
            raise ValueError("denied artifact access cannot become a grant")
        if self.decision.requires_write_claim and self.write_claim is None:
            raise ValueError("artifact access grant requires a write claim")
        if not self.decision.requires_write_claim and self.write_claim is not None:
            raise ValueError("artifact access grant has an unexpected write claim")
        if self.write_claim is not None and (
            self.write_claim.artifact_id != self.decision.artifact_id
            or self.write_claim.run_id != self.decision.run_id
            or self.write_claim.acquired_revision
            != self.decision.artifact_revision
        ):
            raise ValueError("artifact write claim does not match the decision")


class ArtifactAccessPolicy:
    """Fail-closed generic access rules; domains add compatibility checks."""

    def decide(
        self,
        candidate: ArtifactResumeCandidate,
        request: ArtifactAccessRequest,
    ) -> ArtifactAccessDecision:
        reason = self._denial_reason(candidate, request)
        allowed = reason is None
        requires_claim = bool(
            allowed
            and request.mode is ArtifactAccessMode.WRITE
            and candidate.binding.scope is ArtifactScope.WORK_ITEM
        )
        return ArtifactAccessDecision(
            artifact_id=request.artifact_id,
            run_id=request.run_id,
            mode=request.mode,
            allowed=allowed,
            reason=reason or ArtifactAccessReason.ALLOWED,
            artifact_revision=candidate.revision,
            requires_write_claim=requires_claim,
        )

    @staticmethod
    def _denial_reason(
        candidate: ArtifactResumeCandidate,
        request: ArtifactAccessRequest,
    ) -> ArtifactAccessReason | None:
        if candidate.artifact_id != request.artifact_id:
            return ArtifactAccessReason.ARTIFACT_ID_MISMATCH
        if candidate.status is ArtifactStatus.ABORTED:
            return ArtifactAccessReason.ARTIFACT_ABORTED
        if candidate.revision != request.expected_revision:
            return ArtifactAccessReason.ARTIFACT_REVISION_CONFLICT
        if (
            request.mode is ArtifactAccessMode.WRITE
            and candidate.status is ArtifactStatus.FINALIZED
        ):
            return ArtifactAccessReason.ARTIFACT_FINALIZED
        if candidate.binding.scope is ArtifactScope.RUN:
            if candidate.binding.scope_id != request.run_id:
                return ArtifactAccessReason.RUN_SCOPE_MISMATCH
            return None
        if candidate.binding.scope_id != request.work_item_id:
            return ArtifactAccessReason.WORK_ITEM_SCOPE_MISMATCH
        if request.work_item_run_relation is None:
            return ArtifactAccessReason.WORK_ITEM_RUN_NOT_LINKED
        if (
            request.mode is ArtifactAccessMode.WRITE
            and not request.work_item_run_relation.writes_work_item
        ):
            return ArtifactAccessReason.WORK_ITEM_RUN_READ_ONLY
        if (
            request.mode is ArtifactAccessMode.WRITE
            and candidate.work_item_status is not WorkItemStatus.OPEN
        ):
            return ArtifactAccessReason.WORK_ITEM_NOT_OPEN
        return None


__all__ = [
    "ArtifactAccessDecision",
    "ArtifactAccessGrant",
    "ArtifactAccessMode",
    "ArtifactAccessPolicy",
    "ArtifactAccessReason",
    "ArtifactAccessRequest",
    "ArtifactClaimLeaseCommand",
    "ArtifactResumeCandidate",
    "ArtifactScope",
    "ArtifactScopeBinding",
    "ArtifactWriteClaim",
    "ArtifactWriteClaimCommand",
]
