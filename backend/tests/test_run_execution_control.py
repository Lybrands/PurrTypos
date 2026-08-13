from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from purra.contracts import RunBinding, RunCreateParams
from application.run_execution_control import RunExecutionSession
from database.connection import DatabaseConnection
from infrastructure.persistence import run_execution_store, run_store
from infrastructure.persistence.run_execution_store import (
    SqliteExecutionLeaseStore,
)
from infrastructure.persistence.orphan_run_monitor import monitor_orphaned_runs
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


def _writing_binding(session_id: int, command_id: str) -> RunBinding:
    return RunBinding(
        namespace="writing.chat.request",
        aggregate_id=str(session_id),
        command_id=command_id,
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


@pytest.mark.asyncio
async def test_expired_run_is_terminalized_atomically_and_idempotently(db):
    run_id = await run_store.create_run(
        db,
        session_id=7,
        prompt="orphan me",
        mode="agent",
        execution_owner_id="dead-worker",
        heartbeat_at_ms=1_000,
        lease_expires_at_ms=1_100,
    )
    await run_store.upsert_todos(db, run_id, [{
        "id": "read",
        "title": "Read evidence",
        "status": "running",
        "executor": "tool",
        "type": "read",
    }])
    await db.execute(
        "INSERT INTO ai_agent_work_items "
        "(id, namespace, kind, owner_id, created_by_run_id) "
        "VALUES ('orphan-item', 'test', 'draft', 'owner', ?)",
        [run_id],
    )
    await db.execute(
        "INSERT INTO ai_agent_work_item_runs "
        "(work_item_id, run_id, relation, work_item_revision) "
        "VALUES ('orphan-item', ?, 'created', 1)",
        [run_id],
    )
    await db.execute(
        "INSERT INTO ai_agent_artifacts "
        "(id, namespace, kind, owner_id, run_id, artifact_scope, "
        "work_item_id, created_by_run_id) VALUES "
        "('orphan-artifact', 'test', 'draft', 'owner', ?, 'work_item', "
        "'orphan-item', ?)",
        [run_id, run_id],
    )
    await db.execute(
        "INSERT INTO ai_agent_artifact_claims "
        "(artifact_id, work_item_id, run_id, claim_token, "
        "acquired_revision, expires_at_ms) VALUES "
        "('orphan-artifact', 'orphan-item', ?, 'claim-orphan', 1, 999999)",
        [run_id],
    )

    assert not await run_execution_store.terminalize_orphaned_run(
        db,
        run_id,
        timestamp_ms=1_099,
    )
    assert await run_execution_store.terminalize_orphaned_run(
        db,
        run_id,
        timestamp_ms=1_100,
    )
    assert not await run_execution_store.terminalize_orphaned_run(
        db,
        run_id,
        timestamp_ms=1_101,
    )

    run = await run_store.get_run(db, run_id)
    todos = await run_store.get_run_todos(db, run_id)
    events = await run_store.get_run_events(db, run_id)
    assert run is not None and run["status"] == "canceled"
    assert run["execution_owner_id"] is None
    assert todos[0]["status"] == "blocked"
    assert [event["eventType"] for event in events] == ["run.canceled"]
    assert events[0]["payload"]["reason"] == "execution_owner_unavailable"
    assert await db.fetch_one(
        "SELECT artifact_id FROM ai_agent_artifact_claims "
        "WHERE artifact_id = 'orphan-artifact'"
    ) is None


@pytest.mark.asyncio
async def test_restart_recovery_terminalizes_even_unexpired_previous_owner(db):
    live_until_later = await run_store.create_run(
        db,
        session_id=None,
        prompt="old process",
        mode="agent",
        execution_owner_id="previous-process",
        heartbeat_at_ms=10_000,
        lease_expires_at_ms=99_999,
    )
    unowned = await _unowned_run(db)

    recovered = await run_execution_store.recover_orphaned_runs(
        db,
        timestamp_ms=10_001,
        after_restart=True,
    )

    assert set(recovered) == {live_until_later, unowned}
    for run_id in recovered:
        run = await run_store.get_run(db, run_id)
        events = await run_store.get_run_events(db, run_id)
        assert run is not None and run["status"] == "canceled"
        assert events[-1]["payload"]["reason"] == (
            "execution_recovery_after_restart"
        )


@pytest.mark.asyncio
async def test_restart_recovery_materializes_terminal_book_run_before_next_turn(db):
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) "
        "VALUES (91, 'book-91', 'chapter-91')"
    )
    run_id = await run_store.create_run(
        db,
        session_id=91,
        prompt="interrupted before restart",
        mode="agent",
        binding=_writing_binding(91, "restart-request"),
        execution_owner_id="previous-process",
        heartbeat_at_ms=10_000,
        lease_expires_at_ms=99_999,
    )
    recovered = await run_execution_store.recover_orphaned_runs(
        db,
        timestamp_ms=10_001,
        after_restart=True,
    )

    from infrastructure.persistence.run_conversation_store import (
        materialize_terminal_writing_run_holes,
    )

    materialized = await materialize_terminal_writing_run_holes(db)

    assert materialized == (run_id,)
    assert await db.fetch_one(
        "SELECT r.status, r.conversation_id, c.response "
        "FROM ai_agent_runs AS r "
        "JOIN ai_conversations AS c ON c.id = r.conversation_id "
        "WHERE r.id = ?",
        [run_id],
    ) == {
        "status": "canceled",
        "conversation_id": 1,
        "response": "",
    }
    projected = await db.fetch_one(
        "SELECT conversation_id FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )
    from dependencies import set_db
    from routers.conversations import save_conversation
    from schemas.conversations import SaveConversationRequest

    set_db(db)
    next_turn = await save_conversation(SaveConversationRequest(
        sessionId=91,
        bookId="book-91",
        chapterId="chapter-91",
        prompt="next Ask",
        response="next answer",
        clientTurnId="turn-after-restart",
        expectedConversationIds=[int(projected["conversation_id"])],
    ))
    assert next_turn["success"] is True


@pytest.mark.asyncio
async def test_orphan_monitor_reaps_expired_run_without_restart(db):
    run_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="expire while app is open",
        mode="agent",
        execution_owner_id="lost-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=2,
    )
    monitor = asyncio.create_task(monitor_orphaned_runs(
        db,
        poll_interval_seconds=0.01,
    ))
    try:
        for _ in range(100):
            run = await run_store.get_run(db, run_id)
            if run is not None and run["status"] == "canceled":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("orphan monitor did not terminalize the run")
    finally:
        monitor.cancel()
        with pytest.raises(asyncio.CancelledError):
            await monitor


@pytest.mark.asyncio
async def test_orphan_monitor_reconciles_linked_state_after_run_recovery(db):
    run_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="expire and reconcile",
        mode="agent",
        execution_owner_id="lost-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=2,
    )
    reconciled = asyncio.Event()
    calls = 0

    async def reconcile_linked_state():
        nonlocal calls
        calls += 1
        run = await run_store.get_run(db, run_id)
        if run is not None and run["status"] == "canceled":
            reconciled.set()
            return ("linked-operation",)
        return ()

    monitor = asyncio.create_task(monitor_orphaned_runs(
        db,
        poll_interval_seconds=0.01,
        reconcile_linked_state=reconcile_linked_state,
    ))
    try:
        await asyncio.wait_for(reconciled.wait(), timeout=1)
    finally:
        monitor.cancel()
        with pytest.raises(asyncio.CancelledError):
            await monitor

    assert calls >= 1


@pytest.mark.asyncio
async def test_orphan_monitor_materializes_each_recovered_book_run(db):
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) "
        "VALUES (92, 'book-92', 'chapter-92')"
    )
    run_id = await run_store.create_run(
        db,
        session_id=92,
        prompt="expired while app is open",
        mode="agent",
        binding=_writing_binding(92, "live-request"),
        execution_owner_id="lost-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=2,
    )
    from infrastructure.persistence.run_conversation_store import (
        materialize_terminal_writing_run_holes,
    )

    monitor = asyncio.create_task(monitor_orphaned_runs(
        db,
        poll_interval_seconds=0.01,
        reconcile_terminal_holes=lambda: (
            materialize_terminal_writing_run_holes(db)
        ),
    ))
    try:
        for _ in range(100):
            projected = await db.fetch_one(
                "SELECT conversation_id FROM ai_agent_runs WHERE id = ?",
                [run_id],
            )
            if projected and projected.get("conversation_id") is not None:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("orphan monitor did not materialize the run")
    finally:
        monitor.cancel()
        with pytest.raises(asyncio.CancelledError):
            await monitor


@pytest.mark.asyncio
async def test_recovered_screenplay_run_cannot_materialize_into_colliding_book_session(
    db,
):
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) "
        "VALUES (93, 'book-93', 'chapter-93')"
    )
    run_id = await run_store.create_run(
        db,
        session_id=93,
        prompt="screenplay prompt must never become Book history",
        mode="agent",
        binding=RunBinding(
            namespace="screenplay.agent.turn",
            aggregate_id="93",
            command_id="screenplay-request",
        ),
    )
    await db.execute(
        "UPDATE ai_agent_runs SET status = 'canceled' WHERE id = ?",
        [run_id],
    )
    from infrastructure.persistence.run_conversation_store import (
        materialize_recovered_run_conversations,
    )

    materialized = await materialize_recovered_run_conversations(db, (run_id,))

    assert materialized == ()
    assert await db.fetch_one(
        "SELECT id FROM ai_conversations WHERE session_id = 93"
    ) is None


@pytest.mark.asyncio
async def test_terminal_writing_hole_is_reconciled_without_current_recovery_ids(db):
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) "
        "VALUES (94, 'book-94', 'chapter-94')"
    )
    run_id = await run_store.create_run(
        db,
        session_id=94,
        prompt="terminal before process restart",
        mode="agent",
        binding=_writing_binding(94, "preexisting-terminal-request"),
    )
    await db.execute(
        "UPDATE ai_agent_runs SET status = 'failed', "
        "final_response = 'provider_error' WHERE id = ?",
        [run_id],
    )
    from infrastructure.persistence.run_conversation_store import (
        materialize_terminal_writing_run_holes,
    )

    materialized = await materialize_terminal_writing_run_holes(db)

    assert materialized == (run_id,)
    assert await db.fetch_one(
        "SELECT response FROM ai_conversations WHERE session_id = 94"
    ) == {"response": ""}


@pytest.mark.asyncio
async def test_orphan_monitor_retries_terminal_writing_holes_after_projection_failure(
    db,
):
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) "
        "VALUES (95, 'book-95', 'chapter-95')"
    )
    run_id = await run_store.create_run(
        db,
        session_id=95,
        prompt="projection retry",
        mode="agent",
        binding=_writing_binding(95, "retry-request"),
        execution_owner_id="lost-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=2,
    )
    from infrastructure.persistence.run_conversation_store import (
        materialize_terminal_writing_run_holes,
    )

    attempts = 0

    async def flaky_reconcile():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient projection failure")
        return await materialize_terminal_writing_run_holes(db)

    monitor = asyncio.create_task(monitor_orphaned_runs(
        db,
        poll_interval_seconds=0.01,
        reconcile_terminal_holes=flaky_reconcile,
    ))
    try:
        for _ in range(100):
            projected = await db.fetch_one(
                "SELECT conversation_id FROM ai_agent_runs WHERE id = ?",
                [run_id],
            )
            if projected and projected.get("conversation_id") is not None:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("terminal Writing hole was not retried")
    finally:
        monitor.cancel()
        with pytest.raises(asyncio.CancelledError):
            await monitor

    assert attempts >= 2
