from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from purra.contracts import RunCreateParams
from database.connection import DatabaseConnection
from infrastructure.persistence import run_store
from infrastructure.persistence.run_execution_store import (
    SqliteExecutionLeaseStore,
)
from infrastructure.persistence.sqlite_checkpoint_store import (
    SqliteCheckpointStore,
)
from infrastructure.persistence.sqlite_delegation_repository import (
    SqliteDelegationRepository,
)
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from infrastructure.persistence.sqlite_tool_idempotency_gateway import (
    SqliteToolIdempotencyGateway,
)
from tests.support.agent_adapter_contracts import (
    assert_checkpoint_store_contract,
    assert_delegation_repository_contract,
    assert_execution_lease_store_contract,
    assert_tool_idempotency_gateway_contract,
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
async def test_sqlite_execution_lease_store_conforms(db):
    async def create_run() -> str:
        return await run_store.create_run(
            db,
            session_id=None,
            prompt="lease contract",
            mode="agent",
        )

    await assert_execution_lease_store_contract(
        SqliteExecutionLeaseStore(db),
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

    await assert_delegation_repository_contract(
        SqliteDelegationRepository(db),
        create_parent,
    )


@pytest.mark.asyncio
async def test_sqlite_checkpoint_store_conforms(db):
    async def seed_run() -> str:
        run_id = await run_store.create_run(
            db,
            session_id=None,
            prompt="checkpoint contract",
            mode="agent",
        )
        await run_store.append_event(db, run_id, "contract.first", {"index": 1})
        await run_store.append_event(db, run_id, "contract.second", {"index": 2})
        return run_id

    await assert_checkpoint_store_contract(SqliteCheckpointStore(db), seed_run)


@pytest.mark.asyncio
async def test_sqlite_tool_idempotency_gateway_conforms(db):
    repository = SqliteRunRepository(db, owner_id="contract-owner")
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="idempotency contract",
        mode="agent",
    ))
    await assert_tool_idempotency_gateway_contract(
        SqliteToolIdempotencyGateway(db, owner_id=repository.owner_id),
        run_id,
    )
