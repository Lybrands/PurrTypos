from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI

from application.agent_run_queries import AgentRunQueryService
from application.writing_proposal_read_model import (
    SqliteWritingProposalReadModel,
)
from application.agent_composition import (
    get_agent_composition,
    set_agent_composition,
)
from application.composition_factory import create_agent_composition
from database.connection import DatabaseConnection
from database.crud.screenplay_project_deletion import (
    delete_screenplay_project_data,
)
from dependencies import set_db
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
from infrastructure.persistence.run_execution_store import now_ms
from infrastructure.persistence.sqlite_run_snapshot_reader import (
    SqliteRunSnapshotReader,
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
from purra.contracts import RunBinding, RunStatus
from purra.events import AgentEvent, CoreEventType
from purra.ports import RunCommit
from purra.output import RunLifecycleOutputDraft
from purra.api import (
    AgentCapabilityGrant,
    BeginRootAgentCommand,
    ChildAgentSpec,
    ContinueAgentCommand,
    SpawnAgentsCommand,
)


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
        SqliteRunSnapshotReader(db),
        SqliteAgentOutputRepository(db),
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
    await temp_db.execute(
        "UPDATE ai_agent_runs SET model_attempt_count = 2, "
        "unreported_usage_attempts = 1, input_tokens = 1200, "
        "output_tokens = 80, reasoning_tokens = 25, "
        "provider_output_events = 12, provider_output_bytes = 3456 WHERE id = ?",
        [run_id],
    )
    service = _queries(temp_db)

    first = await service.get_snapshot(run_id, limit=2)
    assert first is not None
    assert first["version"] == 2
    assert first["run"]["runId"] == run_id
    assert first["run"]["status"] == "running"
    assert first["run"]["activity"] == {
        "modelAttemptCount": 2,
        "usage": {
            "inputTokens": 1200,
            "generationTokens": 80,
            "reasoningTokens": 25,
            "totalTokens": 1280,
            "unreportedAttempts": 1,
            "unreportedReasoningAttempts": 0,
        },
        "providerOutputEvents": 12,
        "providerOutputBytes": 3456,
    }
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


async def test_child_run_snapshot_does_not_route_as_a_product_agent(
    temp_db, monkeypatch,
):
    root_run_id = await _seed_run(temp_db)
    child_run_id = await create_run(
        temp_db,
        session_id=7,
        prompt="子 Agent 任务",
        mode="agent",
        root_run_id=root_run_id,
        parent_run_id=root_run_id,
        agent_id="child-agent",
    )

    async def reject_product_routing(*args, **kwargs):
        del args, kwargs
        raise AssertionError("Child Run must not use product implementation routing")

    monkeypatch.setattr(
        get_agent_composition().agent_implementation_router,
        "for_run",
        reject_product_routing,
    )
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")
    response = await request_json(
        app,
        method="GET",
        path=f"/api/ai/agent-runs/{child_run_id}",
        json_body=None,
    )

    assert response.status_code == 200
    assert response.json()["data"]["run"]["runId"] == child_run_id


async def test_run_snapshot_reuses_canonical_live_serializer_for_replay(temp_db):
    run_id = await _seed_run(temp_db)

    snapshot = await _queries(temp_db).get_snapshot(run_id, limit=1)

    assert snapshot is not None
    assert snapshot["events"][0]["chunk"] == {
        "eventId": snapshot["events"][0]["chunk"]["eventId"],
        "rootRunId": run_id,
        "agentId": run_id,
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


async def test_sub_agent_conversation_returns_prompt_and_private_result(temp_db):
    root_run_id = await _seed_run(temp_db)
    composition = get_agent_composition()
    child_run_id = await create_run(
        temp_db,
        session_id=7,
        prompt="检查人物动机是否连续。",
        mode="agent",
        root_run_id=root_run_id,
        parent_run_id=root_run_id,
        execution_owner_id=composition.execution_owner_id,
        lease_expires_at_ms=now_ms() + 60_000,
    )
    result = '{"finding":"人物动机连续"}'
    await composition.output_repository.commit_run_lifecycle(
        child_run_id,
        RunCommit(
            terminal_status=RunStatus.DONE,
            final_response="",
            validated_result=result,
            events=(AgentEvent(
                type=CoreEventType.RUN_COMPLETED,
                run_id=child_run_id,
                payload={"status": RunStatus.DONE.value},
            ),),
        ),
        RunLifecycleOutputDraft(
            source_event_key=f"run:{child_run_id}:done",
            status=RunStatus.DONE,
            payload={"status": RunStatus.DONE.value},
            occurred_at=datetime.now(timezone.utc),
        ),
    )
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="GET",
        path=f"/api/ai/agent-runs/{child_run_id}/sub-agent-conversation",
        json_body=None,
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "version": 2,
            "agentId": child_run_id,
            "selectedRunId": child_run_id,
            "turns": [{
                "runId": child_run_id,
                "prompt": "检查人物动机是否连续。",
                "finalResponse": "人物动机连续",
                "status": "done",
            }],
        },
    }


async def test_sub_agent_conversation_rejects_root_runs(temp_db):
    root_run_id = await _seed_run(temp_db)
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="GET",
        path=f"/api/ai/agent-runs/{root_run_id}/sub-agent-conversation",
        json_body=None,
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "error": "该 Run 不是子 Agent 对话",
    }


async def test_sub_agent_conversation_returns_continued_agent_history(temp_db):
    root_run_id = await _seed_run(temp_db)
    composition = get_agent_composition()
    tree = composition.run_tree_repository
    await tree.begin_root(BeginRootAgentCommand(
        run_id=root_run_id,
        agent_id="root-agent-history",
        name="root",
        title="Root",
        instruction="test",
        objective="test",
        capability_grant=AgentCapabilityGrant(can_spawn_agents=True),
        idempotency_key="begin-history-root",
    ))
    spawned = await tree.spawn_agents(SpawnAgentsCommand(
        parent_run_id=root_run_id,
        idempotency_key="spawn-history-child",
        children=(ChildAgentSpec(
            name="slice-agent",
            title="分片分析",
            instruction="test",
            objective="先分析人物。",
        ),),
    ))
    first = spawned.items[0]
    claimed = await tree.claim_run(first.run.run_id)
    assert claimed is not None
    await tree.complete_run(
        first.run.run_id,
        expected_context_version=0,
        result={"content": "人物结果"},
        content_ref="result:first",
        fingerprint="first",
        lease_owner_id=claimed.lease_owner_id,
        lease_epoch=claimed.lease_epoch,
    )
    continued = await tree.continue_agent(ContinueAgentCommand(
        requester_run_id=root_run_id,
        idempotency_key="continue-history-child",
        agent_id=first.agent.agent_id,
        expected_context_version=1,
        message="继续分析设定。",
    ))
    for tree_run, prompt, result in (
        (first.run, "先分析人物。", "人物结果"),
        (continued.run, "继续分析设定。", "设定结果"),
    ):
        await create_run(
            temp_db,
            run_id=tree_run.run_id,
            session_id=7,
            prompt=prompt,
            mode="agent",
            root_run_id=root_run_id,
            parent_run_id=root_run_id,
            agent_id=first.agent.agent_id,
        )
        await temp_db.execute(
            "UPDATE ai_agent_runs SET status = 'done', final_response = ? WHERE id = ?",
            [result, tree_run.run_id],
        )

    app = FastAPI()
    app.include_router(ai_router, prefix="/api")
    response = await request_json(
        app,
        method="GET",
        path=f"/api/ai/agent-runs/{continued.run.run_id}/sub-agent-conversation",
        json_body=None,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["agentId"] == first.agent.agent_id
    assert [turn["prompt"] for turn in data["turns"]] == [
        "先分析人物。", "继续分析设定。",
    ]
    assert [turn["finalResponse"] for turn in data["turns"]] == [
        "人物结果", "设定结果",
    ]


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
        "terminalized": True,
        "cancellationStatus": "completed",
        "cancellationEpoch": 1,
    }
    assert second.json()["data"] == {
        "status": "canceled",
        "newlyRequested": False,
        "terminalized": False,
        "cancellationStatus": "completed",
        "cancellationEpoch": 1,
    }
    assert snapshot is not None
    assert snapshot["run"]["status"] == "canceled"
    assert snapshot["todos"][0]["status"] == "blocked"
    assert snapshot["events"][-1]["type"] == "run.lifecycle"
    assert snapshot["events"][-1]["payload"]["status"] == "canceled"


async def test_run_cancel_route_replays_tombstone_after_project_cleanup(temp_db):
    project_id = "project-canceled-audit"
    turn_id = "turn-canceled-audit"
    operation_id = "operation-canceled-audit"
    root_run_id = "run-canceled-audit"
    await temp_db.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_kind, source_snapshot_json) "
        "VALUES (?, 'Canceled audit', 'original', '{}')",
        [project_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, scope, screenplay_project_id) "
        "VALUES (77, 'screenplay', ?)",
        [project_id],
    )
    await temp_db.execute(
        "INSERT INTO screenplay_agent_turns "
        "(id, project_id, session_id, command_id, status, user_content, "
        "planner_run_id, operation_id) VALUES "
        "(?, ?, 77, 'cancel-audit-command', 'canceled', 'cancel', ?, ?)",
        [turn_id, project_id, root_run_id, operation_id],
    )
    await temp_db.execute(
        "INSERT INTO screenplay_agent_operations "
        "(id, turn_id, project_id, session_id, status, target_role, "
        "manifest_digest) VALUES (?, ?, ?, 77, 'canceled', "
        "'screenplayDraft', 'cancel-audit-digest')",
        [operation_id, turn_id, project_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_runs "
        "(id, session_id, status, prompt, cancellation_epoch, "
        "cancel_requested_at_ms) VALUES (?, 77, 'canceled', '', 1, 1)",
        [root_run_id],
    )
    await temp_db.execute(
        "INSERT INTO ai_agent_run_cancellations "
        "(run_id, cancellation_epoch, status, requested_at_ms, "
        "completed_at_ms) VALUES (?, 1, 'completed', 1, 2)",
        [root_run_id],
    )
    assert await delete_screenplay_project_data(temp_db, project_id) is True
    root_before = await temp_db.fetch_one(
        "SELECT * FROM ai_agent_runs WHERE id = ?",
        [root_run_id],
    )
    assert await temp_db.fetch_one(
        "SELECT run_id FROM ai_agent_run_cancellations "
        "WHERE run_id = ?",
        [root_run_id],
    ) is None
    app = FastAPI()
    app.include_router(ai_router, prefix="/api")

    response = await request_json(
        app,
        method="POST",
        path=f"/api/ai/agent-runs/{root_run_id}/cancel",
        json_body={},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "status": "canceled",
        "newlyRequested": False,
        "terminalized": False,
        "cancellationStatus": "completed",
        "cancellationEpoch": 1,
        "cancellationReceipt": "tombstoned",
    }
    assert await temp_db.fetch_one(
        "SELECT * FROM ai_agent_runs WHERE id = ?",
        [root_run_id],
    ) == root_before
    assert await temp_db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_run_cancellations"
    ) == {"count": 0}


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
        "terminalized": False,
        "cancellationStatus": "draining",
        "cancellationEpoch": 1,
    }
    assert await temp_db.fetch_one(
        "SELECT cancellation_epoch, status FROM ai_agent_run_cancellations "
        "WHERE run_id = ?",
        [run_id],
    ) == {"cancellation_epoch": 1, "status": "draining"}
    assert snapshot is not None
    assert snapshot["run"]["status"] == "running"
    assert snapshot["run"]["execution"]["leaseExpiresAtMs"] == (
        timestamp + 60_000
    )
    assert snapshot["run"]["execution"]["cancellationRequested"] is True


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
