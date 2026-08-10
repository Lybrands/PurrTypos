from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from purra.artifacts import (
    ArtifactCreateCommand,
    ArtifactMaintenancePolicy,
    ArtifactMaintenanceReport,
    ArtifactMaintenanceSnapshot,
)
from purra.artifacts.ports import ArtifactMaintenanceRepository
from application.artifact_maintenance import monitor_artifact_maintenance
from database.connection import DatabaseConnection
from infrastructure.persistence import run_store
from infrastructure.persistence.sqlite_artifact_maintenance_repository import (
    SqliteArtifactMaintenanceRepository,
)
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from infrastructure.persistence.sqlite_work_item_artifact_lifecycle import (
    SqliteWorkItemArtifactLifecycle,
)


@pytest_asyncio.fixture
async def maintenance_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


async def _running_run(db, label: str) -> str:
    return await run_store.create_run(
        db,
        session_id=None,
        prompt=label,
        mode="agent",
        execution_owner_id=f"worker-{label}",
        heartbeat_at_ms=1_000,
        lease_expires_at_ms=100_000,
    )


async def _start_artifact(
    db,
    *,
    run_id: str,
    label: str,
    expires_at_ms: int,
):
    starter = SqliteWorkItemArtifactLifecycle(
        db,
        clock=lambda: 1_000,
        token_factory=lambda: f"claim-{label}",
        write_lease_duration_ms=expires_at_ms - 1_000,
    )
    return await starter.begin(ArtifactCreateCommand(
        namespace="purrtypos.screenplay",
        kind=f"artifact-{label}",
        owner_id=f"project-{label}",
        run_id=run_id,
    ))


@pytest.mark.asyncio
async def test_maintenance_reaps_only_disposable_invalid_claims(
    maintenance_db,
) -> None:
    expired_run = await _running_run(maintenance_db, "expired")
    expired = await _start_artifact(
        maintenance_db,
        run_id=expired_run,
        label="expired",
        expires_at_ms=2_000,
    )

    terminal_run = await _running_run(maintenance_db, "terminal")
    terminal = await _start_artifact(
        maintenance_db,
        run_id=terminal_run,
        label="terminal",
        expires_at_ms=10_000,
    )
    await maintenance_db.execute(
        "UPDATE ai_agent_runs SET status = 'completed' WHERE id = ?",
        [terminal_run],
    )

    stale_revision_run = await _running_run(maintenance_db, "revision")
    stale_revision = await _start_artifact(
        maintenance_db,
        run_id=stale_revision_run,
        label="revision",
        expires_at_ms=10_000,
    )
    await maintenance_db.execute(
        "UPDATE ai_agent_artifacts SET revision = revision + 1 WHERE id = ?",
        [stale_revision.artifact.id],
    )

    valid_run = await _running_run(maintenance_db, "valid")
    valid = await _start_artifact(
        maintenance_db,
        run_id=valid_run,
        label="valid",
        expires_at_ms=10_000,
    )

    repository = SqliteArtifactMaintenanceRepository(maintenance_db)
    assert isinstance(repository, ArtifactMaintenanceRepository)
    before = await repository.inspect(timestamp_ms=2_000)
    assert before.work_item_count == 4
    assert before.artifact_count == 4
    assert before.claim_count == 4
    assert before.active_claims == 1
    assert before.expired_claims == 1
    assert before.unavailable_run_claims == 1
    assert before.invalid_target_claims == 1
    assert before.reclaimable_claims == 3
    assert before.requires_attention
    scoped = await repository.inspect(
        run_id=valid_run,
        timestamp_ms=2_000,
    )
    assert scoped.scope_run_id == valid_run
    assert scoped.work_item_count == 1
    assert scoped.artifact_count == 1
    assert scoped.active_claims == 1
    assert not scoped.requires_attention
    report = await repository.maintain(
        ArtifactMaintenancePolicy(),
        timestamp_ms=2_000,
    )

    assert report.expired_claims_released == 1
    assert report.unavailable_run_claims_released == 1
    assert report.invalid_target_claims_released == 1
    assert report.released_claims == 3
    assert not report.purged_artifacts
    assert not report.purged_work_items
    claims = await maintenance_db.fetch_all(
        "SELECT artifact_id FROM ai_agent_artifact_claims ORDER BY artifact_id"
    )
    assert claims == [{"artifact_id": valid.artifact.id}]
    assert await SqliteArtifactRepository(maintenance_db).load(
        expired.artifact.id
    ) is not None
    assert await SqliteArtifactRepository(maintenance_db).load(
        terminal.artifact.id
    ) is not None

    repeated = await repository.maintain(
        ArtifactMaintenancePolicy(),
        timestamp_ms=2_000,
    )
    assert not repeated.changed
    assert repeated.released_claims == 0


@pytest.mark.asyncio
async def test_retention_gc_is_explicit_and_never_purges_open_or_in_use_state(
    maintenance_db,
) -> None:
    terminal_run = await _running_run(maintenance_db, "purge-item")
    terminal = await _start_artifact(
        maintenance_db,
        run_id=terminal_run,
        label="purge-item",
        expires_at_ms=10_000,
    )
    await maintenance_db.execute(
        "UPDATE ai_agent_artifacts SET status = 'finalized', "
        "update_time = '1970-01-01 00:00:01' WHERE id = ?",
        [terminal.artifact.id],
    )
    await maintenance_db.execute(
        "UPDATE ai_agent_work_items SET status = 'completed', "
        "update_time = '1970-01-01 00:00:01' WHERE id = ?",
        [terminal.work_item.id],
    )
    await maintenance_db.execute(
        "UPDATE ai_agent_runs SET status = 'completed' WHERE id = ?",
        [terminal_run],
    )

    open_run = await _running_run(maintenance_db, "open")
    open_state = await _start_artifact(
        maintenance_db,
        run_id=open_run,
        label="open",
        expires_at_ms=10_000,
    )
    await maintenance_db.execute(
        "UPDATE ai_agent_work_items SET update_time = "
        "'1970-01-01 00:00:01' WHERE id = ?",
        [open_state.work_item.id],
    )
    await maintenance_db.execute(
        "UPDATE ai_agent_artifacts SET update_time = "
        "'1970-01-01 00:00:01' WHERE id = ?",
        [open_state.artifact.id],
    )

    in_use_run = await _running_run(maintenance_db, "in-use")
    in_use = await _start_artifact(
        maintenance_db,
        run_id=in_use_run,
        label="in-use",
        expires_at_ms=10_000,
    )
    await maintenance_db.execute(
        "UPDATE ai_agent_artifacts SET status = 'finalized', "
        "update_time = '1970-01-01 00:00:01' WHERE id = ?",
        [in_use.artifact.id],
    )
    await maintenance_db.execute(
        "UPDATE ai_agent_work_items SET status = 'completed', "
        "update_time = '1970-01-01 00:00:01' WHERE id = ?",
        [in_use.work_item.id],
    )
    await maintenance_db.execute(
        "DELETE FROM ai_agent_artifact_claims WHERE artifact_id = ?",
        [in_use.artifact.id],
    )

    run_artifact_run = await _running_run(maintenance_db, "run-artifact")
    run_artifact = await SqliteArtifactRepository(maintenance_db).create(
        "run-artifact-terminal",
        ArtifactCreateCommand(
            namespace="purrtypos.screenplay",
            kind="legacy-run-output",
            owner_id="project-run-artifact",
            run_id=run_artifact_run,
        ),
    )
    await maintenance_db.execute(
        "UPDATE ai_agent_artifacts SET status = 'finalized', "
        "update_time = '1970-01-01 00:00:01' WHERE id = ?",
        [run_artifact.id],
    )
    await maintenance_db.execute(
        "UPDATE ai_agent_runs SET status = 'completed' WHERE id = ?",
        [run_artifact_run],
    )

    repository = SqliteArtifactMaintenanceRepository(maintenance_db)
    report = await repository.maintain(
        ArtifactMaintenancePolicy(terminal_retention_ms=1_000),
        timestamp_ms=10_000,
    )

    assert report.purged_work_items == 1
    assert report.purged_artifacts == 2
    assert await maintenance_db.fetch_one(
        "SELECT id FROM ai_agent_work_items WHERE id = ?",
        [terminal.work_item.id],
    ) is None
    assert await SqliteArtifactRepository(maintenance_db).load(
        run_artifact.id
    ) is None
    assert await SqliteArtifactRepository(maintenance_db).load(
        open_state.artifact.id
    ) is not None
    assert await SqliteArtifactRepository(maintenance_db).load(
        in_use.artifact.id
    ) is not None

    await maintenance_db.execute(
        "UPDATE ai_agent_runs SET status = 'completed' WHERE id = ?",
        [in_use_run],
    )
    released = await repository.maintain(
        ArtifactMaintenancePolicy(terminal_retention_ms=1_000),
        timestamp_ms=10_000,
    )
    assert released.purged_work_items == 1
    assert released.purged_artifacts == 1


@pytest.mark.asyncio
async def test_inconsistent_open_artifact_is_reported_but_retained(
    maintenance_db,
) -> None:
    run_id = await _running_run(maintenance_db, "inconsistent")
    started = await _start_artifact(
        maintenance_db,
        run_id=run_id,
        label="inconsistent",
        expires_at_ms=20_000,
    )
    await maintenance_db.execute(
        "UPDATE ai_agent_work_items SET status = 'completed', "
        "update_time = '1970-01-01 00:00:01' WHERE id = ?",
        [started.work_item.id],
    )

    report = await SqliteArtifactMaintenanceRepository(
        maintenance_db
    ).maintain(
        ArtifactMaintenancePolicy(terminal_retention_ms=0),
        timestamp_ms=10_000,
    )

    assert report.consistency_issues == 1
    assert report.invalid_target_claims_released == 1
    assert report.purged_work_items == 0
    assert await SqliteArtifactRepository(maintenance_db).load(
        started.artifact.id
    ) is not None


def test_maintenance_policy_requires_explicit_valid_bounds() -> None:
    assert ArtifactMaintenancePolicy().terminal_retention_ms is None
    with pytest.raises(ValueError, match="non-negative"):
        ArtifactMaintenancePolicy(terminal_retention_ms=-1)
    with pytest.raises(ValueError, match="positive"):
        ArtifactMaintenancePolicy(max_purge_work_items=0)
    snapshot = ArtifactMaintenanceSnapshot(
        checked_at_ms=1,
        expired_claims=1,
    )
    assert snapshot.claim_count == 1
    assert snapshot.requires_attention


@pytest.mark.asyncio
async def test_maintenance_monitor_stops_cooperatively() -> None:
    class _RecordingRepository:
        calls = 0

        async def maintain(self, policy, *, timestamp_ms=None):
            assert isinstance(policy, ArtifactMaintenancePolicy)
            assert timestamp_ms is None
            self.calls += 1
            return ArtifactMaintenanceReport()

    repository = _RecordingRepository()
    stop = asyncio.Event()
    monitor = asyncio.create_task(monitor_artifact_maintenance(
        repository,
        ArtifactMaintenancePolicy(),
        poll_interval_seconds=0.01,
        stop_event=stop,
    ))
    for _ in range(100):
        if repository.calls:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("artifact maintenance monitor did not sweep")
    stop.set()
    await asyncio.wait_for(monitor, timeout=0.5)
