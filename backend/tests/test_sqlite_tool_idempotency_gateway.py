from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from purra.contracts import (
    DomainEffect,
    RunCreateParams,
    ToolCall,
    ToolEffectState,
    ToolHandlerResult,
    ToolPlanningDisposition,
    ToolStepDisposition,
)
from purra.errors import ContractViolationError
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
async def test_nested_domain_effect_is_serialized_and_replayed(db):
    repository = SqliteRunRepository(db, owner_id="worker-a")
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="propose",
        mode="agent",
    ))
    gateway = SqliteToolIdempotencyGateway(db, owner_id=repository.owner_id)
    call = ToolCall(
        id="call-proposal",
        name="proposeSourceAnalysis",
        arguments_json='{"title":"分析"}',
    )
    proposal = {
        "kind": "source_analysis",
        "contentJson": {
            "analysis": {
                "characters": [
                    {"name": "林岚", "traits": ["冷静", "敏锐"]},
                ],
            },
        },
        "contentText": "正式分析",
        "derivedFromIds": ["source-1"],
    }
    calls = 0

    async def operation() -> ToolHandlerResult:
        nonlocal calls
        calls += 1
        return ToolHandlerResult(
            '{"proposed":true}',
            effects=(DomainEffect(
                type="screenplay.document_proposal",
                payload=proposal,
            ),),
        )

    first = await gateway.execute_once(run_id, call, operation)
    replay = await gateway.execute_once(run_id, call, operation)

    assert calls == 1
    assert first.from_cache is False
    assert replay.from_cache is True
    assert len(replay.effects) == 1
    assert replay.effects[0].type == "screenplay.document_proposal"
    assert replay.effects[0].payload == proposal


@pytest.mark.asyncio
async def test_partial_step_disposition_survives_idempotent_replay(db):
    repository = SqliteRunRepository(db, owner_id="worker-a")
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="append",
        mode="agent",
    ))
    gateway = SqliteToolIdempotencyGateway(db, owner_id=repository.owner_id)
    call = ToolCall(id="call-partial", name="appendBatch", arguments_json="{}")

    async def operation() -> ToolHandlerResult:
        return ToolHandlerResult(
            '{"remaining":1}',
            step_disposition=ToolStepDisposition.CONTINUE,
        )

    await gateway.execute_once(run_id, call, operation)
    replay = await gateway.execute_once(run_id, call, operation)

    assert replay.from_cache is True
    assert replay.step_disposition is ToolStepDisposition.CONTINUE


@pytest.mark.asyncio
async def test_planning_disposition_survives_idempotent_replay(db):
    repository = SqliteRunRepository(db, owner_id="worker-a")
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="select branch",
        mode="agent",
    ))
    gateway = SqliteToolIdempotencyGateway(db, owner_id=repository.owner_id)
    call = ToolCall(id="call-branch", name="selectBranch", arguments_json="{}")

    async def operation() -> ToolHandlerResult:
        return ToolHandlerResult(
            '{"branch":"selected"}',
            planning_disposition=ToolPlanningDisposition.REPLAN,
        )

    await gateway.execute_once(run_id, call, operation)
    replay = await gateway.execute_once(run_id, call, operation)

    assert replay.from_cache is True
    assert replay.planning_disposition is ToolPlanningDisposition.REPLAN


@pytest.mark.asyncio
async def test_failure_effect_state_survives_idempotent_replay(db):
    repository = SqliteRunRepository(db, owner_id="worker-a")
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="validate",
        mode="agent",
    ))
    gateway = SqliteToolIdempotencyGateway(db, owner_id=repository.owner_id)
    call = ToolCall(id="call-invalid", name="propose", arguments_json="{}")

    async def operation() -> ToolHandlerResult:
        return ToolHandlerResult(
            '{"success":false}',
            error_code="tool_input_invalid",
            effect_state=ToolEffectState.NOT_STARTED,
        )

    await gateway.execute_once(run_id, call, operation)
    replay = await gateway.execute_once(run_id, call, operation)

    assert replay.from_cache is True
    assert replay.effect_state is ToolEffectState.NOT_STARTED


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
@pytest.mark.asyncio
@pytest.mark.parametrize('cancel_at_commit', [False, True])
async def test_gateway_cancellation_preserves_atomic_receipt_boundary(db, monkeypatch, cancel_at_commit):
    repository = SqliteRunRepository(db, owner_id='worker-a')
    run_id = await repository.create(RunCreateParams(session_id=None, prompt='cancel', mode='agent'))
    gateway = SqliteToolIdempotencyGateway(db, owner_id=repository.owner_id)
    call = ToolCall(id='cancel-call', name='increment', arguments_json='{}')
    entered = asyncio.Event()
    release = asyncio.Event()
    original_commit = db._conn.commit
    async def delayed_commit():
        entered.set()
        await release.wait()
        await original_commit()
    if cancel_at_commit:
        monkeypatch.setattr(db._conn, 'commit', delayed_commit)
    async def operation():
        await db.execute('UPDATE test_tool_counter SET value=1 WHERE id=1')
        if not cancel_at_commit:
            entered.set()
            await release.wait()
        return ToolHandlerResult('{"value":1}')
    task = asyncio.create_task(gateway.execute_once(run_id, call, operation))
    await entered.wait()
    task.cancel()
    release.set()
    if cancel_at_commit:
        assert (await task).content == '{"value":1}'
        monkeypatch.setattr(db._conn, 'commit', original_commit)
        assert (await gateway.execute_once(run_id, call, operation)).from_cache
    else:
        with pytest.raises(asyncio.CancelledError):
            await task
    assert (await db.fetch_one('SELECT value FROM test_tool_counter'))['value'] == int(cancel_at_commit)
    assert len(await db.fetch_all('SELECT * FROM ai_agent_tool_receipts')) == int(cancel_at_commit)
