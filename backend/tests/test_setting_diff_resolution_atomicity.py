from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException

from database.connection import DatabaseConnection
from dependencies import clear_db, set_db
from application.agent_composition import (
    clear_agent_composition,
    set_agent_composition,
)
from infrastructure.persistence.run_store import create_run
from application.book_conversation_product_projection import (
    persist_setting_diff_resolution,
)
from routers.setting_diff import (
    commit_background_diff,
    commit_character_diff,
    commit_entity_diff,
)
from routers.conversations import save_conversation
from schemas.conversations import SaveConversationRequest
from schemas.setting_diff import (
    CommitBackgroundDiffRequest,
    CommitCharacterDiffRequest,
    CommitEntityDiffRequest,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def resolution_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    composition = SimpleNamespace(memory_resource=None)
    set_agent_composition(composition)
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) "
        "VALUES (7, 'book-1', 'chapter-1')"
    )
    await db.execute(
        "INSERT INTO story_background (book_id, content) VALUES ('book-1', '旧世界')"
    )
    await db.execute(
        "INSERT INTO characters (id, book_id, name, tags, profile_md) "
        "VALUES (11, 'book-1', '旧人物', '旧标签', '旧人物简介')"
    )
    await db.execute(
        "INSERT INTO setting_entities (id, book_id, name, tags, profile_md) "
        "VALUES (12, 'book-1', '旧地点', '旧标签', '旧地点简介')"
    )
    try:
        yield db
    finally:
        clear_agent_composition(composition)
        clear_db(db)
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


async def _proposal_for(
    db: DatabaseConnection,
    *,
    kind: str,
    payload: dict,
    tool_name: str,
    tool_call_id: str,
) -> tuple[str, str]:
    from application.writing_proposal_read_model import proposal_occurrence_id

    run_id = await create_run(db, session_id=7, prompt="修改设定", mode="agent")
    await db.execute(
        "INSERT INTO ai_agent_tool_receipts "
        "(run_id, tool_call_id, tool_name, arguments_digest, effects_json) "
        "VALUES (?, ?, ?, 'digest', ?)",
        [run_id, tool_call_id, tool_name, json.dumps([{
            "type": "writing.proposed_setting_diff",
            "payload": {"kind": kind, "bookId": "book-1", **payload},
        }], ensure_ascii=False)],
    )
    proposal = {"kind": kind, "bookId": "book-1", **payload}
    await db.execute(
        "INSERT INTO ai_agent_run_events (run_id, event_type, payload_json) "
        "VALUES (?, 'writing.proposed_setting_diff', ?)",
        [run_id, json.dumps(proposal, ensure_ascii=False)],
    )
    await db.execute(
        "INSERT INTO ai_agent_run_events (run_id, event_type, payload_json) "
        "VALUES (?, 'tool.call_completed', ?)",
        [run_id, json.dumps({
            "toolCallId": tool_call_id,
            "toolName": tool_name,
        })],
    )
    return run_id, proposal_occurrence_id(run_id, tool_call_id, 0)


async def _request_for_kind(db: DatabaseConnection, kind: str):
    if kind == "background":
        run_id, proposal_id = await _proposal(db)
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
                "kind": kind,
                "title": "故事背景",
                "status": "committed",
                "acceptedSegments": 1,
                "rejectedSegments": 0,
            },
        })
        return commit_background_diff, "book-1", request, run_id, proposal_id
    target_id = 11 if kind == "character" else 12
    noun = "人物" if kind == "character" else "地点"
    before = {
        "name": f"旧{noun}",
        "tags": "旧标签",
        "profileMd": f"旧{noun}简介",
    }
    proposed = {
        "name": f"新{noun}",
        "tags": "新标签",
        "profileMd": f"新{noun}简介",
    }
    run_id, proposal_id = await _proposal_for(
        db,
        kind=kind,
        payload={f"{kind}Id": target_id, "before": before, "proposed": proposed},
        tool_name="updateCharacter" if kind == "character" else "updateSettingEntity",
        tool_call_id=f"call-{kind}-upgrade-fixture",
    )
    request_type = (
        CommitCharacterDiffRequest if kind == "character" else CommitEntityDiffRequest
    )
    request = request_type.model_validate({
        **proposed,
        "before": before,
        "after": proposed,
        "source": "ai_tool",
        "accepted_segments": 3,
        "resolution": {
            "sessionId": 7,
            "agentRunId": run_id,
            "proposalId": proposal_id,
            "sessionKey": f"{kind}:{target_id}",
            "kind": kind,
            "title": noun,
            "status": "committed",
            "acceptedSegments": 3,
            "rejectedSegments": 0,
        },
    })
    commit = commit_character_diff if kind == "character" else commit_entity_diff
    return commit, str(target_id), request, run_id, proposal_id


async def _remove_mutation_digest(
    db: DatabaseConnection,
    run_id: str,
    proposal_id: str,
) -> None:
    row = await db.fetch_one(
        "SELECT c.id, c.agent_process FROM ai_conversations AS c "
        "JOIN ai_agent_runs AS r ON r.conversation_id = c.id WHERE r.id = ?",
        [run_id],
    )
    process = json.loads(str(row["agent_process"]))
    process["settingDiff"]["resolutions"][proposal_id].pop("mutationDigest", None)
    await db.execute(
        "UPDATE ai_conversations SET agent_process = ? WHERE id = ?",
        [json.dumps(process, ensure_ascii=False), int(row["id"])],
    )


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

    with pytest.raises(HTTPException) as conflict:
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
    assert conflict.value.status_code == 409

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


@pytest.mark.parametrize("kind", ["background", "character", "entity"])
async def test_resolution_replay_rejects_a_changed_mutation_body(
    resolution_db,
    kind: str,
):
    if kind == "background":
        run_id, proposal_id = await _proposal(resolution_db)
        resolution = {
            "sessionId": 7,
            "agentRunId": run_id,
            "proposalId": proposal_id,
            "sessionKey": "background:book-1",
            "kind": kind,
            "title": "故事背景",
            "status": "committed",
            "acceptedSegments": 1,
            "rejectedSegments": 0,
        }
        await commit_background_diff("book-1", CommitBackgroundDiffRequest.model_validate({
            "content": "新世界",
            "before_content": "旧世界",
            "after_content": "新世界",
            "resolution": resolution,
        }))
        replay = commit_background_diff(
            "book-1",
            CommitBackgroundDiffRequest.model_validate({
                "content": "旧世界",
                "before_content": "伪造旧世界",
                "after_content": "伪造新世界",
                "resolution": resolution,
            }),
        )
    else:
        target_id = 11 if kind == "character" else 12
        noun = "人物" if kind == "character" else "地点"
        before = {"name": f"旧{noun}", "tags": "旧标签", "profileMd": f"旧{noun}简介"}
        proposed = {"name": f"新{noun}", "tags": "新标签", "profileMd": f"新{noun}简介"}
        run_id, proposal_id = await _proposal_for(
            resolution_db,
            kind=kind,
            payload={f"{kind}Id": target_id, "before": before, "proposed": proposed},
            tool_name="updateCharacter" if kind == "character" else "updateSettingEntity",
            tool_call_id=f"call-{kind}-replay-digest",
        )
        resolution = {
            "sessionId": 7,
            "agentRunId": run_id,
            "proposalId": proposal_id,
            "sessionKey": f"{kind}:{target_id}",
            "kind": kind,
            "title": noun,
            "status": "committed",
            "acceptedSegments": 3,
            "rejectedSegments": 0,
        }
        request_type = (
            CommitCharacterDiffRequest
            if kind == "character"
            else CommitEntityDiffRequest
        )
        commit = commit_character_diff if kind == "character" else commit_entity_diff
        await commit(str(target_id), request_type.model_validate({
            **proposed,
            "before": before,
            "after": proposed,
            "resolution": resolution,
        }))
        replay = commit(
            str(target_id),
            request_type.model_validate({
                **before,
                "before": {**before, "name": f"伪造旧{noun}"},
                "after": {**proposed, "name": f"伪造新{noun}"},
                "resolution": resolution,
            }),
        )

    with pytest.raises(HTTPException) as conflict:
        await replay
    assert conflict.value.status_code == 409


@pytest.mark.parametrize("kind", ["character", "entity"])
async def test_structured_resolution_identical_replay_is_idempotent(
    resolution_db,
    kind: str,
):
    target_id = 11 if kind == "character" else 12
    noun = "人物" if kind == "character" else "地点"
    before = {"name": f"旧{noun}", "tags": "旧标签", "profileMd": f"旧{noun}简介"}
    proposed = {"name": f"新{noun}", "tags": "新标签", "profileMd": f"新{noun}简介"}
    run_id, proposal_id = await _proposal_for(
        resolution_db,
        kind=kind,
        payload={f"{kind}Id": target_id, "before": before, "proposed": proposed},
        tool_name="updateCharacter" if kind == "character" else "updateSettingEntity",
        tool_call_id=f"call-{kind}-valid-replay",
    )
    request_type = (
        CommitCharacterDiffRequest
        if kind == "character"
        else CommitEntityDiffRequest
    )
    commit = commit_character_diff if kind == "character" else commit_entity_diff
    request = request_type.model_validate({
        **proposed,
        "before": before,
        "after": proposed,
        "resolution": {
            "sessionId": 7,
            "agentRunId": run_id,
            "proposalId": proposal_id,
            "sessionKey": f"{kind}:{target_id}",
            "kind": kind,
            "title": noun,
            "status": "committed",
            "acceptedSegments": 3,
            "rejectedSegments": 0,
        },
    })

    first = await commit(str(target_id), request)
    replay = await commit(str(target_id), request)

    assert first["success"] is True
    assert replay == {
        "success": True,
        "data": {f"{kind}Id": target_id, "replayed": True},
    }


@pytest.mark.parametrize("kind", ["background", "character", "entity"])
async def test_upgrade_backfills_digest_only_for_a_proven_legacy_replay(
    resolution_db,
    kind: str,
):
    commit, target, request, run_id, proposal_id = await _request_for_kind(
        resolution_db,
        kind,
    )
    await commit(target, request)
    await _remove_mutation_digest(resolution_db, run_id, proposal_id)

    replay = await commit(target, request)

    assert replay["success"] is True
    assert replay["data"]["replayed"] is True
    row = await resolution_db.fetch_one(
        "SELECT c.agent_process FROM ai_conversations AS c "
        "JOIN ai_agent_runs AS r ON r.conversation_id = c.id WHERE r.id = ?",
        [run_id],
    )
    stored = json.loads(str(row["agent_process"]))
    assert stored["settingDiff"]["resolutions"][proposal_id][
        "mutationDigest"
    ].startswith("sha256:")


@pytest.mark.parametrize("kind", ["background", "character", "entity"])
async def test_upgrade_never_backfills_legacy_digest_for_a_forged_final(
    resolution_db,
    kind: str,
):
    commit, target, request, run_id, proposal_id = await _request_for_kind(
        resolution_db,
        kind,
    )
    await commit(target, request)
    await _remove_mutation_digest(resolution_db, run_id, proposal_id)
    if kind == "background":
        forged = request.model_copy(update={"content": "旧世界"})
    else:
        forged = request.model_copy(update={"name": request.before.name})

    with pytest.raises(HTTPException) as conflict:
        await commit(target, forged)

    assert conflict.value.status_code == 409
    row = await resolution_db.fetch_one(
        "SELECT c.agent_process FROM ai_conversations AS c "
        "JOIN ai_agent_runs AS r ON r.conversation_id = c.id WHERE r.id = ?",
        [run_id],
    )
    stored = json.loads(str(row["agent_process"]))
    assert "mutationDigest" not in (
        stored["settingDiff"]["resolutions"][proposal_id]
    )


@pytest.mark.parametrize("kind", ["background", "character", "entity"])
async def test_legacy_backfill_rejects_a_different_legal_final_even_if_current_matches(
    resolution_db,
    kind: str,
):
    commit, target, request, run_id, proposal_id = await _request_for_kind(
        resolution_db,
        kind,
    )
    await commit(target, request)
    await _remove_mutation_digest(resolution_db, run_id, proposal_id)
    if kind == "background":
        await resolution_db.execute(
            "UPDATE story_background SET content = ? WHERE book_id = ?",
            [request.before_content, target],
        )
        forged = request.model_copy(update={"content": request.before_content})
    else:
        table = "characters" if kind == "character" else "setting_entities"
        await resolution_db.execute(
            f"UPDATE {table} SET name = ?, tags = ?, profile_md = ? WHERE id = ?",
            [
                request.before.name,
                request.before.tags,
                request.before.profileMd,
                int(target),
            ],
        )
        forged = request.model_copy(update={
            "name": request.before.name,
            "tags": request.before.tags,
            "profileMd": request.before.profileMd,
        })

    with pytest.raises(HTTPException) as conflict:
        await commit(target, forged)

    assert conflict.value.status_code == 409
    row = await resolution_db.fetch_one(
        "SELECT c.agent_process FROM ai_conversations AS c "
        "JOIN ai_agent_runs AS r ON r.conversation_id = c.id WHERE r.id = ?",
        [run_id],
    )
    stored = json.loads(str(row["agent_process"]))
    assert "mutationDigest" not in stored["settingDiff"]["resolutions"][proposal_id]


@pytest.mark.parametrize("kind", ["character", "entity"])
async def test_exact_resolution_replay_survives_target_deletion(
    resolution_db,
    kind: str,
):
    commit, target, request, _run_id, _proposal_id = await _request_for_kind(
        resolution_db,
        kind,
    )
    await commit(target, request)
    table = "characters" if kind == "character" else "setting_entities"
    await resolution_db.execute(f"DELETE FROM {table} WHERE id = ?", [int(target)])

    replay = await commit(target, request)

    assert replay == {
        "success": True,
        "data": {f"{kind}Id": int(target), "replayed": True},
    }


@pytest.mark.parametrize("kind", ["character", "entity"])
async def test_new_resolution_for_missing_target_is_a_conflict(
    resolution_db,
    kind: str,
):
    commit, target, request, _run_id, _proposal_id = await _request_for_kind(
        resolution_db,
        kind,
    )
    table = "characters" if kind == "character" else "setting_entities"
    await resolution_db.execute(f"DELETE FROM {table} WHERE id = ?", [int(target)])

    with pytest.raises(HTTPException) as conflict:
        await commit(target, request)

    assert conflict.value.status_code == 409


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


@pytest.mark.parametrize("kind", ["character", "entity", "background"])
async def test_setting_commit_rejects_concurrent_target_change_from_journal_before(
    resolution_db,
    kind: str,
):
    snapshot = {
        "name": "旧人物" if kind == "character" else "旧地点",
        "tags": "旧标签",
        "profileMd": "旧人物简介" if kind == "character" else "旧地点简介",
    }
    proposed = {
        "name": "新人物" if kind == "character" else "新地点",
        "tags": "新标签",
        "profileMd": "新人物简介" if kind == "character" else "新地点简介",
    }
    if kind == "background":
        payload = {
            "before": {"content": "旧世界"},
            "proposed": {"content": "新世界"},
        }
        tool_name = "editStoryBackground"
        tool_call_id = "call-bg-cas"
    else:
        target_id = 11 if kind == "character" else 12
        payload = {
            f"{kind}Id": target_id,
            "before": snapshot,
            "proposed": proposed,
        }
        tool_name = "updateCharacter" if kind == "character" else "updateSettingEntity"
        tool_call_id = f"call-{kind}-cas"
    run_id, proposal_id = await _proposal_for(
        resolution_db,
        kind=kind,
        payload=payload,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
    )
    if kind == "character":
        await resolution_db.execute(
            "UPDATE characters SET profile_md = '并发人物编辑' WHERE id = 11"
        )
        request = CommitCharacterDiffRequest.model_validate({
            **proposed,
            "before": snapshot,
            "after": proposed,
            "resolution": {
                "sessionId": 7, "agentRunId": run_id,
                "proposalId": proposal_id, "sessionKey": "character:11",
                "kind": kind, "title": "人物", "status": "committed",
            },
        })
        commit = commit_character_diff("11", request)
    elif kind == "entity":
        await resolution_db.execute(
            "UPDATE setting_entities SET profile_md = '并发实体编辑' WHERE id = 12"
        )
        request = CommitEntityDiffRequest.model_validate({
            **proposed,
            "before": snapshot,
            "after": proposed,
            "resolution": {
                "sessionId": 7, "agentRunId": run_id,
                "proposalId": proposal_id, "sessionKey": "entity:12",
                "kind": kind, "title": "设定", "status": "committed",
            },
        })
        commit = commit_entity_diff("12", request)
    else:
        await resolution_db.execute(
            "UPDATE story_background SET content = '并发背景编辑' WHERE book_id = 'book-1'"
        )
        request = CommitBackgroundDiffRequest.model_validate({
            "content": "新世界",
            "before_content": "旧世界",
            "after_content": "新世界",
            "resolution": {
                "sessionId": 7, "agentRunId": run_id,
                "proposalId": proposal_id, "sessionKey": "background:book-1",
                "kind": kind, "title": "故事背景", "status": "committed",
            },
        })
        commit = commit_background_diff("book-1", request)

    with pytest.raises(HTTPException) as conflict:
        await commit
    assert conflict.value.status_code == 409
    assert "changed" in str(conflict.value.detail)


async def test_background_commit_accepts_only_a_reviewed_mixed_composition(
    resolution_db,
):
    before = "第一段\n第二段\n第三段"
    proposed = "第一段\n改写第二段\n第三段\n新增第四段"
    await resolution_db.execute(
        "UPDATE story_background SET content = ? WHERE book_id = 'book-1'",
        [before],
    )
    run_id, proposal_id = await _proposal_for(
        resolution_db,
        kind="background",
        payload={
            "before": {"content": before},
            "proposed": {"content": proposed},
        },
        tool_name="editStoryBackground",
        tool_call_id="call-bg-mixed",
    )
    request = CommitBackgroundDiffRequest.model_validate({
        "content": "第一段\n改写第二段\n第三段",
        "before_content": before,
        "after_content": proposed,
        "accepted_segments": 1,
        "rejected_segments": 1,
        "resolution": {
            "sessionId": 7,
            "agentRunId": run_id,
            "proposalId": proposal_id,
            "sessionKey": "background:book-1",
            "kind": "background",
            "title": "故事背景",
            "status": "committed",
            "acceptedSegments": 1,
            "rejectedSegments": 1,
        },
    })

    result = await commit_background_diff("book-1", request)

    assert result["success"] is True
    assert await resolution_db.fetch_one(
        "SELECT content FROM story_background WHERE book_id = 'book-1'"
    ) == {"content": "第一段\n改写第二段\n第三段"}


async def test_setting_commit_rejects_client_snapshots_that_differ_from_journal(
    resolution_db,
):
    run_id, proposal_id = await _proposal(resolution_db)
    request = CommitBackgroundDiffRequest.model_validate({
        "content": "新世界",
        "before_content": "伪造旧世界",
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
    })

    with pytest.raises(HTTPException) as conflict:
        await commit_background_diff("book-1", request)

    assert conflict.value.status_code == 409
    assert "snapshots changed" in str(conflict.value.detail)
    assert await resolution_db.fetch_one(
        "SELECT content FROM story_background WHERE book_id = 'book-1'"
    ) == {"content": "旧世界"}


async def test_background_commit_matches_ui_composition_that_omits_empty_paragraphs(
    resolution_db,
):
    before = "第一段\n\n第三段"
    proposed = "第一段\n改写空段\n第三段"
    await resolution_db.execute(
        "UPDATE story_background SET content = ? WHERE book_id = 'book-1'",
        [before],
    )
    run_id, proposal_id = await _proposal_for(
        resolution_db,
        kind="background",
        payload={
            "before": {"content": before},
            "proposed": {"content": proposed},
        },
        tool_name="editStoryBackground",
        tool_call_id="call-bg-empty-paragraph",
    )

    result = await commit_background_diff(
        "book-1",
        CommitBackgroundDiffRequest.model_validate({
            "content": "第一段\n第三段",
            "before_content": before,
            "after_content": proposed,
            "resolution": {
                "sessionId": 7,
                "agentRunId": run_id,
                "proposalId": proposal_id,
                "sessionKey": "background:book-1",
                "kind": "background",
                "title": "故事背景",
                "status": "committed",
                "acceptedSegments": 0,
                "rejectedSegments": 1,
            },
        }),
    )

    assert result["success"] is True
    assert await resolution_db.fetch_one(
        "SELECT content FROM story_background WHERE book_id = 'book-1'"
    ) == {"content": "第一段\n第三段"}
