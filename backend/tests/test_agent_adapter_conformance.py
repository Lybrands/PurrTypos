from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from purra.contracts import RunCreateParams
from purra.ports import RunControlStore
from purra.testing import (
    assert_delegation_repository_conforms,
    assert_execution_lease_store_conforms,
    assert_tool_idempotency_gateway_conforms,
)
from database.connection import DatabaseConnection
from infrastructure.persistence import run_store
from infrastructure.persistence.run_execution_store import (
    SqliteRunControlStore,
)
from infrastructure.persistence.sqlite_delegation_repository import (
    SqliteDelegationRepository,
)
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from infrastructure.persistence.sqlite_tool_idempotency_gateway import (
    SqliteToolIdempotencyGateway,
)


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        yield connection
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_sqlite_run_control_store_conforms(db):
    async def create_run() -> str:
        return await run_store.create_run(
            db,
            session_id=None,
            prompt="lease contract",
            mode="agent",
        )

    store = SqliteRunControlStore(db)
    assert isinstance(store, RunControlStore)
    await assert_execution_lease_store_conforms(
        store,
        create_run,
    )


@pytest.mark.asyncio
async def test_sqlite_delegation_repository_conforms(db):
    async def create_parent() -> str:
        return await run_store.create_run(
            db,
            session_id=None,
            prompt="delegation contract",
            mode="agent",
        )

    await assert_delegation_repository_conforms(
        SqliteDelegationRepository(db),
        create_parent,
    )


@pytest.mark.asyncio
async def test_delegation_schema_migrates_agent_role_to_agent_name(tmp_path: Path):
    legacy = DatabaseConnection(tmp_path)
    await legacy.init()
    try:
        await legacy.execute(
            "ALTER TABLE ai_agent_delegations ADD COLUMN agent_role TEXT"
        )
        await legacy.execute(
            "INSERT INTO ai_agent_delegations "
            "(id, run_id, batch_id, agent_name, agent_role, agent_title, "
            "agent_instruction, objective) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "delegation-legacy",
                "run-legacy",
                "batch-legacy",
                "",
                "legacy-reviewer",
                "",
                "",
                "review",
            ],
        )
    finally:
        await legacy.close()

    migrated = DatabaseConnection(tmp_path)
    await migrated.init()
    try:
        columns = {
            str(row["name"])
            for row in await migrated.fetch_all(
                "PRAGMA table_info(ai_agent_delegations)"
            )
        }
        rows = await SqliteDelegationRepository(migrated).list_for_run(
            "run-legacy"
        )
    finally:
        await migrated.close()

    assert "agent_role" not in columns
    assert rows[0].agent_name == "legacy-reviewer"
    assert rows[0].agent_title == "legacy-reviewer"


@pytest.mark.asyncio
async def test_sqlite_tool_idempotency_gateway_conforms(db):
    repository = SqliteRunRepository(db, owner_id="contract-owner")
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="idempotency contract",
        mode="agent",
    ))
    await assert_tool_idempotency_gateway_conforms(
        SqliteToolIdempotencyGateway(db, owner_id=repository.owner_id),
        run_id,
    )
