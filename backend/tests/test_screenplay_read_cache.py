from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from infrastructure.screenplay.tools import read_cache
from infrastructure.screenplay.tools.query import ScreenplayToolQuery
from infrastructure.screenplay.tools.tool_catalog import build_screenplay_tool_catalog
from purra.contracts import ExecutionState
from purra.contracts import AgentRunRequest, ModelRequest
from domains.screenplay_agent.adapter import ScreenplayExecutionStateFactory
from domains.screenplay_agent.agent_context import ScreenplayAgentDomainContext
from infrastructure.screenplay.tools.prepared_reads import ScreenplayPreparedReads


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


@pytest.mark.parametrize("indexed", [True, False])
@pytest.mark.parametrize("chapter_ids", [["chapter-1"], ["chapter-2", "chapter-1"]])
async def test_prepared_sources_respect_permissions_scope_and_database_invalidation(cache_db, monkeypatch, indexed, chapter_ids):
    context = ScreenplayAgentDomainContext(project_id="project", source_book_id="book",
                                          task_id="task", unit_id="scene-1", target_role="creativeBrief",
                                          expected_part_type="document", expected_part_key="main",
                                          source_scope={"mode": "whole_book"})
    request = AgentRunRequest(messages=(), model=ModelRequest(provider="fixture", model="model"),
                              domain_context=context.to_core_context(), tools_enabled=True)
    state = ScreenplayExecutionStateFactory().create(request)
    state.run_id = "prepared-source-run"
    await _read(cache_db)(state, {"chapterIds": chapter_ids})
    if not indexed:
        await cache_db.execute("UPDATE screenplay_tool_cache SET scope_key = NULL, arguments_json = NULL")
    loader = ScreenplayPreparedReads(cache_db, build_screenplay_tool_catalog(db=cache_db))
    monkeypatch.setattr(ScreenplayToolQuery, "read_source_chapters", AsyncMock(side_effect=AssertionError("unexpected read")))
    supplied = await loader.load(request)
    assert len(supplied) == 1
    assert supplied[0].arguments == {"chapterIds": chapter_ids}
    assert "完整原文1，关键线索在结尾。" in supplied[0].content
    assert supplied[0].metadata["sourceRefs"]
    restricted = replace(context, source_scope={"mode": "selected_chapters", "chapterIds": ["chapter-2"]})
    assert await loader.load(replace(request, domain_context=restricted.to_core_context())) == ()
    no_reads = replace(context, tool_access="candidate_write")
    assert await loader.load(replace(request, domain_context=no_reads.to_core_context())) == ()
    assert await loader.load(replace(request, tools_enabled=False)) == ()
    await cache_db.execute("UPDATE articles SET content = ? WHERE chapter_id = 'chapter-1'", [_lexical("新版原文")])
    assert await loader.load(request) == ()


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
