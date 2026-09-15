from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from purra.contracts import (
    AgentRunRequest,
    AgentRunResult,
    DomainContext,
    ModelRequest,
    RunBinding,
    RunCreateParams,
    RunStatus,
)
from purra.errors import RunCommitProjectionError
from purra.api import (
    AgentCapabilityGrant,
    BeginRootAgentCommand,
    ChildAgentSpec,
    SpawnAgentsCommand,
)
from purra.events import AgentEvent, CoreEventType
from purra.execution import AgentRunSupervisor
from purra.long_tasks import LongTaskCreateCommand, LongTaskUnitSpec
from purra.output import (
    AgentOutputIntent,
    OutputCommitMode,
    OutputStreamSpec,
)
from application.agent_cancellation_service import AgentCancellationService
from application.agent_orphan_recovery_service import AgentOrphanRecoveryService
from application.composition_factory import create_agent_composition
from database.connection import DatabaseConnection
from infrastructure.persistence import run_store
from infrastructure.persistence.agent_output_publisher import InProcessAgentOutputPublisher
from infrastructure.persistence.sqlite_agent_output_repository import SqliteAgentOutputRepository
from infrastructure.persistence.run_execution_store import (
    SqliteRunControlStore,
)
from infrastructure.persistence.orphan_run_monitor import monitor_orphaned_runs
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)


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
    artifact_id = f"orphan-artifact-{suffix}"
    await db.execute(
        "INSERT INTO ai_agent_artifacts "
        "(id, namespace, kind, owner_id, owner_ref_kind, owner_ref_id, "
        "created_by_run_id) VALUES "
        "(?, 'test', 'draft', 'owner', 'run', ?, ?)",
        [artifact_id, run_id, run_id],
    )
    await db.execute(
        "INSERT INTO ai_agent_artifact_claims "
        "(artifact_id, run_id, claim_token, acquired_revision, expires_at_ms) "
        "VALUES (?, ?, ?, 1, 999999)",
        [artifact_id, run_id, f"claim-{suffix}"],
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
    control = SqliteRunControlStore(db)

    claims = await asyncio.gather(
        control.claim(
            run_id,
            "worker-a",
            lease_duration_ms=1_000,
            timestamp_ms=100,
        ),
        control.claim(
            run_id,
            "worker-b",
            lease_duration_ms=1_000,
            timestamp_ms=100,
        ),
    )
    state = await control.get(run_id)

    assert claims.count(True) == 1
    assert claims.count(False) == 1
    assert state is not None
    assert state.owner_id in {"worker-a", "worker-b"}
    assert state.attempt == 1


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed_but_live_lease_cannot(db):
    run_id = await _unowned_run(db)
    control = SqliteRunControlStore(db)
    assert await control.claim(
        run_id,
        "worker-a",
        lease_duration_ms=100,
        timestamp_ms=1_000,
    )
    assert not await control.claim(
        run_id,
        "worker-b",
        lease_duration_ms=100,
        timestamp_ms=1_099,
    )
    assert await control.claim(
        run_id,
        "worker-b",
        lease_duration_ms=100,
        timestamp_ms=1_100,
    )

    state = await control.get(run_id)
    assert state is not None
    assert state.owner_id == "worker-b"
    assert state.attempt == 2
    assert not await control.renew(
        run_id,
        "worker-a",
        lease_duration_ms=100,
        timestamp_ms=1_101,
    )
    assert await control.renew(
        run_id,
        "worker-b",
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
    canceled = asyncio.Event()

    async def execute(_request, _options, signal):
        yield AgentEvent(type=CoreEventType.RUN_STARTED, run_id=run_id)
        await signal.wait()
        canceled.set()
        yield AgentRunResult(run_id=run_id, status=RunStatus.CANCELED)

    control = SqliteRunControlStore(db)
    supervisor = AgentRunSupervisor(
        output_repository=SqliteAgentOutputRepository(db),
        output_publisher=InProcessAgentOutputPublisher(),
        execution_factory=execute,
        lease_store=control,
        owner_id=repository.owner_id,
        lease_duration_ms=repository.lease_duration_ms,
        poll_interval_seconds=0.01,
    )
    try:
        handle = await supervisor.submit(AgentRunRequest(
            messages=(),
            model=ModelRequest(provider="openai", model="test-model"),
            domain_context=DomainContext(namespace="test.lease"),
        ))
        assert handle.run_id == run_id
        assert await control.request_cancellation(run_id)
        assert not await control.request_cancellation(run_id)
        result = await asyncio.wait_for(handle.wait(), timeout=0.5)
        assert result.status is RunStatus.CANCELED
        assert canceled.is_set()
    finally:
        await supervisor.close()

    assert not await control.claim(
        run_id,
        "worker-next",
        lease_duration_ms=1_000,
    )
    state = await control.get(run_id)
    assert state is not None
    assert state.owner_id is None
    assert state.cancellation_requested_at_ms is not None


@pytest.mark.asyncio
async def test_cancellation_receipt_preserves_competing_done_status(db):
    run_id = await _unowned_run(db)
    control = SqliteRunControlStore(db)

    await control.fence_cancellation(run_id)
    await db.execute(
        "UPDATE ai_agent_runs SET status = 'done' WHERE id = ?",
        [run_id],
    )
    completed = await control.complete_cancellation(
        run_id,
        terminalized=False,
    )
    replayed = await control.load_cancellation_receipt(run_id)

    assert completed.status.value == "done"
    assert replayed is not None and replayed.status.value == "done"


@pytest.mark.asyncio
async def test_cancellation_service_completes_after_competing_done_status(db):
    run_id = await _unowned_run(db)
    composition = create_agent_composition(db)
    try:
        await composition.run_control_store.fence_cancellation(run_id)
        await db.execute(
            "UPDATE ai_agent_runs SET status = 'done' WHERE id = ?",
            [run_id],
        )
        result = await AgentCancellationService(db, composition).cancel(run_id)
    finally:
        await composition.shutdown()

    assert result is not None
    assert result["status"] == "done"
    assert result["cancellationStatus"] == "completed"


@pytest.mark.asyncio
async def test_cancellation_without_live_executor_cancels_agent_tree(db):
    run_id = await _unowned_run(db)
    composition = create_agent_composition(db)
    tree = composition.run_tree_repository
    try:
        await tree.begin_root(BeginRootAgentCommand(
            run_id=run_id,
            agent_id="cancel-root-agent",
            name="root",
            title="Root",
            instruction="test",
            objective="test",
            capability_grant=AgentCapabilityGrant(can_spawn_agents=True),
            idempotency_key=f"begin:{run_id}",
        ))
        spawned = await tree.spawn_agents(SpawnAgentsCommand(
            parent_run_id=run_id,
            idempotency_key="spawn-before-cancel",
            children=(ChildAgentSpec(
                name="unfinished",
                title="Unfinished Child",
                instruction="test",
                objective="test",
            ),),
        ))
        child_id = spawned.items[0].run.run_id

        result = await AgentCancellationService(db, composition).cancel(run_id)

        assert result is not None and result["status"] == "canceled"
        assert (await tree.get_run(run_id)).status.value == "canceled"
        assert (await tree.get_run(child_id)).status.value == "canceled"
    finally:
        await composition.shutdown()


@pytest.mark.asyncio
async def test_expired_run_is_terminalized_atomically_and_idempotently(
    db,
    orphan_recovery,
):
    control = SqliteRunControlStore(db)
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
    await _seed_orphan_artifact_claim(db, run_id, "orphan")

    assert await control.list_orphans(
        timestamp_ms=1_099,
    ) == ()
    candidates = await control.list_orphans(
        timestamp_ms=1_100,
    )
    assert [candidate.run_id for candidate in candidates] == [run_id]
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
        "WHERE artifact_id = 'orphan-artifact-orphan'"
    ) is None


@pytest.mark.asyncio
async def test_orphan_waits_for_live_task_then_checkpoints_recoverable_task(db):
    run_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="durable orphan",
        mode="agent",
        execution_owner_id="dead-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=2,
    )
    tasks = SqliteLongTaskRepository(db)
    task = await tasks.create(
        "orphan-durable-task",
        LongTaskCreateCommand(
            namespace="test",
            kind="write",
            owner_id="owner",
            created_by_run_id=run_id,
            units=(LongTaskUnitSpec(id="unit-1", position=0),),
        ),
    )
    task = await tasks.start(task.id, expected_revision=task.revision)
    unit = await tasks.claim_ready_unit(
        task.id,
        worker_id="task-worker",
        lease_duration_ms=1_000,
    )
    assert unit is not None
    await db.execute(
        "UPDATE ai_agent_long_task_units SET lease_expires_at_ms = 9999999999999 "
        "WHERE task_id = ? AND unit_id = ?",
        [task.id, unit.id],
    )
    composition = create_agent_composition(db)
    recovery = AgentOrphanRecoveryService(db, composition)
    try:
        assert await recovery.recover() == ()
        await db.execute(
            "UPDATE ai_agent_long_task_units SET lease_expires_at_ms = 1 "
            "WHERE task_id = ? AND unit_id = ?",
            [task.id, unit.id],
        )
        assert await recovery.recover() == (run_id,)
    finally:
        await composition.shutdown()

    run = await run_store.get_run(db, run_id)
    task = await tasks.load(task.id)
    units = await tasks.list_units(task.id)
    assert run is not None and run["status"] == "canceled"
    assert task is not None and task.status.value == "paused"
    assert units[0].status.value == "pending"
    assert units[0].error_code == "durable_task_interrupted"


@pytest.mark.asyncio
async def test_orphan_preserves_a_pending_durable_task(db):
    run_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="pending durable orphan",
        mode="agent",
        execution_owner_id="dead-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=2,
    )
    tasks = SqliteLongTaskRepository(db)
    task = await tasks.create(
        "pending-orphan-task",
        LongTaskCreateCommand(
            namespace="test",
            kind="write",
            owner_id="owner",
            created_by_run_id=run_id,
            units=(LongTaskUnitSpec(id="unit-1", position=0),),
        ),
    )
    composition = create_agent_composition(db)
    try:
        recovered = await AgentOrphanRecoveryService(db, composition).recover()
    finally:
        await composition.shutdown()

    run = await run_store.get_run(db, run_id)
    task = await tasks.load(task.id)
    assert recovered == (run_id,)
    assert run is not None and run["status"] == "canceled"
    assert task is not None and task.status.value == "paused"


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
        await composition.output_repository.open_stream(OutputStreamSpec(
            output_stream_id=f"orphan-stream-{run_id}",
            run_id=run_id,
            turn_id="orphan-turn",
            invocation_id=f"orphan-invocation-{run_id}",
            intent=AgentOutputIntent.STRUCTURED_PRIVATE,
            commit_mode=OutputCommitMode.PRIVATE,
        ))
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
    assert await db.fetch_one(
        "SELECT status, error_code FROM ai_agent_output_streams WHERE id = ?",
        [f"orphan-stream-{run_id}"],
    ) == {"status": "aborted", "error_code": "run_terminalized"}
    abort_event = await db.fetch_one(
        "SELECT payload_json FROM ai_agent_run_events "
        "WHERE output_stream_id = ? AND kind = 'stream.aborted'",
        [f"orphan-stream-{run_id}"],
    )
    assert abort_event is not None
    assert '"cause":"run_terminal_commit"' in abort_event["payload_json"]


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
    control = SqliteRunControlStore(db)
    run_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="canceled orphan",
        mode="agent",
        execution_owner_id="dead-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=2,
    )
    assert await control.request_cancellation(run_id)

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
        "SELECT status FROM ai_agent_run_cancellations WHERE run_id = ?",
        [run_id],
    ) == {"status": "completed"}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events WHERE run_id = ? "
        "AND source_event_key = ?",
        [run_id, f"run:{run_id}:canceled"],
    ) == {"count": 1}


@pytest.mark.asyncio
async def test_cancel_requested_retired_orphan_skips_removed_projector(db):
    control = SqliteRunControlStore(db)
    run_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="retired analysis orphan",
        mode="novel_analysis",
        binding=RunBinding(
            namespace="novel_source_analysis",
            aggregate_id="retired-revision",
            command_id="retired-command",
        ),
        execution_owner_id="dead-worker",
        heartbeat_at_ms=1,
        lease_expires_at_ms=2,
    )
    assert await control.request_cancellation(run_id)
    composition = create_agent_composition(db)
    try:
        recovered = await AgentOrphanRecoveryService(db, composition).recover()
    finally:
        await composition.shutdown()

    assert recovered == (run_id,)
    assert await db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [run_id],
    ) == {"status": "canceled"}
    assert await db.fetch_one(
        "SELECT status FROM ai_agent_run_cancellations WHERE run_id = ?",
        [run_id],
    ) is None


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
async def test_restart_recovery_cancels_unfinished_agent_tree_children(db):
    run_id = await run_store.create_run(
        db,
        session_id=None,
        prompt="root with unfinished child",
        mode="agent",
        execution_owner_id="previous-process",
        heartbeat_at_ms=10_000,
        lease_expires_at_ms=99_999,
    )
    composition = create_agent_composition(db)
    tree = composition.run_tree_repository
    try:
        await tree.begin_root(BeginRootAgentCommand(
            run_id=run_id,
            agent_id="root-agent",
            name="root",
            title="Root",
            instruction="test",
            objective="test",
            capability_grant=AgentCapabilityGrant(can_spawn_agents=True),
            idempotency_key=f"begin:{run_id}",
        ))
        spawned = await tree.spawn_agents(SpawnAgentsCommand(
            parent_run_id=run_id,
            idempotency_key="spawn-before-restart",
            children=(ChildAgentSpec(
                name="unfinished",
                title="Unfinished Child",
                instruction="test",
                objective="test",
            ),),
        ))
        child_id = spawned.items[0].run.run_id

        assert await AgentOrphanRecoveryService(
            db, composition
        ).recover(after_restart=True) == (run_id,)

        fresh_tree = type(tree)(db)
        assert (await fresh_tree.get_run(run_id)).status.value == "canceled"
        assert (await fresh_tree.get_run(child_id)).status.value == "canceled"
        aggregation = await fresh_tree.aggregate_runs(run_id, (child_id,))
        assert aggregation.pending_run_ids == ()
        assert aggregation.required_failures == (child_id,)
    finally:
        await composition.shutdown()


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
async def test_child_terminal_result_never_materializes_as_a_conversation(db):
    from infrastructure.persistence.run_conversation_store import (
        ensure_terminal_run_conversation,
        materialize_recovered_run_conversations,
        materialize_terminal_writing_run_holes,
    )
    await db.execute("INSERT INTO ai_sessions (id, book_id) VALUES (195, 'synthetic')")
    root = await run_store.create_run(db, session_id=195, prompt="root", mode="agent")
    child = await run_store.create_run(
        db, session_id=195, prompt="PRIVATE_CHILD_INSTRUCTION", mode="agent",
        root_run_id=root, parent_run_id=root,
    )
    await db.execute("UPDATE ai_agent_runs SET status='done', final_response='PRIVATE_CHILD_RESULT' WHERE id=?", [child])
    assert (await run_store.get_latest_run_for_session(db, 195))["id"] == root
    assert await ensure_terminal_run_conversation(db, child) is None
    assert await materialize_recovered_run_conversations(db, [child]) == ()
    assert await materialize_terminal_writing_run_holes(db) == ()
    assert await db.fetch_all("SELECT id FROM ai_conversations WHERE session_id=195") == []
    await db.execute("UPDATE ai_agent_runs SET status='done', final_response='ROOT_FINAL' WHERE id=?", [root])
    assert await materialize_terminal_writing_run_holes(db) == (root,)
    assert await db.fetch_all("SELECT response FROM ai_conversations WHERE session_id=195") == [{"response": "ROOT_FINAL"}]


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
