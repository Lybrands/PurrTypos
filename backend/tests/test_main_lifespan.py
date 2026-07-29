from __future__ import annotations

from pathlib import Path

import pytest

import config
import dependencies
import main
from application.agent_composition import get_agent_composition
from database import connection as database_connection
from exceptions import DatabaseNotReadyError


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
    with pytest.raises(DatabaseNotReadyError):
        dependencies.get_db()
    with pytest.raises(RuntimeError, match="not been initialized"):
        get_agent_composition()


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

    assert len(created) == 1
    assert created[0]._conn is None
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
        assert created == [first_db]
        assert first_db._conn is not None
    finally:
        if first_open:
            await first_context.__aexit__(None, None, None)
