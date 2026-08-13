from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio

from purra.artifacts import (
    ArtifactAppendCommand,
    ArtifactClaimLeaseCommand,
    ArtifactCreateCommand,
    ArtifactFinalizeCommand,
    ArtifactLifecycle,
    ArtifactMutationLease,
    ArtifactScope,
    ArtifactWriteClaimCommand,
)
from purra.artifacts.errors import (
    ArtifactConflictError,
    ArtifactStateError,
)
from purra.artifacts.ports import ArtifactClaimRepository
from purra.work_items import (
    WorkItemCreateCommand,
    WorkItemRunLinkCommand,
    WorkItemRunRelation,
    WorkItemTransitionCommand,
)
from purra.work_items.ports import WorkItemRepository
from database.connection import DatabaseConnection
from tests.support import screenplay_v2_driver as screenplay_crud
from infrastructure.persistence.sqlite_artifact_claim_repository import (
    SqliteArtifactClaimRepository,
)
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from infrastructure.persistence.sqlite_work_item_repository import (
    SqliteWorkItemRepository,
)
from infrastructure.persistence.sqlite_work_item_artifact_lifecycle import (
    SqliteWorkItemArtifactLifecycle,
)
from application.product_owner_deletion import ProductOwnerActiveError
from tests.support.agent_adapter_contracts import (
    assert_artifact_claim_repository_contract,
    assert_work_item_repository_contract,
)


@pytest_asyncio.fixture
async def continuity_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sqlite_work_item_repository_satisfies_contract(
    continuity_db,
) -> None:
    repository = SqliteWorkItemRepository(continuity_db)
    assert isinstance(repository, WorkItemRepository)
    await assert_work_item_repository_contract(repository)


async def _seed_work_item_artifact(db):
    work_items = SqliteWorkItemRepository(db)
    item = await work_items.create(
        "work-item-1",
        WorkItemCreateCommand(
            namespace="purrtypos.screenplay",
            kind="source_adaptation",
            owner_id="project-1",
            created_by_run_id="run-a",
        ),
    )
    for run_id in ("run-b", "run-c"):
        await work_items.link_run(WorkItemRunLinkCommand(
            work_item_id=item.id,
            run_id=run_id,
            relation=WorkItemRunRelation.CONTINUATION,
            expected_revision=item.revision,
        ))
    artifacts = SqliteArtifactRepository(db)
    artifact = await artifacts.create(
        "artifact-work-item-1",
        ArtifactCreateCommand(
            namespace="purrtypos.screenplay",
            kind="source_analysis",
            owner_id="project-1",
            run_id="run-a",
            scope=ArtifactScope.WORK_ITEM,
            work_item_id=item.id,
            created_by_run_id="run-a",
            expected_item_count=10,
        ),
    )
    return work_items, artifacts, item, artifact


@pytest.mark.asyncio
async def test_atomic_work_item_artifact_lifecycle_owns_claim_and_completion(
    continuity_db,
) -> None:
    clock = {"now": 1_000}
    starter = SqliteWorkItemArtifactLifecycle(
        continuity_db,
        clock=lambda: clock["now"],
        token_factory=lambda: "claim-atomic",
        write_lease_duration_ms=1_000,
    )
    command = ArtifactCreateCommand(
        namespace="purrtypos.screenplay",
        kind="scene_list_batches",
        owner_id="project-atomic",
        run_id="run-atomic",
        expected_item_count=1,
        metadata={"title": "原子场景表"},
    )
    started = await starter.begin(command)

    assert started.artifact.scope is ArtifactScope.WORK_ITEM
    assert started.artifact.work_item_id == started.work_item.id
    assert started.write_claim is not None
    assert started.write_claim.run_id == "run-atomic"
    assert started.write_claim.acquired_revision == 1
    counts = await continuity_db.fetch_one(
        "SELECT "
        "(SELECT COUNT(*) FROM ai_agent_work_items) AS work_items, "
        "(SELECT COUNT(*) FROM ai_agent_work_item_runs) AS run_links, "
        "(SELECT COUNT(*) FROM ai_agent_artifacts) AS artifacts, "
        "(SELECT COUNT(*) FROM ai_agent_artifact_claims) AS claims"
    )
    assert counts == {
        "work_items": 1,
        "run_links": 1,
        "artifacts": 1,
        "claims": 1,
    }

    artifacts = SqliteArtifactRepository(
        continuity_db,
        clock=lambda: clock["now"],
    )
    lifecycle = ArtifactLifecycle(artifacts)
    lease = ArtifactMutationLease(
        run_id="run-atomic",
        claim_token=started.write_claim.claim_token,
        lease_duration_ms=1_000,
    )
    clock["now"] = 1_200
    receipt = await lifecycle.append(ArtifactAppendCommand(
        artifact_id=started.artifact.id,
        expected_revision=1,
        sequence=1,
        batch_id="atomic-batch",
        idempotency_key="atomic-batch",
        items=({"sceneId": "scene-1"},),
        coverage_keys=("scene-1",),
        write_lease=lease,
    ))
    claim = await continuity_db.fetch_one(
        "SELECT acquired_revision, expires_at_ms FROM "
        "ai_agent_artifact_claims WHERE artifact_id = ?",
        [started.artifact.id],
    )
    assert receipt.committed_revision == 2
    assert claim == {"acquired_revision": 2, "expires_at_ms": 2_200}

    finalized = await lifecycle.finalize(ArtifactFinalizeCommand(
        artifact_id=started.artifact.id,
        expected_revision=2,
        expected_item_count=1,
        expected_coverage_keys=("scene-1",),
        resource_ref="artifact://purrtypos.screenplay/atomic",
        write_lease=lease,
        complete_work_item=True,
    ))
    item = await SqliteWorkItemRepository(continuity_db).load(
        started.work_item.id
    )
    active_claim = await continuity_db.fetch_one(
        "SELECT artifact_id FROM ai_agent_artifact_claims WHERE artifact_id = ?",
        [started.artifact.id],
    )
    assert finalized.status.value == "finalized"
    assert item is not None and item.status.value == "completed"
    assert active_claim is None

    replayed = await starter.begin(command)
    assert replayed.artifact.id == started.artifact.id
    assert replayed.write_claim is None


@pytest.mark.asyncio
async def test_work_item_artifact_scope_persists_and_has_distinct_lookup(
    continuity_db,
) -> None:
    _, artifacts, item, artifact = await _seed_work_item_artifact(continuity_db)

    assert artifact.scope is ArtifactScope.WORK_ITEM
    assert artifact.work_item_id == item.id
    assert artifact.created_by_run_id == "run-a"
    assert await artifacts.find_for_run(
        namespace=artifact.namespace,
        kind=artifact.kind,
        owner_id=artifact.owner_id,
        run_id="run-a",
    ) is None
    assert await artifacts.find_for_work_item(
        namespace=artifact.namespace,
        kind=artifact.kind,
        owner_id=artifact.owner_id,
        work_item_id=item.id,
    ) == artifact

    run_artifact = await artifacts.create(
        "artifact-run-1",
        ArtifactCreateCommand(
            namespace=artifact.namespace,
            kind=artifact.kind,
            owner_id=artifact.owner_id,
            run_id="run-a",
        ),
    )
    assert run_artifact.scope is ArtifactScope.RUN
    assert await artifacts.find_for_run(
        namespace=artifact.namespace,
        kind=artifact.kind,
        owner_id=artifact.owner_id,
        run_id="run-a",
    ) == run_artifact
    assert await artifacts.find_for_work_item(
        namespace=artifact.namespace,
        kind=artifact.kind,
        owner_id=artifact.owner_id,
        work_item_id=item.id,
    ) == artifact


@pytest.mark.asyncio
async def test_sqlite_artifact_claim_is_exclusive_between_runs(
    continuity_db,
) -> None:
    _, _, item, artifact = await _seed_work_item_artifact(continuity_db)
    clock = {"now": 1_000}
    tokens = iter(("claim-a", "claim-b", "claim-c"))
    claims = SqliteArtifactClaimRepository(
        continuity_db,
        clock=lambda: clock["now"],
        token_factory=lambda: next(tokens),
    )

    assert isinstance(claims, ArtifactClaimRepository)
    await assert_artifact_claim_repository_contract(
        claims,
        artifact_id=artifact.id,
        work_item_id=item.id,
        revision=artifact.revision,
        first_run_id="run-b",
        second_run_id="run-c",
    )


@pytest.mark.asyncio
async def test_expired_claim_can_be_taken_over_and_wrong_token_cannot_renew(
    continuity_db,
) -> None:
    _, _, item, artifact = await _seed_work_item_artifact(continuity_db)
    clock = {"now": 10_000}
    tokens = iter(("claim-run-b", "claim-run-c"))
    claims = SqliteArtifactClaimRepository(
        continuity_db,
        clock=lambda: clock["now"],
        token_factory=lambda: next(tokens),
    )
    first = await claims.acquire(ArtifactWriteClaimCommand(
        artifact_id=artifact.id,
        work_item_id=item.id,
        run_id="run-b",
        expected_revision=1,
        lease_duration_ms=100,
    ))
    clock["now"] = first.expires_at_ms
    assert await claims.load_active(artifact.id) is None

    second = await claims.acquire(ArtifactWriteClaimCommand(
        artifact_id=artifact.id,
        work_item_id=item.id,
        run_id="run-c",
        expected_revision=1,
        lease_duration_ms=200,
    ))
    assert second.run_id == "run-c"
    assert second.claim_token != first.claim_token

    with pytest.raises(ArtifactConflictError) as wrong_token:
        await claims.renew(ArtifactClaimLeaseCommand(
            artifact_id=artifact.id,
            run_id="run-c",
            claim_token="wrong-token",
            lease_duration_ms=200,
        ))
    assert wrong_token.value.code == "artifact_claim_owner_mismatch"


@pytest.mark.asyncio
async def test_claim_revision_is_checked_and_renew_tracks_owned_progress(
    continuity_db,
) -> None:
    _, artifacts, item, artifact = await _seed_work_item_artifact(continuity_db)
    clock = {"now": 20_000}
    artifacts = SqliteArtifactRepository(
        continuity_db,
        clock=lambda: clock["now"],
    )
    claims = SqliteArtifactClaimRepository(
        continuity_db,
        clock=lambda: clock["now"],
        token_factory=lambda: "claim-progress",
    )
    claim = await claims.acquire(ArtifactWriteClaimCommand(
        artifact_id=artifact.id,
        work_item_id=item.id,
        run_id="run-b",
        expected_revision=1,
        lease_duration_ms=1_000,
    ))
    await artifacts.append(ArtifactAppendCommand(
        artifact_id=artifact.id,
        expected_revision=1,
        sequence=1,
        batch_id="batch-1",
        idempotency_key="batch-1",
        items=({"chapter": 1},),
        write_lease=ArtifactMutationLease(
            run_id="run-b",
            claim_token=claim.claim_token,
            lease_duration_ms=1_000,
        ),
    ))
    clock["now"] += 50
    renewed = await claims.renew(ArtifactClaimLeaseCommand(
        artifact_id=artifact.id,
        run_id="run-b",
        claim_token=claim.claim_token,
        lease_duration_ms=1_000,
    ))
    assert renewed.acquired_revision == 2
    assert await claims.release(ArtifactClaimLeaseCommand(
        artifact_id=artifact.id,
        run_id="run-b",
        claim_token=claim.claim_token,
    ))

    with pytest.raises(ArtifactConflictError) as stale:
        await claims.acquire(ArtifactWriteClaimCommand(
            artifact_id=artifact.id,
            work_item_id=item.id,
            run_id="run-c",
            expected_revision=1,
            lease_duration_ms=1_000,
        ))
    assert stale.value.code == "artifact_revision_conflict"


@pytest.mark.asyncio
async def test_reference_only_run_cannot_claim_artifact_write(
    continuity_db,
) -> None:
    work_items, _, item, artifact = await _seed_work_item_artifact(continuity_db)
    await work_items.link_run(WorkItemRunLinkCommand(
        work_item_id=item.id,
        run_id="run-reader",
        relation=WorkItemRunRelation.REFERENCE,
        expected_revision=1,
    ))
    claims = SqliteArtifactClaimRepository(
        continuity_db,
        clock=lambda: 30_000,
        token_factory=lambda: "should-not-be-used",
    )

    with pytest.raises(ArtifactStateError) as denied:
        await claims.acquire(ArtifactWriteClaimCommand(
            artifact_id=artifact.id,
            work_item_id=item.id,
            run_id="run-reader",
            expected_revision=1,
            lease_duration_ms=1_000,
        ))
    assert denied.value.code == "artifact_claim_run_not_linked"


@pytest.mark.asyncio
async def test_screenplay_project_delete_cleans_continuity_state(
    continuity_db,
) -> None:
    await continuity_db.execute(
        "INSERT INTO screenplay_projects (id, title) VALUES (?, ?)",
        ["project-1", "Continuity cleanup"],
    )
    _, _, item, artifact = await _seed_work_item_artifact(continuity_db)
    claims = SqliteArtifactClaimRepository(
        continuity_db,
        clock=lambda: 40_000,
        token_factory=lambda: "claim-cleanup",
    )
    await claims.acquire(ArtifactWriteClaimCommand(
        artifact_id=artifact.id,
        work_item_id=item.id,
        run_id="run-b",
        expected_revision=1,
        lease_duration_ms=1_000,
    ))

    with pytest.raises(ProductOwnerActiveError):
        await screenplay_crud.delete_project(continuity_db, "project-1")
    await SqliteWorkItemRepository(continuity_db).complete(
        WorkItemTransitionCommand(
            work_item_id=item.id,
            expected_revision=item.revision,
        )
    )
    assert await screenplay_crud.delete_project(continuity_db, "project-1")
    for table in (
        "ai_agent_artifact_claims",
        "ai_agent_artifact_batches",
        "ai_agent_artifacts",
        "ai_agent_long_task_units",
        "ai_agent_long_tasks",
        "ai_agent_work_item_runs",
        "ai_agent_work_items",
    ):
        row = await continuity_db.fetch_one(
            f"SELECT COUNT(*) AS count FROM {table}"
        )
        assert int(row["count"]) == 0


@pytest.mark.asyncio
async def test_legacy_artifact_schema_is_backfilled_without_data_loss(
    tmp_path: Path,
) -> None:
    path = tmp_path / "purrtypos.db"
    connection = sqlite3.connect(path)
    connection.execute("""CREATE TABLE ai_agent_artifacts (
        id TEXT PRIMARY KEY NOT NULL,
        namespace TEXT NOT NULL,
        kind TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        run_id TEXT DEFAULT NULL,
        schema_version INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'open',
        revision INTEGER NOT NULL DEFAULT 1,
        next_sequence INTEGER NOT NULL DEFAULT 1,
        committed_item_count INTEGER NOT NULL DEFAULT 0,
        expected_item_count INTEGER DEFAULT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        resource_ref TEXT DEFAULT NULL,
        coverage_digest TEXT DEFAULT NULL,
        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
        update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    connection.execute(
        "INSERT INTO ai_agent_artifacts "
        "(id, namespace, kind, owner_id, run_id) VALUES (?, ?, ?, ?, ?)",
        ("legacy-artifact", "legacy", "draft", "owner-1", "legacy-run"),
    )
    connection.execute(
        "CREATE UNIQUE INDEX idx_ai_agent_artifacts_run_kind_unique "
        "ON ai_agent_artifacts(namespace, owner_id, kind, run_id) "
        "WHERE run_id IS NOT NULL"
    )
    connection.commit()
    connection.close()

    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        artifact = await SqliteArtifactRepository(db).load("legacy-artifact")
        assert artifact is not None
        assert artifact.scope is ArtifactScope.RUN
        assert artifact.run_id == "legacy-run"
        assert artifact.created_by_run_id == "legacy-run"
    finally:
        await db.close()
