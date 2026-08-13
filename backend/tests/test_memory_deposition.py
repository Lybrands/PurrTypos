from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        yield db
    finally:
        await db.close()


async def _seed_chapter(db: DatabaseConnection, book_id: str = "b1", chapter_id: str = "ch1"):
    await db.execute("INSERT INTO books (id, title) VALUES (?, ?)", [book_id, "Book"])
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) VALUES (?, ?, ?, ?)",
        ["ol1", "写作目录", "writing", book_id],
    )
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title) VALUES (?, ?, ?)",
        [chapter_id, "ol1", "第一章"],
    )


async def _set_setting(db: DatabaseConnection, key: str, value):
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        [key, json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value],
    )


async def test_chapter_diff_commit_creates_pending_plot_candidate(temp_db):
    from routers.chapter_diff import commit_chapter_diff
    from schemas.chapter_diff import CommitDiffRequest
    from services import long_term_memory_service

    await _seed_chapter(temp_db)

    res = await commit_chapter_diff("ch1", CommitDiffRequest(
        content="{}",
        before_text="主角拿起玉佩。",
        after_text="主角拿起玉佩，玉佩在雨夜发光。",
        source="ai_rewrite",
        accepted_segments=1,
    ))

    assert res["success"] is True
    rows = await long_term_memory_service.search_memory_items(
        "b1",
        "玉佩",
        options={"statuses": ["pending"]},
    )
    assert len(rows) == 1
    assert rows[0]["kind"] == "plot"
    assert rows[0]["scope_type"] == "chapter"
    assert rows[0]["scope_id"] == "ch1"
    assert "玉佩在雨夜发光" in rows[0]["content"]
    assert rows[0]["source_type"] == "chapter_diff"


async def test_chapter_diff_commit_uses_intelligence_candidates_when_enabled(temp_db, monkeypatch):
    from routers.chapter_diff import commit_chapter_diff
    from schemas.chapter_diff import CommitDiffRequest
    from services import long_term_memory_service, memory_intelligence_service

    await _seed_chapter(temp_db)
    await _set_setting(temp_db, "memory_intelligence_enabled", True)
    await _set_setting(temp_db, "ai_model_configs", [
        {
            "id": "m1",
            "apiProvider": "openai",
            "name": "test-model",
            "apiKey": "key",
            "baseUrl": "http://example.test/v1",
        }
    ])

    async def fake_chat(*_args, **_kwargs):
        return {
            "message": {
                "content": json.dumps({
                    "memories": [
                        {
                            "kind": "foreshadowing",
                            "content": "玉佩在雨夜发光，是后续火系封印线索",
                            "keywords": "玉佩 火系 封印",
                            "importance": 5,
                        }
                    ]
                }, ensure_ascii=False)
            }
        }

    monkeypatch.setattr(memory_intelligence_service, "create_chat_no_stream", fake_chat)

    await commit_chapter_diff("ch1", CommitDiffRequest(
        content="{}",
        before_text="主角拿起玉佩。",
        after_text="主角拿起玉佩，玉佩在雨夜发光。",
        source="ai_rewrite",
        accepted_segments=1,
    ))

    rows = await long_term_memory_service.search_memory_items(
        "b1",
        "火系封印",
        options={"statuses": ["pending"]},
    )
    assert len(rows) == 1
    assert rows[0]["kind"] == "foreshadowing"
    assert rows[0]["source_type"] == "ai_intelligence:chapter_diff"


async def test_chapter_diff_commit_skips_when_no_accepted_segments(temp_db):
    from routers.chapter_diff import commit_chapter_diff
    from schemas.chapter_diff import CommitDiffRequest
    from services import long_term_memory_service

    await _seed_chapter(temp_db)

    await commit_chapter_diff("ch1", CommitDiffRequest(
        content="{}",
        before_text="旧文本",
        after_text="新文本",
        source="ai_rewrite",
        accepted_segments=0,
    ))

    rows = await long_term_memory_service.search_memory_items(
        "b1",
        "新文本",
        options={"statuses": ["pending"]},
    )
    assert rows == []


async def test_character_setting_diff_commit_creates_pending_character_candidate(temp_db):
    from routers.setting_diff import commit_character_diff
    from schemas.setting_diff import CharacterSnapshot, CommitCharacterDiffRequest
    from services import long_term_memory_service

    await temp_db.execute("INSERT INTO books (id, title) VALUES (?, ?)", ["b1", "Book"])
    cid = await temp_db.execute_and_get_id(
        "INSERT INTO characters (book_id, name, tags, profile_md) VALUES (?, ?, ?, ?)",
        ["b1", "林夜", "主角", "## 性格\n冷静"],
    )

    res = await commit_character_diff(str(cid), CommitCharacterDiffRequest(
        name="林夜",
        tags="主角",
        profileMd="## 性格\n冷静\n\n## 弱点\n怕水",
        before=CharacterSnapshot(name="林夜", tags="主角", profileMd="## 性格\n冷静"),
        after=CharacterSnapshot(name="林夜", tags="主角", profileMd="## 性格\n冷静\n\n## 弱点\n怕水"),
        source="ai_tool",
        accepted_segments=1,
    ))

    assert res["success"] is True
    rows = await long_term_memory_service.search_memory_items(
        "b1",
        "怕水",
        options={"statuses": ["pending"]},
    )
    assert len(rows) == 1
    assert rows[0]["kind"] == "character"
    assert rows[0]["scope_type"] == "character"
    assert rows[0]["scope_id"] == str(cid)


async def test_character_setting_diff_refreshes_active_character_memory(temp_db):
    from routers.characters import create_character
    from routers.setting_diff import commit_character_diff
    from schemas.characters import CreateCharacterRequest
    from schemas.setting_diff import CharacterSnapshot, CommitCharacterDiffRequest
    from services import long_term_memory_service

    await temp_db.execute("INSERT INTO books (id, title) VALUES (?, ?)", ["b1", "Book"])
    created = await create_character("b1", CreateCharacterRequest(
        data={"name": "林夜", "tags": "主角", "profile_md": "## 性格\n冷静"},
    ))
    cid = created["data"]["id"]

    active_before = await long_term_memory_service.search_memory_items("b1", "冷静")
    assert len(active_before) == 1

    await commit_character_diff(str(cid), CommitCharacterDiffRequest(
        name="林夜",
        tags="主角",
        profileMd="## 性格\n冷静\n\n## 弱点\n怕水",
        before=CharacterSnapshot(name="林夜", tags="主角", profileMd="## 性格\n冷静"),
        after=CharacterSnapshot(name="林夜", tags="主角", profileMd="## 性格\n冷静\n\n## 弱点\n怕水"),
        source="ai_tool",
        accepted_segments=1,
    ))

    active_after = await long_term_memory_service.search_memory_items("b1", "怕水")
    assert len(active_after) == 1
    assert active_after[0]["status"] == "active"
    assert active_after[0]["source_type"] == "character"
    assert "怕水" in active_after[0]["content"]


async def test_save_conversation_remember_instruction_creates_active_memory(temp_db):
    from routers.conversations import save_conversation
    from schemas.conversations import SaveConversationRequest
    from services import long_term_memory_service

    await temp_db.execute("INSERT INTO books (id, title) VALUES (?, ?)", ["b1", "Book"])
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id, title) VALUES (?, ?, ?)",
        [1, "b1", "Session"],
    )

    res = await save_conversation(SaveConversationRequest(
        sessionId=1,
        bookId="b1",
        prompt="请记住：主角不能使用火系法术",
        response="好的，我会记住。",
    ))

    assert res["success"] is True
    rows = await long_term_memory_service.search_memory_items("b1", "火系法术")
    assert len(rows) == 1
    assert rows[0]["status"] == "active"
    assert rows[0]["kind"] == "canon"
    assert rows[0]["content"] == "主角不能使用火系法术"
    assert rows[0]["source_type"] == "conversation"


async def test_explicit_memory_reactivates_after_source_truncation_but_not_manual_archive(
    temp_db,
):
    from routers.conversations import delete_after_turn, save_conversation
    from schemas.conversations import SaveConversationRequest
    from services import long_term_memory_service

    await temp_db.execute("INSERT INTO books (id, title) VALUES ('b1', 'Book')")
    await temp_db.execute(
        "INSERT INTO ai_sessions (id, book_id, title) VALUES (1, 'b1', 'Session')"
    )
    first = await save_conversation(SaveConversationRequest(
        sessionId=1,
        bookId="b1",
        prompt="请记住：月门只能在雨夜开启",
        response="记住了",
    ))
    memory = await temp_db.fetch_one(
        "SELECT id, source_id FROM memory_items WHERE book_id = 'b1'"
    )

    await delete_after_turn("1", keepTurnCount=0)
    assert await temp_db.fetch_one(
        "SELECT status, source_type FROM memory_items WHERE id = ?",
        [memory["id"]],
    ) == {
        "status": "archived",
        "source_type": "conversation_truncated",
    }

    second = await save_conversation(SaveConversationRequest(
        sessionId=1,
        bookId="b1",
        prompt="请记住：月门只能在雨夜开启",
        response="再次记住",
    ))
    reactivated = await temp_db.fetch_one(
        "SELECT id, status, pinned, source_id FROM memory_items WHERE id = ?",
        [memory["id"]],
    )
    assert reactivated == {
        "id": memory["id"],
        "status": "active",
        "pinned": 1,
        "source_id": str(second["data"]["id"]),
    }

    await long_term_memory_service.archive_memory_item(memory["id"])
    third = await save_conversation(SaveConversationRequest(
        sessionId=1,
        bookId="b1",
        prompt="请记住：月门只能在雨夜开启",
        response="第三次",
    ))
    assert third["success"] is True
    assert await temp_db.fetch_one(
        "SELECT status, source_id FROM memory_items WHERE id = ?",
        [memory["id"]],
    ) == {
        "status": "archived",
        "source_id": str(second["data"]["id"]),
    }


async def test_inline_article_save_creates_pending_candidate_but_plain_autosave_does_not(temp_db):
    from routers.articles import save_article
    from schemas.articles import SaveArticleRequest
    from services import long_term_memory_service

    await _seed_chapter(temp_db)

    plain = await save_article("ch1", SaveArticleRequest(content="用户手写：玉佩没有异常"))
    assert plain["success"] is True
    assert await long_term_memory_service.search_memory_items(
        "b1",
        "玉佩",
        options={"statuses": ["pending"]},
    ) == []

    inline = await save_article("ch1", SaveArticleRequest(
        content="AI 改写：玉佩在雨夜发光",
        source="inline_edit",
    ))
    assert inline["success"] is True
    rows = await long_term_memory_service.search_memory_items(
        "b1",
        "雨夜发光",
        options={"statuses": ["pending"]},
    )
    assert len(rows) == 1
    assert rows[0]["kind"] == "plot"
    assert rows[0]["source_type"] == "article_save"


async def test_manual_character_save_creates_active_character_memory(temp_db):
    from routers.characters import create_character, update_character
    from schemas.characters import CreateCharacterRequest, UpdateCharacterRequest
    from services import long_term_memory_service

    await temp_db.execute("INSERT INTO books (id, title) VALUES (?, ?)", ["b1", "Book"])
    created = await create_character("b1", CreateCharacterRequest(
        data={"name": "林夜", "tags": "主角", "profile_md": "## 性格\n冷静"},
    ))
    assert created["success"] is True
    cid = created["data"]["id"]

    await update_character(str(cid), UpdateCharacterRequest(
        data={"profile_md": "## 性格\n冷静\n\n## 弱点\n怕水"},
    ))

    rows = await long_term_memory_service.search_memory_items("b1", "怕水")
    assert len(rows) == 1
    assert rows[0]["kind"] == "character"
    assert rows[0]["status"] == "active"
    assert rows[0]["source_type"] == "character"


async def test_manual_background_and_entity_save_create_active_world_memory(temp_db):
    from routers.setting_entities import create_setting_entity, update_setting_entity
    from routers.story_background import save_story_background
    from schemas.setting_entities import CreateSettingEntityRequest, UpdateSettingEntityRequest
    from schemas.story_background import SaveStoryBackgroundRequest
    from services import long_term_memory_service

    await temp_db.execute("INSERT INTO books (id, title) VALUES (?, ?)", ["b1", "Book"])

    await save_story_background("b1", SaveStoryBackgroundRequest(content="昆仑山门只在月食时开启"))
    bg_rows = await long_term_memory_service.search_memory_items("b1", "月食")
    assert len(bg_rows) == 1
    assert bg_rows[0]["kind"] == "world"
    assert bg_rows[0]["status"] == "active"

    created = await create_setting_entity("b1", CreateSettingEntityRequest(
        entityType="location",
        name="昆仑山门",
        profileMd="只在月食时开启",
    ))
    eid = created["data"]["id"]
    await update_setting_entity(str(eid), UpdateSettingEntityRequest(profileMd="只在月食时开启，门后有龙骨井"))

    rows = await long_term_memory_service.search_memory_items("b1", "龙骨井")
    assert len(rows) == 1
    assert rows[0]["kind"] == "world"
    assert rows[0]["status"] == "active"
    assert rows[0]["source_type"] == "setting_entity"


async def test_outline_save_creates_active_plan_memory(temp_db):
    from routers.outlines import save_outline, update_outline
    from schemas.outlines import SaveOutlineRequest, UpdateOutlineRequest
    from services import long_term_memory_service

    await temp_db.execute("INSERT INTO books (id, title) VALUES (?, ?)", ["b1", "Book"])
    created = await save_outline(SaveOutlineRequest(
        title="第三章大纲",
        type="chapter",
        book_id="b1",
        markdown_content="主角将在第三章发现玉佩秘密",
    ))
    assert created["success"] is True
    outline_id = created["data"]["id"]

    await update_outline(outline_id, UpdateOutlineRequest(
        markdown_content="主角将在第三章发现玉佩秘密，并遇见黑猫",
    ))

    rows = await long_term_memory_service.search_memory_items("b1", "黑猫")
    assert len(rows) == 1
    assert rows[0]["kind"] == "summary"
    assert rows[0]["scope_type"] == "outline"
    assert rows[0]["status"] == "active"
    assert "计划" in rows[0]["keywords"]
