"""Application scheduling for storage-neutral Artifact maintenance."""

from __future__ import annotations

import asyncio
import logging

from purra.artifacts.maintenance import (
    ArtifactMaintenancePolicy,
    ArtifactMaintenanceReport,
    ArtifactMaintenanceSnapshot,
)
from purra.artifacts.ports import ArtifactMaintenanceRepository


logger = logging.getLogger(__name__)


async def run_artifact_maintenance(
    repository: ArtifactMaintenanceRepository,
    policy: ArtifactMaintenancePolicy,
    *,
    timestamp_ms: int | None = None,
) -> ArtifactMaintenanceReport:
    """Run and observably report one content-free maintenance sweep."""

    report = await repository.maintain(policy, timestamp_ms=timestamp_ms)
    if report.changed:
        logger.info(
            "Artifact maintenance releasedClaims=%s purgedWorkItems=%s "
            "purgedArtifacts=%s",
            report.released_claims,
            report.purged_work_items,
            report.purged_artifacts,
        )
    if report.consistency_issues:
        logger.warning(
            "Artifact maintenance found %s durable consistency issue(s); "
            "content was retained for diagnosis",
            report.consistency_issues,
        )
    return report


async def monitor_artifact_maintenance(
    repository: ArtifactMaintenanceRepository,
    policy: ArtifactMaintenancePolicy,
    *,
    poll_interval_seconds: float,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Continuously reap stale leases and apply configured retention."""

    interval = float(poll_interval_seconds)
    if interval <= 0:
        raise ValueError("artifact maintenance interval must be positive")
    stop = stop_event or asyncio.Event()
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
        if stop.is_set():
            return
        try:
            await run_artifact_maintenance(repository, policy)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Failed to maintain Agent Artifacts")


def artifact_maintenance_report_view(
    report: ArtifactMaintenanceReport,
) -> dict[str, int | bool]:
    """Map a Core report without exposing Artifact content or claim tokens."""

    return {
        "expiredClaimsReleased": report.expired_claims_released,
        "unavailableRunClaimsReleased": (
            report.unavailable_run_claims_released
        ),
        "invalidTargetClaimsReleased": (
            report.invalid_target_claims_released
        ),
        "releasedClaims": report.released_claims,
        "purgedWorkItems": report.purged_work_items,
        "purgedArtifacts": report.purged_artifacts,
        "consistencyIssues": report.consistency_issues,
        "changed": report.changed,
    }


def artifact_maintenance_snapshot_view(
    snapshot: ArtifactMaintenanceSnapshot,
) -> dict[str, int | str | bool | None]:
    """Map a bounded operational snapshot to the API wire contract."""

    return {
        "checkedAtMs": snapshot.checked_at_ms,
        "scopeRunId": snapshot.scope_run_id,
        "workItemCount": snapshot.work_item_count,
        "openWorkItems": snapshot.open_work_items,
        "completedWorkItems": snapshot.completed_work_items,
        "canceledWorkItems": snapshot.canceled_work_items,
        "unknownWorkItems": snapshot.unknown_work_items,
        "artifactCount": snapshot.artifact_count,
        "openArtifacts": snapshot.open_artifacts,
        "finalizedArtifacts": snapshot.finalized_artifacts,
        "abortedArtifacts": snapshot.aborted_artifacts,
        "unknownArtifacts": snapshot.unknown_artifacts,
        "claimCount": snapshot.claim_count,
        "activeClaims": snapshot.active_claims,
        "expiredClaims": snapshot.expired_claims,
        "unavailableRunClaims": snapshot.unavailable_run_claims,
        "invalidTargetClaims": snapshot.invalid_target_claims,
        "reclaimableClaims": snapshot.reclaimable_claims,
        "consistencyIssues": snapshot.consistency_issues,
        "requiresAttention": snapshot.requires_attention,
    }


__all__ = [
    "artifact_maintenance_report_view",
    "artifact_maintenance_snapshot_view",
    "monitor_artifact_maintenance",
    "run_artifact_maintenance",
]
