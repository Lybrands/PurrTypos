"""故事健康度聚合与 readWritingDashboard 工具的共享实现测试。"""

from __future__ import annotations

import pytest

from agents.writing.read_model import SqliteWritingReadRepository, WritingReadScope
from agents.writing.read_tools import build_writing_read_tool_catalog
from application.story_health import build_story_health
from database.connection import DatabaseConnection
from purra.contracts import ToolExecutionMode


@pytest.fixture
async def db(tmp_path):
    conn = DatabaseConnection(tmp_path)
    await conn.init()
    try:
        await _seed(conn)
        yield conn
    finally:
        await conn.close()


async def _seed(db) -> None:
    await db.execute(
        "INSERT INTO books (id, title) VALUES ('book-1', '第一本'), ('book-2', '第二本')"
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, sort, book_id, markdown_content) "
        "VALUES ('writing-1', '写作目录', 'writing', 0, 'book-1', '')"
    )
    await db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, level, progress, sort, parent_id) VALUES "
        "('chapter-1', 'writing-1', '启程', 1, 'done', 1, NULL), "
        "('chapter-2', 'writing-1', '灯塔', 1, 'done', 2, NULL), "
        "('chapter-3', 'writing-1', '终章', 1, 'todo', 3, NULL)"
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES "
        "('chapter-1', '阿澈 阿澈 白砚 出发。'), "
        "('chapter-2', '白砚 白砚 白砚 守着灯塔。'), "
        "('chapter-3', '')"
    )
    await db.execute(
        "INSERT INTO characters (book_id, name, tags, profile_md) VALUES "
        "('book-1', '阿澈', '主角', ''), "
        "('book-1', '白砚', '导师', ''), "
        "('book-1', '无戏份', '路人', ''), "
        "('book-2', '外书人物', '', '')"
    )
    await db.execute(
        "INSERT INTO ai_foreshadowing "
        "(book_id, content, type, chapter_id, expected_chapter_id, status) VALUES "
        "('book-1', '潮汐门钥匙下落', '悬念', 'chapter-1', 'chapter-2', '未回收'), "
        "('book-1', '灯塔看守人的身份', '悬念', 'chapter-1', NULL, '未回收'), "
        "('book-1', '已回收的旧伏笔', '悬念', 'chapter-1', NULL, '已回收')"
    )


@pytest.mark.asyncio
async def test_character_chapter_refs_carry_mentions_in_reading_order(db):
    health = await build_story_health(db, "book-1")

    by_name = {c["name"]: c for c in health["characters"]}
    assert set(by_name) == {"阿澈", "白砚", "无戏份"}

    a_che = by_name["阿澈"]
    assert a_che["appearChapters"] == 1
    assert a_che["chapterRefs"] == [
        {"chapterId": "chapter-1", "index": 1, "title": "启程", "mentions": 2},
    ]
    assert a_che["lastChapterIndex"] == 1
    assert a_che["gapChapters"] == 1  # 最新已写章为第 2 章

    bai_yan = by_name["白砚"]
    assert bai_yan["appearChapters"] == 2
    assert bai_yan["chapterRefs"] == [
        {"chapterId": "chapter-1", "index": 1, "title": "启程", "mentions": 1},
        {"chapterId": "chapter-2", "index": 2, "title": "灯塔", "mentions": 3},
    ]
    assert bai_yan["lastChapterTitle"] == "灯塔"

    # 从未出场的人物排在最后且无明细
    absent = by_name["无戏份"]
    assert absent["appearChapters"] == 0
    assert absent["chapterRefs"] == []
    assert absent["gapChapters"] is None


@pytest.mark.asyncio
async def test_foreshadowing_warnings_keep_overdue_first(db):
    health = await build_story_health(db, "book-1")

    fs = health["foreshadowing"]
    assert fs["unresolvedCount"] == 2
    assert fs["resolvedCount"] == 1
    overdue = fs["unresolved"][0]
    # 预期第 2 章回收、最新已写章即第 2 章：不算逾期也不算临期之外的普通未回收
    assert overdue["content"] == "潮汐门钥匙下落"


@pytest.mark.asyncio
async def test_story_dashboard_repository_reuses_shared_aggregation(db):
    repository = SqliteWritingReadRepository(db)
    scope = WritingReadScope("book-1")

    payload = await repository.story_dashboard(scope)

    dashboard = payload["dashboard"]
    assert dashboard["totalChapters"] == 3
    assert dashboard["writtenChapters"] == 2
    names = {c["name"] for c in dashboard["characters"]}
    assert names == {"阿澈", "白砚", "无戏份"}  # 他书人物不进入本书仪表盘
    assert payload["scope"]["bookId"] == "book-1"


@pytest.mark.asyncio
async def test_writing_catalog_registers_read_only_dashboard_tool(db):
    catalog = build_writing_read_tool_catalog(db)
    registration = next(
        item for item in catalog.registrations() if item.schema.name == "readWritingDashboard"
    )

    assert registration.policy.mode is ToolExecutionMode.READ
    assert registration.schema.display_names["zh-CN"] == "读取写作仪表盘"
    assert registration.schema.parameters == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
