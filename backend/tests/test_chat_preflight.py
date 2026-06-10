"""
表征测试：对话前置上下文（utils/chat_preflight）与协作模式简化后的行为。
"""

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
from utils.collab_prompt import (
    COLLAB_WRITE_TOOL_NAMES,
    build_collab_turn_appendix,
    filter_collab_tools,
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


# ── 协作模式：turn appendix 简化 + 写意图过滤拓宽 ─────────────────


async def test_collab_appendix_first_turn_vs_later():
    first = build_collab_turn_appendix([{"role": "user", "content": "想写个故事"}])
    assert "对话尚浅" in first
    later = build_collab_turn_appendix([
        {"role": "user", "content": "想写个故事"},
        {"role": "assistant", "content": "提案……"},
        {"role": "user", "content": "不同意，换个方向"},
    ])
    # 旧版关键词正则会让「不同意」命中「同意」分支；现在意图判断交给模型
    assert "用户已倾向确认" not in later
    assert "延续协商" in later


def _tools_with_write():
    return [
        {"function": {"name": "editChapterContent"}},
        {"function": {"name": "listWritingChapters"}},
    ]


async def test_filter_collab_tools_keeps_write_on_broad_intent():
    # 拓宽后的意图词：改成 / 修改 / 更新 等也算写意图
    kept = filter_collab_tools(_tools_with_write(), "把第三章结尾改成开放式")
    assert any(t["function"]["name"] in COLLAB_WRITE_TOOL_NAMES for t in kept)


async def test_filter_collab_tools_strips_write_on_pure_discussion():
    kept = filter_collab_tools(_tools_with_write(), "你觉得主角的动机够吗")
    assert all(t["function"]["name"] not in COLLAB_WRITE_TOOL_NAMES for t in kept)
    assert any(t["function"]["name"] == "listWritingChapters" for t in kept)
