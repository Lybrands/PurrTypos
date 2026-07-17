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


async def _set_setting(db: DatabaseConnection, key: str, value):
    await db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        [key, json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value],
    )


async def test_intelligence_disabled_does_not_call_model(temp_db, monkeypatch):
    from services import memory_intelligence_service

    called = False

    async def fake_chat(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"message": {"content": "{}"}}

    monkeypatch.setattr(memory_intelligence_service, "create_chat_no_stream", fake_chat)

    rows = await memory_intelligence_service.extract_and_store_candidates(
        temp_db,
        book_id="b1",
        source_type="chapter_diff",
        source_id=1,
        source_text="玉佩在雨夜发光",
        scope_type="chapter",
        scope_id="ch1",
    )

    assert rows == []
    assert called is False


async def test_intelligence_enabled_stores_model_candidates_and_links(temp_db, monkeypatch):
    from services import long_term_memory_service, memory_intelligence_service

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
    existing = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="canon",
        content="主角不能使用火系法术",
        source_type="manual",
    )

    async def fake_chat(key, messages, options, api_provider, signal=None):
        assert key == "key"
        assert options["model"] == "test-model"
        assert options["baseURL"] == "http://example.test/v1"
        assert api_provider == "openai"
        assert "玉佩在雨夜发光" in messages[-1]["content"]
        return {
            "message": {
                "content": json.dumps({
                    "memories": [
                        {
                            "kind": "plot",
                            "content": "玉佩在雨夜发光，疑似与火系禁制冲突",
                            "keywords": "玉佩 火系 冲突",
                            "importance": 4,
                            "confidence": 0.8,
                            "relation": {
                                "targetId": existing["id"],
                                "type": "contradicts",
                                "note": "火系禁制可能被玉佩触发",
                            },
                        }
                    ]
                }, ensure_ascii=False)
            }
        }

    monkeypatch.setattr(memory_intelligence_service, "create_chat_no_stream", fake_chat)

    rows = await memory_intelligence_service.extract_and_store_candidates(
        temp_db,
        book_id="b1",
        source_type="chapter_diff",
        source_id=7,
        source_text="玉佩在雨夜发光",
        scope_type="chapter",
        scope_id="ch1",
    )

    assert len(rows) == 1
    assert rows[0]["kind"] == "plot"
    assert rows[0]["status"] == "pending"
    assert rows[0]["source_type"] == "ai_intelligence:chapter_diff"
    links = await temp_db.fetch_all("SELECT * FROM memory_links")
    assert len(links) == 1
    assert links[0]["relation"] == "contradicts"
