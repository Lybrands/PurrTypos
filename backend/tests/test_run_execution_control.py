from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from agent_core.contracts import RunCreateParams
from application.run_execution_control import RunExecutionSession
from database.connection import DatabaseConnection
from infrastructure.persistence import run_execution_store, run_store
from infrastructure.persistence.run_execution_store import (
    SqliteExecutionLeaseStore,
)
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        yield connection
    finally:
        await connection.close()


async def _unowned_run(db: DatabaseConnection) -> str:
    return await run_store.create_run(
        db,
        session_id=None,
        prompt="execute",
        mode="agent",
    )


@pytest.mark.asyncio
async def test_concurrent_claim_has_one_winner(db):
    run_id = await _unowned_run(db)

    claims = await asyncio.gather(
        run_execution_store.claim_run(
            db,
            run_id=run_id,
            owner_id="worker-a",
            lease_duration_ms=1_000,
            timestamp_ms=100,
        ),
        run_execution_store.claim_run(
            db,
            run_id=run_id,
            owner_id="worker-b",
            lease_duration_ms=1_000,
            timestamp_ms=100,
        ),
    )
    state = await run_execution_store.get_execution_state(db, run_id)

    assert claims.count(True) == 1
    assert claims.count(False) == 1
    assert state is not None
    assert state["execution_owner_id"] in {"worker-a", "worker-b"}
    assert state["execution_attempt"] == 1


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed_but_live_lease_cannot(db):
    run_id = await _unowned_run(db)
    assert await run_execution_store.claim_run(
        db,
        run_id=run_id,
        owner_id="worker-a",
        lease_duration_ms=100,
        timestamp_ms=1_000,
    )
    assert not await run_execution_store.claim_run(
        db,
        run_id=run_id,
        owner_id="worker-b",
        lease_duration_ms=100,
        timestamp_ms=1_099,
    )
    assert await run_execution_store.claim_run(
        db,
        run_id=run_id,
        owner_id="worker-b",
        lease_duration_ms=100,
        timestamp_ms=1_100,
    )

    state = await run_execution_store.get_execution_state(db, run_id)
    assert state is not None
    assert state["execution_owner_id"] == "worker-b"
    assert state["execution_attempt"] == 2
    assert not await run_execution_store.renew_lease(
        db,
        run_id=run_id,
        owner_id="worker-a",
        lease_duration_ms=100,
        timestamp_ms=1_101,
    )
    assert await run_execution_store.renew_lease(
        db,
        run_id=run_id,
        owner_id="worker-b",
        lease_duration_ms=100,
        timestamp_ms=1_101,
    )


@pytest.mark.asyncio
async def test_cancellation_blocks_future_claim_and_wakes_live_session(db):
    repository = SqliteRunRepository(
        db,
        owner_id="worker-live",
        lease_duration_ms=1_000,
    )
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="cancel me",
        mode="agent",
    ))
    external = asyncio.Event()
    session = RunExecutionSession(
        SqliteExecutionLeaseStore(db),
        owner_id=repository.owner_id,
        lease_duration_ms=repository.lease_duration_ms,
        external_signal=external,
        poll_interval_seconds=0.01,
    )
    await session.bind(run_id)

    assert await run_execution_store.request_cancellation(db, run_id)
    assert not await run_execution_store.request_cancellation(db, run_id)
    await asyncio.wait_for(session.signal.wait(), timeout=0.5)
    assert session.signal.is_set()
    await session.close()

    assert not await run_execution_store.claim_run(
        db,
        run_id=run_id,
        owner_id="worker-next",
        lease_duration_ms=1_000,
    )
    state = await run_execution_store.get_execution_state(db, run_id)
    assert state is not None
    assert state["execution_owner_id"] is None
    assert state["cancel_requested_at_ms"] is not None
