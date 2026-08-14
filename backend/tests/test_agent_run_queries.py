from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI

from application.agent_run_queries import AgentRunQueryService
from application.writing_proposal_read_model import (
    SqliteWritingProposalReadModel,
    unseen_product_chunks,
)
from application.agent_composition import set_agent_composition
from application.agent_composition import get_agent_composition
from application.agent_cancellation_service import AgentCancellationService
from application.composition_factory import create_agent_composition
from database.connection import DatabaseConnection
from dependencies import set_db
from domains.writing.agent_roles import build_writing_agent_role_registry
from domains.screenplay_agent.agent_context import (
    SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
)
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
from infrastructure.persistence.sqlite_host_child_run_registry import (
    SqliteHostChildRunRegistry,
)
from purra.errors import ContractViolationError
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
    composition = create_agent_composition(db)
    set_agent_composition(composition)
    try:
        yield db
    finally:
        await composition.shutdown()
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
        binding=RunBinding(
            namespace="writing.chat.request",
            aggregate_id="7",
            command_id="fixture-writing-run",
            attributes={
                "agentProfile": "writing",
                "domainNamespace": "purrtypos.writing",
            },
        ),
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
    reservation = await SqliteHostChildRunRegistry(
        temp_db,
        reservation_ttl_ms=60_000,
    ).reserve(
        host_child_key="terminal-root-unbound-child",
        identity_digest="terminal-root-unbound-child-digest",
        contract={"rootRunId": run_id, "parentRunId": run_id},
        owner_token="terminal-root-worker",
        timestamp_ms=now_ms(),
    )
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
        "cancellationStatus": "completed",
        "cancellationEpoch": 1,
    }
    assert second.json()["data"] == {
        "status": "canceled",
        "newlyRequested": False,
        "childrenCanceled": 0,
        "terminalized": False,
        "cancellationStatus": "completed",
        "cancellationEpoch": 1,
    }
    assert snapshot is not None
    assert snapshot["run"]["status"] == "canceled"
    assert snapshot["todos"][0]["status"] == "blocked"
    assert snapshot["events"][-1]["type"] == "run.lifecycle"
    assert snapshot["events"][-1]["payload"]["status"] == "canceled"
    assert await temp_db.fetch_one(
        "SELECT generation, attempt_key, reservation_owner, "
        "reservation_expires_at_ms FROM ai_agent_host_child_runs "
        "WHERE host_child_key = ?",
        [reservation.host_child_key],
    ) == {
        "generation": reservation.generation,
        "attempt_key": reservation.attempt_key,
        "reservation_owner": None,
        "reservation_expires_at_ms": None,
    }


async def test_run_cancel_route_cascades_to_host_child_lineage(temp_db):
    root_run_id = await _seed_run(temp_db)
    child_run_id = await create_run(
        temp_db,
        session_id=7,
        prompt="host child",
        mode="agent",
        parent_run_id=root_run_id,
        root_run_id=root_run_id,
        delegation_id=None,
        agent_role="screenplay-part",
        run_depth=1,
    )
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="POST",
        path=f"/api/ai/agent-runs/{root_run_id}/cancel",
        json_body={},
    )
    child = await temp_db.fetch_one(
        "SELECT status, cancel_requested_at_ms, execution_owner_id, "
        "lease_expires_at_ms FROM ai_agent_runs WHERE id = ?",
        [child_run_id],
    )

    assert response.json()["data"]["childrenCanceled"] == 1
    assert child == {
        "status": "canceled",
        "cancel_requested_at_ms": child["cancel_requested_at_ms"],
        "execution_owner_id": None,
        "lease_expires_at_ms": None,
    }
    assert child["cancel_requested_at_ms"] is not None


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
        "cancellationStatus": "draining",
        "cancellationEpoch": 1,
    }
    assert await temp_db.fetch_one(
        "SELECT cancellation_epoch, status FROM ai_agent_run_cancellations "
        "WHERE root_run_id = ?",
        [run_id],
    ) == {"cancellation_epoch": 1, "status": "draining"}
    assert snapshot is not None
    assert snapshot["run"]["status"] == "running"
    assert snapshot["run"]["execution"]["leaseExpiresAtMs"] == (
        timestamp + 60_000
    )
    assert snapshot["run"]["execution"]["cancellationRequested"] is True


async def test_root_cancel_fence_rejects_new_child_run_and_host_receipt(temp_db):
    timestamp = now_ms()
    root_run_id = await create_run(
        temp_db,
        session_id=7,
        prompt="live root",
        mode="agent",
        execution_owner_id="live-worker",
        heartbeat_at_ms=timestamp,
        lease_expires_at_ms=timestamp + 60_000,
    )
    foreign_root_run_id = await create_run(
        temp_db,
        session_id=7,
        prompt="foreign root",
        mode="agent",
    )
    registry = SqliteHostChildRunRegistry(temp_db, reservation_ttl_ms=60_000)
    reserved = await registry.reserve(
        host_child_key="reserved-before-root-cancel",
        identity_digest="reserved-child-digest",
        contract={
            "rootRunId": root_run_id,
            "parentRunId": root_run_id,
        },
        owner_token="reserved-worker",
        timestamp_ms=timestamp,
    )
    foreign = await registry.reserve(
        host_child_key="foreign-reservation",
        identity_digest="foreign-child-digest",
        contract={
            "rootRunId": foreign_root_run_id,
            "parentRunId": foreign_root_run_id,
        },
        owner_token="foreign-worker",
        timestamp_ms=timestamp,
    )
    result = await AgentCancellationService(
        temp_db,
        get_agent_composition(),
    ).cancel(root_run_id)
    assert result is not None and result["cancellationStatus"] == "draining"

    with pytest.raises(ContractViolationError, match="cancellation"):
        await create_run(
            temp_db,
            session_id=7,
            prompt="late child",
            mode="agent",
            parent_run_id=root_run_id,
            root_run_id=root_run_id,
            agent_role="screenplay-part",
            run_depth=1,
        )
    with pytest.raises(ContractViolationError, match="cancellation"):
        await SqliteHostChildRunRegistry(temp_db).reserve(
            host_child_key="late-host-child",
            identity_digest="late-child-digest",
            contract={
                "rootRunId": root_run_id,
                "parentRunId": root_run_id,
            },
            owner_token="late-worker",
            timestamp_ms=timestamp + 1,
        )
    with pytest.raises(ValueError, match="cancellation"):
        await SqliteDelegationRepository(temp_db).create(
            parent_run_id=root_run_id,
            agent_role="researcher",
            objective="late delegation",
        )
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE root_run_id = ?",
        [root_run_id],
    ) == {"count": 1}
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_host_child_runs "
        "WHERE host_child_key = 'late-host-child'"
    ) == {"count": 0}
    assert await temp_db.fetch_one(
        "SELECT generation, attempt_key, identity_digest, reservation_owner, "
        "reservation_expires_at_ms FROM ai_agent_host_child_runs "
        "WHERE host_child_key = ?",
        [reserved.host_child_key],
    ) == {
        "generation": reserved.generation,
        "attempt_key": reserved.attempt_key,
        "identity_digest": reserved.identity_digest,
        "reservation_owner": None,
        "reservation_expires_at_ms": None,
    }
    assert await temp_db.fetch_one(
        "SELECT reservation_owner, reservation_expires_at_ms "
        "FROM ai_agent_host_child_runs WHERE host_child_key = ?",
        [foreign.host_child_key],
    ) == {
        "reservation_owner": "foreign-worker",
        "reservation_expires_at_ms": timestamp + 60_000,
    }


async def test_child_attach_waiting_on_root_cancel_transaction_sees_fence(
    temp_db,
    monkeypatch,
):
    timestamp = now_ms()
    root_run_id = await create_run(
        temp_db,
        session_id=7,
        prompt="live root",
        mode="agent",
        execution_owner_id="live-worker",
        heartbeat_at_ms=timestamp,
        lease_expires_at_ms=timestamp + 60_000,
    )
    fence_written = asyncio.Event()
    release_fence = asyncio.Event()
    original_execute = temp_db.execute

    async def hold_fence(sql, params=None):
        result = await original_execute(sql, params)
        if "INSERT INTO ai_agent_run_cancellations" in sql:
            fence_written.set()
            await release_fence.wait()
        return result

    monkeypatch.setattr(temp_db, "execute", hold_fence)
    cancel_task = asyncio.create_task(
        AgentCancellationService(
            temp_db,
            get_agent_composition(),
        ).cancel(root_run_id)
    )
    await asyncio.wait_for(fence_written.wait(), timeout=1)
    child_task = asyncio.create_task(
        create_run(
            temp_db,
            session_id=7,
            prompt="racing child",
            mode="agent",
            parent_run_id=root_run_id,
            root_run_id=root_run_id,
            agent_role="screenplay-part",
            run_depth=1,
        )
    )
    await asyncio.sleep(0)
    release_fence.set()
    await asyncio.wait_for(cancel_task, timeout=1)
    with pytest.raises(ContractViolationError, match="cancellation"):
        await asyncio.wait_for(child_task, timeout=1)

    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_runs WHERE root_run_id = ?",
        [root_run_id],
    ) == {"count": 1}


async def test_root_cancel_fence_transaction_rolls_back_every_write(
    temp_db,
    monkeypatch,
):
    root_run_id = await _seed_run(temp_db)
    reservation_timestamp = now_ms()
    reserved = await SqliteHostChildRunRegistry(
        temp_db,
        reservation_ttl_ms=60_000,
    ).reserve(
        host_child_key="rollback-host-child",
        identity_digest="rollback-host-child-digest",
        contract={
            "rootRunId": root_run_id,
            "parentRunId": root_run_id,
        },
        owner_token="rollback-worker",
        timestamp_ms=reservation_timestamp,
    )
    original_execute = temp_db.execute

    async def fail_delegation_cancel(sql, params=None):
        if (
            "UPDATE ai_agent_delegations" in sql
            and "parent_canceled" in sql
        ):
            raise RuntimeError("injected delegation cancellation failure")
        return await original_execute(sql, params)

    monkeypatch.setattr(temp_db, "execute", fail_delegation_cancel)
    with pytest.raises(RuntimeError, match="injected delegation"):
        await AgentCancellationService(
            temp_db,
            get_agent_composition(),
        ).cancel(root_run_id)
    monkeypatch.setattr(temp_db, "execute", original_execute)

    assert await temp_db.fetch_one(
        "SELECT cancel_requested_at_ms, cancellation_epoch FROM ai_agent_runs "
        "WHERE id = ?",
        [root_run_id],
    ) == {"cancel_requested_at_ms": None, "cancellation_epoch": 0}
    assert await temp_db.fetch_one(
        "SELECT root_run_id FROM ai_agent_run_cancellations WHERE root_run_id = ?",
        [root_run_id],
    ) is None
    assert await temp_db.fetch_one(
        "SELECT reservation_owner, reservation_expires_at_ms "
        "FROM ai_agent_host_child_runs WHERE host_child_key = ?",
        [reserved.host_child_key],
    ) == {
        "reservation_owner": "rollback-worker",
        "reservation_expires_at_ms": reservation_timestamp + 60_000,
    }


async def test_root_cancel_receipt_recovers_draining_tree_after_restart(temp_db):
    timestamp = now_ms()
    root_run_id = await create_run(
        temp_db,
        session_id=7,
        prompt="live root",
        mode="agent",
        execution_owner_id="old-process",
        heartbeat_at_ms=timestamp,
        lease_expires_at_ms=timestamp + 60_000,
    )
    first = await AgentCancellationService(
        temp_db,
        get_agent_composition(),
    ).cancel(root_run_id)
    assert first is not None and first["cancellationStatus"] == "draining"
    await temp_db.execute(
        "UPDATE ai_agent_runs SET lease_expires_at_ms = 0 WHERE id = ?",
        [root_run_id],
    )

    recovered = await AgentCancellationService(
        temp_db,
        get_agent_composition(),
    ).cancel(root_run_id)

    assert recovered is not None
    assert recovered["cancellationStatus"] == "completed"
    assert recovered["cancellationEpoch"] == first["cancellationEpoch"] == 1
    assert await temp_db.fetch_one(
        "SELECT status, cancellation_epoch FROM ai_agent_runs WHERE id = ?",
        [root_run_id],
    ) == {"status": "canceled", "cancellation_epoch": 1}


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


async def test_legacy_writing_run_without_profile_attributes_keeps_roles(
    temp_db,
):
    await temp_db.execute(
        "INSERT OR IGNORE INTO ai_sessions (id, title, scope, book_id) "
        "VALUES (17, 'Legacy Writing session', 'chapter', 'book-legacy')"
    )
    run_id = await create_run(
        temp_db,
        session_id=17,
        prompt="legacy writing run",
        mode="agent",
        binding=RunBinding(
            namespace="writing.chat.request",
            aggregate_id="17",
            command_id="legacy-writing-request",
        ),
    )
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    delegated = await request_json(
        app,
        method="POST",
        path=f"/api/ai/agent-runs/{run_id}/delegations",
        json_body={
            "agentRole": "researcher",
            "objective": "Keep legacy Writing roles available",
        },
    )
    snapshot = await request_json(
        app,
        method="GET",
        path=f"/api/ai/agent-runs/{run_id}",
        json_body=None,
    )

    assert delegated.json()["success"] is True
    assert delegated.json()["data"]["agentTitle"] == "研究 Agent"
    assert snapshot.json()["success"] is True
    assert snapshot.json()["data"]["delegations"]["items"][0][
        "agentTitle"
    ] == "研究 Agent"


async def test_legacy_writing_run_uses_persisted_session_scope_without_binding(
    temp_db,
):
    await temp_db.execute(
        "INSERT OR IGNORE INTO ai_sessions (id, title, scope, book_id) "
        "VALUES (19, 'Legacy setting session', 'setting', 'book-setting')"
    )
    run_id = await create_run(
        temp_db,
        session_id=19,
        prompt="legacy unbound writing run",
        mode="agent",
    )
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="POST",
        path=f"/api/ai/agent-runs/{run_id}/delegations",
        json_body={
            "agentRole": "researcher",
            "objective": "Resolve the persisted Writing session owner",
        },
    )

    assert response.json()["success"] is True
    assert response.json()["data"]["agentTitle"] == "研究 Agent"


async def test_persisted_profile_conflict_between_attributes_and_binding_fails_closed(
    temp_db,
):
    await temp_db.execute(
        "INSERT OR IGNORE INTO ai_sessions (id, title, scope, book_id) "
        "VALUES (18, 'Conflicted Writing session', 'chapter', 'book-conflict')"
    )
    run_id = await create_run(
        temp_db,
        session_id=18,
        prompt="conflicted profile",
        mode="agent",
        binding=RunBinding(
            namespace="writing.chat.request",
            aggregate_id="18",
            command_id="conflicted-writing-request",
            attributes={
                "agentProfile": "screenplay",
                "domainNamespace": SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
            },
        ),
    )
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="POST",
        path=f"/api/ai/agent-runs/{run_id}/delegations",
        json_body={
            "agentRole": "researcher",
            "objective": "must not trust conflicting profile attributes",
        },
    )

    assert response.json()["success"] is False
    assert "conflict" in response.json()["error"].lower()
    assert await SqliteDelegationRepository(temp_db).list_for_parent(run_id) == ()


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


async def _seed_screenplay_run(
    db: DatabaseConnection,
    *,
    persist_profile_attributes: bool = True,
) -> str:
    await db.execute(
        "INSERT OR IGNORE INTO ai_sessions (id, title, scope) "
        "VALUES (8, 'Screenplay session', 'screenplay')"
    )
    return await create_run(
        db,
        session_id=8,
        prompt="screenplay run",
        mode="agent",
        binding=RunBinding(
            namespace="screenplay.agent.turn",
            aggregate_id="project-1",
            command_id="turn-1",
            attributes=(
                {
                    "agentProfile": "screenplay",
                    "domainNamespace": SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
                }
                if persist_profile_attributes else {}
            ),
        ),
    )


async def test_screenplay_snapshot_uses_its_optional_role_registry(temp_db):
    run_id = await _seed_screenplay_run(temp_db)
    await SqliteDelegationRepository(temp_db).create(
        parent_run_id=run_id,
        agent_role="researcher",
        objective="legacy cross-profile fixture",
        input_payload=None,
        required=False,
        priority=0,
        max_depth=3,
    )
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="GET",
        path=f"/api/ai/agent-runs/{run_id}",
        json_body=None,
    )

    assert response.json()["success"] is True
    item = response.json()["data"]["delegations"]["items"][0]
    assert item["agentRole"] == "researcher"
    assert item["agentTitle"] == "researcher"


async def test_screenplay_parent_rejects_writing_delegation_roles(temp_db):
    run_id = await _seed_screenplay_run(temp_db)
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="POST",
        path=f"/api/ai/agent-runs/{run_id}/delegations",
        json_body={
            "agentRole": "researcher",
            "objective": "must not cross profile boundaries",
        },
    )
    stored = await SqliteDelegationRepository(temp_db).list_for_parent(run_id)

    assert response.json()["success"] is False
    assert "does not support delegation" in response.json()["error"]
    assert stored == ()


async def test_legacy_screenplay_parent_without_profile_attributes_stays_no_role(
    temp_db,
):
    run_id = await _seed_screenplay_run(
        temp_db,
        persist_profile_attributes=False,
    )
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="POST",
        path=f"/api/ai/agent-runs/{run_id}/delegations",
        json_body={
            "agentRole": "researcher",
            "objective": "must not infer Writing roles",
        },
    )

    assert response.json()["success"] is False
    assert "does not support delegation" in response.json()["error"]
    assert await SqliteDelegationRepository(temp_db).list_for_parent(run_id) == ()


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
