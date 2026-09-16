from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection

from agents.writing.chapter_create_tools import (
    WRITING_CREATED_CHAPTER_IDS_STATE_KEY,
    SqliteWritingChapterCreateRepository,
    build_writing_chapter_create_registrations,
    created_chapter_ids,
)
from agents.writing.chapter_write_model import (
    SqliteWritingChapterRepository,
    WritingChapterMutationError,
    _content_revision,
)
from agents.writing.read_model import WritingReadScope


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        yield db
    finally:
        await db.close()


async def _seed_book(db) -> tuple[str, str]:
    await db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)", ["book-1", "测试书"],
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, sort, book_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ["wo-1", "写作", "writing", 1, "book-1"],
    )
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title, level, sort) "
        "VALUES (?, ?, ?, ?, ?)",
        ["ch-1", "wo-1", "第一章", 1, 1],
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, sort, book_id, writing_chapter_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["ol-1", "第一章", "chapter", 1, "book-1", "ch-1"],
    )
    return "book-1", "ch-1"


async def _seed_volume(db, parent_of_volume: str | None = "ch-1") -> str:
    await db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title, level, sort, parent_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["vol-1", "wo-1", "第一卷", 1, 2, parent_of_volume],
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, sort, book_id, writing_chapter_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["ol-vol", "第一卷", "volume", 1, "book-1", "vol-1"],
    )
    return "vol-1"


def _state(domain: dict | None = None):
    class _State:
        def __init__(self, d):
            self.domain = d if d is not None else {}

    return _State(domain=None) if False else _State(domain)


def _tool_arguments(*titles: str, parent_id: str | None = None):
    return {
        "chapters": [
            {
                "title": title,
                **({"parentId": parent_id} if parent_id else {}),
            }
            for title in titles
        ]
    }


@pytest.mark.asyncio
async def test_create_chapters_batch_appends_and_emits_effect(temp_db):
    db = temp_db
    await _seed_book(db)
    registrations = build_writing_chapter_create_registrations(db)
    create = registrations[0]

    state = _state({"writingReadScope": {"bookId": "book-1", "chapterId": "ch-1"}})
    result = await create.handler(
        state,
        _tool_arguments("第二章", "第三章"),
        None,
    )
    payload = json.loads(result.content)
    assert [item["title"] for item in payload["chapters"]] == ["第二章", "第三章"]
    assert payload["chapters"][0]["order"] == 2
    assert [e.type for e in result.effects] == ["writing.chapters_created"]

    rows = await db.fetch_all(
        "SELECT title FROM outline_chapters WHERE outline_id = ? ORDER BY sort",
        ["wo-1"],
    )
    assert [r["title"] for r in rows] == ["第一章", "第二章", "第三章"]
    outline_rows = await db.fetch_all(
        "SELECT title, type FROM outlines WHERE book_id = ? AND "
        "writing_chapter_id IS NOT NULL",
        ["book-1"],
    )
    assert {(r["title"], r["type"]) for r in outline_rows} == {
        ("第一章", "chapter"), ("第二章", "chapter"), ("第三章", "chapter"),
    }
    # 本 Run 创建的章节记录进 run state，供 editChapterContent 授权
    assert created_chapter_ids(state) == [
        item["chapterId"] for item in payload["chapters"]
    ]


@pytest.mark.asyncio
async def test_create_volume_and_chapter_under_volume(temp_db):
    db = temp_db
    await _seed_book(db)
    await _seed_volume(db)
    repository = SqliteWritingChapterCreateRepository(db)
    scope = WritingReadScope("book-1")

    payload = await repository.create(
        scope,
        chapters=({"title": "卷内第一章", "parentId": "vol-1"},),
        default_parent_id=None,
    )
    chapter_id = payload["chapters"][0]["chapterId"]
    row = await db.fetch_one(
        "SELECT parent_id FROM outline_chapters WHERE id = ?", [chapter_id],
    )
    assert row["parent_id"] == "vol-1"

    volume_payload = await repository.create(
        scope,
        chapters=({"title": "第二卷", "isVolume": True},),
        default_parent_id=None,
    )
    assert volume_payload["chapters"][0]["isVolume"] is True
    volume_outline = await db.fetch_one(
        "SELECT type FROM outlines WHERE writing_chapter_id = ?",
        [volume_payload["chapters"][0]["chapterId"]],
    )
    assert volume_outline["type"] == "volume"


@pytest.mark.asyncio
async def test_create_rejects_bad_parent_and_duplicate_titles(temp_db):
    db = temp_db
    await _seed_book(db)
    repository = SqliteWritingChapterCreateRepository(db)
    scope = WritingReadScope("book-1")

    from agents.writing.chapter_create_tools import WritingChapterCreateError

    with pytest.raises(WritingChapterCreateError) as parent_error:
        await repository.create(
            scope,
            chapters=({"title": "X", "parentId": "ch-1"},),
            default_parent_id=None,
        )
    assert parent_error.value.code == "writing_chapter_parent_invalid"

    with pytest.raises(WritingChapterCreateError) as duplicate_error:
        await repository.create(
            scope,
            chapters=({"title": "Y"}, {"title": "Y"}),
            default_parent_id=None,
        )
    assert duplicate_error.value.code == "writing_chapter_title_duplicate"


@pytest.mark.asyncio
async def test_scope_validator_has_no_write_side_effects(temp_db):
    db = temp_db
    await _seed_book(db)
    registrations = build_writing_chapter_create_registrations(db)
    create = registrations[0]

    state = _state({"writingReadScope": {"bookId": "book-1"}})
    code = await create.scope_validator(
        state, _tool_arguments("第二章"), None,
    )
    assert code is None
    rows = await db.fetch_all("SELECT title FROM outline_chapters")
    assert [r["title"] for r in rows] == ["第一章"]


@pytest.mark.asyncio
async def test_edit_allows_run_created_chapter_in_bound_session(temp_db):
    db = temp_db
    await _seed_book(db)
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) VALUES (?, ?, ?)",
        [7, "book-1", "ch-1"],
    )
    scope = WritingReadScope("book-1", session_id=7, chapter_id="ch-1")

    registrations = build_writing_chapter_create_registrations(db)
    create_state = _state({"writingReadScope": {"bookId": "book-1", "chapterId": "ch-1"}})
    create_result = await registrations[0].handler(
        create_state, _tool_arguments("第二章"), None,
    )
    created_payload = json.loads(create_result.content)
    new_chapter_id = created_payload["chapters"][0]["chapterId"]

    repository = SqliteWritingChapterRepository(db)
    base_revision = _content_revision("")

    # 绑定章节的会话：foreign 章节未在 created_ids 中 → 拒绝
    with pytest.raises(WritingChapterMutationError) as foreign:
        await repository.commit_edit(
            scope,
            content=json.dumps({"root": {"children": []}}),
            base_revision=base_revision,
            clear_content=False,
            chapter_id="other-chapter",
            created_ids=(),
        )
    assert foreign.value.code == "writing_chapter_scope_conflict"

    # 本 Run 创建的章节 → 允许写入
    receipt = await repository.commit_edit(
        scope,
        content=json.dumps({"root": {"children": []}}),
        base_revision=base_revision,
        clear_content=False,
        chapter_id=new_chapter_id,
        created_ids=(new_chapter_id,),
    )
    assert receipt["chapterId"] == new_chapter_id
    article = await db.fetch_one(
        "SELECT chapter_id FROM articles WHERE chapter_id = ?",
        [new_chapter_id],
    )
    assert article is not None


@pytest.mark.asyncio
async def test_edit_allows_any_chapter_in_global_conversation(temp_db):
    db = temp_db
    await _seed_book(db)
    await _seed_volume(db)
    # 全局对话：session 无 chapter 绑定
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id) VALUES (?, ?, NULL)",
        [8, "book-1"],
    )
    scope = WritingReadScope("book-1", session_id=8)
    repository = SqliteWritingChapterRepository(db)

    receipt = await repository.commit_edit(
        scope,
        content=json.dumps({"root": {"children": []}}),
        base_revision=_content_revision(""),
        clear_content=False,
        chapter_id="vol-1",
        created_ids=(),
    )
    assert receipt["chapterId"] == "vol-1"


@pytest.mark.asyncio
async def test_scope_validator_accepts_frozen_purra_arguments(temp_db):
    """purra 把参数冻结为 FrozenList/FrozenDict（非原生 list/dict），
    生产事故 run_a3d725c 曾因此误报 writing_chapter_batch_invalid。"""
    from purra.json_values import FrozenDict, FrozenList

    db = temp_db
    await _seed_book(db)
    registrations = build_writing_chapter_create_registrations(db)
    create = registrations[0]

    state = _state({
        "writingReadScope": {
            "bookId": "book-1",
            "sessionId": 7,
            "chapterId": "ch-1",
        },
    })
    frozen_arguments = FrozenDict({
        "chapters": FrozenList([FrozenDict({"title": "第55章"})]),
    })
    code = await create.scope_validator(state, frozen_arguments, None)
    assert code is None

    result = await create.handler(state, frozen_arguments, None)
    payload = json.loads(result.content)
    assert payload["chapters"][0]["title"] == "第55章"
    display = create.operation_display_params(state, frozen_arguments, None)
    assert display["toolArguments"]["chapters"] == [{"title": "第55章"}]
