"""
设定类写工具（人物 / 故事背景）的表征测试：
createCharacter / updateCharacter / editStoryBackground 的成功路径、
归属校验、部分更新语义、settingUpdated 副作用 chunk，
以及人物档案 Markdown 化的存量数据迁移。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from database.crud.characters import get_characters
from database.crud.story_background import get_story_background
from database.schema import init_schema
from dependencies import set_db
from services.tool_executor import TOOL_HANDLERS
from utils.text import format_characters_as_text

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


def _collect_chunks() -> tuple[list, callable]:
    chunks: list = []
    return chunks, chunks.append


async def _run(name: str, ctx: dict, args: dict, send_chunk=None):
    result = await TOOL_HANDLERS[name](ctx, args, send_chunk)
    return json.loads(result.content)


# ── createCharacter ──────────────────────────────────────────────


async def test_create_character_success_and_side_effect(temp_db):
    chunks, send = _collect_chunks()
    out = await _run(
        "createCharacter",
        {"bookId": "b1"},
        {"name": "林夜", "tags": "主角, 神秘人物", "profileMd": "## 性格\n冷静, 多疑"},
        send,
    )
    assert out["success"] is True
    assert out["name"] == "林夜"

    rows = await get_characters(temp_db, "b1")
    assert len(rows) == 1
    assert rows[0]["tags"] == "主角, 神秘人物"
    assert rows[0]["profile_md"] == "## 性格\n冷静, 多疑"

    assert any(
        c.get("settingUpdated", {}).get("kind") == "character"
        and c["settingUpdated"].get("action") == "create"
        for c in chunks
    )


async def test_create_character_requires_name(temp_db):
    out = await _run("createCharacter", {"bookId": "b1"}, {"name": "  "})
    assert out["success"] is False
    assert "name" in out["error"]


# ── updateCharacter ──────────────────────────────────────────────


async def test_update_character_partial_update(temp_db):
    await _run(
        "createCharacter",
        {"bookId": "b1"},
        {"name": "苏棠", "tags": "配角", "profileMd": "## 经历\n幼年随父行医"},
    )
    cid = (await get_characters(temp_db, "b1"))[0]["id"]

    chunks, send = _collect_chunks()
    out = await _run(
        "updateCharacter",
        {"bookId": "b1"},
        {"characterId": cid, "tags": "配角, 医生"},
        send,
    )
    assert out["success"] is True
    assert out["updatedFields"] == ["tags"]

    row = (await get_characters(temp_db, "b1"))[0]
    assert row["tags"] == "配角, 医生"
    # 部分更新：未传字段保持原值
    assert row["profile_md"] == "## 经历\n幼年随父行医"
    assert row["name"] == "苏棠"
    assert any(c.get("settingUpdated", {}).get("kind") == "character" for c in chunks)


async def test_update_character_rejects_other_books_character(temp_db):
    await _run("createCharacter", {"bookId": "b1"}, {"name": "甲"})
    cid = (await get_characters(temp_db, "b1"))[0]["id"]

    out = await _run("updateCharacter", {"bookId": "b2"}, {"characterId": cid, "name": "乙"})
    assert out["success"] is False
    assert "不属于当前书籍" in out["error"]
    assert (await get_characters(temp_db, "b1"))[0]["name"] == "甲"


async def test_update_character_requires_updatable_field(temp_db):
    await _run("createCharacter", {"bookId": "b1"}, {"name": "甲"})
    cid = (await get_characters(temp_db, "b1"))[0]["id"]
    out = await _run("updateCharacter", {"bookId": "b1"}, {"characterId": cid})
    assert out["success"] is False
    assert "缺少可更新字段" in out["error"]


# ── 人物档案 Markdown 化：迁移 + prompt 文本 ─────────────────────


async def test_legacy_character_fields_migrate_to_profile_md(temp_db):
    # 模拟旧版本写入的表单字段数据（profile_md 为 NULL = 未迁移哨兵）
    await temp_db.execute(
        "INSERT INTO characters (book_id, name, gender, age, occupation, "
        "personality, biography, tags, profile_md) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        ["b1", "老周", "男", "52", "茶馆老板", "圆滑, 重情义", "退伍后开茶馆", "配角"],
    )
    # 再跑一次 schema 初始化触发迁移（幂等）
    await init_schema(temp_db)

    row = (await get_characters(temp_db, "b1"))[0]
    md = row["profile_md"]
    assert "## 基本信息" in md
    assert "- 性别：男" in md
    assert "- 职业：茶馆老板" in md
    assert "## 性格\n圆滑, 重情义" in md
    assert "## 人物小传\n退伍后开茶馆" in md
    # tags 保持结构化，不进 markdown
    assert row["tags"] == "配角"
    assert "配角" not in md

    # 已迁移的行不会被重复改写
    await temp_db.execute(
        "UPDATE characters SET profile_md = ? WHERE id = ?", ["手动改过", row["id"]]
    )
    await init_schema(temp_db)
    row2 = (await get_characters(temp_db, "b1"))[0]
    assert row2["profile_md"] == "手动改过"


async def test_format_characters_as_text_renders_markdown(temp_db):
    await _run(
        "createCharacter",
        {"bookId": "b1"},
        {"name": "林夜", "tags": "主角", "profileMd": "## 性格\n冷静"},
    )
    rows = await get_characters(temp_db, "b1")
    text = format_characters_as_text(rows)
    assert f"### 林夜（人物ID:{rows[0]['id']}）" in text
    assert "标签：主角" in text
    assert "## 性格\n冷静" in text


# ── editStoryBackground ──────────────────────────────────────────


async def test_edit_story_background_overwrites(temp_db):
    chunks, send = _collect_chunks()
    out = await _run(
        "editStoryBackground",
        {"bookId": "b1"},
        {"content": "# 世界观\n\n灵气复苏后的近未来都市。"},
        send,
    )
    assert out["success"] is True

    row = await get_story_background(temp_db, "b1")
    assert "灵气复苏" in row["content"]
    assert any(c.get("settingUpdated", {}).get("kind") == "background" for c in chunks)

    # 二次覆盖写入
    await _run("editStoryBackground", {"bookId": "b1"}, {"content": "新版本"})
    row = await get_story_background(temp_db, "b1")
    assert row["content"] == "新版本"


async def test_edit_story_background_requires_string_content(temp_db):
    out = await _run("editStoryBackground", {"bookId": "b1"}, {"content": None})
    assert out["success"] is False
