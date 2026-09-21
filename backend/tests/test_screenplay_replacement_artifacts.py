from __future__ import annotations

import pytest
import pytest_asyncio

from agents.screenplay.candidate_artifact import ScreenplayCandidateArtifactStore
from agents.screenplay.capture_artifact import ScreenplayCaptureArtifactStore
from agents.screenplay.contracts import ScreenplayPartOperationScope
from database.connection import DatabaseConnection
from infrastructure.persistence.run_store import create_run
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from purra.artifacts import ArtifactLifecycle


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await create_run(
        db,
        run_id="run-1",
        session_id=None,
        prompt="candidate artifact test",
        mode="screenplay",
    )
    try:
        yield db
    finally:
        await db.close()


def _scope(attempt: int) -> ScreenplayPartOperationScope:
    return ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-1",
        unit_id="unit-1",
        attempt=attempt,
        part_kind="document_section",
        part_key="section:premise",
        target_role="creativeBrief",
        source_revision_refs=(),
        deliverable_revision_scope={},
    )


def _payload(text: str) -> dict:
    return {
        "schemaVersion": 1,
        "partKind": "document_section",
        "partKey": "section:premise",
        "targetRole": "creativeBrief",
        "payload": {"content": text},
    }


@pytest.mark.asyncio
async def test_same_attempt_replays_one_finalized_candidate(temp_db) -> None:
    store = ScreenplayCandidateArtifactStore(temp_db)

    first = await store.commit(scope=_scope(1), run_id="run-1", payload=_payload("A"))
    replay = await store.commit(scope=_scope(1), run_id="run-1", payload=_payload("A"))

    assert replay.artifact_id == first.artifact_id
    assert replay.replayed is True
    assert (await store.load(first.artifact_id))["payload"] == {"content": "A"}


@pytest.mark.asyncio
async def test_next_attempt_gets_a_distinct_candidate_artifact(temp_db) -> None:
    store = ScreenplayCandidateArtifactStore(temp_db)

    first = await store.commit(scope=_scope(1), run_id="run-1", payload=_payload("A"))
    second = await store.commit(scope=_scope(2), run_id="run-1", payload=_payload("B"))

    assert second.artifact_id != first.artifact_id
    assert second.resource_ref != first.resource_ref


@pytest.mark.asyncio
async def test_same_attempt_rejects_a_different_candidate(temp_db) -> None:
    store = ScreenplayCandidateArtifactStore(temp_db)
    await store.commit(scope=_scope(1), run_id="run-1", payload=_payload("A"))

    with pytest.raises(ValueError, match="identity conflict"):
        await store.commit(
            scope=_scope(1),
            run_id="run-1",
            payload=_payload("different"),
        )


@pytest.mark.asyncio
async def test_append_then_interruption_resumes_finalize_without_model_rewrite(
    temp_db,
) -> None:
    lifecycle = ArtifactLifecycle(SqliteArtifactRepository(temp_db))

    class _FailFinalizeOnce:
        async def append(self, command):
            return await lifecycle.append(command)

        async def get(self, artifact_id):
            return await lifecycle.get(artifact_id)

        async def finalize(self, command):
            raise RuntimeError("simulated finalize interruption")

    arguments = {
        "scope": _scope(1),
        "run_id": "run-1",
        "payload": _payload("A"),
    }
    with pytest.raises(RuntimeError, match="finalize interruption"):
        await ScreenplayCandidateArtifactStore(
            temp_db,
            lifecycle=_FailFinalizeOnce(),
        ).commit(**arguments)

    recovered = await ScreenplayCandidateArtifactStore(temp_db).commit(**arguments)

    assert recovered.replayed is True
    assert (await ScreenplayCandidateArtifactStore(temp_db).load(
        recovered.artifact_id
    ))["payload"] == {"content": "A"}


@pytest.mark.asyncio
async def test_candidate_payload_cannot_change_host_bound_part_scope(temp_db) -> None:
    store = ScreenplayCandidateArtifactStore(temp_db)
    payload = _payload("A")
    payload["partKey"] = "other"

    with pytest.raises(ValueError, match="scope conflicts"):
        await store.commit(scope=_scope(1), run_id="run-1", payload=payload)


def _scene_scope(attempt: int) -> ScreenplayPartOperationScope:
    return ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-scene",
        unit_id="unit-scene",
        attempt=attempt,
        part_kind="draft_scene",
        part_key="scene-1",
        target_role="screenplayDraft",
        source_revision_refs=(),
        deliverable_revision_scope={"sceneList": "scene-list-1"},
        episode_number=1,
        scene_id="scene-1",
    )


def _scene_list_scope(attempt: int) -> ScreenplayPartOperationScope:
    return ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-scene-list",
        unit_id="unit-scene-list",
        attempt=attempt,
        part_kind="document_section",
        part_key="scene-list:episode:2",
        target_role="sceneList",
        source_revision_refs=("structure-1",),
        deliverable_revision_scope={"structure": "structure-1"},
        episode_number=2,
    )


def _review_scope(attempt: int) -> ScreenplayPartOperationScope:
    return ScreenplayPartOperationScope(
        project_id="project-1",
        task_id="task-review",
        unit_id="unit-review",
        attempt=attempt,
        part_kind="review_dimension",
        part_key="review:main",
        target_role="review",
        source_revision_refs=("scene-list-1", "draft-1"),
        deliverable_revision_scope={
            "sceneList": "scene-list-1",
            "screenplayDraft": "draft-1",
        },
    )


@pytest.mark.asyncio
async def test_scene_list_candidate_requires_host_bound_episode_shape(temp_db) -> None:
    store = ScreenplayCandidateArtifactStore(temp_db)
    valid = {
        "schemaVersion": 1,
        "partKind": "document_section",
        "partKey": "scene-list:episode:2",
        "targetRole": "sceneList",
        "payload": {
            "episodeNumber": 2,
            "title": "第二集",
            "scenes": [{"id": "ep02-s01", "heading": "仓库"}],
        },
    }

    receipt = await store.commit(
        scope=_scene_list_scope(1), run_id="run-1", payload=valid
    )
    assert (await store.load(receipt.artifact_id))["payload"]["episodeNumber"] == 2

    invalid = {**valid, "payload": {**valid["payload"], "episodeNumber": 3}}
    with pytest.raises(ValueError, match="episode scope conflicts"):
        await store.commit(
            scope=_scene_list_scope(2), run_id="run-1", payload=invalid
        )


@pytest.mark.asyncio
async def test_review_candidate_requires_authoritative_review_shape(temp_db) -> None:
    store = ScreenplayCandidateArtifactStore(temp_db)
    valid = {
        "schemaVersion": 1,
        "partKind": "review_dimension",
        "partKey": "review:main",
        "targetRole": "review",
        "payload": {
            "verdict": "revise",
            "issues": [{
                "id": "pace-1",
                "severity": "major",
                "description": "中段推进缺少转折。",
                "sceneIds": ["scene-2"],
            }],
        },
    }

    receipt = await store.commit(
        scope=_review_scope(1), run_id="run-1", payload=valid
    )
    assert (await store.load(receipt.artifact_id))["payload"]["verdict"] == "revise"

    without_severity = {
        **valid,
        "payload": {
            **valid["payload"],
            "issues": [{
                key: value
                for key, value in valid["payload"]["issues"][0].items()
                if key != "severity"
            }],
        },
    }
    await store.commit(
        scope=_review_scope(2), run_id="run-1", payload=without_severity
    )

    duplicate = {
        **valid,
        "payload": {
            **valid["payload"],
            "issues": [valid["payload"]["issues"][0]] * 2,
        },
    }
    with pytest.raises(ValueError, match="ids must be unique"):
        await store.commit(
            scope=_review_scope(3), run_id="run-1", payload=duplicate
        )


@pytest.mark.asyncio
async def test_host_capture_is_attempt_scoped_and_replayable(temp_db) -> None:
    store = ScreenplayCaptureArtifactStore(temp_db)
    payload = {"sceneId": "scene-1", "sceneText": "雨夜，门被推开。"}

    first = await store.commit(scope=_scene_scope(1), run_id="run-1", payload=payload)
    replay = await store.commit(scope=_scene_scope(1), run_id="run-1", payload=payload)
    retry = await store.commit(scope=_scene_scope(2), run_id="run-1", payload=payload)

    assert replay[0] == first[0]
    assert replay[2] is True
    assert retry[0] != first[0]
    assert (await store.try_load(_scene_scope(2)))[2] == payload


@pytest.mark.asyncio
async def test_host_capture_rejects_scene_identity_and_content_changes(temp_db) -> None:
    store = ScreenplayCaptureArtifactStore(temp_db)
    with pytest.raises(ValueError, match="scope is invalid"):
        await store.commit(
            scope=_scene_scope(1),
            run_id="run-1",
            payload={"sceneId": "scene-other", "sceneText": "正文"},
        )
    await store.commit(
        scope=_scene_scope(1),
        run_id="run-1",
        payload={"sceneId": "scene-1", "sceneText": "版本一"},
    )
    with pytest.raises(ValueError, match="identity conflict"):
        await store.commit(
            scope=_scene_scope(1),
            run_id="run-1",
            payload={"sceneId": "scene-1", "sceneText": "版本二"},
        )
