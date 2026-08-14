from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from purra.contracts import RunBinding, RunCreateParams
from purra.errors import RunCommitProjectionError
from application.agent_orphan_recovery_service import AgentOrphanRecoveryService
from application.composition_factory import create_agent_composition
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


@pytest_asyncio.fixture
async def orphan_recovery(db):
    composition = create_agent_composition(db)
    try:
        yield AgentOrphanRecoveryService(db, composition)
    finally:
        await composition.shutdown()


async def _unowned_run(db: DatabaseConnection) -> str:
    return await run_store.create_run(
        db,
        session_id=None,
        prompt="execute",
        mode="agent",
    )


async def _seed_orphan_artifact_claim(db, run_id: str, suffix: str) -> None:
    work_item_id = f"orphan-item-{suffix}"
    artifact_id = f"orphan-artifact-{suffix}"
    await db.execute(
        "INSERT INTO ai_agent_work_items "
        "(id, namespace, kind, owner_id, created_by_run_id) "
        "VALUES (?, 'test', 'draft', 'owner', ?)",
        [work_item_id, run_id],
    )
    await db.execute(
        "INSERT INTO ai_agent_work_item_runs "
        "(work_item_id, run_id, relation, work_item_revision) "
        "VALUES (?, ?, 'created', 1)",
        [work_item_id, run_id],
    )
    await db.execute(
        "INSERT INTO ai_agent_artifacts "
        "(id, namespace, kind, owner_id, run_id, artifact_scope, "
        "work_item_id, created_by_run_id) VALUES "
        "(?, 'test', 'draft', 'owner', ?, 'work_item', ?, ?)",
        [artifact_id, run_id, work_item_id, run_id],
    )
    await db.execute(
        "INSERT INTO ai_agent_artifact_claims "
        "(artifact_id, work_item_id, run_id, claim_token, "
        "acquired_revision, expires_at_ms) VALUES (?, ?, ?, ?, 1, 999999)",
        [artifact_id, work_item_id, run_id, f"claim-{suffix}"],
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
async def test_expired_run_is_terminalized_atomically_and_idempotently(
    db,
    orphan_recovery,
):
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

    assert await run_execution_store.list_orphaned_run_candidates(
        db,
        timestamp_ms=1_099,
    ) == ()
    candidates = await run_execution_store.list_orphaned_run_candidates(
        db,
        timestamp_ms=1_100,
    )
    assert [candidate["id"] for candidate in candidates] == [run_id]
    assert await orphan_recovery.recover() == (run_id,)
    assert await orphan_recovery.recover() == ()

    run = await run_store.get_run(db, run_id)
    todos = await run_store.get_run_todos(db, run_id)
    events = await run_store.get_run_events(db, run_id)
    assert run is not None and run["status"] == "failed"
    assert run["execution_owner_id"] is None
    assert todos[0]["status"] == "failed"
    assert [event["eventType"] for event in events] == ["run.lifecycle"]
    assert events[0]["payload"]["reason"] == "execution_lease_expired"
    assert await db.fetch_one(
        "SELECT artifact_id FROM ai_agent_artifact_claims "
        "WHERE artifact_id = 'orphan-artifact'"
    ) is None


@pytest.mark.asyncio
async def test_orphan_recovery_uses_canonical_failed_commit_and_clears_claim(db):
    run_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="canonical orphan",
        mode="agent",
        execution_owner_id="dead-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=2,
    )
    await _seed_orphan_artifact_claim(db, run_id, "canonical")
    composition = create_agent_composition(db)
    try:
        recovered = await AgentOrphanRecoveryService(db, composition).recover()
    finally:
        await composition.shutdown()

    assert recovered == (run_id,)
    assert await db.fetch_one(
        "SELECT status, execution_owner_id, lease_expires_at_ms FROM "
        "ai_agent_runs WHERE id = ?",
        [run_id],
    ) == {
        "status": "failed",
        "execution_owner_id": None,
        "lease_expires_at_ms": None,
    }
    terminal = await db.fetch_one(
        "SELECT source_event_key, event_id, source, kind FROM "
        "ai_agent_run_events WHERE run_id = ? AND source_event_key = ?",
        [run_id, f"run:{run_id}:failed"],
    )
    assert terminal is not None
    assert terminal["source_event_key"] == f"run:{run_id}:failed"
    assert str(terminal["event_id"] or "")
    assert terminal["source"] == "runtime"
    assert terminal["kind"] == "run.lifecycle"
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ? "
        "AND event_id IS NULL",
        [run_id],
    ) == {"count": 0}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifact_claims WHERE run_id = ?",
        [run_id],
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_orphan_recovery_retries_full_terminal_projection_once(db):
    run_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="retry canonical orphan",
        mode="agent",
        execution_owner_id="dead-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=2,
    )
    await _seed_orphan_artifact_claim(db, run_id, "retry")

    class FailOnce:
        def __init__(self):
            self.calls = 0

        async def project(self, projected_run_id, commit):
            assert projected_run_id == run_id
            assert commit.terminal_status.value == "failed"
            self.calls += 1
            if self.calls == 1:
                raise RunCommitProjectionError(
                    "transient orphan projection",
                    retryable=True,
                )

    projector = FailOnce()
    composition = create_agent_composition(
        db,
        run_commit_projector=projector,
    )
    try:
        recovered = await AgentOrphanRecoveryService(db, composition).recover()
    finally:
        await composition.shutdown()

    assert recovered == (run_id,)
    assert projector.calls == 2
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ? "
        "AND source_event_key = ?",
        [run_id, f"run:{run_id}:failed"],
    ) == {"count": 1}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifact_claims WHERE run_id = ?",
        [run_id],
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_orphan_projection_failure_rolls_back_terminal_and_claim_cleanup(db):
    run_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="rollback canonical orphan",
        mode="agent",
        execution_owner_id="dead-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=2,
    )
    await _seed_orphan_artifact_claim(db, run_id, "rollback")

    class RejectProjection:
        async def project(self, projected_run_id, commit):
            assert projected_run_id == run_id
            assert commit.terminal_status.value == "failed"
            raise RunCommitProjectionError(
                "permanent orphan projection",
                retryable=False,
            )

    composition = create_agent_composition(
        db,
        run_commit_projector=RejectProjection(),
    )
    try:
        with pytest.raises(
            RunCommitProjectionError,
            match="permanent orphan projection",
        ):
            await AgentOrphanRecoveryService(db, composition).recover()
    finally:
        await composition.shutdown()

    assert await db.fetch_one(
        "SELECT status, execution_owner_id, lease_expires_at_ms FROM "
        "ai_agent_runs WHERE id = ?",
        [run_id],
    ) == {
        "status": "running",
        "execution_owner_id": None,
        "lease_expires_at_ms": None,
    }
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ? "
        "AND source_event_key = ?",
        [run_id, f"run:{run_id}:failed"],
    ) == {"count": 0}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifact_claims WHERE run_id = ?",
        [run_id],
    ) == {"count": 1}


@pytest.mark.asyncio
async def test_cancel_requested_orphan_recovers_as_canceled(
    db,
    orphan_recovery,
):
    run_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="canceled orphan",
        mode="agent",
        execution_owner_id="dead-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=2,
    )
    assert await run_execution_store.request_cancellation(db, run_id)

    assert await orphan_recovery.recover() == (run_id,)

    assert await db.fetch_one(
        "SELECT status, execution_owner_id, lease_expires_at_ms FROM "
        "ai_agent_runs WHERE id = ?",
        [run_id],
    ) == {
        "status": "canceled",
        "execution_owner_id": None,
        "lease_expires_at_ms": None,
    }
    assert await db.fetch_one(
        "SELECT status FROM ai_agent_run_cancellations WHERE root_run_id = ?",
        [run_id],
    ) == {"status": "completed"}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ? "
        "AND source_event_key = ?",
        [run_id, f"run:{run_id}:canceled"],
    ) == {"count": 1}


@pytest.mark.asyncio
async def test_concurrent_orphan_monitors_have_one_recovery_owner(db):
    run_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="one recovery owner",
        mode="agent",
        execution_owner_id="dead-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=2,
    )
    first_composition = create_agent_composition(db)
    second_composition = create_agent_composition(db)
    try:
        results = await asyncio.gather(
            AgentOrphanRecoveryService(db, first_composition).recover(),
            AgentOrphanRecoveryService(db, second_composition).recover(),
        )
    finally:
        await first_composition.shutdown()
        await second_composition.shutdown()

    assert sorted(len(result) for result in results) == [0, 1]
    assert await db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [run_id],
    ) == {"status": "failed"}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ? "
        "AND source_event_key = ?",
        [run_id, f"run:{run_id}:failed"],
    ) == {"count": 1}


@pytest.mark.asyncio
async def test_live_descendant_defers_runtime_root_orphan_recovery(db):
    root_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="expired root",
        mode="agent",
        execution_owner_id="dead-root-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=2,
    )
    child_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="live child",
        mode="agent",
        parent_run_id=root_id,
        root_run_id=root_id,
        run_depth=1,
        execution_owner_id="live-child-worker",
        heartbeat_at_ms=10,
        lease_expires_at_ms=9999999999999,
    )

    assert await run_execution_store.list_orphaned_run_candidates(db) == ()
    restart = await run_execution_store.list_orphaned_run_candidates(
        db,
        after_restart=True,
    )
    assert {row["id"] for row in restart} == {root_id, child_id}


@pytest.mark.asyncio
async def test_restart_cancel_recovery_drains_root_and_old_child(
    db,
    orphan_recovery,
):
    root_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="canceled old root",
        mode="agent",
        execution_owner_id="old-root-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=9999999999999,
    )
    child_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="canceled old child",
        mode="agent",
        parent_run_id=root_id,
        root_run_id=root_id,
        run_depth=1,
        execution_owner_id="old-child-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=9999999999999,
    )
    assert await run_execution_store.request_cancellation(db, root_id)

    recovered = await orphan_recovery.recover(after_restart=True)

    assert set(recovered) == {root_id, child_id}
    assert await db.fetch_all(
        "SELECT id, status FROM ai_agent_runs WHERE id IN (?, ?) ORDER BY id",
        [root_id, child_id],
    ) == sorted(
        [
            {"id": root_id, "status": "canceled"},
            {"id": child_id, "status": "canceled"},
        ],
        key=lambda row: row["id"],
    )
    assert await db.fetch_one(
        "SELECT status FROM ai_agent_run_cancellations WHERE root_run_id = ?",
        [root_id],
    ) == {"status": "completed"}


@pytest.mark.asyncio
async def test_restart_recovery_terminalizes_even_unexpired_previous_owner(
    db,
    orphan_recovery,
):
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

    recovered = await orphan_recovery.recover(
        after_restart=True,
    )

    assert set(recovered) == {live_until_later, unowned}
    for run_id in recovered:
        run = await run_store.get_run(db, run_id)
        events = await run_store.get_run_events(db, run_id)
        assert run is not None and run["status"] == "failed"
        assert events[-1]["payload"]["reason"] == (
            "execution_recovery_after_restart"
        )


@pytest.mark.asyncio
async def test_restart_recovery_materializes_terminal_book_run_before_next_turn(
    db,
    orphan_recovery,
):
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
    recovered = await orphan_recovery.recover(
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
        "status": "failed",
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
async def test_orphan_monitor_reaps_expired_run_without_restart(
    db,
    orphan_recovery,
):
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
        recover_orphans=orphan_recovery.recover,
        poll_interval_seconds=0.01,
    ))
    try:
        for _ in range(100):
            run = await run_store.get_run(db, run_id)
            if run is not None and run["status"] == "failed":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("orphan monitor did not terminalize the run")
    finally:
        monitor.cancel()
        with pytest.raises(asyncio.CancelledError):
            await monitor


@pytest.mark.asyncio
async def test_orphan_monitor_reconciles_linked_state_after_run_recovery(
    db,
    orphan_recovery,
):
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
        if run is not None and run["status"] == "failed":
            reconciled.set()
            return ("linked-operation",)
        return ()

    monitor = asyncio.create_task(monitor_orphaned_runs(
        recover_orphans=orphan_recovery.recover,
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
async def test_orphan_monitor_materializes_each_recovered_book_run(
    db,
    orphan_recovery,
):
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
        recover_orphans=orphan_recovery.recover,
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
async def test_legacy_unbound_book_root_materializes_but_screenplay_scope_does_not(db):
    await db.execute(
        "INSERT INTO screenplay_projects (id, title, source_kind) "
        "VALUES ('screenplay-legacy', '剧本', 'original')"
    )
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id, scope) "
        "VALUES (96, 'book-legacy', 'chapter-legacy', 'chapter')"
    )
    await db.execute(
        "INSERT INTO ai_sessions "
        "(id, book_id, scope, screenplay_project_id) "
        "VALUES (97, 'book-legacy', 'screenplay', 'screenplay-legacy')"
    )
    book_run = await run_store.create_run(
        db,
        session_id=96,
        prompt="legacy Book prompt",
        mode="agent",
    )
    screenplay_run = await run_store.create_run(
        db,
        session_id=97,
        prompt="legacy Screenplay prompt",
        mode="agent",
    )
    await db.execute(
        "UPDATE ai_agent_runs SET status = 'canceled' WHERE id IN (?, ?)",
        [book_run, screenplay_run],
    )
    from infrastructure.persistence.run_conversation_store import (
        materialize_terminal_writing_run_holes,
    )

    materialized = await materialize_terminal_writing_run_holes(db)

    assert materialized == (book_run,)
    assert await db.fetch_one(
        "SELECT prompt FROM ai_conversations WHERE session_id = 96"
    ) == {"prompt": "legacy Book prompt"}
    assert await db.fetch_one(
        "SELECT prompt FROM ai_conversations WHERE session_id = 97"
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
    orphan_recovery,
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
        recover_orphans=orphan_recovery.recover,
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
