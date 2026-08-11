from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from purra.contracts import RunCreateParams, RunStatus
from purra.errors import ContractViolationError
from purra.events import AgentEvent, CoreEventType
from purra.ports import RunCommit
from purra.run_controller import AgentRunController
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository


@pytest_asyncio.fixture
async def sqlite_repository(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield SqliteRunRepository(db), db
    finally:
        await db.close()


class RecordingSink:
    def __init__(self):
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_domain_projector_cannot_replace_event_envelope(tmp_path: Path):
    class ReplacingProjector:
        async def project(self, run_id, event):
            return AgentEvent(
                type=event.type,
                run_id=run_id,
                payload={"replacement": True},
            )

    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        repository = SqliteRunRepository(
            db,
            event_projector=ReplacingProjector(),
        )
        run_id = await repository.create(
            RunCreateParams(session_id=None, prompt="project", mode="agent")
        )
        event = AgentEvent(
            type=CoreEventType.MODEL_CONTENT_DELTA,
            run_id=run_id,
            payload={"delta": "original"},
        )

        with pytest.raises(
            ContractViolationError,
            match="must not replace event envelope",
        ):
            await repository.append_event(run_id, event)

        rows = await db.fetch_all(
            "SELECT id FROM ai_agent_run_events WHERE run_id = ?",
            [run_id],
        )
        assert rows == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_concurrent_terminal_commits_have_one_winner_and_one_outbox_event(
    sqlite_repository,
):
    repository, db = sqlite_repository
    begun = await repository.begin(
        RunCreateParams(session_id=None, prompt="race", mode="agent"),
        AgentEvent(
            type=CoreEventType.RUN_STARTED,
            payload={"status": RunStatus.RUNNING.value},
        ),
    )

    done_event = AgentEvent(
        type=CoreEventType.RUN_COMPLETED,
        run_id=begun.run_id,
        payload={"status": RunStatus.DONE.value},
    )
    canceled_event = AgentEvent(
        type=CoreEventType.RUN_CANCELED,
        run_id=begun.run_id,
        payload={"status": RunStatus.CANCELED.value},
    )
    results = await asyncio.gather(
        repository.commit(
            begun.run_id,
            RunCommit(
                terminal_status=RunStatus.DONE,
                final_response="done",
                events=(done_event,),
            ),
        ),
        repository.commit(
            begun.run_id,
            RunCommit(
                terminal_status=RunStatus.CANCELED,
                events=(canceled_event,),
            ),
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, tuple) for result in results) == 1
    assert sum(isinstance(result, ContractViolationError) for result in results) == 1
    run = await db.fetch_one(
        "SELECT status FROM ai_agent_runs WHERE id = ?",
        [begun.run_id],
    )
    events = await db.fetch_all(
        "SELECT event_type FROM ai_agent_run_events WHERE run_id = ? ORDER BY id",
        [begun.run_id],
    )
    terminal_types = {
        CoreEventType.RUN_COMPLETED.value,
        CoreEventType.RUN_CANCELED.value,
    }
    persisted_terminal_events = [
        event["event_type"]
        for event in events
        if event["event_type"] in terminal_types
    ]

    assert run is not None
    assert run["status"] in {RunStatus.DONE.value, RunStatus.CANCELED.value}
    assert len(persisted_terminal_events) == 1
    assert (
        persisted_terminal_events[0] == CoreEventType.RUN_COMPLETED.value
    ) is (run["status"] == RunStatus.DONE.value)


@pytest.mark.asyncio
async def test_double_cancel_applies_durable_sqlite_receipt_then_cancels_caller(
    sqlite_repository,
    monkeypatch: pytest.MonkeyPatch,
):
    repository, db = sqlite_repository
    sink = RecordingSink()
    controller = AgentRunController(repository=repository, event_sink=sink)
    await controller.start(
        RunCreateParams(session_id=None, prompt="double cancel", mode="agent")
    )

    connection = db._ensure_conn()
    original_commit = connection.commit
    durable = asyncio.Event()
    release_commit_ack = asyncio.Event()
    commit_cancel_count = 0

    async def commit_then_hold_ack():
        nonlocal commit_cancel_count
        await original_commit()
        durable.set()
        try:
            await release_commit_ack.wait()
        except asyncio.CancelledError:
            commit_cancel_count += 1
            raise

    monkeypatch.setattr(connection, "commit", commit_then_hold_ack)
    completion = asyncio.create_task(controller.complete("durable answer"))
    await asyncio.wait_for(durable.wait(), timeout=1)

    completion.cancel()
    await asyncio.sleep(0)
    completion.cancel()
    await asyncio.sleep(0)

    assert not completion.done()
    assert commit_cancel_count == 0

    release_commit_ack.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(completion, timeout=1)

    assert controller.status is RunStatus.DONE
    assert commit_cancel_count == 0
    row = await db.fetch_one(
        "SELECT status, final_response FROM ai_agent_runs WHERE id = ?",
        [controller.run_id],
    )
    events = await db.fetch_all(
        "SELECT event_type FROM ai_agent_run_events WHERE run_id = ? ORDER BY id",
        [controller.run_id],
    )
    assert row == {"status": RunStatus.DONE.value, "final_response": "durable answer"}
    assert [event["event_type"] for event in events] == [
        CoreEventType.RUN_STARTED.value,
        CoreEventType.RUN_COMPLETED.value,
    ]
    assert [event.type for event in sink.events] == [CoreEventType.RUN_STARTED]
