"""
表征测试：outlines 路由收敛到 crud 层之后的行为。

覆盖双轨合并后的关键行为：
- save 走 crud（sort 递增、global 去重、markdown_content 落库）
- update 写入前快照到 outline_history，restore 可回退
- delete 级联清理 outline_chapters
- writing/global ensure 语义与 books 路由保持一致
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from application.agent_composition import (
    clear_agent_composition,
    set_agent_composition,
)
from dependencies import set_db
from routers.outlines import (
    delete_outline,
    ensure_global_outline,
    get_global_outline,
    get_outline_history_list,
    get_outlines,
    get_writing_outline,
    restore_outline_history,
    save_outline,
    update_outline,
)
from schemas.outlines import SaveOutlineRequest, UpdateOutlineRequest

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def temp_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    composition = SimpleNamespace(memory_resource=None)
    set_agent_composition(composition)
    try:
        yield db
    finally:
        clear_agent_composition(composition)
        await db.close()


async def _insert_book(db: DatabaseConnection, book_id: str = "book1") -> str:
    await db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)", [book_id, "测试书"]
    )
    return book_id


async def test_save_outline_returns_db_row_with_markdown(temp_db: DatabaseConnection):
    bid = await _insert_book(temp_db)
    res = await save_outline(SaveOutlineRequest(
        title="第一章大纲",
        type="chapter",
        book_id=bid,
        markdown_content="# 开篇",
    ))
    assert res["success"] is True
    row = res["data"]
    assert row["title"] == "第一章大纲"
    assert row["markdown_content"] == "# 开篇"
    assert row["book_id"] == bid


async def test_save_outline_assigns_incrementing_sort(temp_db: DatabaseConnection):
    bid = await _insert_book(temp_db)
    r1 = await save_outline(SaveOutlineRequest(title="A", type="chapter", book_id=bid))
    r2 = await save_outline(SaveOutlineRequest(title="B", type="chapter", book_id=bid))
    assert r2["data"]["sort"] == r1["data"]["sort"] + 1


async def test_save_global_outline_dedups(temp_db: DatabaseConnection):
    bid = await _insert_book(temp_db)
    r1 = await save_outline(SaveOutlineRequest(title="总纲v1", type="global", book_id=bid))
    r2 = await save_outline(SaveOutlineRequest(title="总纲v2", type="global", book_id=bid))
    # 第二次保存应复用同一行而不是新建
    assert r2["data"]["id"] == r1["data"]["id"]
    assert r2["data"]["title"] == "总纲v2"
    rows = (await get_outlines(type="global"))["data"]
    assert len(rows) == 1


async def test_get_outlines_filters_any_type(temp_db: DatabaseConnection):
    bid = await _insert_book(temp_db)
    await save_outline(SaveOutlineRequest(title="卷一", type="volume", book_id=bid))
    await save_outline(SaveOutlineRequest(title="章一", type="chapter", book_id=bid))
    vols = (await get_outlines(type="volume"))["data"]
    assert [r["title"] for r in vols] == ["卷一"]
    everything = (await get_outlines(type=None))["data"]
    assert len(everything) == 2


async def test_update_outline_snapshots_history_and_restores(
    temp_db: DatabaseConnection,
):
    bid = await _insert_book(temp_db)
    created = await save_outline(SaveOutlineRequest(
        title="原标题", type="chapter", book_id=bid, markdown_content="旧内容",
    ))
    oid = created["data"]["id"]

    await update_outline(oid, UpdateOutlineRequest(markdown_content="新内容"))

    history = (await get_outline_history_list(oid))["data"]
    assert len(history) == 1
    assert history[0]["source"] == "user"
    assert history[0]["markdown_preview"] == "旧内容"

    restored = await restore_outline_history(history[0]["id"])
    assert restored["success"] is True
    assert restored["data"]["markdown_content"] == "旧内容"
    # 回退本身也要再留一条快照（可 redo）
    history2 = (await get_outline_history_list(oid))["data"]
    assert len(history2) == 2


async def test_update_side_fields_skip_history(temp_db: DatabaseConnection):
    bid = await _insert_book(temp_db)
    created = await save_outline(SaveOutlineRequest(
        title="标题", type="chapter", book_id=bid,
    ))
    oid = created["data"]["id"]
    res = await update_outline(oid, UpdateOutlineRequest(type="volume"))
    assert res["data"]["type"] == "volume"
    history = (await get_outline_history_list(oid))["data"]
    assert history == []


async def test_delete_outline_cascades_chapters(temp_db: DatabaseConnection):
    bid = await _insert_book(temp_db)
    created = await save_outline(SaveOutlineRequest(
        title="写作", type="writing", book_id=bid,
    ))
    oid = created["data"]["id"]
    await temp_db.execute(
        "INSERT INTO outline_chapters (id, outline_id, title) VALUES (?, ?, ?)",
        ["ch1", oid, "第一章"],
    )

    await delete_outline(oid)

    row = await temp_db.fetch_one("SELECT * FROM outlines WHERE id = ?", [oid])
    assert row is None
    ch = await temp_db.fetch_one(
        "SELECT * FROM outline_chapters WHERE outline_id = ?", [oid]
    )
    assert ch is None


async def test_get_writing_outline_creates_with_book_title(
    temp_db: DatabaseConnection,
):
    bid = await _insert_book(temp_db)
    res = await get_writing_outline(bid)
    assert res["data"]["type"] == "writing"
    # 与 books 路由建书逻辑一致：写作大纲标题用书名
    assert res["data"]["title"] == "测试书"
    # 再取一次应复用同一行
    res2 = await get_writing_outline(bid)
    assert res2["data"]["id"] == res["data"]["id"]


async def test_ensure_global_outline_idempotent(temp_db: DatabaseConnection):
    bid = await _insert_book(temp_db)
    r1 = await ensure_global_outline(bid)
    r2 = await ensure_global_outline(bid)
    assert r1["data"]["id"] == r2["data"]["id"]
    assert r1["data"]["title"] == "总纲"


async def test_get_global_outline_falls_back_to_legacy_row(
    temp_db: DatabaseConnection,
):
    bid = await _insert_book(temp_db)
    # 旧版本数据：book_id 为 NULL 的全局总纲
    await temp_db.execute(
        "INSERT INTO outlines (id, title, type) VALUES (?, ?, 'global')",
        ["legacy01", "旧总纲"],
    )
    res = await get_global_outline(bid)
    assert res["data"] is not None
    assert res["data"]["id"] == "legacy01"
