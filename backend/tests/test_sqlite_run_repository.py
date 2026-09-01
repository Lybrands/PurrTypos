from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio

from purra.contracts import (
    ExecutionPlan,
    ModelTokenUsage,
    RunCreateParams,
    RunExecutionIntent,
    RunBinding,
    RunProvenance,
    RunStatus,
    StepExecutor,
    StepStatus,
    StepType,
    TaskStep,
    TaskStepUpdate,
    ToolRiskLevel,
    TraceRecord,
    RuntimeLimits,
)
from purra.errors import ContractViolationError, RunCancellationConflictError
from purra.events import AgentEvent, CoreEventType
from purra.ports import RunCommit, RunRepository
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from infrastructure.persistence import run_store
from infrastructure.persistence.run_store import get_run, get_run_events, get_run_todos


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
    columns = {
        row["name"]
        for row in await run_db.fetch_all("PRAGMA table_info(ai_agent_runs)")
    }
    assert columns == {
        "id",
        "session_id",
        "conversation_id",
        "status",
        "mode",
        "prompt",
        "model_provider",
        "model_name",
        "context_window",
        "endpoint_digest",
        "request_profile_digest",
        "requested_reasoning_mode",
        "output_contract",
        "tool_protocol_contract",
        "recovery_policy_id",
        "capability_snapshot_digest",
        "capability_snapshot_json",
        "binding_namespace",
        "binding_aggregate_id",
        "binding_command_id",
        "binding_attributes_json",
        "root_run_id",
        "agent_id",
        "parent_run_id",
        "agent_tree_lease_owner_id",
        "agent_tree_lease_epoch",
        "execution_owner_id",
        "lease_expires_at_ms",
        "heartbeat_at_ms",
        "execution_attempt",
        "cancel_requested_at_ms",
        "cancellation_epoch",
        "deadline_at_ms",
        "runtime_limits_json",
        "agent_preset_snapshot_json",
        "plan_title",
        "plan_goal",
        "task_spec_json",
        "work_step_ids_json",
        "execution_checkpoint_json",
        "error",
        "model_attempt_count",
        "unreported_usage_attempts",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "provider_output_events",
        "provider_output_bytes",
        "final_response",
        "create_time",
        "update_time",
    }
    todo_columns = {
        row["name"]
        for row in await run_db.fetch_all("PRAGMA table_info(ai_agent_run_todos)")
    }
    assert {
        "step_type",
        "risk_level",
        "description",
        "assignment_json",
        "depends_on_json",
    }.issubset(todo_columns)
    assert "agent_role" not in todo_columns

    run_id = await repository.create(RunCreateParams(
        session_id=7,
        prompt="test prompt",
        mode="agent",
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
    assert run["final_response"] == "final"
    assert todos == [{
        "id": "read",
        "title": "Read context",
        "status": "done",
        "executor": "tool",
        "type": "read",
        "riskLevel": "read",
        "suggestedTools": ["readThing"],
        "resultSummary": "Read complete",
    }]
    assert [event["eventType"] for event in events] == [
        "test.progress",
        "agentRunTrace",
    ]


@pytest.mark.asyncio
async def test_run_model_budget_is_idempotent_and_records_overage(run_db):
    repository = SqliteRunRepository(run_db)
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="budgeted run",
        mode="agent",
        runtime_limits=RuntimeLimits(
            max_run_output_tokens=None,
            max_model_invocation_attempts=1,
            max_input_tokens=5,
        ),
    ))

    first = await repository.reserve_model_attempt(run_id, "model-call-1")
    replay = await repository.reserve_model_attempt(run_id, "model-call-1")
    assert first.model_attempts == replay.model_attempts == 1

    with pytest.raises(ContractViolationError) as attempt_error:
        await repository.reserve_model_attempt(run_id, "model-call-2")
    assert attempt_error.value.code == "runtime_budget_exceeded"

    usage = ModelTokenUsage(input_tokens=6, output_tokens=2)
    with pytest.raises(ContractViolationError) as usage_error:
        await repository.settle_model_attempt(run_id, "model-call-1", usage)
    assert usage_error.value.code == "runtime_budget_exceeded"

    row = await run_db.fetch_one(
        "SELECT model_attempt_count, input_tokens, output_tokens "
        "FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )
    assert row is not None
    assert row["model_attempt_count"] == 1
    assert row["input_tokens"] == 6
    assert row["output_tokens"] == 2


@pytest.mark.asyncio
async def test_requested_run_identity_and_root_scope_are_persisted_once(run_db):
    repository = SqliteRunRepository(run_db)
    root_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="root",
        mode="agent",
        requested_run_id="root-run",
        agent_id="root-agent",
    ))
    child_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="child",
        mode="agent",
        requested_run_id="child-run",
        root_run_id=root_id,
        agent_id="child-agent",
        parent_run_id=root_id,
        lease_owner_id="tree-worker",
        lease_epoch=3,
    ))

    assert root_id == "root-run"
    assert child_id == "child-run"
    assert await run_db.fetch_one(
        "SELECT root_run_id, agent_id, parent_run_id, "
        "agent_tree_lease_owner_id, agent_tree_lease_epoch "
        "FROM ai_agent_runs WHERE id = ?",
        [child_id],
    ) == {
        "root_run_id": root_id,
        "agent_id": "child-agent",
        "parent_run_id": root_id,
        "agent_tree_lease_owner_id": "tree-worker",
        "agent_tree_lease_epoch": 3,
    }
    with pytest.raises(ContractViolationError) as conflict:
        await repository.create(RunCreateParams(
            session_id=None,
            prompt="duplicate",
            mode="agent",
            requested_run_id=root_id,
        ))
    assert conflict.value.code == "run_identity_conflict"
    with pytest.raises(sqlite3.IntegrityError, match="scope is immutable"):
        await run_db.execute(
            "UPDATE ai_agent_runs SET agent_id = ? WHERE id = ?",
            ["replacement", child_id],
        )


@pytest.mark.asyncio
async def test_child_run_rejects_parent_from_another_root(run_db):
    repository = SqliteRunRepository(run_db)
    first = await repository.create(RunCreateParams(
        session_id=None,
        prompt="first root",
        mode="agent",
        requested_run_id="root-first",
    ))
    second = await repository.create(RunCreateParams(
        session_id=None,
        prompt="second root",
        mode="agent",
        requested_run_id="root-second",
    ))

    with pytest.raises(ContractViolationError) as conflict:
        await repository.create(RunCreateParams(
            session_id=None,
            prompt="invalid child",
            mode="agent",
            requested_run_id="child-invalid",
            root_run_id=first,
            parent_run_id=second,
        ))
    assert conflict.value.code == "run_scope_conflict"


@pytest.mark.asyncio
async def test_run_provenance_migration_persists_once_and_rejects_updates(run_db):
    repository = SqliteRunRepository(run_db)
    provenance = RunProvenance(
        model_provider="openai",
        model_name="writing-model",
        context_window=200_000,
        endpoint_digest="a" * 64,
        request_profile_digest="b" * 64,
        capability_snapshot={
            "schemaVersion": 1,
            "profileId": "test:profile",
            "contextWindowTokens": 200_000,
            "digest": "c" * 64,
        },
        execution_intent=RunExecutionIntent(
            requested_reasoning_mode="enabled",
            output_contract="assistant_text",
            tool_protocol_contract="host_tools",
            recovery_policy_id="purra.default.v1",
            capability_snapshot_digest="c" * 64,
        ),
    )

    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="immutable provenance",
        mode="agent",
        provenance=provenance,
    ))
    row = await get_run(run_db, run_id)

    assert row is not None
    assert {
        "model_provider": row["model_provider"],
        "model_name": row["model_name"],
        "context_window": row["context_window"],
        "endpoint_digest": row["endpoint_digest"],
        "request_profile_digest": row["request_profile_digest"],
        "requested_reasoning_mode": row["requested_reasoning_mode"],
        "capability_snapshot_digest": row["capability_snapshot_digest"],
        "capability_snapshot": json.loads(row["capability_snapshot_json"]),
    } == {
        "model_provider": "openai",
        "model_name": "writing-model",
        "context_window": 200_000,
        "endpoint_digest": "a" * 64,
        "request_profile_digest": "b" * 64,
        "requested_reasoning_mode": "enabled",
        "capability_snapshot_digest": "c" * 64,
        "capability_snapshot": {
            "schemaVersion": 1,
            "profileId": "test:profile",
            "contextWindowTokens": 200_000,
            "digest": "c" * 64,
        },
    }
    with pytest.raises(sqlite3.IntegrityError, match="provenance is immutable"):
        await run_db.execute(
            "UPDATE ai_agent_runs SET model_name = ? WHERE id = ?",
            ["replacement", run_id],
        )
    with pytest.raises(sqlite3.IntegrityError, match="provenance is immutable"):
        await run_db.execute(
            "UPDATE ai_agent_runs SET capability_snapshot_json = ? WHERE id = ?",
            ['{"digest":"replacement"}', run_id],
        )

    # Provenance-free callers remain valid for operational runs that do not
    # originate from a model request.
    plain_run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="operational run",
        mode="agent",
    ))
    plain_run = await get_run(run_db, plain_run_id)
    assert plain_run is not None
    assert plain_run["request_profile_digest"] is None

    await repository.transition(run_id, RunStatus.DONE, final_response="done")
    assert (await get_run(run_db, run_id) or {})["status"] == "done"


@pytest.mark.asyncio
async def test_run_binding_is_opaque_persisted_and_immutable(run_db):
    repository = SqliteRunRepository(run_db)
    run_id = await repository.create(RunCreateParams(
        session_id=None,
        prompt="bound command",
        mode="agent",
        binding=RunBinding(
            namespace="screenplay.operation",
            aggregate_id="project-1",
            command_id="operation-1",
            attributes={"target": "structure"},
        ),
    ))

    row = await get_run(run_db, run_id)
    assert row is not None
    assert row["binding_namespace"] == "screenplay.operation"
    assert row["binding_aggregate_id"] == "project-1"
    assert row["binding_command_id"] == "operation-1"
    assert json.loads(row["binding_attributes_json"]) == {
        "target": "structure",
    }
    with pytest.raises(sqlite3.IntegrityError, match="binding is immutable"):
        await run_db.execute(
            "UPDATE ai_agent_runs SET binding_command_id = ? WHERE id = ?",
            ["operation-2", run_id],
        )


@pytest.mark.asyncio
async def test_schema_migrates_existing_agent_runs_without_fabricating_provenance(
    tmp_path: Path,
):
    db_path = tmp_path / "purrtypos.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("""CREATE TABLE ai_agent_runs (
            id TEXT PRIMARY KEY NOT NULL,
            session_id INTEGER DEFAULT NULL,
            conversation_id INTEGER DEFAULT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            mode TEXT DEFAULT NULL,
            prompt TEXT NOT NULL DEFAULT '',
            parent_run_id TEXT DEFAULT NULL,
            root_run_id TEXT DEFAULT NULL,
            delegation_id TEXT DEFAULT NULL,
            agent_role TEXT DEFAULT NULL,
            run_depth INTEGER NOT NULL DEFAULT 0,
            final_response TEXT DEFAULT '',
            create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            update_time DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        connection.execute(
            "INSERT INTO ai_agent_runs (id, prompt) VALUES (?, ?)",
            ["run-before-provenance", "historical"],
        )
        connection.execute(
            "INSERT INTO ai_agent_runs "
            "(id, prompt, root_run_id, parent_run_id) VALUES (?, ?, ?, ?)",
            [
                "child-before-provenance",
                "historical child",
                "run-before-provenance",
                "run-before-provenance",
            ],
        )

    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(ai_agent_runs)")
        }
        assert {
            "model_provider",
            "model_name",
            "context_window",
            "endpoint_digest",
            "request_profile_digest",
            "binding_namespace",
            "binding_aggregate_id",
            "binding_command_id",
            "binding_attributes_json",
        }.issubset(columns)
        assert {
            "delegation_id",
            "agent_role",
            "run_depth",
        }.isdisjoint(columns)
        assert {
            "parent_run_id",
            "root_run_id",
            "agent_id",
            "agent_tree_lease_owner_id",
            "agent_tree_lease_epoch",
        }.issubset(columns)
        historical = await get_run(db, "run-before-provenance")
        assert historical is not None
        assert historical["request_profile_digest"] is None
        assert historical["binding_namespace"] is None
        assert historical["binding_aggregate_id"] is None
        assert historical["binding_command_id"] is None
        assert historical["binding_attributes_json"] is None
        historical_scope = await db.fetch_one(
            "SELECT root_run_id, agent_id, parent_run_id "
            "FROM ai_agent_runs WHERE id = ?",
            ["run-before-provenance"],
        )
        assert historical_scope == {
            "root_run_id": "run-before-provenance",
            "agent_id": "run-before-provenance",
            "parent_run_id": None,
        }
        assert await db.fetch_one(
            "SELECT root_run_id, agent_id, parent_run_id "
            "FROM ai_agent_runs WHERE id = ?",
            ["child-before-provenance"],
        ) == {
            "root_run_id": "run-before-provenance",
            "agent_id": "child-before-provenance",
            "parent_run_id": "run-before-provenance",
        }
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_schema_removes_obsolete_task_plan_agent_role(tmp_path: Path):
    legacy = DatabaseConnection(tmp_path)
    await legacy.init()
    try:
        await legacy.execute(
            "ALTER TABLE ai_agent_run_todos ADD COLUMN agent_role TEXT"
        )
    finally:
        await legacy.close()

    migrated = DatabaseConnection(tmp_path)
    await migrated.init()
    try:
        columns = {
            str(row["name"])
            for row in await migrated.fetch_all(
                "PRAGMA table_info(ai_agent_run_todos)"
            )
        }
    finally:
        await migrated.close()

    assert "agent_role" not in columns


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

    monkeypatch.setattr(run_store, "append_event", fail_append)
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
        RunCommit(
            replace_plan=ExecutionPlan(title="Answer", steps=(step,)),
            events=(todo_event,),
        ),
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
@pytest.mark.parametrize(
    "terminal_status",
    (RunStatus.DONE, RunStatus.FAILED, RunStatus.BLOCKED),
)
async def test_cancel_fence_rejects_every_non_canceled_terminal_commit(
    run_db,
    terminal_status,
):
    repository = SqliteRunRepository(run_db)
    begun = await repository.begin(
        RunCreateParams(session_id=None, prompt="cancel wins", mode="agent"),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    await run_db.execute(
        "UPDATE ai_agent_runs SET cancel_requested_at_ms = 1, "
        "cancellation_epoch = 1 WHERE id = ?",
        [begun.run_id],
    )
    event_type = {
        RunStatus.DONE: CoreEventType.RUN_COMPLETED,
        RunStatus.FAILED: CoreEventType.RUN_FAILED,
        RunStatus.BLOCKED: CoreEventType.RUN_BLOCKED,
    }[terminal_status]

    with pytest.raises(RunCancellationConflictError, match="cancel-requested"):
        await repository.commit(
            begun.run_id,
            RunCommit(
                terminal_status=terminal_status,
                final_response=("late answer" if terminal_status is RunStatus.DONE else None),
                error=("late failure" if terminal_status is RunStatus.FAILED else None),
                events=(AgentEvent(
                    type=event_type,
                    run_id=begun.run_id,
                    payload={"status": terminal_status.value},
                ),),
            ),
        )

    assert await run_db.fetch_one(
        "SELECT status, cancel_requested_at_ms FROM ai_agent_runs WHERE id = ?",
        [begun.run_id],
    ) == {"status": "running", "cancel_requested_at_ms": 1}
    await repository.commit(
        begun.run_id,
        RunCommit(
            terminal_status=RunStatus.CANCELED,
            events=(AgentEvent(
                type=CoreEventType.RUN_CANCELED,
                run_id=begun.run_id,
                payload={"status": RunStatus.CANCELED.value},
            ),),
        ),
    )
    assert await run_db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [begun.run_id],
    ) == {"status": "canceled"}


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
        RunCommit(
            replace_plan=ExecutionPlan(title="Answer", steps=(step,)),
            events=(installed,),
        ),
    )
    events_before = await get_run_events(run_db, begun.run_id)

    async def fail_append(*_args, **_kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(run_store, "append_event", fail_append)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [None, 3])
async def test_new_run_output_budget_persists_and_settles_across_calls(run_db, limit):
    repository = SqliteRunRepository(run_db)
    run_id = await repository.create(RunCreateParams(
        session_id=None, prompt="output budget", mode="agent",
        runtime_limits=RuntimeLimits(max_run_output_tokens=limit),
    ))
    row = await run_db.fetch_one("SELECT runtime_limits_json FROM ai_agent_runs WHERE id = ?", [run_id])
    stored = json.loads(row["runtime_limits_json"])
    assert stored["max_run_output_tokens"] == limit
    assert "max_output_tokens" not in stored
    await repository.reserve_model_attempt(run_id, "first")
    await repository.settle_model_attempt(run_id, "first", ModelTokenUsage(input_tokens=1, output_tokens=2))
    await repository.reserve_model_attempt(run_id, "second")
    if limit is None:
        await repository.settle_model_attempt(run_id, "second", ModelTokenUsage(input_tokens=1, output_tokens=2))
    else:
        with pytest.raises(ContractViolationError) as error:
            await repository.settle_model_attempt(run_id, "second", ModelTokenUsage(input_tokens=1, output_tokens=2))
        assert error.value.code == "runtime_budget_exceeded"
        assert error.value.details["budgetKind"] == "output_tokens"
    row = await run_db.fetch_one("SELECT output_tokens FROM ai_agent_runs WHERE id = ?", [run_id])
    assert row["output_tokens"] == 4


@pytest.mark.asyncio
async def test_old_run_budget_cannot_silently_become_unlimited(run_db):
    await run_db.execute(
        "INSERT INTO ai_agent_runs (id, status, prompt, root_run_id, runtime_limits_json) "
        "VALUES (?, 'running', 'old run', ?, ?)",
        ["old-run", "old-run", json.dumps({"max_output_tokens": 1})],
    )
    repository = SqliteRunRepository(run_db)
    with pytest.raises(ContractViolationError) as error:
        await repository.reserve_model_attempt("old-run", "first")
    assert error.value.code == "runtime_limits_invalid"
    row = await run_db.fetch_one("SELECT model_attempt_count FROM ai_agent_runs WHERE id = 'old-run'")
    assert row["model_attempt_count"] == 0


@pytest.mark.parametrize("value", [
    {"maxOutputTokens": 1},
    {"max_run_output_tokens": 1},
    {"maxRunOutputToken": 1},
    {"maxRunOutputTokens": -1},
])
def test_invalid_long_task_budget_cannot_silently_become_unlimited(value):
    from infrastructure.persistence.sqlite_long_task_repository import _budget_limits

    with pytest.raises(ContractViolationError) as error:
        _budget_limits(value)
    assert error.value.code == "runtime_limits_invalid"
