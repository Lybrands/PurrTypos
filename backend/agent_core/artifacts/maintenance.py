"""Storage-neutral maintenance policy for durable Agent artifacts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArtifactMaintenancePolicy:
    """Bound safe lease cleanup and optional terminal-state retention.

    Claims are disposable execution leases, so invalid claims are always
    eligible for cleanup. Artifact content is durable recovery/audit state and
    is only eligible for deletion when ``terminal_retention_ms`` is explicit.
    Open Work Items and open Artifacts are never retention-GC candidates.
    """

    terminal_retention_ms: int | None = None
    max_purge_work_items: int = 100
    max_purge_run_artifacts: int = 100

    def __post_init__(self) -> None:
        if self.terminal_retention_ms is not None:
            retention = int(self.terminal_retention_ms)
            if retention < 0:
                raise ValueError("terminal_retention_ms must be non-negative")
            object.__setattr__(self, "terminal_retention_ms", retention)
        for name in ("max_purge_work_items", "max_purge_run_artifacts"):
            value = int(getattr(self, name))
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class ArtifactMaintenanceReport:
    """Content-free result of one idempotent maintenance sweep."""

    expired_claims_released: int = 0
    unavailable_run_claims_released: int = 0
    invalid_target_claims_released: int = 0
    purged_work_items: int = 0
    purged_artifacts: int = 0
    consistency_issues: int = 0

    def __post_init__(self) -> None:
        for name in (
            "expired_claims_released",
            "unavailable_run_claims_released",
            "invalid_target_claims_released",
            "purged_work_items",
            "purged_artifacts",
            "consistency_issues",
        ):
            value = int(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)

    @property
    def released_claims(self) -> int:
        return (
            self.expired_claims_released
            + self.unavailable_run_claims_released
            + self.invalid_target_claims_released
        )

    @property
    def changed(self) -> bool:
        return bool(
            self.released_claims
            or self.purged_work_items
            or self.purged_artifacts
        )


@dataclass(frozen=True, slots=True)
class ArtifactMaintenanceSnapshot:
    """Content-free operational state globally or for one linked Run."""

    checked_at_ms: int
    scope_run_id: str | None = None
    open_work_items: int = 0
    completed_work_items: int = 0
    canceled_work_items: int = 0
    unknown_work_items: int = 0
    open_artifacts: int = 0
    finalized_artifacts: int = 0
    aborted_artifacts: int = 0
    unknown_artifacts: int = 0
    active_claims: int = 0
    expired_claims: int = 0
    unavailable_run_claims: int = 0
    invalid_target_claims: int = 0
    consistency_issues: int = 0

    def __post_init__(self) -> None:
        checked_at = int(self.checked_at_ms)
        if checked_at < 0:
            raise ValueError("checked_at_ms must be non-negative")
        object.__setattr__(self, "checked_at_ms", checked_at)
        object.__setattr__(
            self,
            "scope_run_id",
            str(self.scope_run_id or "").strip() or None,
        )
        for name in (
            "open_work_items",
            "completed_work_items",
            "canceled_work_items",
            "unknown_work_items",
            "open_artifacts",
            "finalized_artifacts",
            "aborted_artifacts",
            "unknown_artifacts",
            "active_claims",
            "expired_claims",
            "unavailable_run_claims",
            "invalid_target_claims",
            "consistency_issues",
        ):
            value = int(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)

    @property
    def work_item_count(self) -> int:
        return (
            self.open_work_items
            + self.completed_work_items
            + self.canceled_work_items
            + self.unknown_work_items
        )

    @property
    def artifact_count(self) -> int:
        return (
            self.open_artifacts
            + self.finalized_artifacts
            + self.aborted_artifacts
            + self.unknown_artifacts
        )

    @property
    def claim_count(self) -> int:
        return self.active_claims + self.reclaimable_claims

    @property
    def reclaimable_claims(self) -> int:
        return (
            self.expired_claims
            + self.unavailable_run_claims
            + self.invalid_target_claims
        )

    @property
    def requires_attention(self) -> bool:
        return bool(self.reclaimable_claims or self.consistency_issues)


__all__ = [
    "ArtifactMaintenancePolicy",
    "ArtifactMaintenanceReport",
    "ArtifactMaintenanceSnapshot",
]
