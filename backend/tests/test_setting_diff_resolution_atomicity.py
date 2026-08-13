from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db
from infrastructure.persistence.run_store import create_run
from application.book_conversation_product_projection import (
    BookSettingResolutionConflictError,
    persist_setting_diff_resolution,
)
from routers.setting_diff import commit_background_diff
from routers.conversations import save_conversation
from schemas.conversations import SaveConversationRequest
from schemas.setting_diff import CommitBackgroundDiffRequest

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def resolution_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) "
        "VALUES (7, 'book-1', 'chapter-1')"
    )
    await db.execute(
        "INSERT INTO story_background (book_id, content) VALUES ('book-1', '旧世界')"
    )
    try:
        yield db
    finally:
        await db.close()


async def _proposal(db: DatabaseConnection) -> tuple[str, str]:
    from application.writing_proposal_read_model import proposal_occurrence_id

    run_id = await create_run(
        db,
        session_id=7,
        prompt="修改世界设定",
        mode="agent",
    )
    payload = {
        "kind": "background",
        "bookId": "book-1",
        "before": {"content": "旧世界"},
        "proposed": {"content": "新世界"},
    }
    await db.execute(
        "INSERT INTO ai_agent_tool_receipts "
        "(run_id, tool_call_id, tool_name, arguments_digest, effects_json) "
        "VALUES (?, 'call-bg', 'editStoryBackground', 'digest', ?)",
        [run_id, json.dumps([{
            "type": "writing.proposed_setting_diff",
            "payload": payload,
        }], ensure_ascii=False)],
    )
    await db.execute(
        "INSERT INTO ai_agent_run_events (run_id, event_type, payload_json) "
        "VALUES (?, 'writing.proposed_setting_diff', ?)",
        [run_id, json.dumps(payload, ensure_ascii=False)],
    )
    await db.execute(
        "INSERT INTO ai_agent_run_events (run_id, event_type, payload_json) "
        "VALUES (?, 'tool.call_completed', ?)",
        [run_id, json.dumps({
            "toolCallId": "call-bg",
            "toolName": "editStoryBackground",
        })],
    )
    return run_id, proposal_occurrence_id(run_id, "call-bg", 0)


async def test_setting_commit_and_resolution_share_one_transaction(resolution_db):
    run_id, proposal_id = await _proposal(resolution_db)
    result = await commit_background_diff(
        "book-1",
        CommitBackgroundDiffRequest.model_validate({
            "content": "新世界",
            "before_content": "旧世界",
            "after_content": "新世界",
            "source": "ai_tool",
            "accepted_segments": 1,
            "resolution": {
                "sessionId": 7,
                "agentRunId": run_id,
                "proposalId": proposal_id,
                "sessionKey": "background:book-1",
                "kind": "background",
                "title": "故事背景",
                "status": "committed",
                "acceptedSegments": 1,
                "rejectedSegments": 0,
            },
        }),
    )

    assert result["success"] is True
    conversation = await resolution_db.fetch_one(
        "SELECT response, agent_process FROM ai_conversations WHERE session_id = 7"
    )
    assert conversation["response"] == ""
    stored = json.loads(conversation["agent_process"])
    assert stored["settingDiff"]["resolutions"][proposal_id]["status"] == "committed"


async def test_resolution_failure_rolls_back_setting_mutation(
    resolution_db,
    monkeypatch: pytest.MonkeyPatch,
):
    run_id, proposal_id = await _proposal(resolution_db)

    async def fail_resolution(*_args, **_kwargs):
        raise RuntimeError("durable resolution unavailable")

    monkeypatch.setattr(
        "routers.setting_diff.persist_setting_diff_resolution",
        fail_resolution,
        raising=False,
    )
    with pytest.raises(RuntimeError, match="resolution unavailable"):
        await commit_background_diff(
            "book-1",
            CommitBackgroundDiffRequest.model_validate({
                "content": "新世界",
                "before_content": "旧世界",
                "after_content": "新世界",
                "resolution": {
                    "sessionId": 7,
                    "agentRunId": run_id,
                    "proposalId": proposal_id,
                    "sessionKey": "background:book-1",
                    "kind": "background",
                    "title": "故事背景",
                    "status": "committed",
                },
            }),
        )

    assert await resolution_db.fetch_one(
        "SELECT content FROM story_background WHERE book_id = 'book-1'"
    ) == {"content": "旧世界"}


async def test_conflicting_resolution_rolls_back_setting_and_history(
    resolution_db,
):
    run_id, proposal_id = await _proposal(resolution_db)
    rejected = {
        "sessionId": 7,
        "agentRunId": run_id,
        "proposalId": proposal_id,
        "sessionKey": "background:book-1",
        "kind": "background",
        "title": "故事背景",
        "status": "rejected",
        "acceptedSegments": 0,
        "rejectedSegments": 1,
    }
    async with resolution_db.transaction(cancellation_linearizable=True):
        await persist_setting_diff_resolution(
            resolution_db,
            session_id=7,
            run_id=run_id,
            resolution=rejected,
        )

    with pytest.raises(BookSettingResolutionConflictError):
        await commit_background_diff(
            "book-1",
            CommitBackgroundDiffRequest.model_validate({
                "content": "新世界",
                "before_content": "旧世界",
                "after_content": "新世界",
                "resolution": {
                    **rejected,
                    "status": "committed",
                    "acceptedSegments": 1,
                    "rejectedSegments": 0,
                },
            }),
        )

    assert await resolution_db.fetch_one(
        "SELECT content FROM story_background WHERE book_id = 'book-1'"
    ) == {"content": "旧世界"}
    assert await resolution_db.fetch_one(
        "SELECT COUNT(*) AS count FROM story_background_history "
        "WHERE book_id = 'book-1'"
    ) == {"count": 0}


async def test_identical_resolution_replay_does_not_duplicate_history(
    resolution_db,
):
    run_id, proposal_id = await _proposal(resolution_db)
    request = CommitBackgroundDiffRequest.model_validate({
        "content": "新世界",
        "before_content": "旧世界",
        "after_content": "新世界",
        "source": "ai_tool",
        "accepted_segments": 1,
        "resolution": {
            "sessionId": 7,
            "agentRunId": run_id,
            "proposalId": proposal_id,
            "sessionKey": "background:book-1",
            "kind": "background",
            "title": "故事背景",
            "status": "committed",
            "acceptedSegments": 1,
            "rejectedSegments": 0,
        },
    })

    first = await commit_background_diff("book-1", request)
    replay = await commit_background_diff("book-1", request)

    assert first["success"] is True
    assert replay == {
        "success": True,
        "data": {"bookId": "book-1", "replayed": True},
    }
    assert await resolution_db.fetch_one(
        "SELECT COUNT(*) AS count FROM story_background_history "
        "WHERE book_id = 'book-1'"
    ) == {"count": 1}


async def test_generic_conversation_save_cannot_create_committed_resolution(
    resolution_db,
):
    run_id, proposal_id = await _proposal(resolution_db)

    with pytest.raises(Exception, match="setting transaction"):
        await save_conversation(SaveConversationRequest.model_validate({
            "sessionId": 7,
            "agentRunId": run_id,
            "prompt": "客户端不可代替设置事务",
            "response": "",
            "agentProcess": {
                "settingDiff": {
                    "resolutions": {
                        proposal_id: {
                            "proposalId": proposal_id,
                            "sessionKey": "background:book-1",
                            "kind": "background",
                            "title": "故事背景",
                            "status": "committed",
                            "acceptedSegments": 1,
                            "rejectedSegments": 0,
                        },
                    },
                },
            },
        }))

    assert await resolution_db.fetch_one(
        "SELECT content FROM story_background WHERE book_id = 'book-1'"
    ) == {"content": "旧世界"}
