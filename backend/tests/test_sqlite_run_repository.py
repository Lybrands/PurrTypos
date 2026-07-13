from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from agent_core.contracts import (
    RunCreateParams,
    RunStatus,
    StepExecutor,
    StepStatus,
    StepType,
    TaskStep,
    TaskStepUpdate,
    ToolRiskLevel,
    TraceRecord,
)
from agent_core.errors import ContractViolationError
from agent_core.events import AgentEvent, CoreEventType
from agent_core.ports import RunCommit, RunRepository
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from services import agent_run_store
from services.agent_run_store import get_run, get_run_events, get_run_todos


@pytest_asyncio.fixture
async def run_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sqlite_repository_maps_the_complete_write_side_contract(run_db):
    repository = SqliteRunRepository(run_db)
    assert isinstance(repository, RunRepository)

    run_id = await repository.create(RunCreateParams(
        session_id=7,
        prompt="test prompt",
        mode="agent",
        release_version="test-version",
        rollout_cohort="test-cohort",
    ))
    await repository.replace_steps(run_id, [
        TaskStep(
            id="read",
            title="Read context",
            type=StepType.READ,
            executor=StepExecutor.TOOL,
            status=StepStatus.RUNNING,
            risk_level=ToolRiskLevel.READ,
            suggested_tools=("readThing",),
        ),
    ])
    await repository.update_step(run_id, TaskStepUpdate(
        step_id="read",
        status=StepStatus.DONE,
        result_summary="Read complete",
    ))
    await repository.append_event(
        run_id,
        AgentEvent(type="test.progress", run_id=run_id, payload={"stepId": "read"}),
    )
    await repository.append_trace(
        run_id,
        TraceRecord(stage="planner", outcome="model_plan", duration_ms=12),
    )
    await repository.bind_conversation(run_id, 99)
    await repository.transition(run_id, RunStatus.DONE, final_response="final")

    run = await get_run(run_db, run_id)
    todos = await get_run_todos(run_db, run_id)
    events = await get_run_events(run_db, run_id)
    assert run is not None
    assert run["status"] == "done"
    assert run["conversation_id"] == 99
    assert run["release_version"] == "test-version"
    assert run["rollout_cohort"] == "test-cohort"
    assert run["final_response"] == "final"
    assert todos == [{
        "id": "read",
        "title": "Read context",
        "status": "done",
        "executor": "tool",
        "suggestedTools": ["readThing"],
        "resultSummary": "Read complete",
    }]
    assert [event["eventType"] for event in events] == [
        "test.progress",
        "agentRunTrace",
    ]


@pytest.mark.asyncio
async def test_sqlite_repository_rejects_cross_run_event_binding(run_db):
    repository = SqliteRunRepository(run_db)
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="test",
        mode="agent",
    ))

    with pytest.raises(ContractViolationError, match="run_id"):
        await repository.append_event(
            run_id,
            AgentEvent(type="run.failed", run_id="another-run"),
        )


@pytest.mark.asyncio
async def test_sqlite_repository_preserves_numeric_session_ids_and_rejects_invalid_values(run_db):
    repository = SqliteRunRepository(run_db)
    run_id = await repository.create(RunCreateParams(
        session_id="42",
        prompt="numeric session",
        mode="agent",
    ))

    row = await run_db.fetch_one("SELECT session_id FROM ai_agent_runs WHERE id = ?", [run_id])
    assert row and row["session_id"] == 42

    for invalid_session_id in (True, "not-a-session"):
        with pytest.raises(ContractViolationError, match="session_id"):
            await repository.create(RunCreateParams(
                session_id=invalid_session_id,
                prompt="invalid session",
                mode="agent",
            ))


@pytest.mark.asyncio
async def test_sqlite_repository_rejects_reopen_and_invalid_failed_transitions(run_db):
    repository = SqliteRunRepository(run_db)
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="terminal contract",
        mode="agent",
    ))

    with pytest.raises(ContractViolationError, match="reopen"):
        await repository.transition(run_id, RunStatus.RUNNING)  # type: ignore[arg-type]
    for invalid_error in (None, "", "   "):
        with pytest.raises(ContractViolationError, match="non-empty error"):
            await repository.transition(
                run_id,
                RunStatus.FAILED,
                error=invalid_error,
            )

    run = await get_run(run_db, run_id)
    assert run and run["status"] == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (RunStatus.BLOCKED, "blocked"),
        (RunStatus.CANCELED, "canceled"),
        (RunStatus.FAILED, "failed"),
    ],
)
async def test_sqlite_repository_writes_each_terminal_status(run_db, status, expected):
    repository = SqliteRunRepository(run_db)
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt=expected,
        mode="agent",
    ))

    await repository.transition(
        run_id,
        status,
        error="failure detail" if status is RunStatus.FAILED else None,
    )

    run = await get_run(run_db, run_id)
    assert run and run["status"] == expected
    if status is RunStatus.FAILED:
        assert run["final_response"] == "failure detail"


@pytest.mark.asyncio
async def test_sqlite_repository_rejects_updates_for_unknown_steps(run_db):
    repository = SqliteRunRepository(run_db)
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="missing step",
        mode="agent",
    ))

    with pytest.raises(ContractViolationError, match="does not exist"):
        await repository.update_step(run_id, TaskStepUpdate(
            step_id="missing",
            status=StepStatus.DONE,
        ))


@pytest.mark.asyncio
async def test_sqlite_repository_distinguishes_unchanged_and_explicit_empty_step_fields(run_db):
    repository = SqliteRunRepository(run_db)
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="step patch semantics",
        mode="agent",
    ))
    await repository.replace_steps(run_id, [TaskStep(
        id="answer",
        title="Answer",
        type=StepType.REVIEW,
        executor=StepExecutor.MODEL,
        result_summary="before",
        error="before-error",
    )])

    await repository.update_step(run_id, TaskStepUpdate(
        step_id="answer",
        status=StepStatus.RUNNING,
    ))
    unchanged = await run_db.fetch_one(
        "SELECT result_summary, error FROM ai_agent_run_todos WHERE run_id = ? AND step_id = ?",
        [run_id, "answer"],
    )
    assert unchanged == {"result_summary": "before", "error": "before-error"}

    await repository.update_step(run_id, TaskStepUpdate(
        step_id="answer",
        status=StepStatus.DONE,
        result_summary="",
        error="",
    ))
    cleared = await run_db.fetch_one(
        "SELECT result_summary, error FROM ai_agent_run_todos WHERE run_id = ? AND step_id = ?",
        [run_id, "answer"],
    )
    assert cleared == {"result_summary": "", "error": ""}


@pytest.mark.asyncio
async def test_sqlite_repository_begin_atomically_creates_run_and_started_outbox(run_db):
    repository = SqliteRunRepository(run_db)
    template = AgentEvent(
        type=CoreEventType.RUN_STARTED,
        payload={"status": "running", "title": "To-dos"},
    )

    result = await repository.begin(
        RunCreateParams(session_id=7, prompt="atomic begin", mode="agent"),
        template,
    )

    run = await get_run(run_db, result.run_id)
    events = await get_run_events(run_db, result.run_id)
    assert run and run["status"] == "running"
    assert result.event.run_id == result.run_id
    assert result.event.type == CoreEventType.RUN_STARTED
    assert [event["eventType"] for event in events] == [CoreEventType.RUN_STARTED]


@pytest.mark.asyncio
async def test_atomic_paths_serialize_recursively_frozen_event_payloads(run_db):
    repository = SqliteRunRepository(run_db)
    begun = await repository.begin(
        RunCreateParams(session_id=None, prompt="immutable payload", mode="agent"),
        AgentEvent(
            type=CoreEventType.RUN_STARTED,
            payload={"context": {"items": [1, {"ready": True}]}},
        ),
    )
    completed = AgentEvent(
        type=CoreEventType.RUN_COMPLETED,
        run_id=begun.run_id,
        payload={"result": {"sections": ["one", {"count": 2}]}},
    )

    await repository.commit(
        begun.run_id,
        RunCommit(
            terminal_status=RunStatus.DONE,
            final_response="done",
            events=(completed,),
        ),
    )

    events = await get_run_events(run_db, begun.run_id)
    assert events[0]["payload"] == {
        "context": {"items": [1, {"ready": True}]},
    }
    assert events[1]["payload"] == {
        "result": {"sections": ["one", {"count": 2}]},
    }


@pytest.mark.asyncio
async def test_sqlite_repository_begin_rolls_back_run_when_outbox_write_fails(
    run_db,
    monkeypatch: pytest.MonkeyPatch,
):
    repository = SqliteRunRepository(run_db)

    async def fail_append(*_args, **_kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(agent_run_store, "append_event", fail_append)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await repository.begin(
            RunCreateParams(session_id=None, prompt="rollback begin", mode="agent"),
            AgentEvent(type=CoreEventType.RUN_STARTED),
        )

    row = await run_db.fetch_one("SELECT COUNT(*) AS count FROM ai_agent_runs")
    event_row = await run_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events"
    )
    assert row == {"count": 0}
    assert event_row == {"count": 0}


@pytest.mark.asyncio
async def test_sqlite_repository_begin_cancellation_before_commit_rolls_back(
    run_db,
    monkeypatch: pytest.MonkeyPatch,
):
    repository = SqliteRunRepository(run_db)
    append_entered = asyncio.Event()

    async def block_before_outbox(*_args, **_kwargs):
        append_entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(repository, "_append_event_unchecked", block_before_outbox)
    begin_task = asyncio.create_task(repository.begin(
        RunCreateParams(session_id=None, prompt="cancel begin", mode="agent"),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    ))
    await asyncio.wait_for(append_entered.wait(), timeout=1)
    begin_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(begin_task, timeout=1)

    row = await run_db.fetch_one("SELECT COUNT(*) AS count FROM ai_agent_runs")
    event_row = await run_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events"
    )
    assert row == {"count": 0}
    assert event_row == {"count": 0}


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_precommit_rollback_receipt(
    run_db,
    monkeypatch: pytest.MonkeyPatch,
):
    connection = run_db._ensure_conn()
    original_rollback = connection.rollback
    body_entered = asyncio.Event()
    rollback_applied = asyncio.Event()
    release_rollback_ack = asyncio.Event()
    rollback_cancel_count = 0

    async def rollback_then_hold_ack():
        nonlocal rollback_cancel_count
        await original_rollback()
        rollback_applied.set()
        try:
            await release_rollback_ack.wait()
        except asyncio.CancelledError:
            rollback_cancel_count += 1
            raise

    await run_db.execute(
        "CREATE TABLE rollback_cancel_probe (value TEXT NOT NULL)"
    )
    monkeypatch.setattr(connection, "rollback", rollback_then_hold_ack)

    async def precommit_work():
        async with run_db.transaction(cancellation_linearizable=True):
            await run_db.execute(
                "INSERT INTO rollback_cancel_probe (value) VALUES (?)",
                ["must roll back"],
            )
            body_entered.set()
            await asyncio.Event().wait()

    transaction_task = asyncio.create_task(precommit_work())
    await asyncio.wait_for(body_entered.wait(), timeout=1)

    transaction_task.cancel()
    await asyncio.wait_for(rollback_applied.wait(), timeout=1)
    transaction_task.cancel()
    await asyncio.sleep(0)
    remained_pending = not transaction_task.done()
    cancels_during_cleanup = rollback_cancel_count

    release_rollback_ack.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(transaction_task, timeout=1)

    assert remained_pending
    assert cancels_during_cleanup == 0
    assert rollback_cancel_count == 0
    row = await run_db.fetch_one(
        "SELECT COUNT(*) AS count FROM rollback_cancel_probe"
    )
    assert row == {"count": 0}


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_begin_rollback_receipt(
    run_db,
    monkeypatch: pytest.MonkeyPatch,
):
    repository = SqliteRunRepository(run_db)
    connection = run_db._ensure_conn()
    original_execute = connection.execute
    original_rollback = connection.rollback
    begin_applied = asyncio.Event()
    rollback_applied = asyncio.Event()
    release_rollback_ack = asyncio.Event()
    hold_begin_ack = asyncio.Event()
    rollback_cancel_count = 0

    async def execute_then_hold_begin_ack(sql, *args, **kwargs):
        cursor = await original_execute(sql, *args, **kwargs)
        if sql == "BEGIN IMMEDIATE":
            begin_applied.set()
            await hold_begin_ack.wait()
        return cursor

    async def rollback_then_hold_ack():
        nonlocal rollback_cancel_count
        await original_rollback()
        rollback_applied.set()
        try:
            await release_rollback_ack.wait()
        except asyncio.CancelledError:
            rollback_cancel_count += 1
            raise

    monkeypatch.setattr(connection, "execute", execute_then_hold_begin_ack)
    monkeypatch.setattr(connection, "rollback", rollback_then_hold_ack)
    begin_task = asyncio.create_task(repository.begin(
        RunCreateParams(session_id=None, prompt="cancel begin twice", mode="agent"),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    ))
    await asyncio.wait_for(begin_applied.wait(), timeout=1)

    begin_task.cancel()
    await asyncio.wait_for(rollback_applied.wait(), timeout=1)
    begin_task.cancel()
    await asyncio.sleep(0)
    remained_pending = not begin_task.done()
    cancels_during_cleanup = rollback_cancel_count

    release_rollback_ack.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(begin_task, timeout=1)

    assert remained_pending
    assert cancels_during_cleanup == 0
    assert rollback_cancel_count == 0
    row = await run_db.fetch_one("SELECT COUNT(*) AS count FROM ai_agent_runs")
    event_row = await run_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events"
    )
    assert row == {"count": 0}
    assert event_row == {"count": 0}


@pytest.mark.asyncio
async def test_sqlite_repository_commit_wins_cancellation_during_commit_ack(
    run_db,
    monkeypatch: pytest.MonkeyPatch,
):
    repository = SqliteRunRepository(run_db)
    connection = run_db._ensure_conn()
    original_commit = connection.commit
    durable = asyncio.Event()
    release_ack = asyncio.Event()

    async def commit_then_delay_ack():
        await original_commit()
        durable.set()
        await release_ack.wait()

    monkeypatch.setattr(connection, "commit", commit_then_delay_ack)
    begin_task = asyncio.create_task(repository.begin(
        RunCreateParams(session_id=None, prompt="commit wins", mode="agent"),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    ))
    await asyncio.wait_for(durable.wait(), timeout=1)
    begin_task.cancel()
    await asyncio.sleep(0)
    assert not begin_task.done()

    release_ack.set()
    begun = await asyncio.wait_for(begin_task, timeout=1)

    run = await get_run(run_db, begun.run_id)
    events = await get_run_events(run_db, begun.run_id)
    assert run and run["status"] == "running"
    assert [event["eventType"] for event in events] == [
        CoreEventType.RUN_STARTED
    ]


@pytest.mark.asyncio
async def test_repeated_cancellation_cannot_interrupt_sqlite_commit_receipt(
    run_db,
    monkeypatch: pytest.MonkeyPatch,
):
    repository = SqliteRunRepository(run_db)
    connection = run_db._ensure_conn()
    original_commit = connection.commit
    durable = asyncio.Event()
    release_ack = asyncio.Event()
    commit_cancel_count = 0

    async def commit_then_hold_ack():
        nonlocal commit_cancel_count
        await original_commit()
        durable.set()
        try:
            await release_ack.wait()
        except asyncio.CancelledError:
            commit_cancel_count += 1
            raise

    monkeypatch.setattr(connection, "commit", commit_then_hold_ack)
    begin_task = asyncio.create_task(repository.begin(
        RunCreateParams(session_id=None, prompt="double cancel", mode="agent"),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    ))
    await asyncio.wait_for(durable.wait(), timeout=1)

    # The first cancel is handled at the COMMIT receipt boundary. Let that
    # handler reach its next shield before deterministically canceling again.
    begin_task.cancel()
    await asyncio.sleep(0)
    begin_task.cancel()
    await asyncio.sleep(0)

    assert not begin_task.done()
    assert commit_cancel_count == 0

    release_ack.set()
    begun = await asyncio.wait_for(begin_task, timeout=1)

    assert commit_cancel_count == 0
    run = await get_run(run_db, begun.run_id)
    events = await get_run_events(run_db, begun.run_id)
    assert run and run["status"] == "running"
    assert [event["eventType"] for event in events] == [
        CoreEventType.RUN_STARTED
    ]


@pytest.mark.asyncio
async def test_sqlite_repository_commit_atomically_writes_plan_terminal_and_events(run_db):
    repository = SqliteRunRepository(run_db)
    begun = await repository.begin(
        RunCreateParams(session_id=None, prompt="atomic commit", mode="agent"),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    step = TaskStep(
        id="answer",
        title="Answer",
        type=StepType.REVIEW,
        executor=StepExecutor.MODEL,
        status=StepStatus.RUNNING,
    )
    todo_event = AgentEvent(
        type=CoreEventType.RUN_TODOS_UPDATED,
        run_id=begun.run_id,
        payload={"status": "running"},
    )
    returned = await repository.commit(
        begun.run_id,
        RunCommit(replace_steps=(step,), events=(todo_event,)),
    )
    terminal_event = AgentEvent(
        type=CoreEventType.RUN_COMPLETED,
        run_id=begun.run_id,
        payload={"status": "done"},
    )
    returned_terminal = await repository.commit(
        begun.run_id,
        RunCommit(
            step_updates=(TaskStepUpdate(
                step_id="answer",
                status=StepStatus.DONE,
                result_summary="complete",
            ),),
            terminal_status=RunStatus.DONE,
            final_response="final",
            events=(terminal_event,),
        ),
    )

    assert returned[0] is todo_event
    assert returned_terminal[0] is terminal_event
    run = await get_run(run_db, begun.run_id)
    todos = await get_run_todos(run_db, begun.run_id)
    events = await get_run_events(run_db, begun.run_id)
    assert run and run["status"] == "done" and run["final_response"] == "final"
    assert todos[0]["status"] == "done"
    assert [event["eventType"] for event in events] == [
        CoreEventType.RUN_STARTED,
        CoreEventType.RUN_TODOS_UPDATED,
        CoreEventType.RUN_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_sqlite_repository_rejects_every_commit_after_terminal_state(run_db):
    repository = SqliteRunRepository(run_db)
    begun = await repository.begin(
        RunCreateParams(session_id=None, prompt="one terminal", mode="agent"),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    completed = AgentEvent(
        type=CoreEventType.RUN_COMPLETED,
        run_id=begun.run_id,
        payload={"status": "done"},
    )
    await repository.commit(
        begun.run_id,
        RunCommit(
            terminal_status=RunStatus.DONE,
            final_response="done",
            events=(completed,),
        ),
    )
    events_before = await get_run_events(run_db, begun.run_id)

    with pytest.raises(ContractViolationError, match="terminal run"):
        await repository.commit(
            begun.run_id,
            RunCommit(
                terminal_status=RunStatus.CANCELED,
                events=(AgentEvent(
                    type=CoreEventType.RUN_CANCELED,
                    run_id=begun.run_id,
                ),),
            ),
        )
    with pytest.raises(ContractViolationError, match="terminal run"):
        await repository.commit(
            begun.run_id,
            RunCommit(events=(AgentEvent(
                type=CoreEventType.CONTEXT_BUDGETED,
                run_id=begun.run_id,
            ),)),
        )

    run = await get_run(run_db, begun.run_id)
    events_after = await get_run_events(run_db, begun.run_id)
    assert run and run["status"] == "done"
    assert events_after == events_before


@pytest.mark.asyncio
async def test_sqlite_repository_commit_rolls_back_steps_terminal_and_outbox_together(
    run_db,
    monkeypatch: pytest.MonkeyPatch,
):
    repository = SqliteRunRepository(run_db)
    begun = await repository.begin(
        RunCreateParams(session_id=None, prompt="rollback commit", mode="agent"),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    step = TaskStep(
        id="answer",
        title="Answer",
        type=StepType.REVIEW,
        executor=StepExecutor.MODEL,
        status=StepStatus.RUNNING,
    )
    installed = AgentEvent(
        type=CoreEventType.RUN_TODOS_UPDATED,
        run_id=begun.run_id,
    )
    await repository.commit(
        begun.run_id,
        RunCommit(replace_steps=(step,), events=(installed,)),
    )
    events_before = await get_run_events(run_db, begun.run_id)

    async def fail_append(*_args, **_kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(agent_run_store, "append_event", fail_append)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await repository.commit(
            begun.run_id,
            RunCommit(
                step_updates=(TaskStepUpdate(
                    step_id="answer",
                    status=StepStatus.DONE,
                    result_summary="must roll back",
                ),),
                terminal_status=RunStatus.DONE,
                final_response="must roll back",
                events=(AgentEvent(
                    type=CoreEventType.RUN_COMPLETED,
                    run_id=begun.run_id,
                ),),
            ),
        )

    run = await get_run(run_db, begun.run_id)
    todos = await get_run_todos(run_db, begun.run_id)
    events_after = await get_run_events(run_db, begun.run_id)
    assert run and run["status"] == "running" and not run["final_response"]
    assert todos[0]["status"] == "running"
    assert todos[0].get("resultSummary") is None
    assert events_after == events_before
