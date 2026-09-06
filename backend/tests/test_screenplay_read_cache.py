from __future__ import annotations

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from infrastructure.screenplay.tools import read_cache
from infrastructure.screenplay.tools.query import ScreenplayToolQuery
from infrastructure.screenplay.tools.tool_catalog import build_screenplay_tool_catalog
from purra.contracts import ExecutionState


@pytest_asyncio.fixture
async def cache_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute("INSERT INTO books (id, title) VALUES ('book', '原作')")
    await db.execute(
        "INSERT INTO screenplay_projects "
        "(id, title, source_book_id, source_kind, source_snapshot_json) "
        "VALUES ('project', '改编', 'book', 'book', '{}')"
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES ('writing', '正文', 'writing', 'book')"
    )
    for index in (1, 2):
        await db.execute(
            "INSERT INTO outline_chapters (id, title, outline_id, sort) "
            "VALUES (?, ?, 'writing', ?)", [f"chapter-{index}", f"第{index}章", index],
        )
        await db.execute(
            "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
            [f"chapter-{index}", _lexical(f"完整原文{index}，关键线索在结尾。")],
        )
    try:
        yield db
    finally:
        await db.close()


def _lexical(text):
    return json.dumps({"root": {"children": [{"type": "paragraph", "children": [
        {"type": "text", "text": text},
    ]}]}}, ensure_ascii=False)


def _state(number=1, **scope):
    return ExecutionState(
        run_id=f"run-{number}",
        domain={
            "projectId": "project", "sourceBookId": "book",
            "sourceScope": {"mode": "whole_book"},
            "taskId": "task", "unitId": f"scene-{number}",
            **scope,
        },
    )


def _read(db):
    return next(item.handler for item in build_screenplay_tool_catalog(db=db).registrations()
                if item.schema.name == "readSourceChapters")


def test_scene_context_cache_identity_includes_host_bound_scene_and_dependencies():
    scope = _state(
        expectedPartType="scene",
        expectedPartKey="scene-2",
        boundEpisodeNumber=2,
        dependencyPartKeys=["draft:2:scene-1"],
        deliverableRevisionScope={
            "sourceAnalysis": "analysis-1",
            "creativeBrief": "brief-1",
            "sceneList": "scene-list-1",
        },
    ).domain
    original, _ = read_cache.screenplay_cache_identity(
        "getScreenplaySceneContext",
        scope,
        {},
    )
    changed_scene, _ = read_cache.screenplay_cache_identity(
        "getScreenplaySceneContext",
        {**scope, "expectedPartKey": "scene-3"},
        {},
    )
    changed_dependency, _ = read_cache.screenplay_cache_identity(
        "getScreenplaySceneContext",
        {**scope, "dependencyPartKeys": ["draft:2:scene-0"]},
        {},
    )

    assert len({original, changed_scene, changed_dependency}) == 3


@pytest.mark.parametrize("tool_name", ["getScreenplaySceneContext", "readScreenplayDeliverable"])
async def test_enriched_read_contract_does_not_reuse_old_cached_shape(cache_db, tool_name):
    scope = _state(expectedPartType="scene", expectedPartKey="scene-1", boundEpisodeNumber=1).domain
    current_key, scope_key = read_cache.screenplay_cache_identity(tool_name, scope, {})
    old_key = hashlib.sha256(json.dumps(
        [3, tool_name, json.loads(scope_key), {}],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()
    assert current_key != old_key
    await cache_db.execute(
        "INSERT INTO screenplay_tool_cache (cache_key, tool_name, content) VALUES (?, ?, ?)",
        [old_key, tool_name, '{"oldShape":true}'],
    )
    method = AsyncMock(return_value={"currentShape": True})
    value, hit = await read_cache.cached_screenplay_read(cache_db, tool_name, method, scope, {})
    assert value == {"currentShape": True}
    assert hit is False
    repeated, hit = await read_cache.cached_screenplay_read(cache_db, tool_name, method, scope, {})
    assert repeated == value
    assert hit is True
    method.assert_awaited_once()


async def test_reads_reuse_complete_results_across_rounds_runs_and_catalogs(cache_db, monkeypatch):
    original = ScreenplayToolQuery.read_source_chapters
    calls = []

    async def counted(query, scope, arguments):
        calls.append(arguments)
        return await original(query, scope, arguments)

    monkeypatch.setattr(ScreenplayToolQuery, "read_source_chapters", counted)
    read = _read(cache_db)
    arguments = {"chapterIds": ["chapter-1"]}
    first, second = await asyncio.gather(
        read(_state(1), arguments), read(_state(2), arguments),
    )
    third = await _read(cache_db)(_state(3), arguments)
    assert [first.from_cache, second.from_cache, third.from_cache] == [False, True, True]
    assert first.content == second.content == third.content
    assert json.loads(third.content)["chapters"][0]["content"] == "完整原文1，关键线索在结尾。"
    assert len(calls) == 1
    receipts = await cache_db.fetch_all(
        "SELECT agent_run_id, source_revision FROM screenplay_source_receipts "
        "ORDER BY agent_run_id"
    )
    assert [item["agent_run_id"] for item in receipts] == ["run-1", "run-2", "run-3"]
    assert len({item["source_revision"] for item in receipts}) == 1
    sibling = await read(_state(3), {"chapterIds": ["chapter-2"]})
    assert not sibling.from_cache and "完整原文2" in sibling.content


async def test_source_edits_and_external_writers_invalidate_cached_body(cache_db, tmp_path):
    read = _read(cache_db)
    arguments = {"chapterIds": ["chapter-1"]}
    await read(_state(), arguments)
    await cache_db.execute(
        "UPDATE articles SET content = ? WHERE chapter_id = 'chapter-1'",
        [_lexical("同一秒内修改后的正文")],
    )
    edited = await read(_state(), arguments)
    assert not edited.from_cache and "修改后的正文" in edited.content
    peer = DatabaseConnection(tmp_path)
    await peer.init(initialize_schema=False)
    try:
        await peer.execute(
            "UPDATE outline_chapters SET title = '外部重命名' WHERE id = 'chapter-1'"
        )
    finally:
        await peer.close()
    renamed = await read(_state(), arguments)
    assert not renamed.from_cache and "外部重命名" in renamed.content
    await cache_db.execute("DELETE FROM articles WHERE chapter_id = 'chapter-1'")
    deleted = await read(_state(), arguments)
    assert not deleted.from_cache
    assert json.loads(deleted.content)["chapters"][0]["content"] == ""


async def test_cache_cannot_reuse_a_broader_scope_or_revoked_access(cache_db):
    read = _read(cache_db)
    arguments = {"chapterIds": ["chapter-1"]}
    await read(_state(), arguments)
    restricted = {"mode": "selected_chapters", "chapterIds": ["chapter-2"]}
    rejected = await read(_state(sourceScope=restricted), arguments)
    assert not rejected.from_cache and rejected.error_code == "tool_input_invalid"
    with pytest.raises(ValueError, match="bound source book"):
        await read(_state(sourceBookId="other-book"), arguments)
    await cache_db.execute(
        "UPDATE screenplay_projects SET source_scope_json = ? WHERE id = 'project'",
        [json.dumps(restricted)],
    )
    revoked = await read(_state(), arguments)
    assert not revoked.from_cache and revoked.error_code == "tool_input_invalid"
    await cache_db.execute("UPDATE screenplay_projects SET source_scope_json = '{}' WHERE id = 'project'")
    restored = await read(_state(), arguments)
    assert not restored.from_cache and restored.error_code is None


async def test_cache_rolls_back_failed_reads_and_bounds_storage(cache_db, monkeypatch):
    monkeypatch.setattr(read_cache, "_MAX_ENTRIES", 2)
    monkeypatch.setattr(read_cache, "_MAX_CONTENT_BYTES", 200)
    method = AsyncMock(return_value={"value": "完整内容"})
    scope = _state().domain

    async def read(arguments):
        return await read_cache.cached_screenplay_read(
            cache_db, "readSourceChapters", method, scope, arguments,
        )

    with pytest.raises(RuntimeError, match="rollback"):
        async with cache_db.transaction():
            await read({"key": 0})
            raise RuntimeError("rollback")
    assert (await read({"key": 0}))[1] is False
    method.side_effect = ValueError("failed read")
    with pytest.raises(ValueError, match="failed read"):
        await read({"key": 1})
    method.side_effect = None
    assert (await read({"key": 1}))[1] is False
    await read({"key": 2})
    assert (await read({"key": 0}))[1] is False
    method.return_value = {"value": "大" * 200}
    assert (await read({"key": 3}))[1] is False
    assert (await read({"key": 3}))[1] is False
    row = await cache_db.fetch_one("SELECT COUNT(*) AS count FROM screenplay_tool_cache")
    assert row["count"] == 2


async def test_unrelated_task_progress_preserves_source_and_episode_cache(cache_db):
    method = AsyncMock(return_value={"episode": {"title": "完整分集"}})
    scope = _state(boundEpisodeNumber=1, deliverableRevisionScope={"sceneList": "r1"}).domain

    async def read():
        return await read_cache.cached_screenplay_read(
            cache_db, "getScreenplayEpisodeContext", method, scope, {},
        )

    await read()
    await cache_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, total_units) "
        "VALUES ('task', 'screenplay', 'screenplay', 'project', 'run-1', 1)"
    )
    assert (await read())[1] is True
    await cache_db.execute(
        "INSERT INTO screenplay_revisions "
        "(id, project_id, deliverable_id, revision_no, content_digest, created_by) "
        "VALUES ('r1', 'project', 'deliverable', 1, 'digest', 'test')"
    )
    assert (await read())[1] is False
    await cache_db.execute(
        "INSERT INTO screenplay_project_heads (project_id, deliverable_id, revision_id) "
        "VALUES ('project', 'deliverable', 'r1')"
    )
    assert (await read())[1] is False
    assert method.await_count == 3
