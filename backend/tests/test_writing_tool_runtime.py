from __future__ import annotations

import asyncio
import inspect
import json
from functools import partial
from pathlib import Path

import pytest

import dependencies
from purra.contracts import (
    ExecutionState,
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolCall,
    ToolPlanningDisposition,
)
from purra.tools.executor import CoreToolExecutor
from database.connection import DatabaseConnection
from domains.writing.policies import WRITING_TOOL_POLICIES
from domains.writing.tools.contracts import ToolResult, _err
from exceptions import DatabaseNotReadyError
from infrastructure.persistence.writing import (
    SqliteWritingSourceRepository,
)
from infrastructure.writing import (
    WritingSkillCatalog,
    WritingToolDependencies,
    build_writing_tool_catalog,
)
from infrastructure.writing.tools.handlers import WRITING_TOOL_OPERATIONS
from infrastructure.writing.tools.runtime import bind_writing_tool_handlers


BACKEND_DIR = Path(__file__).resolve().parent.parent
SKILL_ITEMS = tuple(
    WritingSkillCatalog(BACKEND_DIR / "skills").skill_items()
)
REPLANNING_EVIDENCE_TOOL_NAMES = frozenset({
    "batchGetChapterContents",
    "getBookCharacters",
    "getChapterContent",
    "getGlobalOutline",
    "getSettingEntities",
    "getStoryBackground",
    "getStoryHealthDashboard",
    "getWritingStatsDashboard",
    "queryOutline",
    "searchMemories",
    "searchSparkIdeas",
})


class _MemoryOperations:
    def __init__(self):
        self.calls = []

    async def add_source(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "memory-1", "version": 1, "state": "active"}


def _tool_dependencies(db, memories=None) -> WritingToolDependencies:
    return WritingToolDependencies(
        db,
        SqliteWritingSourceRepository(db),
        memories or _MemoryOperations(),
    )


def _registration(db, name: str):
    catalog = build_writing_tool_catalog(
        dependencies=_tool_dependencies(db),
        skill_items=SKILL_ITEMS,
    )
    return next(
        item for item in catalog.registrations() if item.schema.name == name
    )


class _RecordingSink:
    def __init__(self) -> None:
        self.events = []

    async def emit(self, event) -> None:
        self.events.append(event)


def _lexical(text: str) -> str:
    return json.dumps({
        "root": {
            "children": [{
                "type": "paragraph",
                "children": [{"type": "text", "text": text}],
            }],
        },
    }, ensure_ascii=False)


async def _seed_writing_chapter(
    db: DatabaseConnection,
    *,
    book_id: str,
    outline_id: str,
    chapter_id: str,
    title: str,
    content: str,
    sort: int,
) -> None:
    if await db.fetch_one("SELECT id FROM books WHERE id = ?", [book_id]) is None:
        await db.execute(
            "INSERT INTO books (id, title) VALUES (?, ?)",
            [book_id, f"{book_id} title"],
        )
        await db.execute(
            "INSERT INTO outlines (id, title, type, book_id) "
            "VALUES (?, ?, 'writing', ?)",
            [outline_id, f"{book_id} writing", book_id],
        )
    await db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, level, sort) VALUES (?, ?, ?, 1, ?)",
        [chapter_id, outline_id, title, sort],
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        [chapter_id, _lexical(content)],
    )


def _edit_request(
    *,
    arguments: dict,
    domain: dict,
) -> ToolBatchRequest:
    return ToolBatchRequest(
        run_id="writing-candidate-run",
        calls=(ToolCall(
            id="edit-call",
            name="editChapterContent",
            arguments_json=json.dumps(arguments, ensure_ascii=False),
        ),),
        allowed_tool_names=frozenset({"editChapterContent"}),
        state=ExecutionState(domain=domain),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "expected"),
    [
        (
            name,
            (
                ToolPlanningDisposition.REPLAN
                if name in REPLANNING_EVIDENCE_TOOL_NAMES
                else ToolPlanningDisposition.KEEP_PLAN
            ),
        )
        for name in sorted(WRITING_TOOL_POLICIES)
    ],
)
async def test_catalog_replans_only_after_explicit_evidence_reads(
    tool_name: str,
    expected: ToolPlanningDisposition,
    monkeypatch,
):
    from infrastructure.writing.retrieval import WritingMemoryRetriever, WritingMethodRetriever

    from infrastructure.writing.knowledge_retrieval import NovelKnowledgeRetriever

    async def _successful(_ctx, _args, _send_chunk):
        return ToolResult('{"evidence":"new fact"}')

    async def _retrieved(self, request, signal=None):
        return ()

    retriever = {
        "searchMemories": WritingMemoryRetriever,
        "searchWritingMethods": WritingMethodRetriever,
        "searchNovelKnowledge": NovelKnowledgeRetriever,
        "readNovelKnowledge": NovelKnowledgeRetriever,
    }.get(tool_name)
    if retriever is not None:
        monkeypatch.setattr(retriever, "retrieve", _retrieved)
    catalog = build_writing_tool_catalog(
        dependencies=WritingToolDependencies(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
        ),
        skill_items=SKILL_ITEMS,
        handler_overrides={} if retriever is not None else {tool_name: _successful},
    )
    registration = next(
        item for item in catalog.registrations()
        if item.schema.name == tool_name
    )

    result = await registration.handler(
        ExecutionState(domain={"bookId": "book-a"}),
        {"query": "evidence"} if retriever is not None else {},
    )

    assert result.error_code is None
    assert result.planning_disposition is expected


@pytest.mark.asyncio
async def test_failed_evidence_read_keeps_the_current_plan():
    async def _failed(_ctx, _args, _send_chunk):
        return _err({"error": "evidence unavailable"})

    catalog = build_writing_tool_catalog(
        dependencies=WritingToolDependencies(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
        ),
        skill_items=SKILL_ITEMS,
        handler_overrides={"getStoryBackground": _failed},
    )
    registration = next(
        item for item in catalog.registrations()
        if item.schema.name == "getStoryBackground"
    )

    result = await registration.handler(
        ExecutionState(domain={"bookId": "book-a"}),
        {},
    )

    assert result.error_code == "tool_execution_failed"
    assert result.planning_disposition is ToolPlanningDisposition.KEEP_PLAN


@pytest.mark.asyncio
async def test_two_catalogs_keep_explicit_db_dependencies_isolated(
    monkeypatch, tmp_path,
):
    from infrastructure.writing.tools.handlers import story_background_tools

    global_db = object()
    first_db = DatabaseConnection(tmp_path / "first")
    second_db = DatabaseConnection(tmp_path / "second")
    await first_db.init()
    await second_db.init()
    monkeypatch.setattr(dependencies, "_db_instance", global_db)
    observed: list[tuple[object, str]] = []

    async def _get_story_background(db, book_id):
        await asyncio.sleep(0)
        observed.append((db, book_id))
        return None

    monkeypatch.setattr(
        story_background_tools, "get_story_background", _get_story_background
    )
    try:
        first = _registration(first_db, "getStoryBackground")
        second = _registration(second_db, "getStoryBackground")
        await asyncio.gather(
            first.handler(ExecutionState(domain={"bookId": "first"}), {}),
            second.handler(ExecutionState(domain={"bookId": "second"}), {}),
        )
        assert set(observed) == {(first_db, "first"), (second_db, "second")}
        assert dependencies.get_db() is global_db
    finally:
        await first_db.close()
        await second_db.close()


@pytest.mark.asyncio
async def test_memory_tool_uses_host_book_and_trusted_tool_call_id(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    memories = _MemoryOperations()
    try:
        catalog = build_writing_tool_catalog(
            dependencies=_tool_dependencies(db, memories),
            skill_items=SKILL_ITEMS,
        )
        registration = next(
            item for item in catalog.registrations()
            if item.schema.name == "createMemory"
        )
        missing = await registration.handler(
            ExecutionState(domain={"bookId": "book-a"}),
            {"kind": "summary", "content": "需要记住"},
        )
        created = await registration.handler(
            ExecutionState(domain={"bookId": "book-a"}),
            {
                "kind": "summary",
                "content": "需要记住",
                "__toolCallId": "call-1",
            },
        )

        assert missing.error_code == "tool_execution_failed"
        assert created.error_code is None
        assert memories.calls[0]["book_id"] == "book-a"
        assert memories.calls[0]["key"] == "tool-create:call-1"
        assert memories.calls[0]["source"].id == "tool:call-1"
    finally:
        await db.close()


def test_clear_db_only_clears_the_expected_lifespan_owner(monkeypatch):
    owner = object()
    replacement = object()
    monkeypatch.setattr(dependencies, "_db_instance", owner)

    dependencies.clear_db(replacement)
    assert dependencies.get_db() is owner

    dependencies.clear_db(owner)
    with pytest.raises(DatabaseNotReadyError):
        dependencies.get_db()


def test_all_bound_handlers_keep_the_writing_operation_call_signature():
    bound = bind_writing_tool_handlers(
        WRITING_TOOL_OPERATIONS,
        WritingToolDependencies(
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
        ),
    )

    assert set(bound) == set(WRITING_TOOL_POLICIES) - {
        "searchMemories", "searchWritingMethods", "searchNovelKnowledge", "readNovelKnowledge",
    }
    for name, handler in bound.items():
        assert isinstance(handler, partial)
        assert handler.func is WRITING_TOOL_OPERATIONS[name]
        assert inspect.iscoroutinefunction(handler)
        assert len(inspect.signature(handler).parameters) == 3


@pytest.mark.asyncio
async def test_atomic_handler_rolls_back_when_operation_returns_error(
    tmp_path,
):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        async def _write_then_fail(dependencies, ctx, _args, _send_chunk):
            await dependencies.db.execute(
                "INSERT INTO books (id, title) VALUES (?, ?)",
                ["rolled-back", "must not persist"],
            )
            ctx["chapterId"] = "mutated"
            ctx["readToolCache"].clear()
            return _err({"success": False, "error": "synthetic failure"})

        handler = bind_writing_tool_handlers(
            {"writeThenFail": _write_then_fail},
            _tool_dependencies(db),
            atomic_operation_names=frozenset({"writeThenFail"}),
        )["writeThenFail"]

        context = {
            "chapterId": "original",
            "readToolCache": {"stable": "cached"},
        }
        result = await handler(context, {}, None)
        assert "synthetic failure" in result.content
        assert context == {
            "chapterId": "original",
            "readToolCache": {"stable": "cached"},
        }
        assert await db.fetch_one(
            "SELECT id FROM books WHERE id = ?",
            ["rolled-back"],
        ) is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_edit_chapter_content_emits_only_a_candidate_before_user_apply(
    tmp_path,
):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        await _seed_writing_chapter(
            db,
            book_id="book-a",
            outline_id="writing-a",
            chapter_id="chapter-current",
            title="第一章",
            content="正式正文保持不变",
            sort=1,
        )
        await _seed_writing_chapter(
            db,
            book_id="book-a",
            outline_id="writing-a",
            chapter_id="chapter-authorized",
            title="第二章",
            content="第二章正式正文",
            sort=2,
        )
        stored_before = await db.fetch_one(
            "SELECT content FROM articles WHERE chapter_id = ?",
            ["chapter-authorized"],
        )
        domain = {
            "bookId": "book-a",
            "chapterId": "chapter-current",
            "writingChapters": [
                {"id": "chapter-current", "title": "第一章"},
                {"id": "chapter-authorized", "title": "第二章"},
            ],
        }
        result = await CoreToolExecutor(
            build_writing_tool_catalog(
                dependencies=_tool_dependencies(db),
                skill_items=SKILL_ITEMS,
            )
        ).execute_batch(
            _edit_request(
                arguments={
                    "chapterId": "chapter-authorized",
                    "content": "仅供用户审阅的第二章候选正文",
                },
                domain=domain,
            ),
            _RecordingSink(),
        )
        stored_after = await db.fetch_one(
            "SELECT content FROM articles WHERE chapter_id = ?",
            ["chapter-authorized"],
        )

        assert result.outcome is ToolBatchOutcome.COMPLETED
        assert json.loads(result.results[0].content) == {
            "success": True,
            "message": "已向用户提交差异预览，需用户在编辑器接受/拒绝后才会写入正文",
            "chapterId": "chapter-authorized",
            "pendingUserApproval": True,
        }
        assert [
            {"type": effect.type, "payload": dict(effect.payload)}
            for effect in result.results[0].effects
        ] == [{
            "type": "writing.proposed_chapter_diff",
            "payload": {
                "chapterId": "chapter-authorized",
                "beforeText": "第二章正式正文",
                "proposedText": "仅供用户审阅的第二章候选正文",
                "source": "ai_tool_edit",
            },
        }]
        assert stored_before == stored_after == {
            "content": _lexical("第二章正式正文"),
        }
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_edit_chapter_content_fails_closed_outside_host_scope(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    try:
        await _seed_writing_chapter(
            db,
            book_id="book-a",
            outline_id="writing-a",
            chapter_id="chapter-a",
            title="第一章",
            content="甲书正式正文",
            sort=1,
        )
        await _seed_writing_chapter(
            db,
            book_id="book-b",
            outline_id="writing-b",
            chapter_id="chapter-b",
            title="第一章",
            content="乙书正式正文",
            sort=1,
        )
        executor = CoreToolExecutor(build_writing_tool_catalog(
            dependencies=_tool_dependencies(db),
            skill_items=SKILL_ITEMS,
        ))
        domain = {
            "bookId": "book-a",
            "chapterId": "chapter-a",
            "writingChapters": [
                {"id": "chapter-a", "title": "第一章"},
                # Deliberately forged into the runtime catalog. The database
                # ownership check must still reject the cross-book chapter.
                {"id": "chapter-b", "title": "伪造的第二章"},
            ],
        }
        stored_before = await db.fetch_all(
            "SELECT chapter_id, content FROM articles ORDER BY chapter_id"
        )

        book_conflict = await executor.execute_batch(
            _edit_request(
                arguments={
                    "bookId": "book-b",
                    "chapterId": "chapter-a",
                    "content": "模型伪造书籍绑定",
                },
                domain=domain,
            ),
            _RecordingSink(),
        )
        unknown_chapter = await executor.execute_batch(
            _edit_request(
                arguments={
                    "chapterId": "chapter-not-in-catalog",
                    "content": "模型伪造目录外章节",
                },
                domain=domain,
            ),
            _RecordingSink(),
        )
        cross_book_chapter = await executor.execute_batch(
            _edit_request(
                arguments={
                    "chapterId": "chapter-b",
                    "content": "模型尝试跨书改写",
                },
                domain=domain,
            ),
            _RecordingSink(),
        )
        stored_after = await db.fetch_all(
            "SELECT chapter_id, content FROM articles ORDER BY chapter_id"
        )

        assert book_conflict.outcome is ToolBatchOutcome.REJECTED
        assert book_conflict.error == "tool_scope_violation"
        for result in (unknown_chapter, cross_book_chapter):
            assert result.outcome is ToolBatchOutcome.FAILED
            assert result.error == "tool_execution_failed"
            assert result.results[0].effects == ()
        assert stored_after == stored_before
    finally:
        await db.close()
