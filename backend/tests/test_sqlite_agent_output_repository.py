from __future__ import annotations

import importlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from purra.contracts import ModelFinishReason, RunCreateParams, RunStatus
from purra.errors import ContractViolationError
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
    runs = SqliteRunRepository(db)
    run_id = await runs.create(
        RunCreateParams(session_id=7, prompt="审阅当前正文", mode="agent")
    )
    try:
        yield db, run_id, runs
    finally:
        await db.close()


def _repository(db, *, run_repository=None, domain_projector=None):
    SqliteAgentOutputRepository, _port = _repository_types()
    return SqliteAgentOutputRepository(
        db,
        run_repository=run_repository,
        domain_projector=domain_projector,
    )


def _stream(
    run_id: str,
    *,
    stream_id: str = "output-1",
    invocation_id: str = "invocation-1",
) -> OutputStreamSpec:
    return OutputStreamSpec(
        output_stream_id=stream_id,
        run_id=run_id,
        turn_id="turn-1",
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
) -> AgentOutputEventDraft:
    return AgentOutputEventDraft.public_text(
        run_id=run_id,
        turn_id="turn-1",
        output_stream_id=stream_id,
        invocation_id=invocation_id,
        source_event_key=source_event_key,
        source=OutputSource.PROVIDER,
        channel=OutputChannel.FINAL,
        delta=text,
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
    }.issubset(event_columns)


@pytest.mark.asyncio
async def test_append_allocates_turn_sequence_and_duplicate_source_is_idempotent(
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
        "SELECT prompt, response FROM ai_conversations WHERE id = ?",
        [run["conversation_id"]],
    )

    assert committed.payload["finishReason"] == "stop"
    assert run["final_response"] == "已完成审阅。"
    assert conversation == {
        "prompt": "审阅当前正文",
        "response": "已完成审阅。",
    }


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

    event = await repository.commit_run_lifecycle(
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
    assert await repository.list_events(run_id, after_sequence=0) == (event,)
    assert legacy_terminal == []


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
