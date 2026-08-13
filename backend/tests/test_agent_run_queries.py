from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI

from application.agent_run_queries import AgentRunQueryService
from application.writing_proposal_read_model import (
    SqliteWritingProposalReadModel,
    unseen_product_chunks,
)
from application.agent_composition import set_agent_composition
from database.connection import DatabaseConnection
from dependencies import set_db
from domains.writing.agent_roles import build_writing_agent_role_registry
from infrastructure.persistence.run_store import (
    create_run,
    get_latest_run_for_session,
    upsert_todos,
)
from infrastructure.persistence.writing_chat_request_store import (
    SqliteWritingChatRequestStore,
)
from infrastructure.persistence.sqlite_agent_output_repository import (
    SqliteAgentOutputRepository,
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
from purra.output import (
    AgentOutputEventDraft,
    OutputChannel,
    OutputEventKind,
    OutputSource,
    OutputVisibility,
)
from purra.contracts import RunBinding


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    checkpoint_store = SqliteCheckpointStore(db)
    output_repository = SqliteAgentOutputRepository(db)
    role_registry = build_writing_agent_role_registry()

    def agent_role_registry_for_request(request):
        assert request.domain_context.namespace == "purrtypos.writing"
        return role_registry

    set_agent_composition(SimpleNamespace(
        checkpoint_store=checkpoint_store,
        output_repository=output_repository,
        delegation_repository=SqliteDelegationRepository(db),
        execution_lease_store=SqliteExecutionLeaseStore(db),
        agent_role_registry_for_request=agent_role_registry_for_request,
    ))
    try:
        yield db
    finally:
        set_agent_composition(None)
        await db.close()


def _queries(db: DatabaseConnection) -> AgentRunQueryService:
    return AgentRunQueryService(
        SqliteCheckpointStore(db),
        SqliteAgentOutputRepository(db),
        role_registry=build_writing_agent_role_registry(),
        product_event_query=SqliteWritingProposalReadModel(db),
    )


async def _append_public_runtime_event(
    db: DatabaseConnection,
    run_id: str,
    event_type: str,
    data: dict,
) -> None:
    await SqliteAgentOutputRepository(db).append_event(
        AgentOutputEventDraft(
            run_id=run_id,
            turn_id=None,
            output_stream_id=None,
            invocation_id=None,
            source_event_key=f"fixture:{run_id}:{event_type}",
            source=OutputSource.RUNTIME,
            kind=OutputEventKind.RUNTIME,
            channel=OutputChannel.LIFECYCLE,
            visibility=OutputVisibility.PUBLIC,
            payload={"eventType": event_type, "data": data},
            occurred_at=datetime.now(timezone.utc),
        )
    )


async def _seed_run(db: DatabaseConnection) -> str:
    await db.execute(
        "INSERT OR IGNORE INTO ai_sessions (id, title, book_id) "
        "VALUES (7, 'Book session', 'book-1')"
    )
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
        await _append_public_runtime_event(
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


async def test_run_snapshot_reuses_canonical_live_serializer_for_replay(temp_db):
    run_id = await _seed_run(temp_db)

    snapshot = await _queries(temp_db).get_snapshot(run_id, limit=1)

    assert snapshot is not None
    assert snapshot["events"][0]["chunk"] == {
        "eventId": snapshot["events"][0]["chunk"]["eventId"],
        "outputStreamId": None,
        "runId": run_id,
        "turnId": None,
        "invocationId": None,
        "sequence": 1,
        "source": "runtime",
        "kind": "runtime.event",
        "channel": "lifecycle",
        "visibility": "public",
        "payload": {
            "eventType": "fixture.event_1",
            "data": {"index": 1},
        },
        "occurredAt": snapshot["events"][0]["chunk"]["occurredAt"],
        "emittedAt": snapshot["events"][0]["chunk"]["emittedAt"],
    }


async def test_run_snapshot_projects_stable_setting_diff_occurrences_from_receipts(
    temp_db,
):
    run_id = await _seed_run(temp_db)
    await temp_db.execute(
        "INSERT INTO ai_agent_tool_receipts "
        "(run_id, tool_call_id, tool_name, arguments_digest, effects_json) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            run_id,
            "call-setting-1",
            "updateCharacter",
            "digest",
            json.dumps([
                {
                    "type": "writing.proposed_setting_diff",
                    "payload": {
                        "kind": "character",
                        "bookId": "book-1",
                        "characterId": 9,
                        "before": {"name": "旧名", "tags": "", "profileMd": "旧"},
                        "proposed": {"name": "新名", "tags": "", "profileMd": "新"},
                    },
                },
                {"type": "writing.setting_updated", "payload": {"kind": "character"}},
                {
                    "type": "writing.proposed_setting_diff",
                    "payload": {
                        "kind": "background",
                        "bookId": "book-1",
                        "before": {"content": "旧"},
                        "proposed": {"content": "新"},
                    },
                },
            ], ensure_ascii=False),
        ],
    )
    for payload in (
        {
            "kind": "character",
            "bookId": "book-1",
            "characterId": 9,
            "before": {"name": "旧名", "tags": "", "profileMd": "旧"},
            "proposed": {"name": "新名", "tags": "", "profileMd": "新"},
        },
        {
            "kind": "background",
            "bookId": "book-1",
            "before": {"content": "旧"},
            "proposed": {"content": "新"},
        },
    ):
        await temp_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json) VALUES (?, ?, ?)",
            [
                run_id,
                "writing.proposed_setting_diff",
                json.dumps(payload, ensure_ascii=False),
            ],
        )
    await temp_db.execute(
        "INSERT INTO ai_agent_run_events "
        "(run_id, event_type, payload_json) VALUES (?, 'tool.call_completed', ?)",
        [
            run_id,
            json.dumps({
                "toolCallId": "call-setting-1",
                "toolName": "updateCharacter",
                "outcome": "completed",
            }),
        ],
    )

    snapshot = await _queries(temp_db).get_snapshot(run_id, limit=1)

    assert snapshot is not None
    assert len(snapshot["productEvents"]) == 2
    assert snapshot["productEvents"][0]["proposalId"] != (
        snapshot["productEvents"][1]["proposalId"]
    )
    assert snapshot["productEvents"][0]["toolCallId"] == "call-setting-1"
    assert snapshot["productEvents"][0]["effectIndex"] == 0
    assert snapshot["productEvents"][1]["effectIndex"] == 2
    first_proposal_id = snapshot["productEvents"][0]["proposalId"]
    assert snapshot["productEvents"][0]["chunk"] == {
        "runId": run_id,
        "proposedSettingDiff": {
            "proposalId": first_proposal_id,
            "kind": "character",
            "bookId": "book-1",
            "characterId": 9,
            "before": {"name": "旧名", "tags": "", "profileMd": "旧"},
            "proposed": {"name": "新名", "tags": "", "profileMd": "新"},
        },
    }
    assert len(snapshot["events"]) == 1
    assert snapshot["nextCursor"] == snapshot["events"][0]["cursor"]


async def test_setting_diff_product_projection_requires_journal_and_receipt(
    temp_db,
):
    run_id = await _seed_run(temp_db)
    await temp_db.execute(
        "INSERT INTO ai_agent_run_events (run_id, event_type, payload_json) "
        "VALUES (?, 'writing.proposed_setting_diff', ?)",
        [
            run_id,
            json.dumps({
                "kind": "background",
                "bookId": "book-legacy",
                "before": {"content": "旧"},
                "proposed": {"content": "新"},
            }, ensure_ascii=False),
        ],
    )

    journal_only = await _queries(temp_db).get_snapshot(run_id)
    assert journal_only is not None
    assert journal_only["productEvents"] == []

    await temp_db.execute(
        "DELETE FROM ai_agent_run_events "
        "WHERE run_id = ? AND event_type = 'writing.proposed_setting_diff'",
        [run_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_tool_receipts "
        "(run_id, tool_call_id, tool_name, arguments_digest, effects_json) "
        "VALUES (?, 'receipt-only', 'editStoryBackground', 'digest', ?)",
        [
            run_id,
            json.dumps([{
                "type": "writing.proposed_setting_diff",
                "payload": {
                    "kind": "background",
                    "bookId": "book-1",
                    "before": {"content": "旧"},
                    "proposed": {"content": "新"},
                },
            }], ensure_ascii=False),
        ],
    )
    receipt_only = await _queries(temp_db).get_snapshot(run_id)

    assert receipt_only is not None
    assert receipt_only["productEvents"] == []


async def test_setting_diff_projection_preserves_interleaved_tool_occurrences(
    temp_db,
):
    run_id = await _seed_run(temp_db)
    proposals = [
        {
            "kind": "background",
            "bookId": "book-1",
            "before": {"content": "旧 A"},
            "proposed": {"content": "新 A"},
        },
        {
            "kind": "background",
            "bookId": "book-1",
            "before": {"content": "旧 B"},
            "proposed": {"content": "新 B"},
        },
    ]
    for index, proposal in enumerate(proposals):
        await temp_db.execute(
            "INSERT INTO ai_agent_tool_receipts "
            "(run_id, tool_call_id, tool_name, arguments_digest, effects_json) "
            "VALUES (?, ?, 'editStoryBackground', ?, ?)",
            [
                run_id,
                f"call-{index}",
                f"digest-{index}",
                json.dumps([{
                    "type": "writing.proposed_setting_diff",
                    "payload": proposal,
                }], ensure_ascii=False),
            ],
        )
        await temp_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json) VALUES "
            "(?, 'writing.proposed_setting_diff', ?)",
            [run_id, json.dumps(proposal, ensure_ascii=False)],
        )
    for index in range(2):
        await temp_db.execute(
            "INSERT INTO ai_agent_run_events "
            "(run_id, event_type, payload_json) VALUES "
            "(?, 'tool.call_completed', ?)",
            [run_id, json.dumps({
                "toolCallId": f"call-{index}",
                "toolName": "editStoryBackground",
            })],
        )

    snapshot = await _queries(temp_db).get_snapshot(run_id)

    assert [
        event["payload"]["proposed"]["content"]
        for event in snapshot["productEvents"]
    ] == ["新 A", "新 B"]


async def test_live_product_projection_emits_each_snapshot_occurrence_once():
    events = [
        {"proposalId": "run:call:0", "chunk": {"proposedSettingDiff": {"proposalId": "run:call:0"}}},
        {"proposalId": "run:call:2", "chunk": {"proposedSettingDiff": {"proposalId": "run:call:2"}}},
    ]
    seen = {"run:call:0"}

    assert unseen_product_chunks(events, seen) == [events[1]["chunk"]]
    assert unseen_product_chunks(events, seen) == []


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


async def test_latest_session_run_breaks_same_timestamp_ties_by_insertion(temp_db):
    first_run_id = await _seed_run(temp_db)
    second_run_id = await create_run(
        temp_db,
        session_id=7,
        prompt="new request in the same SQLite second",
        mode="agent",
    )
    await temp_db.execute(
        "UPDATE ai_agent_runs SET create_time = '2026-08-13 01:00:00' "
        "WHERE id IN (?, ?)",
        [first_run_id, second_run_id],
    )

    latest = await get_latest_run_for_session(temp_db, 7)

    assert latest is not None
    assert latest["id"] == second_run_id


async def test_latest_session_run_route_can_correlate_the_exact_chat_request(temp_db):
    await _seed_run(temp_db)
    correlated_run_id = await create_run(
        temp_db,
        session_id=7,
        prompt="correlated request",
        mode="agent",
        binding=RunBinding(
            namespace="writing.chat.request",
            aggregate_id="7",
            command_id="chat-request-exact",
        ),
    )
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="GET",
        path=(
            "/api/ai/session-runs/latest?sessionId=7"
            "&requestId=chat-request-exact"
        ),
        json_body=None,
    )

    assert response.status_code == 200
    assert response.json()["data"]["snapshot"]["run"]["runId"] == correlated_run_id


async def test_exact_request_query_exposes_accepted_then_bound_receipt(temp_db):
    await temp_db.execute(
        "INSERT OR IGNORE INTO ai_sessions (id, title, book_id) "
        "VALUES (7, 'Book session', 'book-1')"
    )
    store = SqliteWritingChatRequestStore(temp_db)
    await store.reserve(
        request_id="chat-receipt-query",
        session_id=7,
        request_digest="sha256:a",
        book_id="book-1",
        chapter_id=None,
    )
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    accepted = await request_json(
        app,
        method="GET",
        path=(
            "/api/ai/session-runs/latest?sessionId=7"
            "&requestId=chat-receipt-query"
        ),
        json_body=None,
    )
    assert accepted.json()["data"] == {
        "request": {
            "requestId": "chat-receipt-query",
            "sessionId": 7,
            "status": "accepted",
            "runId": None,
            "cancelRequested": False,
            "rejectionCode": None,
            "revision": 1,
        },
        "prompt": "",
        "snapshot": None,
    }

    await store.claim(
        request_id="chat-receipt-query",
        session_id=7,
        request_digest="sha256:a",
        book_id="book-1",
        chapter_id=None,
    )
    run_id = await create_run(
        temp_db,
        session_id=7,
        prompt="bound request",
        mode="agent",
        binding=RunBinding(
            namespace="writing.chat.request",
            aggregate_id="7",
            command_id="chat-receipt-query",
        ),
    )
    await store.bind_run("chat-receipt-query", run_id)

    bound = await request_json(
        app,
        method="GET",
        path=(
            "/api/ai/session-runs/latest?sessionId=7"
            "&requestId=chat-receipt-query"
        ),
        json_body=None,
    )
    assert bound.json()["data"]["request"]["status"] == "run_bound"
    assert bound.json()["data"]["snapshot"]["run"]["runId"] == run_id


async def test_session_latest_exposes_an_unposted_active_request_for_fresh_recovery(
    temp_db,
):
    await temp_db.execute(
        "INSERT OR IGNORE INTO ai_sessions (id, title, book_id) "
        "VALUES (7, 'Book session', 'book-1')"
    )
    await SqliteWritingChatRequestStore(temp_db).reserve(
        request_id="chat-put-only",
        session_id=7,
        request_digest="sha256:put-only",
        book_id="book-1",
        chapter_id=None,
    )
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="GET",
        path="/api/ai/session-runs/latest?sessionId=7",
        json_body=None,
    )

    assert response.json()["data"]["request"]["requestId"] == "chat-put-only"
    assert response.json()["data"]["request"]["status"] == "accepted"
    assert response.json()["data"]["snapshot"] is None

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
    assert snapshot["events"][-1]["type"] == "fixture.event_3"


async def test_run_cancel_route_does_not_steal_live_executor_lease(temp_db):
    timestamp = now_ms()
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id) VALUES (?, ?)",
        [7, "book-1"],
    )
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
