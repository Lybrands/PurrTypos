from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from purra.artifacts import (
    ArtifactAppendCommand,
    ArtifactCreateCommand,
    ArtifactFinalizeCommand,
    ArtifactLifecycle,
    ArtifactStatus,
    ArtifactValidationResult,
)
from purra.artifacts.errors import (
    ArtifactConflictError,
    ArtifactStateError,
    ArtifactValidationError,
)
from purra.artifacts.ports import ArtifactRepository, ArtifactValidator
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
    get_run_artifact_metrics,
)


@pytest_asyncio.fixture
async def artifact_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_artifact_batches_are_versioned_replay_safe_and_finalizable(
    artifact_db,
):
    repository = SqliteArtifactRepository(artifact_db)
    lifecycle = ArtifactLifecycle(
        repository,
        id_factory=lambda: "artifact-1",
    )

    artifact = await lifecycle.begin(ArtifactCreateCommand(
        namespace="screenplay",
        kind="scene_list",
        owner_id="project-1",
        run_id="run-1",
        expected_item_count=2,
        metadata={"structureId": "structure-1"},
    ))

    assert isinstance(repository, ArtifactRepository)
    assert artifact.status is ArtifactStatus.OPEN
    assert artifact.revision == 1
    assert artifact.next_sequence == 1
    assert await repository.find_for_run(
        namespace="screenplay",
        kind="scene_list",
        owner_id="project-1",
        run_id="run-1",
    ) == artifact

    first_command = ArtifactAppendCommand(
        artifact_id=artifact.id,
        expected_revision=1,
        sequence=1,
        batch_id="scenes-1",
        idempotency_key="artifact-1:scenes-1",
        items=({"sceneId": "scene-1", "summary": "开场"},),
        coverage_keys=("scene-1",),
    )
    first = await lifecycle.append(first_command)
    replay = await lifecycle.append(first_command)

    assert first.replayed is False
    assert first.committed_revision == 2
    assert replay.replayed is True
    assert replay.committed_revision == first.committed_revision
    assert len(await repository.list_batches(artifact.id)) == 1

    second = await lifecycle.append(ArtifactAppendCommand(
        artifact_id=artifact.id,
        expected_revision=2,
        sequence=2,
        batch_id="scenes-2",
        idempotency_key="artifact-1:scenes-2",
        items=({"sceneId": "scene-2", "summary": "转折"},),
        coverage_keys=("scene-2",),
    ))
    assert second.committed_revision == 3

    finalized = await lifecycle.finalize(ArtifactFinalizeCommand(
        artifact_id=artifact.id,
        expected_revision=3,
        expected_item_count=2,
        expected_coverage_keys=("scene-1", "scene-2"),
        resource_ref="artifact://screenplay/artifact-1",
    ))

    assert finalized.status is ArtifactStatus.FINALIZED
    assert finalized.revision == 4
    assert finalized.committed_item_count == 2
    assert finalized.resource_ref == "artifact://screenplay/artifact-1"
    assert finalized.coverage_digest

    with pytest.raises(ArtifactStateError) as error:
        await lifecycle.append(ArtifactAppendCommand(
            artifact_id=artifact.id,
            expected_revision=4,
            sequence=3,
            batch_id="scenes-3",
            idempotency_key="artifact-1:scenes-3",
            items=({"sceneId": "scene-3"},),
        ))
    assert error.value.code == "artifact_not_open"


@pytest.mark.asyncio
async def test_artifact_idempotency_key_cannot_change_committed_content(artifact_db):
    lifecycle = ArtifactLifecycle(
        SqliteArtifactRepository(artifact_db),
        id_factory=lambda: "artifact-idempotency",
    )
    artifact = await lifecycle.begin(ArtifactCreateCommand(
        namespace="screenplay",
        kind="scene_list",
        owner_id="project-1",
    ))
    original = ArtifactAppendCommand(
        artifact_id=artifact.id,
        expected_revision=1,
        sequence=1,
        batch_id="batch-1",
        idempotency_key="same-key",
        items=({"value": "original"},),
    )
    await lifecycle.append(original)

    with pytest.raises(ArtifactConflictError) as error:
        await lifecycle.append(ArtifactAppendCommand(
            artifact_id=artifact.id,
            expected_revision=1,
            sequence=1,
            batch_id="batch-1",
            idempotency_key="same-key",
            items=({"value": "changed"},),
        ))

    assert error.value.code == "artifact_idempotency_conflict"


@pytest.mark.asyncio
async def test_concurrent_begin_for_same_run_converges_on_one_artifact(artifact_db):
    repository = SqliteArtifactRepository(artifact_db)
    first = ArtifactLifecycle(repository, id_factory=lambda: "artifact-first")
    second = ArtifactLifecycle(repository, id_factory=lambda: "artifact-second")
    command = ArtifactCreateCommand(
        namespace="screenplay",
        kind="scene_list",
        owner_id="project-1",
        run_id="run-concurrent-begin",
        expected_item_count=2,
        metadata={"structureId": "structure-1"},
    )

    records = await asyncio.gather(
        first.begin(command),
        second.begin(command),
    )

    assert records[0].id == records[1].id
    row = await artifact_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifacts "
        "WHERE run_id = ?",
        [command.run_id],
    )
    assert int(row["count"]) == 1


@pytest.mark.asyncio
async def test_artifact_finalize_rejects_incomplete_count_and_coverage(artifact_db):
    lifecycle = ArtifactLifecycle(
        SqliteArtifactRepository(artifact_db),
        id_factory=lambda: "artifact-incomplete",
    )
    artifact = await lifecycle.begin(ArtifactCreateCommand(
        namespace="screenplay",
        kind="scene_list",
        owner_id="project-1",
        expected_item_count=2,
    ))
    await lifecycle.append(ArtifactAppendCommand(
        artifact_id=artifact.id,
        expected_revision=1,
        sequence=1,
        batch_id="batch-1",
        idempotency_key="batch-1",
        items=({"value": 1},),
        coverage_keys=("scene-1",),
    ))

    with pytest.raises(ArtifactValidationError) as error:
        await lifecycle.finalize(ArtifactFinalizeCommand(
            artifact_id=artifact.id,
            expected_revision=2,
            expected_coverage_keys=("scene-1", "scene-2"),
        ))

    assert error.value.code == "artifact_item_count_incomplete"
    current = await lifecycle.get(artifact.id)
    assert current.status is ArtifactStatus.OPEN


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_in_later_batch", [False, True])
async def test_artifact_rejects_duplicate_coverage_before_commit(
    artifact_db,
    repeat_in_later_batch,
):
    repository = SqliteArtifactRepository(artifact_db)
    lifecycle = ArtifactLifecycle(
        repository,
        id_factory=lambda: f"artifact-duplicate-{repeat_in_later_batch}",
    )
    artifact = await lifecycle.begin(ArtifactCreateCommand(
        namespace="screenplay",
        kind="scene_list",
        owner_id="project-1",
    ))
    if repeat_in_later_batch:
        first = await lifecycle.append(ArtifactAppendCommand(
            artifact_id=artifact.id,
            expected_revision=1,
            sequence=1,
            batch_id="batch-1",
            idempotency_key="batch-1",
            items=({"sceneId": "scene-1"},),
            coverage_keys=("scene-1",),
        ))
        expected_revision = first.committed_revision
        sequence = first.next_sequence
        coverage_keys = ("scene-1",)
    else:
        expected_revision = 1
        sequence = 1
        coverage_keys = ("scene-1", "scene-1")

    with pytest.raises(ArtifactValidationError) as error:
        await lifecycle.append(ArtifactAppendCommand(
            artifact_id=artifact.id,
            expected_revision=expected_revision,
            sequence=sequence,
            batch_id="duplicate",
            idempotency_key="duplicate",
            items=({"sceneId": "duplicate"},),
            coverage_keys=coverage_keys,
        ))

    assert error.value.code == "artifact_coverage_duplicate"
    assert len(await repository.list_batches(artifact.id)) == int(
        repeat_in_later_batch
    )


@pytest.mark.asyncio
async def test_domain_validator_rejection_does_not_commit_batch(artifact_db):
    class RejectingValidator:
        async def validate_batch(self, artifact, command):
            return ArtifactValidationResult(
                False,
                "screenplay_scene_invalid",
                {"sequence": command.sequence},
            )

        async def validate_finalization(self, artifact, batches, command):
            return ArtifactValidationResult(True)

    validator = RejectingValidator()
    repository = SqliteArtifactRepository(artifact_db)
    lifecycle = ArtifactLifecycle(
        repository,
        validator=validator,
        id_factory=lambda: "artifact-validation",
    )
    artifact = await lifecycle.begin(ArtifactCreateCommand(
        namespace="screenplay",
        kind="scene_list",
        owner_id="project-1",
    ))

    assert isinstance(validator, ArtifactValidator)
    with pytest.raises(ArtifactValidationError) as error:
        await lifecycle.append(ArtifactAppendCommand(
            artifact_id=artifact.id,
            expected_revision=1,
            sequence=1,
            batch_id="invalid",
            idempotency_key="invalid",
            items=({"sceneId": "bad"},),
        ))

    assert error.value.code == "screenplay_scene_invalid"
    assert await repository.list_batches(artifact.id) == ()
    current = await repository.load(artifact.id)
    assert current.revision == 1


@pytest.mark.asyncio
async def test_large_artifact_survives_restarts_and_replays_without_duplication(
    artifact_db,
):
    repository = SqliteArtifactRepository(artifact_db)
    lifecycle = ArtifactLifecycle(
        repository,
        id_factory=lambda: "artifact-stress",
    )
    artifact = await lifecycle.begin(ArtifactCreateCommand(
        namespace="screenplay",
        kind="stress_batches",
        owner_id="project-stress",
        run_id="run-artifact-stress",
        expected_item_count=500,
    ))

    for batch_index in range(1, 101):
        if batch_index % 10 == 1:
            lifecycle = ArtifactLifecycle(repository)
        current = await lifecycle.get(artifact.id)
        start = (batch_index - 1) * 5
        items = tuple(
            {"index": index, "value": f"item-{index}"}
            for index in range(start + 1, start + 6)
        )
        coverage = tuple(f"item:{item['index']}" for item in items)
        command = ArtifactAppendCommand(
            artifact_id=artifact.id,
            expected_revision=current.revision,
            sequence=current.next_sequence,
            batch_id=f"batch-{batch_index}",
            idempotency_key=f"stress:{batch_index}",
            items=items,
            coverage_keys=coverage,
        )
        receipt = await lifecycle.append(command)
        assert receipt.replayed is False
        if batch_index % 10 == 0:
            replay = await ArtifactLifecycle(repository).append(command)
            assert replay.replayed is True
            assert replay.committed_revision == receipt.committed_revision

    current = await ArtifactLifecycle(repository).get(artifact.id)
    finalized = await ArtifactLifecycle(repository).finalize(
        ArtifactFinalizeCommand(
            artifact_id=artifact.id,
            expected_revision=current.revision,
            expected_item_count=500,
            expected_coverage_keys=tuple(
                f"item:{index}" for index in range(1, 501)
            ),
            resource_ref="artifact://screenplay/stress/artifact-stress",
        )
    )

    assert finalized.status is ArtifactStatus.FINALIZED
    assert finalized.committed_item_count == 500
    batches = await repository.list_batches(artifact.id)
    assert len(batches) == 100
    assert [batch.sequence for batch in batches] == list(range(1, 101))
    metrics = await get_run_artifact_metrics(
        artifact_db,
        "run-artifact-stress",
    )
    assert metrics == {
        "artifactCount": 1,
        "openArtifacts": 0,
        "finalizedArtifacts": 1,
        "abortedArtifacts": 0,
        "batchCount": 100,
        "committedItemCount": 500,
        "expectedItemCount": 500,
        "completionRate": 1.0,
        "artifacts": [{
            "artifactId": "artifact-stress",
            "namespace": "screenplay",
            "kind": "stress_batches",
            "status": "finalized",
            "scope": "run",
            "workItemId": None,
            "workItemStatus": None,
            "runRelation": None,
            "revision": 102,
            "committedItemCount": 500,
            "expectedItemCount": 500,
            "batchCount": 100,
            "batchItemCount": 500,
        }],
    }
