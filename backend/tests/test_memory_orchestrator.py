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


async def test_recalled_items_preserve_service_relevance_order(temp_db, monkeypatch):
    from services import long_term_memory_service, memory_orchestrator

    older = await long_term_memory_service.create_memory_item(
        book_id="b1", kind="plot", content="较低相关的玉佩背景"
    )
    newer = await long_term_memory_service.create_memory_item(
        book_id="b1", kind="plot", content="高度相关的玉佩当前状态"
    )

    async def _ranked(*_args, **_kwargs):
        return [newer, older]

    monkeypatch.setattr(long_term_memory_service, "search_memory_items", _ranked)
    block = await memory_orchestrator.build_memory_context(
        {"bookId": "b1"}, "玉佩现在怎样？", "ask",
    )

    assert block.text.index("高度相关") < block.text.index("较低相关")


async def test_supersedes_relation_expands_new_endpoint_and_hides_old_memory(temp_db):
    from services import long_term_memory_service, memory_orchestrator

    old = await long_term_memory_service.create_memory_item(
        book_id="b1", kind="canon", content="玉佩只能在雨夜发光"
    )
    new = await long_term_memory_service.create_memory_item(
        book_id="b1", kind="canon", content="神器现在可以在任何夜晚启动"
    )
    await long_term_memory_service.link_memory_items(
        book_id="b1",
        from_memory_id=new["id"],
        to_memory_id=old["id"],
        relation="supersedes",
        note="能力限制已经解除",
    )

    block = await memory_orchestrator.build_memory_context(
        {"bookId": "b1"}, "玉佩在雨夜会怎样？", "ask",
    )

    assert "神器现在可以在任何夜晚启动" in block.text
    assert "玉佩只能在雨夜发光" not in block.text
    assert old["id"] in block.suppressed_ids
    assert block.diagnostics["relationExpanded"] == 1


async def test_contradicting_memories_are_injected_with_explicit_warning(temp_db):
    from services import long_term_memory_service, memory_orchestrator

    first = await long_term_memory_service.create_memory_item(
        book_id="b1", kind="character", content="林岚的眼睛是黑色"
    )
    second = await long_term_memory_service.create_memory_item(
        book_id="b1", kind="character", content="女主的瞳色已经变成银色"
    )
    await long_term_memory_service.link_memory_items(
        book_id="b1",
        from_memory_id=second["id"],
        to_memory_id=first["id"],
        relation="contradicts",
        note="瞳色设定尚未确认",
    )

    block = await memory_orchestrator.build_memory_context(
        {"bookId": "b1"}, "林岚的眼睛是什么颜色？", "ask",
    )

    assert "林岚的眼睛是黑色" in block.text
    assert "女主的瞳色已经变成银色" in block.text
    assert "记忆关系警告" in block.text
    assert "存在未解决冲突" in block.text
    assert block.diagnostics["conflicts"] == 1


async def test_budget_diagnostics_only_mark_ids_that_really_appear(temp_db):
    from services import long_term_memory_service, memory_orchestrator

    for idx in range(3):
        await long_term_memory_service.create_memory_item(
            book_id="b1",
            kind="plot",
            content=f"玉佩事实 {idx} " + "字" * 300,
        )

    block = await memory_orchestrator.build_memory_context(
        {"bookId": "b1", "memoryBudget": 320}, "玉佩", "ask",
    )

    assert len(block.text) <= 320
    assert block.deferred_ids
    for memory_id in block.included_ids:
        assert f"[id:{memory_id}|" in block.text
    assert block.diagnostics["characterCount"] == len(block.text)


async def test_explicit_zero_memory_budget_does_not_fall_back_to_default(temp_db):
    from services import long_term_memory_service, memory_orchestrator

    await long_term_memory_service.create_memory_item(
        book_id="b1", kind="plot", content="must not be injected",
    )

    block = await memory_orchestrator.build_memory_context(
        {"bookId": "b1", "memoryBudget": 0}, "injected", "ask",
    )

    assert block.text == ""
    assert block.included_ids == []


async def test_token_estimate_is_not_raw_character_count_for_latin_text():
    from services.memory_orchestrator import estimate_tokens

    text = "the silver pendant glows at midnight"
    assert 0 < estimate_tokens(text) < len(text)
