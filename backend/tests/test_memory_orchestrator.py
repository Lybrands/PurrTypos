from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from application.writing_memory_context import build_writing_memory_context
from database.connection import DatabaseConnection
from dependencies import clear_db, set_db
from schemas.memories import BuildMemoryContextRequest
from services import long_term_memory_service


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        yield db
    finally:
        clear_db(db)
        await db.close()


async def _build(
    db: DatabaseConnection,
    *,
    book_id: str = "b1",
    user_prompt: str = "",
    mode: str = "ask",
    selected_long_term_memory_ids=None,
    selected_memory_ids=None,
    selected_foreshadowing_ids=None,
    memory_budget: int | None = None,
    memory_recall_limit: int | None = None,
    context_window: str | None = None,
):
    return await build_writing_memory_context(
        BuildMemoryContextRequest(
            bookId=book_id,
            userPrompt=user_prompt,
            mode=mode,
            selectedLongTermMemoryIds=selected_long_term_memory_ids,
            selectedMemoryIds=selected_memory_ids,
            selectedForeshadowingIds=selected_foreshadowing_ids,
            memoryBudget=memory_budget,
            memoryRecallLimit=memory_recall_limit,
            contextWindow=context_window,
        ),
        db=db,
    )


async def test_application_context_groups_forced_and_recalled_items(temp_db):
    forced = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="canon",
        content="主角不能撒谎",
        pinned=1,
    )
    recalled = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="plot",
        content="玉佩已经在雨夜发光",
    )

    result = await _build(
        temp_db,
        user_prompt="玉佩现在有什么异常？",
        mode="agent",
        selected_long_term_memory_ids=[forced["id"]],
    )
    block = result.block
    response = result.to_response_data()

    assert set(response) == {
        "text",
        "includedIds",
        "deferredIds",
        "suppressedIds",
        "tokenEstimate",
        "diagnostics",
    }
    assert response["text"] == block.text
    assert response["includedIds"] == block.included_ids
    assert "必须遵循的设定" in block.text
    assert "已发生的剧情事实" in block.text
    assert "主角不能撒谎" in block.text
    assert "玉佩已经在雨夜发光" in block.text
    assert forced["id"] in block.included_ids
    assert recalled["id"] in block.included_ids
    assert block.diagnostics["forced"] == 1
    assert block.diagnostics["recalled"] >= 1
    assert block.selected_fact is not None
    assert block.selected_fact.requested_count == 1
    assert block.selected_fact.complete_count == 1
    assert block.selected_fact.status == "complete"
    assert block.selected_fact.search_tools == ("searchMemories",)


async def test_application_context_defers_items_over_budget(temp_db):
    for idx in range(20):
        await long_term_memory_service.create_memory_item(
            book_id="b1",
            kind="plot",
            content=f"玉佩相关事实 {idx} " + "字" * 200,
            importance=3,
        )

    result = await _build(
        temp_db,
        user_prompt="玉佩",
        memory_budget=500,
    )
    block = result.block

    assert block.included_ids
    assert block.deferred_ids
    assert "未注入" in block.text
    assert block.token_estimate <= 500
    assert block.diagnostics["deferred"] == len(block.deferred_ids)


async def test_application_selected_manifest_marks_shortened_item_truncated(temp_db):
    selected = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="canon",
        content="强制设定 " + "字" * 2_000,
    )

    block = (
        await _build(
            temp_db,
            mode="agent",
            selected_long_term_memory_ids=[selected["id"]],
            memory_budget=240,
        )
    ).block

    assert selected["id"] in block.included_ids
    assert block.selected_fact is not None
    assert block.selected_fact.status == "truncated"
    assert block.selected_fact.complete_count == 0
    assert block.selected_fact.truncated_count == 1


async def test_application_context_labels_outline_plan_as_not_fact(temp_db):
    await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="summary",
        scope_type="outline",
        scope_id="ol1",
        content="第三章计划让主角发现玉佩秘密",
        keywords="玉佩 第三章",
    )

    block = (
        await _build(temp_db, user_prompt="玉佩秘密是什么？")
    ).block

    assert "大纲计划，非既成事实" in block.text
    assert "第三章计划让主角发现玉佩秘密" in block.text


async def test_application_context_filters_selected_items_by_book(temp_db):
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

    block = (
        await _build(
            temp_db,
            user_prompt="主角的限制是什么？",
            selected_long_term_memory_ids=[same_book["id"], other_book["id"]],
        )
    ).block

    assert "本书主角不能撒谎" in block.text
    assert "其他书主角能操控雷电" not in block.text
    assert same_book["id"] in block.included_ids
    assert other_book["id"] not in block.included_ids


async def test_application_context_preserves_repository_relevance_order(temp_db):
    older = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="plot",
        content="较低相关的玉佩背景",
        importance=1,
    )
    newer = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="plot",
        content="高度相关的玉佩当前状态",
        importance=9,
    )

    block = (
        await _build(temp_db, user_prompt="请说明玉佩现在怎样变化")
    ).block

    assert older["id"] in block.included_ids
    assert newer["id"] in block.included_ids
    assert block.text.index("高度相关") < block.text.index("较低相关")


async def test_application_context_applies_supersedes_relation(temp_db):
    old = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="canon",
        content="玉佩只能在雨夜发光",
    )
    new = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="canon",
        content="神器现在可以在任何夜晚启动",
    )
    await long_term_memory_service.link_memory_items(
        book_id="b1",
        from_memory_id=new["id"],
        to_memory_id=old["id"],
        relation="supersedes",
        note="能力限制已经解除",
    )

    block = (
        await _build(temp_db, user_prompt="玉佩在雨夜会怎样？")
    ).block

    assert "神器现在可以在任何夜晚启动" in block.text
    assert "玉佩只能在雨夜发光" not in block.text
    assert old["id"] in block.suppressed_ids
    assert block.diagnostics["relationExpanded"] == 1


async def test_application_context_marks_contradicting_memories(temp_db):
    first = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="character",
        content="林岚的眼睛是黑色",
    )
    second = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="character",
        content="女主的瞳色已经变成银色",
    )
    await long_term_memory_service.link_memory_items(
        book_id="b1",
        from_memory_id=second["id"],
        to_memory_id=first["id"],
        relation="contradicts",
        note="瞳色设定尚未确认",
    )

    block = (
        await _build(temp_db, user_prompt="林岚的眼睛是什么颜色？")
    ).block

    assert "林岚的眼睛是黑色" in block.text
    assert "女主的瞳色已经变成银色" in block.text
    assert "记忆关系警告" in block.text
    assert "存在未解决冲突" in block.text
    assert block.diagnostics["conflicts"] == 1


async def test_application_budget_diagnostics_only_include_rendered_ids(temp_db):
    for idx in range(3):
        await long_term_memory_service.create_memory_item(
            book_id="b1",
            kind="plot",
            content=f"玉佩事实 {idx} " + "字" * 300,
        )

    block = (
        await _build(temp_db, user_prompt="玉佩", memory_budget=320)
    ).block

    assert len(block.text) <= 320
    assert block.deferred_ids
    for memory_id in block.included_ids:
        assert f"[id:{memory_id}|" in block.text
    assert block.diagnostics["characterCount"] == len(block.text)


async def test_application_explicit_zero_budget_never_uses_default(temp_db):
    await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="plot",
        content="must not be injected",
    )

    block = (
        await _build(
            temp_db,
            user_prompt="injected",
            memory_budget=0,
        )
    ).block

    assert block.text == ""
    assert block.included_ids == []

    selected = await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="canon",
        content="selected but unavailable",
    )
    selected_block = (
        await _build(
            temp_db,
            mode="agent",
            selected_long_term_memory_ids=[selected["id"]],
            memory_budget=0,
        )
    ).block
    assert selected_block.selected_fact is not None
    assert selected_block.selected_fact.status == "not_injected"
    assert selected_block.selected_fact.not_injected_count == 1


async def test_application_response_uses_token_estimate_not_character_count(temp_db):
    await long_term_memory_service.create_memory_item(
        book_id="b1",
        kind="plot",
        content="the silver pendant glows at midnight during the winter solstice",
    )

    response = (
        await _build(temp_db, user_prompt="silver pendant")
    ).to_response_data()

    assert "silver pendant" in response["text"]
    assert 0 < response["tokenEstimate"] < len(response["text"])
