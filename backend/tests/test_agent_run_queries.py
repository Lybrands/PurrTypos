from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI

from application.agent_run_queries import AgentRunQueryService
from application.agent_composition import set_agent_composition
from database.connection import DatabaseConnection
from dependencies import set_db
from domains.writing.agent_roles import build_writing_agent_role_registry
from infrastructure.persistence.run_store import (
    append_event,
    create_run,
    upsert_todos,
)
from infrastructure.persistence.run_execution_store import (
    SqliteExecutionLeaseStore,
    now_ms,
)
from infrastructure.persistence.sqlite_checkpoint_store import (
    SqliteCheckpointStore,
)
from infrastructure.persistence.sqlite_delegation_repository import (
    SqliteDelegationRepository,
)
from routers.ai import router as ai_router
from tests.support.asgi_sse import request_json


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    checkpoint_store = SqliteCheckpointStore(db)
    set_agent_composition(SimpleNamespace(
        checkpoint_store=checkpoint_store,
        delegation_repository=SqliteDelegationRepository(db),
        execution_lease_store=SqliteExecutionLeaseStore(db),
        agent_role_registry=build_writing_agent_role_registry(),
    ))
    try:
        yield db
    finally:
        set_agent_composition(None)
        await db.close()


def _queries(db: DatabaseConnection) -> AgentRunQueryService:
    return AgentRunQueryService(
        SqliteCheckpointStore(db),
        role_registry=build_writing_agent_role_registry(),
    )


async def _seed_run(db: DatabaseConnection) -> str:
    run_id = await create_run(
        db,
        session_id=7,
        prompt="private prompt must not be exposed by snapshots",
        mode="agent",
    )
    await upsert_todos(db, run_id, [{
        "id": "read",
        "title": "Read evidence",
        "status": "running",
        "executor": "tool",
        "type": "read",
        "riskLevel": "read",
        "description": "Read the current evidence snapshot.",
        "suggestedTools": ["readThing"],
    }])
    for index in range(3):
        await append_event(
            db,
            run_id,
            f"fixture.event_{index + 1}",
            {"index": index + 1},
        )
    return run_id


async def test_run_snapshot_pages_events_with_a_stable_cursor(temp_db):
    run_id = await _seed_run(temp_db)
    service = _queries(temp_db)

    first = await service.get_snapshot(run_id, limit=2)
    assert first is not None
    assert first["version"] == 1
    assert first["run"]["runId"] == run_id
    assert first["run"]["status"] == "running"
    assert "prompt" not in first["run"]
    assert first["todos"][0]["id"] == "read"
    assert first["todos"][0]["type"] == "read"
    assert first["todos"][0]["riskLevel"] == "read"
    assert [event["type"] for event in first["events"]] == [
        "fixture.event_1",
        "fixture.event_2",
    ]
    assert first["events"][0]["cursor"] < first["events"][1]["cursor"]
    assert first["hasMore"] is True

    second = await service.get_snapshot(
        run_id,
        after_event_id=first["nextCursor"],
        limit=2,
    )
    assert second is not None
    assert [event["type"] for event in second["events"]] == [
        "fixture.event_3"
    ]
    assert second["events"][0]["cursor"] > first["nextCursor"]
    assert second["hasMore"] is False


async def test_run_snapshot_route_uses_the_same_resume_contract(temp_db):
    run_id = await _seed_run(temp_db)
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="GET",
        path=f"/api/ai/agent-runs/{run_id}?after=0&limit=1",
        json_body=None,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["run"]["runId"] == run_id
    assert len(payload["data"]["events"]) == 1
    assert payload["data"]["hasMore"] is True

    missing = await request_json(
        app,
        method="GET",
        path="/api/ai/agent-runs/missing",
        json_body=None,
    )
    assert missing.json() == {"success": False, "error": "Agent Run 不存在"}

    invalid = await request_json(
        app,
        method="GET",
        path=f"/api/ai/agent-runs/{run_id}?after=-1",
        json_body=None,
    )
    assert invalid.status_code == 422


async def test_run_snapshot_reuses_live_sse_mapper_for_replay(temp_db):
    run_id = await create_run(
        temp_db,
        session_id=7,
        prompt="replay delegated tool",
        mode="agent",
    )
    await append_event(
        temp_db,
        run_id,
        "delegation.event",
        {
            "delegationId": "delegation-1",
            "parentRunId": run_id,
            "rootRunId": run_id,
            "childRunId": "child-1",
            "agentRole": "researcher",
            "event": {
                "type": "tool.calls_started",
                "runId": "child-1",
                "payload": {
                    "calls": [{
                        "id": "call-1",
                        "name": "readSource",
                        "arguments_json": "{}",
                    }],
                    "in_progress": True,
                },
            },
        },
    )

    snapshot = await _queries(temp_db).get_snapshot(run_id)

    assert snapshot is not None
    assert snapshot["events"][0]["chunk"] == {
        "agentSubRunEvent": {
            "runId": run_id,
            "parentRunId": run_id,
            "rootRunId": run_id,
            "delegationId": "delegation-1",
            "childRunId": "child-1",
            "agentRole": "researcher",
            "agentTitle": None,
            "objective": None,
            "chunk": {
                "toolCalls": [{
                    "id": "call-1",
                    "type": "function",
                    "displayNames": {},
                    "function": {
                        "name": "readSource",
                        "arguments": "{}",
                    },
                }],
                "toolCallsInProgress": True,
                "model": None,
            },
        },
    }


async def test_latest_session_run_route_returns_prompt_and_snapshot(temp_db):
    run_id = await _seed_run(temp_db)
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="GET",
        path="/api/ai/session-runs/latest?sessionId=7",
        json_body=None,
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["prompt"] == "private prompt must not be exposed by snapshots"
    assert payload["snapshot"]["run"]["runId"] == run_id
    assert "prompt" not in payload["snapshot"]["run"]


async def test_run_cancel_route_is_persistent_and_idempotent(temp_db):
    run_id = await _seed_run(temp_db)
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    first = await request_json(
        app,
        method="POST",
        path=f"/api/ai/agent-runs/{run_id}/cancel",
        json_body={},
    )
    second = await request_json(
        app,
        method="POST",
        path=f"/api/ai/agent-runs/{run_id}/cancel",
        json_body={},
    )
    snapshot = await _queries(temp_db).get_snapshot(run_id)

    assert first.json()["data"] == {
        "status": "canceled",
        "newlyRequested": True,
        "childrenCanceled": 0,
        "terminalized": True,
    }
    assert second.json()["data"] == {
        "status": "canceled",
        "newlyRequested": False,
        "childrenCanceled": 0,
        "terminalized": False,
    }
    assert snapshot is not None
    assert snapshot["run"]["status"] == "canceled"
    assert snapshot["todos"][0]["status"] == "blocked"
    assert snapshot["events"][-1]["type"] == "run.canceled"


async def test_run_cancel_route_does_not_steal_live_executor_lease(temp_db):
    timestamp = now_ms()
    run_id = await create_run(
        temp_db,
        session_id=7,
        prompt="still owned",
        mode="agent",
        execution_owner_id="live-worker",
        heartbeat_at_ms=timestamp,
        lease_expires_at_ms=timestamp + 60_000,
    )
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="POST",
        path=f"/api/ai/agent-runs/{run_id}/cancel",
        json_body={},
    )
    snapshot = await _queries(temp_db).get_snapshot(run_id)

    assert response.json()["data"] == {
        "status": "cancel_requested",
        "newlyRequested": True,
        "childrenCanceled": 0,
        "terminalized": False,
    }
    assert snapshot is not None
    assert snapshot["run"]["status"] == "running"
    assert snapshot["run"]["execution"]["leaseExpiresAtMs"] == (
        timestamp + 60_000
    )
    assert snapshot["run"]["execution"]["cancellationRequested"] is True


async def test_delegation_route_is_visible_in_parent_snapshot(temp_db):
    run_id = await _seed_run(temp_db)
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="POST",
        path=f"/api/ai/agent-runs/{run_id}/delegations",
        json_body={
            "agentRole": "researcher",
            "objective": "Collect chapter evidence",
            "input": {"chapterId": 7},
            "required": True,
            "priority": 5,
        },
    )
    snapshot = await _queries(temp_db).get_snapshot(run_id)

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "queued"
    assert snapshot is not None
    assert snapshot["delegations"]["aggregate"]["state"] == "pending"
    assert snapshot["delegations"]["items"][0]["agentRole"] == "researcher"
    assert snapshot["delegations"]["items"][0]["agentTitle"] == "研究 Agent"


async def test_delegation_route_rejects_roles_missing_from_business_registry(
    temp_db,
):
    run_id = await _seed_run(temp_db)
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="POST",
        path=f"/api/ai/agent-runs/{run_id}/delegations",
        json_body={
            "agentRole": "unregistered-role",
            "objective": "Must not be accepted",
        },
    )
    snapshot = await _queries(temp_db).get_snapshot(run_id)

    assert response.json()["success"] is False
    assert "unsupported Agent role" in response.json()["error"]
    assert snapshot is not None
    assert snapshot["delegations"]["items"] == []


@pytest.mark.parametrize(
    ("after_event_id", "limit"),
    [(-1, 100), (0, 0), (0, 501)],
)
async def test_run_snapshot_rejects_invalid_page_bounds(
    temp_db,
    after_event_id,
    limit,
):
    with pytest.raises(ValueError):
        await _queries(temp_db).get_snapshot(
            "run-id",
            after_event_id=after_event_id,
            limit=limit,
        )
