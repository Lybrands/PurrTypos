"""表征测试：对话前置上下文（utils/chat_preflight）的行为。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import set_db
from utils.chat_preflight import (
    PER_CHAPTER_CAP,
    build_associated_context_block,
    build_selected_memory_block,
    build_session_binding_prompt,
)

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


def _lexical(text: str) -> str:
    return json.dumps({
        "root": {
            "children": [{
                "type": "paragraph",
                "children": [{"type": "text", "text": text}],
            }],
        },
    })


async def _seed_chapter(db: DatabaseConnection, chapter_id: str, text: str) -> None:
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        [chapter_id, _lexical(text)],
    )


# ── build_associated_context_block ──────────────────────────────


async def test_assoc_block_empty_without_associations(temp_db):
    assert await build_associated_context_block({"bookId": "b1"}) == ""
    assert await build_associated_context_block({}) == ""


async def test_assoc_block_injects_chapter_text(temp_db):
    await _seed_chapter(temp_db, "ch1", "第一章正文内容")
    block = await build_associated_context_block({
        "bookId": "b1",
        "associatedChapterIds": ["ch1"],
        "writingChapters": [{"id": "ch1", "title": "第一章"}],
    })
    assert "关联章节《第一章》(chapterId=ch1)" in block
    assert "第一章正文内容" in block
    assert "…（已截断" not in block


async def test_assoc_block_truncates_long_chapter(temp_db):
    long_text = "字" * (PER_CHAPTER_CAP + 500)
    await _seed_chapter(temp_db, "ch1", long_text)
    block = await build_associated_context_block({
        "bookId": "b1",
        "associatedChapterIds": ["ch1"],
        "writingChapters": [{"id": "ch1", "title": "长章"}],
    })
    assert "已截断" in block
    assert 'getChapterContent 读取，参数 chapterId="ch1"' in block
    # 注入内容不超过单章上限（允许格式行的额外长度）
    assert len(block) < PER_CHAPTER_CAP + 1000


async def test_assoc_block_injects_outline_markdown(temp_db):
    await temp_db.execute(
        "INSERT INTO outlines (id, title, type, book_id, markdown_content) "
        "VALUES (?, ?, 'chapter', ?, ?)",
        ["ol1", "第一章大纲", "b1", "# 节拍\n- 开场"],
    )
    block = await build_associated_context_block({
        "bookId": "b1",
        "associatedOutlineIds": ["ol1"],
        "availableOutlines": [{"id": "ol1", "title": "第一章大纲"}],
    })
    assert "关联大纲《第一章大纲》(outlineId=ol1)" in block
    assert "# 节拍" in block


async def test_assoc_block_handles_missing_chapter(temp_db):
    block = await build_associated_context_block({
        "bookId": "b1",
        "associatedChapterIds": ["ghost"],
        "writingChapters": [],
    })
    # 拉不到正文 → 视为暂无正文，不抛错不阻断
    assert "chapterId=ghost" in block
    assert "（本章暂无正文）" in block


# ── build_selected_memory_block ─────────────────────────────────


async def test_memory_block_empty_without_ids(temp_db):
    assert await build_selected_memory_block(None, None) == ""
    assert await build_selected_memory_block([], []) == ""


async def test_memory_block_renders_sparks_and_foreshadowing(temp_db):
    from services import memory_service

    spark = await memory_service.add_spark_idea("b1", "世界观", "灵气复苏始于昆仑")
    fs = await memory_service.add_foreshadowing(
        "b1", "ch1", "主角的玉佩会发光", type_="悬念",
    )

    block = await build_selected_memory_block([spark["id"]], [fs["id"]])
    assert "已勾选的本书设定" in block
    assert "- [世界观] 灵气复苏始于昆仑" in block
    assert "已勾选的伏笔" in block
    assert "- [悬念|未回收] 主角的玉佩会发光" in block


# ── build_session_binding_prompt ────────────────────────────────


async def test_binding_prompt_empty_without_book():
    assert build_session_binding_prompt({}, tools_enabled=True) == ""


async def test_binding_prompt_variants():
    ctx = {"bookId": "b1", "currentChapterTitle": "第三章"}
    agent = build_session_binding_prompt(ctx, tools_enabled=True)
    ask = build_session_binding_prompt(ctx, tools_enabled=False)
    assert "《第三章》" in agent and "勿猜测数据库 id" in agent
    assert "《第三章》" in ask and "无法调用工具" in ask

