from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from application.agent_composition import (
    clear_agent_composition,
    set_agent_composition,
)
from dependencies import clear_db, set_db
from domains.writing.story_memory import (
    StoryMemoryDeltaStatus,
    StoryMemoryStatus,
)
from infrastructure.persistence.writing.sqlite_story_memory_repository import (
    SqliteStoryMemoryRepository,
)
from routers.articles import save_article
from schemas.articles import SaveArticleRequest
from services import story_memory_analysis_service
from services.story_memory_analysis_service import analyze_chapter


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    set_db(connection)
    composition = SimpleNamespace(memory_resource=None)
    set_agent_composition(composition)
    try:
        yield connection
    finally:
        clear_agent_composition(composition)
        clear_db(connection)
        await connection.close()


async def _seed_chapter(db: DatabaseConnection) -> int:
    await db.execute("INSERT INTO books (id, title) VALUES (?, ?)", ["book-1", "Book"])
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) VALUES (?, ?, ?, ?)",
        ["outline-1", "写作目录", "writing", "book-1"],
    )
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title) VALUES (?, ?, ?)",
        ["chapter-1", "outline-1", "第一章"],
    )
    character_id = await db.execute_and_get_id(
        "INSERT INTO characters (book_id, name, tags) VALUES (?, ?, ?)",
        ["book-1", "林墨", "主角"],
    )
    return int(character_id or 0)


async def _set_setting(db: DatabaseConnection, key: str, value: object) -> None:
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        [key, json.dumps(value, ensure_ascii=False)],
    )


async def _configure_model(db: DatabaseConnection, *, automatic: bool = False) -> None:
    await _set_setting(
        db,
        "ai_model_configs",
        [
            {
                "id": "story-model",
                "apiProvider": "openai",
                "name": "test-model",
                "apiKey": "test-key",
                "baseUrl": "http://example.test/v1",
                "profileMaxGenerationTokens": 8_192, "supportsThinking": False, "thinkingOnly": False, "contextWindow": "128k",
            }
        ],
    )
    if automatic:
        await _set_setting(db, "story_memory_analysis_enabled", True)


@pytest.mark.asyncio
async def test_explicit_analysis_stages_inferred_candidates_and_is_idempotent(
    db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    character_id = await _seed_chapter(db)
    await _configure_model(db)
    calls = 0

    async def fake_chat(key, messages, options, provider, signal=None):
        nonlocal calls
        calls += 1
        assert key == "test-key"
        assert options["model"] == "test-model"
        assert "thinking" not in options
        assert "temperature" not in options
        assert provider == "openai"
        assert "林墨抵达旧城区" in messages[-1]["content"]
        assert f'"id":{character_id}' in messages[-1]["content"]
        return {
            "finish_reason": "stop",
            "applied_generation_limit": 8_192,
            "message": {"role": "assistant",
                "content": json.dumps(
                    {
                        "changes": [
                            {
                                "kind": "character_state",
                                "characterId": str(character_id),
                                "attribute": "location",
                                "value": "旧城区",
                                "confidence": 0.94,
                                "source": {"excerpt": "林墨抵达旧城区。"},
                            },
                            {
                                "kind": "plot_thread",
                                "threadId": "missing-sister",
                                "title": "失踪的妹妹",
                                "summary": "线索指向旧城区",
                                "state": "advancing",
                                "confidence": 0.86,
                                "source": {"excerpt": "妹妹的线索也指向这里。"},
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            }
        }

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_no_stream", fake_chat)
    content = "林墨抵达旧城区。妹妹的线索也指向这里。"

    first = await analyze_chapter(
        db,
        book_id="book-1",
        chapter_id="chapter-1",
        content=content,
    )
    second = await analyze_chapter(
        db,
        book_id="book-1",
        chapter_id="chapter-1",
        content=content,
    )

    assert first.status.value == "completed"
    assert first.candidate_count == 2
    assert first.delta_id
    assert second.status.value == "reused"
    assert second.delta_id == first.delta_id
    assert calls == 1

    repository = SqliteStoryMemoryRepository(db)
    delta = await repository.get_delta(str(first.delta_id))
    assert delta is not None
    assert delta.status is StoryMemoryDeltaStatus.PENDING
    assert {change.status for change in delta.changes} == {StoryMemoryStatus.INFERRED}
    assert all(
        change.source.source_revision == first.source_revision
        for change in delta.changes
    )
    assert await repository.list_records("book-1") == ()
    reviews = await db.fetch_all(
        "SELECT classification, recommendation FROM story_memory_evolution_reviews "
        "WHERE delta_id = ? ORDER BY id ASC",
        [first.delta_id],
    )
    assert reviews == [
        {"classification": "addition", "recommendation": "apply"},
        {"classification": "addition", "recommendation": "apply"},
    ]


@pytest.mark.asyncio
async def test_auto_resolved_analysis_reuses_the_same_chapter_revision(
    db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    character_id = await _seed_chapter(db)
    await _configure_model(db)
    await _set_setting(db, "story_memory_auto_apply_enabled", True)
    await _set_setting(db, "story_memory_auto_apply_min_confidence", 0.95)
    calls = 0

    async def fake_chat(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "finish_reason": "stop",
            "applied_generation_limit": 8_192,
            "message": {"role": "assistant",
                "content": json.dumps(
                    {
                        "changes": [
                            {
                                "kind": "character_state",
                                "characterId": str(character_id),
                                "attribute": "location",
                                "value": "旧城区",
                                "confidence": 0.98,
                                "source": {"excerpt": "林墨抵达旧城区。"},
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            }
        }

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_no_stream", fake_chat)
    content = "林墨抵达旧城区。"

    first = await analyze_chapter(
        db,
        book_id="book-1",
        chapter_id="chapter-1",
        content=content,
    )
    second = await analyze_chapter(
        db,
        book_id="book-1",
        chapter_id="chapter-1",
        content=content,
    )

    assert first.status.value == "completed"
    assert second.status.value == "reused"
    assert second.delta_id == first.delta_id
    assert calls == 1

    repository = SqliteStoryMemoryRepository(db)
    original = await repository.get_delta(str(first.delta_id))
    assert original is not None
    assert original.status is StoryMemoryDeltaStatus.INVALIDATED
    review = await db.fetch_one(
        "SELECT review_status, resolution, resolved_delta_id "
        "FROM story_memory_evolution_reviews WHERE delta_id = ?",
        [first.delta_id],
    )
    assert review is not None
    assert review["review_status"] == "resolved"
    assert review["resolution"] == "accepted"
    resolved = await repository.get_delta(str(review["resolved_delta_id"]))
    assert resolved is not None
    assert resolved.status is StoryMemoryDeltaStatus.APPLIED
    records = await repository.list_records("book-1")
    assert len(records) == 1
    assert records[0].status is StoryMemoryStatus.CONFIRMED


@pytest.mark.asyncio
async def test_automatic_analysis_is_disabled_without_opt_in(
    db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    await _seed_chapter(db)
    await _configure_model(db)
    called = False

    async def fake_chat(*_args, **_kwargs):
        nonlocal called
        called = True
        return {
            "message": {"role": "assistant","content": '{"changes":[]}'},
            "finish_reason": "stop",
            "applied_generation_limit": 8_192,
        }

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_no_stream", fake_chat)
    receipt = await analyze_chapter(
        db,
        book_id="book-1",
        chapter_id="chapter-1",
        content="林墨抵达旧城区。",
        automatic=True,
    )

    assert receipt.status.value == "skipped"
    assert receipt.reason == "disabled"
    assert called is False
    assert await db.fetch_all("SELECT * FROM story_memory_analysis_runs") == []


@pytest.mark.asyncio
async def test_inline_article_save_can_trigger_opted_in_analysis(
    db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    character_id = await _seed_chapter(db)
    await _configure_model(db, automatic=True)

    async def fake_chat(*_args, **_kwargs):
        return {
            "finish_reason": "stop",
            "applied_generation_limit": 8_192,
            "message": {"role": "assistant",
                "content": json.dumps(
                    {
                        "changes": [
                            {
                                "kind": "character_state",
                                "characterId": str(character_id),
                                "attribute": "injury",
                                "value": "left_arm",
                                "source": {"excerpt": "林墨左臂受伤。"},
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            }
        }

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_no_stream", fake_chat)
    response = await save_article(
        "chapter-1",
        SaveArticleRequest(content="林墨左臂受伤。", source="inline_edit"),
    )

    analysis = response["data"]["storyMemoryAnalysis"]
    assert analysis["status"].value == "completed"
    assert analysis["candidate_count"] == 1
    article = await db.fetch_one(
        "SELECT content FROM articles WHERE chapter_id = ?",
        ["chapter-1"],
    )
    assert article and article["content"] == "林墨左臂受伤。"


@pytest.mark.asyncio
async def test_new_revision_invalidates_old_pending_delta(
    db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    character_id = await _seed_chapter(db)
    await _configure_model(db)
    call_index = 0

    async def fake_chat(*_args, **_kwargs):
        nonlocal call_index
        call_index += 1
        if call_index == 1:
            excerpt, value = "林墨留在旧城区。", "旧城区"
        else:
            excerpt, value = "林墨已经进入北城。", "北城"
        return {
            "finish_reason": "stop",
            "applied_generation_limit": 8_192,
            "message": {"role": "assistant",
                "content": json.dumps(
                    {
                        "changes": [
                            {
                                "kind": "character_state",
                                "characterId": str(character_id),
                                "attribute": "location",
                                "value": value,
                                "source": {"excerpt": excerpt},
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            }
        }

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_no_stream", fake_chat)
    first = await analyze_chapter(
        db,
        book_id="book-1",
        chapter_id="chapter-1",
        content="林墨留在旧城区。",
    )
    second = await analyze_chapter(
        db,
        book_id="book-1",
        chapter_id="chapter-1",
        content="林墨已经进入北城。",
    )

    repository = SqliteStoryMemoryRepository(db)
    old_delta = await repository.get_delta(str(first.delta_id))
    new_delta = await repository.get_delta(str(second.delta_id))
    assert old_delta is not None
    assert old_delta.status is StoryMemoryDeltaStatus.INVALIDATED
    assert new_delta is not None
    assert new_delta.status is StoryMemoryDeltaStatus.PENDING


@pytest.mark.asyncio
async def test_plain_article_save_invalidates_candidates_without_calling_model(
    db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    character_id = await _seed_chapter(db)
    await _configure_model(db)

    async def fake_chat(*_args, **_kwargs):
        return {
            "finish_reason": "stop",
            "applied_generation_limit": 8_192,
            "message": {"role": "assistant",
                "content": json.dumps(
                    {
                        "changes": [
                            {
                                "kind": "character_state",
                                "characterId": str(character_id),
                                "attribute": "location",
                                "value": "旧城区",
                                "source": {"excerpt": "林墨留在旧城区。"},
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            }
        }

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_no_stream", fake_chat)
    analyzed = await analyze_chapter(
        db,
        book_id="book-1",
        chapter_id="chapter-1",
        content="林墨留在旧城区。",
    )

    await save_article(
        "chapter-1",
        SaveArticleRequest(content="林墨已经进入北城。"),
    )

    delta = await SqliteStoryMemoryRepository(db).get_delta(str(analyzed.delta_id))
    assert delta is not None
    assert delta.status is StoryMemoryDeltaStatus.INVALIDATED


@pytest.mark.asyncio
async def test_returning_to_a_stale_revision_reanalyzes_instead_of_reusing_it(
    db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    character_id = await _seed_chapter(db)
    await _configure_model(db)
    calls = 0

    async def fake_chat(*args, **_kwargs):
        nonlocal calls
        calls += 1
        prompt = args[1][-1]["content"]
        old_revision = "林墨留在旧城区。" in prompt
        excerpt = "林墨留在旧城区。" if old_revision else "林墨进入北城。"
        value = "旧城区" if old_revision else "北城"
        return {
            "finish_reason": "stop",
            "applied_generation_limit": 8_192,
            "message": {"role": "assistant",
                "content": json.dumps(
                    {
                        "changes": [
                            {
                                "kind": "character_state",
                                "characterId": str(character_id),
                                "attribute": "location",
                                "value": value,
                                "source": {"excerpt": excerpt},
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            }
        }

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_no_stream", fake_chat)
    first = await analyze_chapter(
        db,
        book_id="book-1",
        chapter_id="chapter-1",
        content="林墨留在旧城区。",
    )
    await analyze_chapter(
        db,
        book_id="book-1",
        chapter_id="chapter-1",
        content="林墨进入北城。",
    )
    returned = await analyze_chapter(
        db,
        book_id="book-1",
        chapter_id="chapter-1",
        content="林墨留在旧城区。",
    )

    assert calls == 3
    assert returned.status.value == "completed"
    assert returned.delta_id != first.delta_id


@pytest.mark.asyncio
async def test_model_candidates_without_exact_evidence_are_discarded_and_cached(
    db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
):
    await _seed_chapter(db)
    await _configure_model(db)
    calls = 0

    async def fake_chat(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "finish_reason": "stop",
            "applied_generation_limit": 8_192,
            "message": {"role": "assistant",
                "content": json.dumps(
                    {
                        "changes": [
                            {
                                "kind": "world_fact",
                                "factId": "invented-law",
                                "statement": "月光让魔法失效",
                                "source": {"excerpt": "正文里并不存在的证据"},
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            }
        }

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_no_stream", fake_chat)
    first = await analyze_chapter(
        db,
        book_id="book-1",
        chapter_id="chapter-1",
        content="林墨在月光下行走。",
    )
    second = await analyze_chapter(
        db,
        book_id="book-1",
        chapter_id="chapter-1",
        content="林墨在月光下行走。",
    )

    assert first.candidate_count == 0
    assert first.delta_id is None
    assert second.status.value == "reused"
    assert calls == 1


@pytest.mark.parametrize(
    ("provider_result", "expected_error"),
    [
        (
            {
                "finish_reason": "length",
                "applied_generation_limit": 8_192,
            },
            "model output is incomplete",
        ),
        (
            {
                "applied_generation_limit": 8_192,
            },
            "without a finish reason",
        ),
        (
            {
                "finish_reason": "stop",
            },
            "did not apply the requested generation limit",
        ),
    ],
)
@pytest.mark.asyncio
async def test_incomplete_or_unacknowledged_background_completion_never_stages_candidates(
    db: DatabaseConnection,
    monkeypatch: pytest.MonkeyPatch,
    provider_result: dict[str, object],
    expected_error: str,
):
    character_id = await _seed_chapter(db)
    await _configure_model(db)
    content = "林墨抵达旧城区。"

    async def fake_chat(*_args, **_kwargs):
        return {
            **provider_result,
            "message": {"role": "assistant",
                "content": json.dumps(
                    {
                        "changes": [
                            {
                                "kind": "character_state",
                                "characterId": str(character_id),
                                "attribute": "location",
                                "value": "旧城区",
                                "source": {"excerpt": content},
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            },
        }

    monkeypatch.setattr("infrastructure.models.provider_router.create_chat_no_stream", fake_chat)
    receipt = await analyze_chapter(
        db,
        book_id="book-1",
        chapter_id="chapter-1",
        content=content,
    )

    assert receipt.status.value == "failed"
    assert receipt.delta_id is None
    run = await db.fetch_one(
        "SELECT status, delta_id, candidate_count, error "
        "FROM story_memory_analysis_runs WHERE book_id = ? AND chapter_id = ?",
        ["book-1", "chapter-1"],
    )
    assert run is not None
    assert run["status"] == "failed"
    assert run["delta_id"] is None
    assert run["candidate_count"] == 0
    assert expected_error in str(run["error"])
