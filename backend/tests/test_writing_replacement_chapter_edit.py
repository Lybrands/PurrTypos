from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio

from agents.writing.chapter_write_model import SqliteWritingChapterRepository
from agents.writing.profile import WritingReplacementProfile
from agents.writing.read_model import WritingReadScope
from agents.writing.read_tools import WRITING_READ_SCOPE_STATE_KEY
from database.connection import DatabaseConnection
from purra.cancellation import OperationCanceled
from purra.contracts import (
    ApprovalStatus,
    ExecutionState,
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolCall,
)
from purra.events import CoreEventType
from purra.tools.approval import InMemoryApprovalGateway
from purra.tools.executor import CoreToolExecutor


class _EventSink:
    def __init__(self) -> None:
        self.events = []

    async def emit(self, event) -> None:
        self.events.append(event)


@pytest_asyncio.fixture
async def chapter_db(tmp_path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute(
        "INSERT INTO books (id, title) VALUES ('book-1', '测试书')"
    )
    await db.execute(
        "INSERT INTO ai_sessions (id, book_id, chapter_id, title) "
        "VALUES (11, 'book-1', 'chapter-1', '章节会话')"
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, sort, book_id) "
        "VALUES ('writing-1', '写作目录', 'writing', 0, 'book-1')"
    )
    await db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, level, progress, sort, parent_id) "
        "VALUES ('chapter-1', 'writing-1', '第一章', 1, 'done', 1, NULL)"
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES "
        "('chapter-1', '原始正文')"
    )
    try:
        yield db
    finally:
        await db.close()


def _state() -> ExecutionState:
    return ExecutionState(domain={
        WRITING_READ_SCOPE_STATE_KEY: {
            "bookId": "book-1",
            "sessionId": 11,
            "chapterId": "chapter-1",
        },
    })


def _request(arguments: dict[str, object]) -> ToolBatchRequest:
    return ToolBatchRequest(
        run_id="run-edit",
        calls=(ToolCall(
            id="call-edit",
            name="editChapterContent",
            arguments_json=json.dumps(arguments, ensure_ascii=False),
        ),),
        allowed_tool_names=frozenset({"editChapterContent"}),
        state=_state(),
    )


@pytest.mark.asyncio
async def test_chapter_read_returns_host_revision_and_complete_json(chapter_db) -> None:
    profile = WritingReplacementProfile(chapter_db)
    registration = profile.adapter.tool_catalog.get("getChapterContent")

    result = await registration.handler(_state(), {})
    payload = json.loads(result.content)

    assert payload["content"] == "原始正文"
    assert payload["baseRevision"].startswith("sha256:")
    assert payload["truncated"] is False


def test_edit_schema_requires_content_and_revision(chapter_db) -> None:
    registration = WritingReplacementProfile(
        chapter_db
    ).adapter.tool_catalog.get("editChapterContent")

    assert registration.schema.parameters["required"] == [
        "content",
        "baseRevision",
    ]
    assert registration.policy.mode.value == "confirm"
    assert registration.cancellation_linearizable is True


@pytest.mark.asyncio
async def test_edit_waits_for_approval_then_commits_once(chapter_db) -> None:
    repository = SqliteWritingChapterRepository(chapter_db)
    current = await repository.read(WritingReadScope(
        "book-1",
        session_id=11,
        chapter_id="chapter-1",
    ))
    profile = WritingReplacementProfile(chapter_db)
    approvals = InMemoryApprovalGateway()
    sink = _EventSink()
    task = asyncio.create_task(CoreToolExecutor(
        profile.adapter.tool_catalog,
        approval_gateway=approvals,
    ).execute_batch(_request({
        "content": "审批后的正文",
        "baseRevision": current["baseRevision"],
    }), sink))

    for _ in range(100):
        if sink.events:
            break
        await asyncio.sleep(0)
    assert sink.events[0].type == CoreEventType.APPROVAL_REQUESTED
    before = await chapter_db.fetch_one(
        "SELECT content FROM articles WHERE chapter_id = 'chapter-1'"
    )
    assert before == {"content": "原始正文"}

    approval_id = sink.events[0].payload["approvalId"]
    assert await approvals.resolve(
        "run-edit",
        approval_id,
        "approve",
    ) is ApprovalStatus.APPROVED
    result = await task
    stored = await chapter_db.fetch_one(
        "SELECT content FROM articles WHERE chapter_id = 'chapter-1'"
    )

    assert result.outcome is ToolBatchOutcome.COMPLETED
    assert stored == {"content": "审批后的正文"}
    effect = result.results[0].effects[0]
    receipt = json.loads(result.results[0].content)
    assert effect.type == "writing.chapter_content_updated"
    assert effect.payload["committedRevision"] == receipt["committedRevision"]


@pytest.mark.asyncio
async def test_rejected_edit_never_changes_article(chapter_db) -> None:
    repository = SqliteWritingChapterRepository(chapter_db)
    current = await repository.read(WritingReadScope(
        "book-1",
        session_id=11,
        chapter_id="chapter-1",
    ))
    approvals = InMemoryApprovalGateway()
    sink = _EventSink()
    task = asyncio.create_task(CoreToolExecutor(
        WritingReplacementProfile(chapter_db).adapter.tool_catalog,
        approval_gateway=approvals,
    ).execute_batch(_request({
        "content": "不应保存",
        "baseRevision": current["baseRevision"],
    }), sink))

    for _ in range(100):
        if sink.events:
            break
        await asyncio.sleep(0)
    await approvals.resolve(
        "run-edit",
        sink.events[0].payload["approvalId"],
        "reject",
    )
    result = await task
    stored = await chapter_db.fetch_one(
        "SELECT content FROM articles WHERE chapter_id = 'chapter-1'"
    )

    assert result.outcome is ToolBatchOutcome.DECLINED
    assert stored == {"content": "原始正文"}


@pytest.mark.asyncio
async def test_empty_content_requires_explicit_clear_before_approval(chapter_db) -> None:
    repository = SqliteWritingChapterRepository(chapter_db)
    current = await repository.read(WritingReadScope(
        "book-1",
        session_id=11,
        chapter_id="chapter-1",
    ))
    registration = WritingReplacementProfile(
        chapter_db
    ).adapter.tool_catalog.get("editChapterContent")

    error = await registration.scope_validator(_state(), {
        "content": "",
        "baseRevision": current["baseRevision"],
    })
    allowed = await registration.scope_validator(_state(), {
        "content": "",
        "baseRevision": current["baseRevision"],
        "clearContent": True,
    })

    assert error == "writing_chapter_clear_requires_explicit_intent"
    assert allowed is None


@pytest.mark.asyncio
async def test_revision_conflict_is_detected_before_approval(chapter_db) -> None:
    registration = WritingReplacementProfile(
        chapter_db
    ).adapter.tool_catalog.get("editChapterContent")

    error = await registration.scope_validator(_state(), {
        "content": "候选正文",
        "baseRevision": "sha256:stale",
    })

    assert error == "writing_chapter_revision_conflict"


@pytest.mark.asyncio
async def test_exact_committed_replay_is_noop(chapter_db) -> None:
    repository = SqliteWritingChapterRepository(chapter_db)
    scope = WritingReadScope("book-1", session_id=11, chapter_id="chapter-1")
    current = await repository.read(scope)

    first = await repository.commit_edit(
        scope,
        content="相同提交",
        base_revision=current["baseRevision"],
        clear_content=False,
    )
    replay = await repository.commit_edit(
        scope,
        content="相同提交",
        base_revision=current["baseRevision"],
        clear_content=False,
    )

    assert first["noop"] is False
    assert replay["noop"] is True
    assert replay["intentDigest"] == first["intentDigest"]

    registration = WritingReplacementProfile(
        chapter_db
    ).adapter.tool_catalog.get("editChapterContent")
    assert await registration.scope_validator(_state(), {
        "content": "相同提交",
        "baseRevision": current["baseRevision"],
    }) is None


@pytest.mark.asyncio
async def test_canceled_edit_never_starts_database_effect(chapter_db) -> None:
    repository = SqliteWritingChapterRepository(chapter_db)
    scope = WritingReadScope("book-1", session_id=11, chapter_id="chapter-1")
    current = await repository.read(scope)
    signal = asyncio.Event()
    signal.set()

    with pytest.raises(OperationCanceled):
        await repository.commit_edit(
            scope,
            content="不应保存",
            base_revision=current["baseRevision"],
            clear_content=False,
            signal=signal,
        )

    stored = await chapter_db.fetch_one(
        "SELECT content FROM articles WHERE chapter_id = 'chapter-1'"
    )
    assert stored == {"content": "原始正文"}


@pytest.mark.asyncio
async def test_chapter_commit_joins_host_idempotency_transaction(chapter_db) -> None:
    repository = SqliteWritingChapterRepository(chapter_db)
    scope = WritingReadScope("book-1", session_id=11, chapter_id="chapter-1")
    current = await repository.read(scope)

    async with chapter_db.transaction(cancellation_linearizable=True):
        receipt = await repository.commit_edit(
            scope,
            content="宿主事务内正文",
            base_revision=current["baseRevision"],
            clear_content=False,
        )

    assert receipt["noop"] is False
    assert await chapter_db.fetch_one(
        "SELECT content FROM articles WHERE chapter_id = 'chapter-1'"
    ) == {"content": "宿主事务内正文"}
