from __future__ import annotations

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


async def test_build_memory_context_groups_forced_and_recalled_items(temp_db):
    from services import long_term_memory_service, memory_orchestrator

    forced = await long_term_memory_service.create_memory_item(
        book_id="b1", kind="canon", content="主角不能撒谎", pinned=1
    )
    recalled = await long_term_memory_service.create_memory_item(
        book_id="b1", kind="plot", content="玉佩已经在雨夜发光"
    )

    block = await memory_orchestrator.build_memory_context(
        {"bookId": "b1", "selectedLongTermMemoryIds": [forced["id"]]},
        "玉佩现在有什么异常？",
        "agent",
    )

    assert "必须遵循的设定" in block.text
    assert "已发生的剧情事实" in block.text
    assert "主角不能撒谎" in block.text
    assert "玉佩已经在雨夜发光" in block.text
    assert forced["id"] in block.included_ids
    assert recalled["id"] in block.included_ids
    assert block.diagnostics["forced"] == 1
    assert block.diagnostics["recalled"] >= 1


async def test_build_memory_context_defers_items_over_budget(temp_db):
    from services import long_term_memory_service, memory_orchestrator

    for idx in range(20):
        await long_term_memory_service.create_memory_item(
            book_id="b1",
            kind="plot",
            content=f"玉佩相关事实 {idx} " + "字" * 200,
            importance=3,
        )

    block = await memory_orchestrator.build_memory_context(
        {"bookId": "b1", "memoryBudget": 500},
        "玉佩",
        "ask",
    )

    assert block.included_ids
    assert block.deferred_ids
    assert "未注入" in block.text
    assert block.token_estimate <= 500
    assert block.diagnostics["deferred"] == len(block.deferred_ids)


async def test_build_memory_context_labels_outline_plan_as_not_fact(temp_db):
    from services import long_term_memory_service, memory_orchestrator

    await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="summary",
        scope_type="outline",
        scope_id="ol1",
        content="第三章计划让主角发现玉佩秘密",
        keywords="玉佩 第三章",
    )

    block = await memory_orchestrator.build_memory_context(
        {"bookId": "b1"},
        "玉佩秘密是什么？",
        "ask",
    )

    assert "大纲计划，非既成事实" in block.text
    assert "第三章计划让主角发现玉佩秘密" in block.text


async def test_build_memory_context_filters_forced_items_by_book(temp_db):
    from services import long_term_memory_service, memory_orchestrator

    same_book = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="canon",
        content="本书主角不能撒谎",
    )
    other_book = await long_term_memory_service.create_memory_item(
        book_id="b2",
        kind="canon",
        content="其他书主角能操控雷电",
    )

    block = await memory_orchestrator.build_memory_context(
        {
            "bookId": "b1",
            "selectedLongTermMemoryIds": [same_book["id"], other_book["id"]],
        },
        "主角的限制是什么？",
        "ask",
    )

    assert "本书主角不能撒谎" in block.text
    assert "其他书主角能操控雷电" not in block.text
    assert same_book["id"] in block.included_ids
    assert other_book["id"] not in block.included_ids
