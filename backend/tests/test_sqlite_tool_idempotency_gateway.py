from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from agent_core.contracts import RunCreateParams, ToolCall, ToolHandlerResult
from agent_core.errors import ContractViolationError
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from infrastructure.persistence.sqlite_tool_idempotency_gateway import (
    SqliteToolIdempotencyGateway,
)


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    await connection.execute(
        "CREATE TABLE test_tool_counter (id INTEGER PRIMARY KEY, value INTEGER NOT NULL)"
    )
    await connection.execute("INSERT INTO test_tool_counter VALUES (1, 0)")
    try:
        yield connection
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_side_effect_and_receipt_commit_once_under_concurrent_replay(db):
    repository = SqliteRunRepository(db, owner_id="worker-a")
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="increment",
        mode="agent",
    ))
    gateway = SqliteToolIdempotencyGateway(db, owner_id=repository.owner_id)
    call = ToolCall(id="call-1", name="increment", arguments_json='{"by":1}')
    entered = 0

    async def operation() -> ToolHandlerResult:
        nonlocal entered
        entered += 1
        await db.execute("UPDATE test_tool_counter SET value = value + 1 WHERE id = 1")
        await asyncio.sleep(0)
        return ToolHandlerResult('{"value":1}')

    first, second = await asyncio.gather(
        gateway.execute_once(run_id, call, operation),
        gateway.execute_once(run_id, call, operation),
    )
    counter = await db.fetch_one("SELECT value FROM test_tool_counter WHERE id = 1")
    receipts = await db.fetch_all("SELECT * FROM ai_agent_tool_receipts")

    assert entered == 1
    assert counter is not None and counter["value"] == 1
    assert len(receipts) == 1
    assert sorted((first.from_cache, second.from_cache)) == [False, True]


@pytest.mark.asyncio
async def test_reused_call_id_with_different_input_is_rejected(db):
    repository = SqliteRunRepository(db, owner_id="worker-a")
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="increment",
        mode="agent",
    ))
    gateway = SqliteToolIdempotencyGateway(db, owner_id=repository.owner_id)

    async def operation() -> ToolHandlerResult:
        return ToolHandlerResult("ok")

    await gateway.execute_once(
        run_id,
        ToolCall(id="same-call", name="increment", arguments_json='{"by":1}'),
        operation,
    )
    with pytest.raises(ContractViolationError, match="different input"):
        await gateway.execute_once(
            run_id,
            ToolCall(id="same-call", name="increment", arguments_json='{"by":2}'),
            operation,
        )


@pytest.mark.asyncio
async def test_foreign_executor_cannot_start_side_effect(db):
    repository = SqliteRunRepository(db, owner_id="worker-a")
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="increment",
        mode="agent",
    ))
    gateway = SqliteToolIdempotencyGateway(db, owner_id="worker-b")
    called = False

    async def operation() -> ToolHandlerResult:
        nonlocal called
        called = True
        return ToolHandlerResult("unexpected")

    with pytest.raises(ContractViolationError, match="lease is not active"):
        await gateway.execute_once(
            run_id,
            ToolCall(id="call-foreign", name="increment", arguments_json="{}"),
            operation,
        )
    assert called is False
