from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from agent_core.contracts import RunCreateParams, RunStatus
from agent_core.errors import ContractViolationError
from agent_core.events import AgentEvent, CoreEventType
from agent_core.ports import RunCommit
from database.connection import DatabaseConnection
from infrastructure.persistence.sqlite_run_repository import SqliteRunRepository
from services.agent_run_store import get_run, get_run_events


@pytest_asyncio.fixture
async def repository_and_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield SqliteRunRepository(db), db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_public_append_rejects_every_controller_owned_event_type(
    repository_and_db,
):
    repository, db = repository_and_db
    begun = await repository.begin(
        RunCreateParams(session_id=None, prompt="reserved events", mode="agent"),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    reserved_types = (
        CoreEventType.RUN_STARTED,
        CoreEventType.RUN_TODOS_UPDATED,
        CoreEventType.RUN_TODO_UPDATED,
        CoreEventType.RUN_COMPLETED,
        CoreEventType.RUN_BLOCKED,
        CoreEventType.RUN_FAILED,
        CoreEventType.RUN_CANCELED,
    )

    for event_type in reserved_types:
        with pytest.raises(ContractViolationError, match="AgentRunController"):
            await repository.append_event(
                begun.run_id,
                AgentEvent(type=event_type, run_id=begun.run_id),
            )

    events = await get_run_events(db, begun.run_id)
    assert [event["eventType"] for event in events] == [
        CoreEventType.RUN_STARTED
    ]


@pytest.mark.asyncio
async def test_commit_rejects_missing_mismatched_multiple_and_unpaired_terminal_events(
    repository_and_db,
):
    repository, db = repository_and_db
    begun = await repository.begin(
        RunCreateParams(session_id=None, prompt="terminal pairing", mode="agent"),
        AgentEvent(type=CoreEventType.RUN_STARTED),
    )
    completed = AgentEvent(
        type=CoreEventType.RUN_COMPLETED,
        run_id=begun.run_id,
    )
    canceled = AgentEvent(
        type=CoreEventType.RUN_CANCELED,
        run_id=begun.run_id,
    )
    invalid_commits = (
        (
            "exactly one run.completed",
            RunCommit(
                terminal_status=RunStatus.DONE,
                final_response="missing event",
            ),
        ),
        (
            "non-terminal run commit",
            RunCommit(events=(completed,)),
        ),
        (
            "requires run.completed, got run.canceled",
            RunCommit(
                terminal_status=RunStatus.DONE,
                final_response="mismatched event",
                events=(canceled,),
            ),
        ),
        (
            "exactly one run.completed",
            RunCommit(
                terminal_status=RunStatus.DONE,
                final_response="multiple events",
                events=(completed, canceled),
            ),
        ),
        (
            "run.started is only valid",
            RunCommit(events=(AgentEvent(
                type=CoreEventType.RUN_STARTED,
                run_id=begun.run_id,
            ),)),
        ),
    )

    for message, commit in invalid_commits:
        with pytest.raises(ContractViolationError, match=message):
            await repository.commit(begun.run_id, commit)

    run = await get_run(db, begun.run_id)
    events = await get_run_events(db, begun.run_id)
    assert run and run["status"] == RunStatus.RUNNING.value
    assert [event["eventType"] for event in events] == [
        CoreEventType.RUN_STARTED
    ]
