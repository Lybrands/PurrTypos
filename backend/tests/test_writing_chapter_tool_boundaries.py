from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from infrastructure.writing import WritingToolDependencies
from infrastructure.writing.tools.handlers import chapter_tools


@pytest_asyncio.fixture
async def chapter_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
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


async def _seed_book(
    db: DatabaseConnection,
    *,
    book_id: str,
    outline_id: str,
    group_id: str,
    chapter_id: str,
    chapter_text: str,
) -> None:
    await db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        [book_id, book_id],
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES (?, ?, 'writing', ?)",
        [outline_id, outline_id, book_id],
    )
    await db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, level, sort, parent_id) "
        "VALUES (?, ?, ?, 1, 1, NULL)",
        [group_id, outline_id, f"{book_id}分卷"],
    )
    await db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, level, sort, parent_id) "
        "VALUES (?, ?, '第1章', 2, 1, ?)",
        [chapter_id, outline_id, group_id],
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        [chapter_id, _lexical(chapter_text)],
    )


def _catalog_rows(*, book_id: str, group_id: str, chapter_id: str) -> list[dict]:
    return [
        {
            "id": group_id,
            "title": f"{book_id}分卷",
            "parent_id": None,
        },
        {
            "id": chapter_id,
            "title": "第1章",
            "parent_id": group_id,
        },
    ]


@pytest.mark.asyncio
async def test_chapter_tools_reject_cross_book_ids_even_when_catalog_and_cache_allow_them(
    chapter_db: DatabaseConnection,
):
    await _seed_book(
        chapter_db,
        book_id="book-a",
        outline_id="writing-a",
        group_id="group-a",
        chapter_id="chapter-a",
        chapter_text="本书正文",
    )
    await _seed_book(
        chapter_db,
        book_id="book-b",
        outline_id="writing-b",
        group_id="group-b",
        chapter_id="chapter-b",
        chapter_text="另一书秘密正文",
    )
    dependencies = WritingToolDependencies(chapter_db, object())  # type: ignore[arg-type]
    context = {
        "bookId": "book-a",
        "writingChapters": [
            *_catalog_rows(
                book_id="book-a",
                group_id="group-a",
                chapter_id="chapter-a",
            ),
            *_catalog_rows(
                book_id="book-b",
                group_id="group-b",
                chapter_id="chapter-b",
            ),
        ],
        "chapterContentCache": {
            "chapter-b": {
                "plainTextFull": "缓存中的另一书秘密正文",
                "titleResolved": "伪造标题",
            },
        },
    }

    read_result = await chapter_tools._tool_get_chapter_content(
        dependencies,
        context,
        {"chapterId": "chapter-b"},
        None,
    )
    batch_result = await chapter_tools._tool_batch_get_chapter_contents(
        dependencies,
        context,
        {"chapterIds": ["chapter-a", "chapter-b"]},
        None,
    )
    edit_events: list[dict] = []
    edit_result = await chapter_tools._tool_edit_chapter_content(
        dependencies,
        context,
        {"chapterId": "chapter-b", "content": "越权改写"},
        edit_events.append,
    )
    before_count = await chapter_db.fetch_one(
        "SELECT COUNT(*) AS count FROM outline_chapters "
        "WHERE outline_id = 'writing-a'",
    )
    create_result = await chapter_tools._tool_create_writing_chapter(
        dependencies,
        context,
        {"parentId": "group-b"},
        None,
    )
    after_count = await chapter_db.fetch_one(
        "SELECT COUNT(*) AS count FROM outline_chapters "
        "WHERE outline_id = 'writing-a'",
    )

    assert json.loads(read_result.content) == {
        "error": "失败",
        "chapterId": "chapter-b",
    }
    assert read_result.from_cache is False
    assert json.loads(batch_result.content) == {"error": "失败"}
    assert json.loads(edit_result.content) == {
        "success": False,
        "error": "失败",
        "chapterId": "chapter-b",
    }
    assert edit_events == []
    assert json.loads(create_result.content) == {
        "success": False,
        "error": "失败",
        "parentId": "group-b",
    }
    assert before_count == after_count == {"count": 2}
    for result in (read_result, batch_result, edit_result, create_result):
        assert "秘密正文" not in result.content


@pytest.mark.asyncio
async def test_empty_runtime_catalog_uses_database_book_scope_for_all_chapter_tools(
    chapter_db: DatabaseConnection,
):
    await _seed_book(
        chapter_db,
        book_id="book-a",
        outline_id="writing-a",
        group_id="group-a",
        chapter_id="chapter-a",
        chapter_text="数据库中的本书正文",
    )
    dependencies = WritingToolDependencies(chapter_db, object())  # type: ignore[arg-type]
    context = {"bookId": "book-a", "writingChapters": []}

    read_result = await chapter_tools._tool_get_chapter_content(
        dependencies,
        context,
        {"chapterId": "chapter-a"},
        None,
    )
    batch_result = await chapter_tools._tool_batch_get_chapter_contents(
        dependencies,
        context,
        {"chapterIds": ["chapter-a"]},
        None,
    )
    edit_events: list[dict] = []
    edit_result = await chapter_tools._tool_edit_chapter_content(
        dependencies,
        context,
        {"chapterId": "chapter-a", "content": "新的候选正文"},
        edit_events.append,
    )
    create_result = await chapter_tools._tool_create_writing_chapter(
        dependencies,
        context,
        {"parentId": "group-a"},
        None,
    )

    assert json.loads(read_result.content) == {
        "chapterId": "chapter-a",
        "title": "第1章",
        "plainText": "数据库中的本书正文",
    }
    assert json.loads(batch_result.content) == [{
        "chapterId": "chapter-a",
        "title": "第1章",
        "plainText": "数据库中的本书正文",
    }]
    assert json.loads(edit_result.content) == {
        "success": True,
        "message": "已向用户提交差异预览，需用户在编辑器接受/拒绝后才会写入正文",
        "chapterId": "chapter-a",
        "pendingUserApproval": True,
    }
    assert edit_events == [{
        "proposedChapterDiff": {
            "chapterId": "chapter-a",
            "beforeText": "数据库中的本书正文",
            "proposedText": "新的候选正文",
            "source": "ai_tool_edit",
        },
    }]
    created = json.loads(create_result.content)
    assert created["success"] is True
    assert created["chapter"]["title"] == "第2章"
    assert created["chapter"]["parentId"] == "group-a"
    stored = await chapter_db.fetch_one(
        "SELECT outline_id, title, parent_id FROM outline_chapters WHERE id = ?",
        [created["chapter"]["id"]],
    )
    assert stored == {
        "outline_id": "writing-a",
        "title": "第2章",
        "parent_id": "group-a",
    }
    assert {row["id"] for row in context["writingChapters"]} == {
        "group-a",
        "chapter-a",
        created["chapter"]["id"],
    }


@pytest.mark.asyncio
async def test_create_chapter_does_not_initialize_an_unknown_book(
    chapter_db: DatabaseConnection,
):
    result = await chapter_tools._tool_create_writing_chapter(
        WritingToolDependencies(chapter_db, object()),  # type: ignore[arg-type]
        {"bookId": "missing-book", "writingChapters": []},
        {},
        None,
    )

    assert json.loads(result.content) == {
        "success": False,
        "error": "当前书籍不存在，无法创建章节",
    }
    assert await chapter_db.fetch_all(
        "SELECT id, book_id FROM outlines"
    ) == []
