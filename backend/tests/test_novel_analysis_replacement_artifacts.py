from __future__ import annotations

import pytest
import pytest_asyncio

from agents.novel_analysis.attempt_artifact import (
    NovelAnalysisAttemptArtifactStore,
)
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from purra.artifacts import ArtifactLifecycle


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_attempt_commit_is_immutable_and_replay_safe(temp_db) -> None:
    store = NovelAnalysisAttemptArtifactStore(temp_db)
    payload = {"facts": [{"id": "fact-1"}]}

    first = await store.commit(
        task_id="task-1",
        unit_id="extract-1",
        attempt=0,
        operation_id="task-1:extract-1:0",
        run_id="run-1",
        payload=payload,
    )
    replay = await store.commit(
        task_id="task-1",
        unit_id="extract-1",
        attempt=0,
        operation_id="task-1:extract-1:0",
        run_id="run-1",
        payload=payload,
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.artifact_id == first.artifact_id
    assert await store.load_payload(first.artifact_id) == payload


@pytest.mark.asyncio
async def test_new_attempt_never_conflicts_with_previous_attempt_payload(temp_db) -> None:
    store = NovelAnalysisAttemptArtifactStore(temp_db)

    first = await store.commit(
        task_id="task-1",
        unit_id="extract-1",
        attempt=0,
        operation_id="task-1:extract-1:0",
        run_id="run-1",
        payload={"facts": [{"id": "first"}]},
    )
    retry = await store.commit(
        task_id="task-1",
        unit_id="extract-1",
        attempt=1,
        operation_id="task-1:extract-1:1",
        run_id="run-1",
        payload={"facts": [{"id": "retry"}]},
    )

    assert first.artifact_id != retry.artifact_id
    assert await store.load_payload(first.artifact_id) == {
        "facts": [{"id": "first"}]
    }
    assert await store.load_payload(retry.artifact_id) == {
        "facts": [{"id": "retry"}]
    }


@pytest.mark.asyncio
async def test_open_artifact_after_append_resumes_finalization(temp_db) -> None:
    repository = SqliteArtifactRepository(temp_db)
    lifecycle = ArtifactLifecycle(repository)

    class FailFinalizeOnce:
        async def append(self, command):
            return await lifecycle.append(command)

        async def get(self, artifact_id):
            return await lifecycle.get(artifact_id)

        async def finalize(self, command):
            raise RuntimeError("simulated interruption before finalize")

    interrupted = NovelAnalysisAttemptArtifactStore(
        temp_db,
        lifecycle=FailFinalizeOnce(),
    )
    arguments = {
        "task_id": "task-1",
        "unit_id": "extract-1",
        "attempt": 0,
        "operation_id": "task-1:extract-1:0",
        "run_id": "run-1",
        "payload": {"facts": [{"id": "fact-1"}]},
    }
    with pytest.raises(RuntimeError, match="simulated interruption"):
        await interrupted.commit(**arguments)

    recovered = await NovelAnalysisAttemptArtifactStore(temp_db).commit(
        **arguments
    )

    assert recovered.replayed is True
    assert await NovelAnalysisAttemptArtifactStore(temp_db).load_payload(
        recovered.artifact_id
    ) == arguments["payload"]


@pytest.mark.asyncio
async def test_same_attempt_rejects_different_payload(temp_db) -> None:
    store = NovelAnalysisAttemptArtifactStore(temp_db)
    await store.commit(
        task_id="task-1",
        unit_id="extract-1",
        attempt=0,
        operation_id="task-1:extract-1:0",
        run_id="run-1",
        payload={"facts": [{"id": "first"}]},
    )

    with pytest.raises(ValueError, match="identity conflict"):
        await store.commit(
            task_id="task-1",
            unit_id="extract-1",
            attempt=0,
            operation_id="task-1:extract-1:0",
            run_id="run-1",
            payload={"facts": [{"id": "changed"}]},
        )
