from __future__ import annotations

import asyncio
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from purra.api import (
    PLANNING_STREAM_SCHEMA,
    PlanningScope,
    PlanningStreamParser,
)
from purra.contracts import (
    ModelFinishReason,
    RunBinding,
    RunCreateParams,
    RunStatus,
)
from purra.errors import (
    ContractViolationError,
    RunCancellationConflictError,
    RunCommitProjectionError,
)
from purra.events import AgentEvent, CoreEventType
from purra.output import (
    AgentOutputEventDraft,
    AgentOutputIntent,
    OutputChannel,
    OutputCommitMode,
    OutputEventKind,
    OutputSource,
    OutputStreamSpec,
    OutputVisibility,
    RunLifecycleOutputDraft,
)
from purra.ports import RunCommit
from purra.testing import assert_host_adapters_conform
from infrastructure.persistence.agent_output_publisher import (
    InProcessAgentOutputPublisher,
)


def _repository_types():
    try:
        module = importlib.import_module(
            "infrastructure.persistence.sqlite_agent_output_repository"
        )
        ports = importlib.import_module("purra.output.ports")
    except ModuleNotFoundError as error:
        pytest.fail(f"canonical output repository is missing: {error}")
    return module.SqliteAgentOutputRepository, ports.AgentOutputRepository


@pytest_asyncio.fixture
async def output_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) "
        "VALUES (7, 'book-1', 'chapter-7')"
    )
    runs = SqliteRunRepository(db)
    run_id = await runs.create(
        RunCreateParams(session_id=7, prompt="审阅当前正文", mode="agent")
    )
    try:
        yield db, run_id, runs
    finally:
        await db.close()


async def _seed_artifact_claim(db, run_id: str) -> None:
    await db.execute(
        "INSERT INTO ai_agent_artifacts "
        "(id, namespace, kind, owner_id, owner_ref_kind, owner_ref_id, "
        "created_by_run_id) VALUES "
        "('terminal-claim-artifact', 'test', 'draft', 'owner', "
        "'run', ?, ?)",
        [run_id, run_id],
    )
    await db.execute(
        "INSERT INTO ai_agent_artifact_claims "
        "(artifact_id, run_id, claim_token, "
        "acquired_revision, expires_at_ms) VALUES "
        "('terminal-claim-artifact', ?, "
        "'terminal-claim-token', 1, 9999999999999)",
        [run_id],
    )


def _repository(
    db,
    *,
    run_repository=None,
    domain_projector=None,
    run_commit_projector=None,
):
    SqliteAgentOutputRepository, _port = _repository_types()
    return SqliteAgentOutputRepository(
        db,
        run_repository=run_repository,
        domain_projector=domain_projector,
        run_commit_projector=run_commit_projector,
    )


def _stream(
    run_id: str,
    *,
    stream_id: str = "output-1",
    invocation_id: str = "invocation-1",
    turn_id: str = "turn-1",
) -> OutputStreamSpec:
    return OutputStreamSpec(
        output_stream_id=stream_id,
        run_id=run_id,
        turn_id=turn_id,
        invocation_id=invocation_id,
        intent=AgentOutputIntent.FINAL_PUBLIC,
        commit_mode=OutputCommitMode.LIVE,
    )


def _delta(
    run_id: str,
    *,
    source_event_key: str,
    text: str,
    stream_id: str = "output-1",
    invocation_id: str = "invocation-1",
    turn_id: str = "turn-1",
) -> AgentOutputEventDraft:
    return AgentOutputEventDraft.public_text(
        run_id=run_id,
        turn_id=turn_id,
        output_stream_id=stream_id,
        invocation_id=invocation_id,
        source_event_key=source_event_key,
        source=OutputSource.PROVIDER,
        channel=OutputChannel.FINAL,
        delta=text,
        occurred_at=datetime.now(timezone.utc),
    )


def _completed_commit(run_id: str) -> RunCommit:
    return RunCommit(
        terminal_status=RunStatus.DONE,
        final_response="",
        events=(AgentEvent(
            type=CoreEventType.RUN_COMPLETED,
            run_id=run_id,
            payload={"status": RunStatus.DONE.value},
        ),),
    )


def _validated_commit(run_id: str, content: str = '{"answer":"persisted"}') -> RunCommit:
    return RunCommit(
        terminal_status=RunStatus.DONE,
        final_response="",
        validated_result=content,
        events=(AgentEvent(
            type=CoreEventType.RUN_COMPLETED,
            run_id=run_id,
            payload={"status": RunStatus.DONE.value},
        ),),
    )


async def _insert_validated_event(db, run_id: str, **overrides) -> None:
    values = {
        "run_id": run_id,
        "event_type": "run.validated_result",
        "payload_json": json.dumps({
            "schemaVersion": "purra.run-validated-result/v1",
            "content": '{"answer":"persisted"}',
        }),
        "event_id": f"manual-validated-result-{run_id}",
        "turn_id": None,
        "invocation_id": None,
        "output_stream_id": None,
        "sequence": 100,
        "source": "runtime",
        "kind": "run.validated_result",
        "channel": "diagnostic",
        "visibility": "private",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "source_event_key": f"run:{run_id}:validated-result",
    }
    values.update(overrides)
    await db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json, event_id, turn_id, invocation_id, "
        "output_stream_id, sequence, source, kind, channel, visibility, "
        "occurred_at, emitted_at, source_event_key) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        list(values.values()),
    )


def _completed_draft(run_id: str) -> RunLifecycleOutputDraft:
    return RunLifecycleOutputDraft(
        source_event_key=f"run:{run_id}:done",
        status=RunStatus.DONE,
        payload={"status": RunStatus.DONE.value},
        occurred_at=datetime.now(timezone.utc),
    )


def _private_tool_stream(run_id: str) -> OutputStreamSpec:
    return OutputStreamSpec(
        output_stream_id="output-tool-1",
        run_id=run_id,
        turn_id="turn-1",
        invocation_id="invocation-tool-1",
        intent=AgentOutputIntent.STRUCTURED_PRIVATE,
        commit_mode=OutputCommitMode.PRIVATE,
    )


def _private_tool_content(
    run_id: str,
    *,
    source_event_key: str,
    text: str,
) -> AgentOutputEventDraft:
    return AgentOutputEventDraft(
        run_id=run_id,
        turn_id="turn-1",
        output_stream_id="output-tool-1",
        invocation_id="invocation-tool-1",
        source_event_key=source_event_key,
        source=OutputSource.PROVIDER,
        kind=OutputEventKind.PROVIDER_CONTENT_DELTA,
        channel=OutputChannel.DIAGNOSTIC,
        visibility=OutputVisibility.PRIVATE,
        payload={"delta": text},
        occurred_at=datetime.now(timezone.utc),
    )


def _private_tool_call(run_id: str) -> AgentOutputEventDraft:
    return AgentOutputEventDraft(
        run_id=run_id,
        turn_id="turn-1",
        output_stream_id="output-tool-1",
        invocation_id="invocation-tool-1",
        source_event_key="provider:tool:call",
        source=OutputSource.PROVIDER,
        kind=OutputEventKind.PROVIDER_TOOL_CALL_DELTA,
        channel=OutputChannel.DIAGNOSTIC,
        visibility=OutputVisibility.PRIVATE,
        payload={
            "deltas": [{
                "index": 0,
                "id": "call-1",
                "type": "function",
                "name": "readSource",
                "argumentsFragment": "{}",
            }]
        },
        occurred_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_repository_implements_the_output_port_and_creates_schema(
    output_db,
):
    db, _run_id, _runs = output_db
    repository = _repository(db)
    _repository_type, AgentOutputRepository = _repository_types()

    assert isinstance(repository, AgentOutputRepository)
    stream_columns = {
        row["name"]
        for row in await db.fetch_all("PRAGMA table_info(ai_agent_output_streams)")
    }
    event_columns = {
        row["name"]
        for row in await db.fetch_all("PRAGMA table_info(ai_agent_run_events)")
    }

    assert {
        "id",
        "run_id",
        "turn_id",
        "invocation_id",
        "intent",
        "commit_mode",
        "output_protocol",
        "planning_run_id",
        "planning_operation_id",
        "planning_revision",
        "planning_attempt",
        "status",
        "finish_reason",
    }.issubset(stream_columns)
    assert {
        "event_id",
        "turn_id",
        "invocation_id",
        "output_stream_id",
        "sequence",
        "source",
        "kind",
        "channel",
        "visibility",
        "occurred_at",
        "emitted_at",
        "source_event_key",
        "root_run_id",
        "agent_id",
        "parent_run_id",
        "root_sequence",
    }.issubset(event_columns)


@pytest.mark.asyncio
async def test_sqlite_host_adapters_pass_the_shared_conformance_suite(output_db):
    db, _run_id, runs = output_db
    await assert_host_adapters_conform(
        runs=runs,
        outputs=_repository(db, run_repository=runs),
        publisher=InProcessAgentOutputPublisher(),
        session_id=7,
    )


@pytest.mark.asyncio
async def test_planning_stream_identity_and_provider_progress_are_replayable(
    output_db,
):
    db, run_id, runs = output_db
    repository = _repository(db, run_repository=runs)
    operation_id = "planning-operation-1"
    invocation_id = "planning-invocation-1"
    stream_id = "planning-stream-1"
    occurred_at = datetime.now(timezone.utc)

    await repository.append_event(AgentOutputEventDraft(
        run_id=run_id,
        turn_id="turn-1",
        output_stream_id=None,
        invocation_id=None,
        source_event_key=f"operation:{operation_id}:started",
        source=OutputSource.RUNTIME,
        kind=OutputEventKind.OPERATION_STARTED,
        channel=OutputChannel.OPERATION,
        visibility=OutputVisibility.PUBLIC,
        payload={
            "operationId": operation_id,
            "parentOperationId": None,
            "kind": "planning",
            "startedAt": occurred_at.isoformat(),
            "display": {"labelKey": "agent.operation.planning"},
        },
        occurred_at=occurred_at,
    ))
    spec = OutputStreamSpec(
        output_stream_id=stream_id,
        run_id=run_id,
        turn_id="turn-1",
        invocation_id=invocation_id,
        intent=AgentOutputIntent.STRUCTURED_PRIVATE,
        commit_mode=OutputCommitMode.PRIVATE,
        output_protocol=PLANNING_STREAM_SCHEMA,
        planning_scope=PlanningScope(
            run_id=run_id,
            operation_id=operation_id,
            revision=2,
        ),
        planning_attempt=1,
    )
    await repository.open_stream(spec)
    wire = (
        '{"v":1,"type":"progress","text":"正在核对续写范围。"}\n'
        '{"v":1,"type":"progress","text":"正在整理执行步骤。"}\n'
        '{"v":1,"type":"plan","plan":{"needsTodos":false}}\n'
    )
    progress = PlanningStreamParser().feed(wire)
    await repository.append_event(AgentOutputEventDraft(
        run_id=run_id,
        turn_id="turn-1",
        output_stream_id=stream_id,
        invocation_id=invocation_id,
        source_event_key="provider:planning-invocation-1:chunk:1:part:1",
        source=OutputSource.PROVIDER,
        kind=OutputEventKind.PROVIDER_CONTENT_DELTA,
        channel=OutputChannel.DIAGNOSTIC,
        visibility=OutputVisibility.PRIVATE,
        payload={"delta": wire},
        occurred_at=occurred_at,
    ))
    projected = await repository.append_event(AgentOutputEventDraft(
        run_id=run_id,
        turn_id="turn-1",
        output_stream_id=stream_id,
        invocation_id=invocation_id,
        source_event_key="planning:planning-invocation-1:1",
        source=OutputSource.PROVIDER,
        kind=OutputEventKind.PLANNING_PROGRESS,
        channel=OutputChannel.COMMENTARY,
        visibility=OutputVisibility.PUBLIC,
        payload={
            "schemaVersion": PLANNING_STREAM_SCHEMA,
            "operationId": operation_id,
            "revision": 2,
            "attempt": 1,
            **progress[0].to_mapping(),
        },
        occurred_at=occurred_at,
    ))
    with pytest.raises(
        ContractViolationError,
        match="does not match Provider source",
    ):
        await repository.append_event(AgentOutputEventDraft(
            run_id=run_id,
            turn_id="turn-1",
            output_stream_id=stream_id,
            invocation_id=invocation_id,
            source_event_key="planning:planning-invocation-1:2",
            source=OutputSource.PROVIDER,
            kind=OutputEventKind.PLANNING_PROGRESS,
            channel=OutputChannel.COMMENTARY,
            visibility=OutputVisibility.PUBLIC,
            payload={
                "schemaVersion": PLANNING_STREAM_SCHEMA,
                "operationId": operation_id,
                "revision": 2,
                "attempt": 1,
                **progress[1].to_mapping(),
                "text": "已经完成执行。",
            },
            occurred_at=occurred_at,
        ))

    assert projected.payload["text"] == "正在核对续写范围。"
    assert await db.fetch_one(
        "SELECT output_protocol, planning_run_id, planning_operation_id, "
        "planning_revision, planning_attempt FROM ai_agent_output_streams "
        "WHERE id = ?",
        [stream_id],
    ) == {
        "output_protocol": PLANNING_STREAM_SCHEMA,
        "planning_run_id": run_id,
        "planning_operation_id": operation_id,
        "planning_revision": 2,
        "planning_attempt": 1,
    }
    replay = await repository.list_session_events(
        session_id=7,
        after_cursor=0,
    )
    replayed_progress = [
        event for _, event in replay
        if event.kind is OutputEventKind.PLANNING_PROGRESS
    ]
    assert replayed_progress == [projected]
    assert all(
        event.payload.get("delta") != wire
        for _, event in replay
    )


@pytest.mark.asyncio
async def test_schema_backfills_historical_canonical_root_journal(tmp_path: Path):
    original = DatabaseConnection(tmp_path)
    await original.init()
    runs = SqliteRunRepository(original)
    root_id = await runs.create(RunCreateParams(
        session_id=None,
        prompt="historical root",
        mode="agent",
        requested_run_id="historical-root",
        agent_id="historical-agent",
    ))
    timestamp = datetime.now(timezone.utc).isoformat()
    await original.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json, event_id, sequence, source, kind, "
        "channel, visibility, occurred_at, emitted_at, source_event_key) "
        "VALUES (?, 'runtime.event', '{}', 'historical-event', 1, 'runtime', "
        "'runtime.event', 'diagnostic', 'private', ?, ?, 'historical:source')",
        [root_id, timestamp, timestamp],
    )
    await original.close()

    migrated = DatabaseConnection(tmp_path)
    await migrated.init()
    try:
        repository = _repository(migrated)
        journal = await repository.list_root_events(
            root_id,
            after_root_sequence=0,
        )
        assert len(journal) == 1
        assert journal[0].root_run_id == root_id
        assert journal[0].agent_id == "historical-agent"
        assert journal[0].parent_run_id is None
        assert journal[0].root_sequence == 1
        assert journal[0].source_event_key == "historical:source"
    finally:
        await migrated.close()


@pytest.mark.asyncio
async def test_root_journal_migration_receipt_skips_completed_recheck(
    tmp_path: Path,
    monkeypatch,
):
    original = DatabaseConnection(tmp_path)
    await original.init()
    runs = SqliteRunRepository(original)
    run_id = await runs.create(RunCreateParams(
        session_id=None,
        prompt="receipted journal",
        mode="agent",
        requested_run_id="receipted-journal",
    ))
    repository = _repository(original, run_repository=runs)
    await repository.append_event(AgentOutputEventDraft(
        run_id=run_id,
        turn_id=None,
        output_stream_id=None,
        invocation_id=None,
        source_event_key="receipt:event",
        source=OutputSource.RUNTIME,
        kind=OutputEventKind.RUNTIME,
        channel=OutputChannel.DIAGNOSTIC,
        visibility=OutputVisibility.PRIVATE,
        payload={},
        occurred_at=datetime.now(timezone.utc),
    ))
    await original.close()

    migrated = DatabaseConnection(tmp_path)
    await migrated.init()
    assert await migrated.fetch_one(
        "SELECT id FROM app_schema_migrations "
        "WHERE id = 'agent-root-journal-v1'"
    ) == {"id": "agent-root-journal-v1"}
    await migrated.close()

    import database.schema as schema

    async def _unexpected_recheck(_db):
        raise AssertionError("completed migration must not run again")

    monkeypatch.setattr(schema, "_migrate_agent_root_journal", _unexpected_recheck)
    reopened = DatabaseConnection(tmp_path)
    await reopened.init()
    await reopened.close()


@pytest.mark.asyncio
async def test_schema_appends_missing_root_sequences_after_existing_journal(
    tmp_path: Path,
):
    original = DatabaseConnection(tmp_path)
    await original.init()
    runs = SqliteRunRepository(original)
    root_id = await runs.create(RunCreateParams(
        session_id=None,
        prompt="partially migrated root",
        mode="agent",
        requested_run_id="partially-migrated-root",
    ))
    repository = _repository(original, run_repository=runs)
    timestamp = datetime.now(timezone.utc)
    first = await repository.append_event(AgentOutputEventDraft(
        run_id=root_id,
        turn_id=None,
        output_stream_id=None,
        invocation_id=None,
        source_event_key="partial:first",
        source=OutputSource.RUNTIME,
        kind=OutputEventKind.RUNTIME,
        channel=OutputChannel.DIAGNOSTIC,
        visibility=OutputVisibility.PRIVATE,
        payload={},
        occurred_at=timestamp,
    ))
    await original.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json, event_id, sequence, source, kind, "
        "channel, visibility, occurred_at, emitted_at, source_event_key) "
        "VALUES (?, 'runtime.event', '{}', 'partial-missing', 2, 'runtime', "
        "'runtime.event', 'diagnostic', 'private', ?, ?, 'partial:missing')",
        [root_id, timestamp.isoformat(), timestamp.isoformat()],
    )
    second = await repository.append_event(AgentOutputEventDraft(
        run_id=root_id,
        turn_id=None,
        output_stream_id=None,
        invocation_id=None,
        source_event_key="partial:second",
        source=OutputSource.RUNTIME,
        kind=OutputEventKind.RUNTIME,
        channel=OutputChannel.DIAGNOSTIC,
        visibility=OutputVisibility.PRIVATE,
        payload={},
        occurred_at=timestamp,
    ))
    assert (first.root_sequence, second.root_sequence) == (1, 2)
    await original.close()

    migrated = DatabaseConnection(tmp_path)
    await migrated.init()
    try:
        rows = await migrated.fetch_all(
            "SELECT event_id, root_sequence FROM ai_agent_run_events "
            "WHERE run_id = ? ORDER BY root_sequence",
            [root_id],
        )
        assert rows == [
            {"event_id": first.event_id, "root_sequence": 1},
            {"event_id": second.event_id, "root_sequence": 2},
            {"event_id": "partial-missing", "root_sequence": 3},
        ]
    finally:
        await migrated.close()


@pytest.mark.asyncio
async def test_child_events_share_one_atomic_root_journal(output_db):
    db, _run_id, runs = output_db
    root_id = await runs.create(RunCreateParams(
        session_id=7,
        prompt="journal root",
        mode="agent",
        requested_run_id="journal-root",
        agent_id="journal-root-agent",
    ))
    child_ids = tuple([
        await runs.create(RunCreateParams(
            session_id=7,
            prompt=f"journal child {index}",
            mode="agent",
            requested_run_id=f"journal-child-{index}",
            root_run_id=root_id,
            agent_id=f"journal-child-agent-{index}",
            parent_run_id=root_id,
        ))
        for index in (1, 2)
    ])
    repository = _repository(db)
    events = await asyncio.gather(*(
        repository.append_event(AgentOutputEventDraft(
            run_id=child_id,
            turn_id=None,
            output_stream_id=None,
            invocation_id=None,
            source_event_key=f"journal:{child_id}",
            source=OutputSource.RUNTIME,
            kind=OutputEventKind.RUNTIME,
            channel=OutputChannel.DIAGNOSTIC,
            visibility=OutputVisibility.PRIVATE,
            payload={"childId": child_id},
            occurred_at=datetime.now(timezone.utc),
        ))
        for child_id in child_ids
    ))

    assert {event.sequence for event in events} == {1}
    journal = await repository.list_root_events(
        root_id,
        after_root_sequence=0,
    )
    assert [event.root_sequence for event in journal] == [1, 2]
    assert {event.run_id for event in journal} == set(child_ids)
    assert {event.parent_run_id for event in journal} == {root_id}
    assert {
        event.agent_id for event in journal
    } == {"journal-child-agent-1", "journal-child-agent-2"}
    assert await repository.list_root_events(
        root_id,
        after_root_sequence=1,
    ) == (journal[1],)
    with pytest.raises(ContractViolationError) as conflict:
        await repository.list_root_events(
            child_ids[0],
            after_root_sequence=0,
        )
    assert conflict.value.code == "run_scope_conflict"


@pytest.mark.asyncio
async def test_run_begin_and_canonical_lifecycle_commit_together(output_db):
    db, _run_id, runs = output_db
    repository = _repository(db, run_repository=runs)

    begun, output = await repository.begin_run_lifecycle(
        RunCreateParams(
            session_id=8,
            prompt="开始正式审阅",
            mode="agent",
            turn_id="turn-review",
        ),
        AgentEvent(
            type=CoreEventType.RUN_STARTED,
            payload={"status": RunStatus.RUNNING.value},
        ),
    )

    run = await db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [begun.run_id],
    )
    legacy = await db.fetch_all(
        "SELECT id FROM ai_agent_run_events WHERE run_id = ? "
        "AND event_id IS NULL",
        [begun.run_id],
    )
    assert run == {"status": RunStatus.RUNNING.value}
    assert begun.event.run_id == begun.run_id
    assert output.kind is OutputEventKind.RUN_LIFECYCLE
    assert output.turn_id == "turn-review"
    assert output.payload["status"] == RunStatus.RUNNING.value
    assert await repository.list_events(
        begun.run_id,
        after_sequence=0,
    ) == (output,)
    assert legacy == []


@pytest.mark.asyncio
async def test_session_cursor_replays_public_events_without_domain_coupling(
    output_db,
):
    db, run_id, _runs = output_db
    repository = _repository(db)
    await repository.open_stream(_stream(run_id))
    public = await repository.append_event(
        _delta(run_id, source_event_key="provider:session:1", text="甲")
    )

    page = await repository.list_session_events(
        session_id=7,
        after_cursor=0,
    )

    assert len(page) == 1
    assert page[0][0] > 0
    assert page[0][1] == public
    assert await repository.list_session_events(
        session_id=7,
        after_cursor=page[0][0],
    ) == ()


@pytest.mark.asyncio
async def test_committed_private_tool_stream_publishes_replayable_commentary(
    output_db,
):
    db, run_id, _runs = output_db
    repository = _repository(db)
    await repository.open_stream(_private_tool_stream(run_id))
    await repository.append_event(_private_tool_content(
        run_id,
        source_event_key="provider:tool:1",
        text="我先核对",
    ))
    await repository.append_event(_private_tool_content(
        run_id,
        source_event_key="provider:tool:2",
        text="现有资料。",
    ))
    await repository.append_event(_private_tool_call(run_id))
    private_commit = await repository.commit_stream(
        "output-tool-1",
        ModelFinishReason.TOOL_CALLS,
    )

    published = await repository.publish_stream_content_as_commentary(
        "output-tool-1"
    )
    repeated = await repository.publish_stream_content_as_commentary(
        "output-tool-1"
    )

    assert repeated == published
    assert len(published) == 2
    commentary, committed = published
    assert commentary.sequence > private_commit.sequence
    assert commentary.source is OutputSource.PROVIDER
    assert commentary.kind is OutputEventKind.PROVIDER_CONTENT_DELTA
    assert commentary.channel is OutputChannel.COMMENTARY
    assert commentary.visibility is OutputVisibility.PUBLIC
    assert commentary.payload == {"delta": "我先核对现有资料。"}
    assert committed.sequence > commentary.sequence
    assert committed.kind is OutputEventKind.STREAM_COMMITTED
    assert committed.channel is OutputChannel.COMMENTARY
    assert committed.visibility is OutputVisibility.PUBLIC
    assert tuple(
        event
        for _cursor, event in await repository.list_session_events(
            session_id=7,
            after_cursor=0,
        )
    ) == published


@pytest.mark.asyncio
async def test_append_allocates_run_sequence_and_duplicate_source_is_idempotent(
    output_db,
):
    db, run_id, _runs = output_db
    repository = _repository(db)
    await repository.open_stream(_stream(run_id))

    first = await repository.append_event(
        _delta(run_id, source_event_key="provider:1", text="甲")
    )
    duplicate = await repository.append_event(
        _delta(run_id, source_event_key="provider:1", text="甲")
    )
    second = await repository.append_event(
        _delta(run_id, source_event_key="provider:2", text="乙")
    )

    assert first.sequence == 1
    assert duplicate.event_id == first.event_id
    assert duplicate.sequence == first.sequence
    assert second.sequence == 2
    assert await repository.list_events(run_id, after_sequence=0) == (
        first,
        second,
    )
    assert await repository.list_events(run_id, after_sequence=1) == (second,)


@pytest.mark.asyncio
async def test_sequence_is_shared_by_parallel_streams_in_the_same_turn(
    output_db,
):
    db, run_id, _runs = output_db
    repository = _repository(db)
    await repository.open_stream(_stream(run_id))
    await repository.open_stream(
        _stream(
            run_id,
            stream_id="output-2",
            invocation_id="invocation-2",
        )
    )

    first = await repository.append_event(
        _delta(run_id, source_event_key="provider:1", text="甲")
    )
    second = await repository.append_event(
        _delta(
            run_id,
            source_event_key="provider:2",
            text="乙",
            stream_id="output-2",
            invocation_id="invocation-2",
        )
    )

    assert (first.sequence, second.sequence) == (1, 2)


@pytest.mark.asyncio
async def test_sequence_is_shared_by_model_tasks_across_turns_in_the_same_run(
    output_db,
):
    db, run_id, _runs = output_db
    repository = _repository(db)
    await repository.open_stream(_stream(run_id))
    await repository.open_stream(
        _stream(
            run_id,
            stream_id="output-2",
            invocation_id="invocation-2",
            turn_id="turn-2",
        )
    )

    first = await repository.append_event(
        _delta(run_id, source_event_key="provider:1", text="甲")
    )
    second = await repository.append_event(
        _delta(
            run_id,
            source_event_key="provider:2",
            text="乙",
            stream_id="output-2",
            invocation_id="invocation-2",
            turn_id="turn-2",
        )
    )

    assert (first.sequence, second.sequence) == (1, 2)
    assert await repository.list_events(run_id, after_sequence=1) == (second,)


@pytest.mark.asyncio
async def test_commit_final_stream_projects_one_conversation_answer_atomically(
    output_db,
):
    db, run_id, _runs = output_db
    repository = _repository(db)
    await repository.open_stream(_stream(run_id))
    await repository.append_event(
        _delta(run_id, source_event_key="provider:1", text="已完成")
    )
    await repository.append_event(
        _delta(run_id, source_event_key="provider:2", text="审阅。")
    )

    committed = await repository.commit_stream(
        "output-1",
        ModelFinishReason.STOP,
    )
    run = await db.fetch_one(
        "SELECT conversation_id, final_response FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )
    conversation = await db.fetch_one(
        "SELECT chapter_id, prompt, response FROM ai_conversations WHERE id = ?",
        [run["conversation_id"]],
    )

    assert committed.payload["finishReason"] == "stop"
    assert run["final_response"] == "已完成审阅。"
    assert conversation == {
        "chapter_id": "chapter-7",
        "prompt": "审阅当前正文",
        "response": "已完成审阅。",
    }


@pytest.mark.asyncio
async def test_commit_final_stream_completes_existing_resolution_shell(
    output_db,
):
    db, run_id, _runs = output_db
    proposal_id = "setting-proposal:v1:mid-run"
    shell_id = await db.execute_and_get_id(
        "INSERT INTO ai_conversations "
        "(session_id, chapter_id, prompt, response, model, agent_process) "
        "VALUES (7, 'wrong-chapter', 'stale', '', NULL, ?)",
        [
            '{"settingDiff":{"resolutions":{"'
            + proposal_id
            + '":{"status":"rejected"}}}}'
        ],
    )
    await db.execute(
        "UPDATE ai_agent_runs SET conversation_id = ? WHERE id = ?",
        [shell_id, run_id],
    )
    repository = _repository(db)
    await repository.open_stream(_stream(run_id))
    await repository.append_event(
        _delta(run_id, source_event_key="provider:shell:1", text="服务端终稿")
    )

    await repository.commit_stream("output-1", ModelFinishReason.STOP)

    projected = await db.fetch_one(
        "SELECT chapter_id, prompt, response, agent_process "
        "FROM ai_conversations WHERE id = ?",
        [shell_id],
    )
    assert projected["chapter_id"] == "chapter-7"
    assert projected["prompt"] == "审阅当前正文"
    assert projected["response"] == "服务端终稿"
    assert proposal_id in projected["agent_process"]


@pytest.mark.asyncio
async def test_failed_conversation_projection_rolls_back_stream_commit(
    output_db,
    monkeypatch: pytest.MonkeyPatch,
):
    db, run_id, _runs = output_db
    repository = _repository(db)
    await repository.open_stream(_stream(run_id))
    await repository.append_event(
        _delta(run_id, source_event_key="provider:1", text="不可半提交")
    )

    async def fail_projection(*_args, **_kwargs):
        raise RuntimeError("projection failed")

    monkeypatch.setattr(db, "execute_and_get_id", fail_projection)
    with pytest.raises(RuntimeError, match="projection failed"):
        await repository.commit_stream("output-1", ModelFinishReason.STOP)

    stream = await db.fetch_one(
        "SELECT status, finish_reason FROM ai_agent_output_streams WHERE id = ?",
        ["output-1"],
    )
    run = await db.fetch_one(
        "SELECT conversation_id, final_response FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )
    committed_events = await db.fetch_all(
        "SELECT id FROM ai_agent_run_events "
        "WHERE output_stream_id = ? AND kind = ?",
        ["output-1", "stream.committed"],
    )

    assert stream == {"status": "open", "finish_reason": None}
    assert run == {"conversation_id": None, "final_response": ""}
    assert committed_events == []


@pytest.mark.asyncio
async def test_run_terminal_and_canonical_event_commit_together(output_db):
    db, run_id, runs = output_db
    repository = _repository(db, run_repository=runs)
    commit = RunCommit(
        terminal_status=RunStatus.DONE,
        final_response="",
        events=(
            AgentEvent(
                type=CoreEventType.RUN_COMPLETED,
                run_id=run_id,
                payload={"status": RunStatus.DONE.value},
            ),
        ),
    )

    events = await repository.commit_run_lifecycle(
        run_id,
        commit,
        RunLifecycleOutputDraft(
            source_event_key=f"run:{run_id}:done",
            status=RunStatus.DONE,
            payload={"status": RunStatus.DONE.value},
            occurred_at=datetime.now(timezone.utc),
        ),
    )

    run = await db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )
    legacy_terminal = await db.fetch_all(
        "SELECT id FROM ai_agent_run_events "
        "WHERE run_id = ? AND event_id IS NULL AND event_type = ?",
        [run_id, CoreEventType.RUN_COMPLETED.value],
    )
    assert run == {"status": RunStatus.DONE.value}
    assert await repository.list_events(run_id, after_sequence=0) == events
    assert legacy_terminal == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "event_type"),
    (
        (RunStatus.DONE, CoreEventType.RUN_COMPLETED),
        (RunStatus.FAILED, CoreEventType.RUN_FAILED),
        (RunStatus.BLOCKED, CoreEventType.RUN_BLOCKED),
        (RunStatus.CANCELED, CoreEventType.RUN_CANCELED),
    ),
)
async def test_every_terminal_commit_releases_run_artifact_claim(
    output_db,
    status,
    event_type,
):
    db, run_id, runs = output_db
    await _seed_artifact_claim(db, run_id)
    repository = _repository(db, run_repository=runs)
    await repository.open_stream(_stream(run_id))
    event = AgentEvent(
        type=event_type,
        run_id=run_id,
        payload={"status": status.value},
    )

    committed = await repository.commit_run_lifecycle(
        run_id,
        RunCommit(
            terminal_status=status,
            error=("terminal failure" if status is RunStatus.FAILED else None),
            events=(event,),
        ),
        RunLifecycleOutputDraft(
            source_event_key=f"run:{run_id}:{status.value}",
            status=status,
            payload={"status": status.value},
            occurred_at=datetime.now(timezone.utc),
        ),
    )

    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifact_claims WHERE run_id = ?",
        [run_id],
    ) == {"count": 0}
    assert committed[-2].kind is OutputEventKind.STREAM_ABORTED
    assert committed[-2].payload == {
        "errorCode": "run_terminalized",
        "cause": "run_terminal_commit",
        "runStatus": status.value,
    }
    assert await db.fetch_one(
        "SELECT status, error_code FROM ai_agent_output_streams WHERE id = ?",
        ["output-1"],
    ) == {"status": "aborted", "error_code": "run_terminalized"}


@pytest.mark.asyncio
async def test_terminal_event_failure_rolls_back_artifact_claim_cleanup(output_db):
    db, run_id, runs = output_db
    await _seed_artifact_claim(db, run_id)
    await db.execute(
        "CREATE TRIGGER reject_terminal_output BEFORE INSERT ON "
        "ai_agent_run_events WHEN NEW.source_event_key = 'run:" + run_id
        + ":done' BEGIN SELECT RAISE(ABORT, 'terminal output rejected'); END"
    )
    repository = _repository(db, run_repository=runs)
    await repository.open_stream(_stream(run_id))
    event = AgentEvent(
        type=CoreEventType.RUN_COMPLETED,
        run_id=run_id,
        payload={"status": RunStatus.DONE.value},
    )

    with pytest.raises(Exception, match="terminal output rejected"):
        await repository.commit_run_lifecycle(
            run_id,
            RunCommit(terminal_status=RunStatus.DONE, events=(event,)),
            RunLifecycleOutputDraft(
                source_event_key=f"run:{run_id}:done",
                status=RunStatus.DONE,
                payload={"status": RunStatus.DONE.value},
                occurred_at=datetime.now(timezone.utc),
            ),
        )

    assert await db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [run_id],
    ) == {"status": "running"}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_artifact_claims WHERE run_id = ?",
        [run_id],
    ) == {"count": 1}
    assert await db.fetch_one(
        "SELECT status, error_code FROM ai_agent_output_streams WHERE id = ?",
        ["output-1"],
    ) == {"status": "open", "error_code": None}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events "
        "WHERE output_stream_id = ? AND kind = ?",
        ["output-1", OutputEventKind.STREAM_ABORTED.value],
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_validated_result_is_private_and_atomically_readable(output_db):
    db, run_id, runs = output_db
    repository = _repository(db, run_repository=runs)
    content = '{"answer":"persisted"}'

    events = await repository.commit_run_lifecycle(
        run_id,
        _validated_commit(run_id, content),
        _completed_draft(run_id),
    )

    assert await repository.load_validated_result(run_id) == content
    validated = [
        event for event in events
        if event.kind is OutputEventKind.RUN_VALIDATED_RESULT
    ]
    assert len(validated) == 1
    assert validated[0].source is OutputSource.RUNTIME
    assert validated[0].channel is OutputChannel.DIAGNOSTIC
    assert validated[0].visibility is OutputVisibility.PRIVATE
    assert validated[0].output_stream_id is None
    assert validated[0].invocation_id is None
    assert validated[0].payload == {
        "schemaVersion": "purra.run-validated-result/v1",
        "content": content,
    }
    public_events = await repository.list_session_events(
        session_id=7,
        after_cursor=0,
    )
    assert all(
        event.kind is not OutputEventKind.RUN_VALIDATED_RESULT
        for _, event in public_events
    )


@pytest.mark.asyncio
async def test_validated_terminal_commit_exact_replay_is_idempotent(output_db):
    db, run_id, runs = output_db
    repository = _repository(db, run_repository=runs)
    await repository.open_stream(_stream(run_id))
    commit = _validated_commit(run_id)
    draft = _completed_draft(run_id)

    first = await repository.commit_run_lifecycle(run_id, commit, draft)
    replay = await repository.commit_run_lifecycle(run_id, commit, draft)

    assert replay == first
    assert len(first) == 3
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events "
        "WHERE run_id = ? AND kind = ?",
        [run_id, OutputEventKind.RUN_VALIDATED_RESULT.value],
    ) == {"count": 1}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events "
        "WHERE run_id = ? AND kind = ?",
        [run_id, OutputEventKind.STREAM_ABORTED.value],
    ) == {"count": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first", "replay"),
    (
        ("validated", "none"),
        ("none", "validated"),
        ("validated", "other"),
    ),
)
async def test_terminal_commit_replay_rejects_validated_identity_change(
    output_db,
    first: str,
    replay: str,
):
    db, run_id, runs = output_db
    repository = _repository(db, run_repository=runs)
    commits = {
        "none": _completed_commit(run_id),
        "validated": _validated_commit(run_id, '{"answer":"persisted"}'),
        "other": _validated_commit(run_id, '{"answer":"different"}'),
    }
    draft = _completed_draft(run_id)
    await repository.commit_run_lifecycle(run_id, commits[first], draft)

    with pytest.raises(ContractViolationError, match="validated result"):
        await repository.commit_run_lifecycle(run_id, commits[replay], draft)


@pytest.mark.asyncio
async def test_validated_result_readback_fails_closed_when_missing(output_db):
    db, run_id, runs = output_db
    repository = _repository(db, run_repository=runs)
    await repository.commit_run_lifecycle(
        run_id,
        _completed_commit(run_id),
        _completed_draft(run_id),
    )

    with pytest.raises(ContractViolationError, match="validated result"):
        await repository.load_validated_result(run_id)


@pytest.mark.asyncio
async def test_validated_result_readback_fails_closed_when_duplicated(output_db):
    db, run_id, runs = output_db
    repository = _repository(db, run_repository=runs)
    await repository.commit_run_lifecycle(
        run_id,
        _validated_commit(run_id),
        _completed_draft(run_id),
    )
    await _insert_validated_event(
        db,
        run_id,
        event_id="duplicate-validated-result",
        source_event_key=f"run:{run_id}:validated-result:duplicate",
    )

    with pytest.raises(ContractViolationError, match="exactly one"):
        await repository.load_validated_result(run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("source_event_key", "run:wrong:validated-result"),
        ("source", "provider"),
        ("kind", "runtime.event"),
        ("turn_id", "wrong-turn"),
        ("channel", "final"),
        ("visibility", "public"),
    ),
)
async def test_validated_result_readback_rejects_wrong_scope(
    output_db,
    column: str,
    value: str,
):
    db, run_id, runs = output_db
    repository = _repository(db, run_repository=runs)
    await repository.commit_run_lifecycle(
        run_id,
        _completed_commit(run_id),
        _completed_draft(run_id),
    )
    await _insert_validated_event(db, run_id, **{column: value})

    with pytest.raises(ContractViolationError, match="validated result"):
        await repository.load_validated_result(run_id)


@pytest.mark.asyncio
async def test_validated_result_readback_never_crosses_run_scope(output_db):
    db, run_id, runs = output_db
    repository = _repository(db, run_repository=runs)
    other_run_id = await runs.create(
        RunCreateParams(session_id=7, prompt="other", mode="agent")
    )
    await repository.commit_run_lifecycle(
        run_id,
        _completed_commit(run_id),
        _completed_draft(run_id),
    )
    await _insert_validated_event(db, other_run_id)

    with pytest.raises(ContractViolationError, match="validated result"):
        await repository.load_validated_result(run_id)


@pytest.mark.asyncio
async def test_validated_result_and_terminal_state_rollback_together(
    output_db,
    monkeypatch: pytest.MonkeyPatch,
):
    db, run_id, runs = output_db
    repository = _repository(db, run_repository=runs)
    original_append = repository._append_event_in_transaction

    async def fail_terminal_event(draft):
        if draft.kind is OutputEventKind.RUN_LIFECYCLE:
            raise RuntimeError("terminal journal unavailable")
        return await original_append(draft)

    monkeypatch.setattr(
        repository,
        "_append_event_in_transaction",
        fail_terminal_event,
    )
    with pytest.raises(RuntimeError, match="terminal journal unavailable"):
        await repository.commit_run_lifecycle(
            run_id,
            _validated_commit(run_id),
            _completed_draft(run_id),
        )

    assert await db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [run_id],
    ) == {"status": RunStatus.RUNNING.value}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events "
        "WHERE run_id = ? AND kind = ?",
        [run_id, OutputEventKind.RUN_VALIDATED_RESULT.value],
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_retryable_root_projection_retries_the_complete_transaction_once(
    output_db,
):
    class FailOnceProjector:
        def __init__(self):
            self.calls = 0

        async def project(self, projected_run_id, _commit):
            self.calls += 1
            await db.execute(
                "INSERT INTO projection_test_effects (id) VALUES (?)",
                [f"projection:{projected_run_id}"],
            )
            if self.calls == 1:
                raise RunCommitProjectionError(
                    "temporary projection failure",
                    retryable=True,
                )

    db, run_id, runs = output_db
    await db.execute("CREATE TABLE projection_test_effects (id TEXT PRIMARY KEY)")
    projector = FailOnceProjector()
    repository = _repository(
        db,
        run_repository=runs,
        run_commit_projector=projector,
    )
    commit = _completed_commit(run_id)
    draft = _completed_draft(run_id)

    events = await repository.commit_run_lifecycle(run_id, commit, draft)

    assert projector.calls == 2
    assert await db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [run_id],
    ) == {"status": "done"}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events "
        "WHERE source_event_key = ?",
        [draft.source_event_key],
    ) == {"count": 1}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM projection_test_effects WHERE id = ?",
        [f"projection:{run_id}"],
    ) == {"count": 1}
    replay = await repository.commit_run_lifecycle(run_id, commit, draft)
    assert replay == events
    assert projector.calls == 2


@pytest.mark.asyncio
async def test_permanent_retryable_projection_is_bounded_and_rolls_back(
    output_db,
):
    class AlwaysTransientProjector:
        def __init__(self):
            self.calls = 0

        async def project(self, projected_run_id, _commit):
            self.calls += 1
            await db.execute(
                "INSERT INTO projection_test_effects (id) VALUES (?)",
                [f"projection:{projected_run_id}:{self.calls}"],
            )
            raise RunCommitProjectionError(
                "persistent transient projection failure",
                retryable=True,
            )

    db, run_id, runs = output_db
    await db.execute("CREATE TABLE projection_test_effects (id TEXT PRIMARY KEY)")
    projector = AlwaysTransientProjector()
    repository = _repository(
        db,
        run_repository=runs,
        run_commit_projector=projector,
    )

    with pytest.raises(
        RunCommitProjectionError,
        match="persistent transient",
    ):
        await repository.commit_run_lifecycle(
            run_id,
            _completed_commit(run_id),
            _completed_draft(run_id),
        )

    assert projector.calls == 3
    assert await db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [run_id],
    ) == {"status": "running"}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_events "
        "WHERE source_event_key = ?",
        [f"run:{run_id}:done"],
    ) == {"count": 0}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS count FROM projection_test_effects WHERE id LIKE ?",
        [f"projection:{run_id}:%"],
    ) == {"count": 0}


@pytest.mark.asyncio
async def test_nonretryable_projection_failure_runs_once_and_rolls_back(
    output_db,
):
    class InvalidProjection:
        def __init__(self):
            self.calls = 0

        async def project(self, _run_id, _commit):
            self.calls += 1
            raise RuntimeError("invalid projection identity")

    db, run_id, runs = output_db
    projector = InvalidProjection()
    repository = _repository(
        db,
        run_repository=runs,
        run_commit_projector=projector,
    )

    with pytest.raises(
        RunCommitProjectionError,
        match="projector rejected",
    ) as raised:
        await repository.commit_run_lifecycle(
            run_id,
            _completed_commit(run_id),
            _completed_draft(run_id),
        )

    assert projector.calls == 1
    assert raised.value.retryable is False
    assert await db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [run_id],
    ) == {"status": "running"}


@pytest.mark.asyncio
async def test_projection_retry_never_overwrites_a_concurrent_terminal_commit(
    output_db,
):
    db, run_id, runs = output_db
    competitor = _repository(db, run_repository=runs)
    competing_task = None

    class YieldToConcurrentTerminal:
        def __init__(self):
            self.calls = 0

        async def project(self, _run_id, _commit):
            nonlocal competing_task
            self.calls += 1
            competing_task = asyncio.create_task(
                competitor.commit_run_lifecycle(
                    run_id,
                    RunCommit(
                        terminal_status=RunStatus.CANCELED,
                        events=(AgentEvent(
                            type=CoreEventType.RUN_CANCELED,
                            run_id=run_id,
                            payload={"status": RunStatus.CANCELED.value},
                        ),),
                    ),
                    RunLifecycleOutputDraft(
                        source_event_key=f"run:{run_id}:canceled",
                        status=RunStatus.CANCELED,
                        payload={"status": RunStatus.CANCELED.value},
                        occurred_at=datetime.now(timezone.utc),
                    ),
                )
            )
            await asyncio.sleep(0)
            raise RunCommitProjectionError(
                "yield terminal ownership",
                retryable=True,
            )

    projector = YieldToConcurrentTerminal()
    repository = _repository(
        db,
        run_repository=runs,
        run_commit_projector=projector,
    )

    with pytest.raises(ContractViolationError, match="terminal run"):
        await repository.commit_run_lifecycle(
            run_id,
            _completed_commit(run_id),
            _completed_draft(run_id),
        )
    assert competing_task is not None
    await competing_task

    assert projector.calls == 1
    assert await db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [run_id],
    ) == {"status": "canceled"}
    assert await db.fetch_one(
        "SELECT COUNT(*) AS canceled, "
        "SUM(CASE WHEN source_event_key = ? THEN 1 ELSE 0 END) AS done "
        "FROM ai_agent_run_events WHERE run_id = ? AND kind = 'run.lifecycle'",
        [f"run:{run_id}:done", run_id],
    ) == {"canceled": 1, "done": 0}


@pytest.mark.asyncio
async def test_cancel_fence_rejects_done_before_run_projector(output_db):
    db, run_id, runs = output_db

    class RecordingProjector:
        def __init__(self):
            self.calls = 0

        async def project(self, _run_id, _commit):
            self.calls += 1

    projector = RecordingProjector()
    repository = _repository(
        db,
        run_repository=runs,
        run_commit_projector=projector,
    )
    await db.execute(
        "UPDATE ai_agent_runs SET cancel_requested_at_ms = 1, "
        "cancellation_epoch = 1 WHERE id = ?",
        [run_id],
    )

    with pytest.raises(RunCancellationConflictError, match="cancel-requested"):
        await repository.commit_run_lifecycle(
            run_id,
            _completed_commit(run_id),
            _completed_draft(run_id),
        )

    assert projector.calls == 0
    assert await db.fetch_one(
        "SELECT status, cancel_requested_at_ms FROM ai_agent_runs WHERE id = ?",
        [run_id],
    ) == {"status": "running", "cancel_requested_at_ms": 1}


@pytest.mark.asyncio
async def test_source_key_collision_rolls_back_terminal_transition(output_db):
    db, run_id, runs = output_db
    repository = _repository(db, run_repository=runs)
    other_run_id = await runs.create(
        RunCreateParams(session_id=8, prompt="其他任务", mode="agent")
    )
    collision_key = "runtime:terminal:collision"
    await repository.append_event(
        AgentOutputEventDraft(
            run_id=other_run_id,
            turn_id=None,
            output_stream_id=None,
            invocation_id=None,
            source_event_key=collision_key,
            source=OutputSource.RUNTIME,
            kind=OutputEventKind.RUN_LIFECYCLE,
            channel=OutputChannel.LIFECYCLE,
            visibility=OutputVisibility.PRIVATE,
            payload={"status": RunStatus.RUNNING.value},
            occurred_at=datetime.now(timezone.utc),
        )
    )
    commit = RunCommit(
        terminal_status=RunStatus.DONE,
        final_response="",
        events=(
            AgentEvent(
                type=CoreEventType.RUN_COMPLETED,
                run_id=run_id,
                payload={"status": RunStatus.DONE.value},
            ),
        ),
    )

    with pytest.raises(ContractViolationError, match="source event key"):
        await repository.commit_run_lifecycle(
            run_id,
            commit,
            RunLifecycleOutputDraft(
                source_event_key=collision_key,
                status=RunStatus.DONE,
                payload={"status": RunStatus.DONE.value},
                occurred_at=datetime.now(timezone.utc),
            ),
        )

    run = await db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [run_id],
    )
    assert run == {"status": RunStatus.RUNNING.value}


@pytest.mark.asyncio
async def test_schema_migrates_existing_lifecycle_rows_into_canonical_replay(
    tmp_path: Path,
):
    db = DatabaseConnection(tmp_path)
    await db.init()
    runs = SqliteRunRepository(db)
    run_id = await runs.create(
        RunCreateParams(session_id=9, prompt="历史任务", mode="agent")
    )
    await db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json) VALUES (?, ?, ?)",
        [
            run_id,
            CoreEventType.RUN_STARTED.value,
            '{"status":"running"}',
        ],
    )
    await db.close()

    reopened = DatabaseConnection(tmp_path)
    await reopened.init()
    try:
        events = await _repository(reopened).list_events(
            run_id,
            after_sequence=0,
        )
        assert len(events) == 1
        assert events[0].source is OutputSource.RUNTIME
        assert events[0].kind is OutputEventKind.RUN_LIFECYCLE
        assert events[0].sequence == 1
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_domain_effect_projects_inside_the_event_transaction(output_db):
    class RecordingProjector:
        def __init__(self):
            self.effects = []

        async def project(self, run_id, effect):
            self.effects.append((run_id, effect))

    db, run_id, _runs = output_db
    projector = RecordingProjector()
    repository = _repository(db, domain_projector=projector)

    event = await repository.append_event(
        AgentOutputEventDraft(
            run_id=run_id,
            turn_id="turn-1",
            output_stream_id=None,
            invocation_id=None,
            source_event_key="domain:artifact:1",
            source=OutputSource.DOMAIN,
            kind=OutputEventKind.DOMAIN_EFFECT,
            channel=OutputChannel.DIAGNOSTIC,
            visibility=OutputVisibility.PRIVATE,
            payload={
                "type": "artifact.finalized",
                "payload": {"artifactKey": "candidate"},
            },
            occurred_at=datetime.now(timezone.utc),
        )
    )

    assert event.kind is OutputEventKind.DOMAIN_EFFECT
    assert len(projector.effects) == 1
    projected_run_id, projected = projector.effects[0]
    assert projected_run_id == run_id
    assert projected.effect.type == "artifact.finalized"
    assert projected.effect.payload == {"artifactKey": "candidate"}
