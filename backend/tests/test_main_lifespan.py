from __future__ import annotations

from pathlib import Path
import asyncio

import pytest

import config
import dependencies
import main
from application.agent_composition import get_agent_composition
from application.run_execution_control import RunExecutionSession
from database import connection as database_connection
from exceptions import DatabaseNotReadyError
from infrastructure.persistence import run_store
from purra.contracts import RunCreateParams
from purra.long_tasks import LongTaskCreateCommand, LongTaskStatus, LongTaskUnitSpec
from purra.work_items.contracts import WorkItemCreateCommand
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from infrastructure.persistence.sqlite_work_item_repository import (
    SqliteWorkItemRepository,
)


BACKEND_DIR = Path(__file__).resolve().parent.parent

class _RecordingApplication:
    def __init__(self, *, fail_registration: bool = False):
        self.fail_registration = fail_registration
        self.router_count = 0

    def include_router(self, _router, *, prefix: str):
        assert prefix == "/api"
        if self.fail_registration:
            raise RuntimeError("synthetic router registration failure")
        self.router_count += 1


def _capture_database(monkeypatch, tmp_path):
    created = []
    connection_type = database_connection.DatabaseConnection

    def _factory(data_dir):
        db = connection_type(data_dir)
        created.append(db)
        return db

    monkeypatch.setattr(database_connection, "DatabaseConnection", _factory)
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "SKILLS_DIR", BACKEND_DIR / "skills")
    dependencies.clear_db()
    return created


@pytest.mark.asyncio
async def test_lifespan_shutdown_clears_composition_and_global_db(
    monkeypatch,
    tmp_path,
):
    created = _capture_database(monkeypatch, tmp_path)
    application = _RecordingApplication()

    async with main.lifespan(application):
        assert application.router_count == 21
        assert dependencies.get_db() is created[0]
        assert get_agent_composition().writing is not None

    assert created[0]._conn is None
    assert created[1]._conn is None
    with pytest.raises(DatabaseNotReadyError):
        dependencies.get_db()
    with pytest.raises(RuntimeError, match="not been initialized"):
        get_agent_composition()


@pytest.mark.asyncio
async def test_lifespan_execution_heartbeat_is_not_blocked_by_primary_connection(
    monkeypatch,
    tmp_path,
):
    created = _capture_database(monkeypatch, tmp_path)

    async with main.lifespan(_RecordingApplication()):
        composition = get_agent_composition()
        repository = composition._repository
        repository._lease_duration_ms = 120
        run_id = await repository.create(RunCreateParams(
            session_id=None,
            prompt="keep the active run alive",
            mode="agent",
        ))
        initial = await run_store.get_run(created[0], run_id)
        assert initial is not None

        session = RunExecutionSession(
            composition.execution_lease_store,
            owner_id=repository.owner_id,
            lease_duration_ms=repository.lease_duration_ms,
            external_signal=asyncio.Event(),
            poll_interval_seconds=0.01,
        )
        await session.bind(run_id)
        await created[0]._connection_lock.acquire()
        try:
            await asyncio.sleep(0.22)
        finally:
            created[0]._connection_lock.release()

        current = await run_store.get_run(created[0], run_id)
        assert current is not None
        assert current["heartbeat_at_ms"] > initial["heartbeat_at_ms"]
        assert not session.signal.is_set()
        await session.close()


@pytest.mark.asyncio
async def test_lifespan_startup_terminalizes_run_abandoned_by_previous_process(
    monkeypatch,
    tmp_path,
):
    seed_db = database_connection.DatabaseConnection(tmp_path)
    await seed_db.init()
    run_id = await run_store.create_run(
        seed_db,
        session_id=7,
        prompt="abandoned",
        mode="agent",
        execution_owner_id="previous-process",
        heartbeat_at_ms=10_000,
        lease_expires_at_ms=99_999_999_999_999,
    )
    await seed_db.close()

    created = _capture_database(monkeypatch, tmp_path)
    async with main.lifespan(_RecordingApplication()):
        run = await run_store.get_run(created[0], run_id)
        events = await run_store.get_run_events(created[0], run_id)
        assert run is not None and run["status"] == "canceled"
        assert events[-1]["eventType"] == "run.canceled"
        assert events[-1]["payload"]["reason"] == (
            "execution_recovery_after_restart"
        )


@pytest.mark.asyncio
async def test_lifespan_startup_checkpoints_long_task_abandoned_by_previous_process(
    monkeypatch,
    tmp_path,
):
    seed_db = database_connection.DatabaseConnection(tmp_path)
    await seed_db.init()
    await SqliteWorkItemRepository(seed_db).create(
        "startup-long-work",
        WorkItemCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner",
            created_by_run_id="run-parent",
        ),
    )
    repository = SqliteLongTaskRepository(seed_db)
    task = await repository.create(
        "startup-long-task",
        LongTaskCreateCommand(
            namespace="test",
            kind="large_write",
            owner_id="owner",
            work_item_id="startup-long-work",
            created_by_run_id="run-parent",
            units=(
                LongTaskUnitSpec(id="batch-1", position=0, max_attempts=1),
            ),
        ),
    )
    task = await repository.start(task.id, expected_revision=task.revision)
    claimed = await repository.claim_ready_unit(
        task.id,
        worker_id="previous-process",
        lease_duration_ms=300_000,
    )
    assert claimed is not None
    await seed_db.close()

    created = _capture_database(monkeypatch, tmp_path)
    async with main.lifespan(_RecordingApplication()):
        recovered_repository = SqliteLongTaskRepository(created[0])
        recovered = await recovered_repository.load(task.id)
        unit = (await recovered_repository.list_units(task.id))[0]
        assert recovered is not None
        assert recovered.status is LongTaskStatus.PAUSED
        assert unit.status.value == "pending"
        assert unit.attempt == 1
        assert unit.max_attempts == 2
        assert unit.worker_id is None
        assert unit.lease_expires_at_ms is None
        assert unit.error_code == "execution_recovery_after_restart"


@pytest.mark.asyncio
async def test_lifespan_startup_reaps_claim_owned_by_terminal_run(
    monkeypatch,
    tmp_path,
):
    seed_db = database_connection.DatabaseConnection(tmp_path)
    await seed_db.init()
    run_id = await run_store.create_run(
        seed_db,
        session_id=None,
        prompt="already terminal",
        mode="agent",
    )
    await seed_db.execute(
        "UPDATE ai_agent_runs SET status = 'completed' WHERE id = ?",
        [run_id],
    )
    await seed_db.execute(
        "INSERT INTO ai_agent_work_items "
        "(id, namespace, kind, owner_id, created_by_run_id) "
        "VALUES ('startup-item', 'test', 'draft', 'owner', ?)",
        [run_id],
    )
    await seed_db.execute(
        "INSERT INTO ai_agent_work_item_runs "
        "(work_item_id, run_id, relation, work_item_revision) "
        "VALUES ('startup-item', ?, 'created', 1)",
        [run_id],
    )
    await seed_db.execute(
        "INSERT INTO ai_agent_artifacts "
        "(id, namespace, kind, owner_id, run_id, artifact_scope, "
        "work_item_id, created_by_run_id) VALUES "
        "('startup-artifact', 'test', 'draft', 'owner', ?, "
        "'work_item', 'startup-item', ?)",
        [run_id, run_id],
    )
    await seed_db.execute(
        "INSERT INTO ai_agent_artifact_claims "
        "(artifact_id, work_item_id, run_id, claim_token, "
        "acquired_revision, expires_at_ms) VALUES "
        "('startup-artifact', 'startup-item', ?, 'startup-claim', "
        "1, 9999999999999)",
        [run_id],
    )
    await seed_db.close()

    created = _capture_database(monkeypatch, tmp_path)
    async with main.lifespan(_RecordingApplication()):
        assert await created[0].fetch_one(
            "SELECT artifact_id FROM ai_agent_artifact_claims "
            "WHERE artifact_id = 'startup-artifact'"
        ) is None
        assert await created[0].fetch_one(
            "SELECT id FROM ai_agent_artifacts "
            "WHERE id = 'startup-artifact'"
        ) == {"id": "startup-artifact"}


@pytest.mark.asyncio
async def test_lifespan_startup_failure_releases_partial_resources(
    monkeypatch,
    tmp_path,
):
    created = _capture_database(monkeypatch, tmp_path)
    application = _RecordingApplication(fail_registration=True)

    with pytest.raises(RuntimeError, match="synthetic router"):
        async with main.lifespan(application):
            raise AssertionError("startup failure must occur before yield")

    assert len(created) == 2
    assert created[0]._conn is None
    assert created[1]._conn is None
    with pytest.raises(DatabaseNotReadyError):
        dependencies.get_db()
    with pytest.raises(RuntimeError, match="not been initialized"):
        get_agent_composition()


@pytest.mark.asyncio
async def test_overlapping_lifespan_is_rejected_without_disturbing_owner(
    monkeypatch,
    tmp_path,
):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    created = _capture_database(monkeypatch, first_dir)

    first_context = main.lifespan(_RecordingApplication())
    first_open = False
    try:
        await first_context.__aenter__()
        first_open = True
        first_composition = get_agent_composition()
        first_db = dependencies.get_db()

        monkeypatch.setattr(main, "DATA_DIR", second_dir)
        second_context = main.lifespan(_RecordingApplication())
        with pytest.raises(RuntimeError, match="already active"):
            await second_context.__aenter__()

        assert get_agent_composition() is first_composition
        assert dependencies.get_db() is first_db
        assert len(created) == 2
        assert created[0] is first_db
        assert first_db._conn is not None
        assert created[1]._conn is not None
    finally:
        if first_open:
            await first_context.__aexit__(None, None, None)
