from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from purra.contracts import (
    AgentMessage,
    AgentRunRequest,
    MessageOrigin,
    MessageRole,
    ModelRequest,
    TaskContextRequest,
    TaskSpec,
)
from purra.context_budget import allocate_context_budget
from purra.evidence import CONTEXT_EVIDENCE_RECEIPTS_KEY, RunEvidenceStore
from database.connection import DatabaseConnection
from domains.writing.memory_context import (
    MemoryContextRequest,
    WritingMemoryContextBuilder,
)
from domains.writing.context import (
    WRITING_RETRIEVAL_CONTEXT,
    WritingContextProvider,
    writing_context_claims,
)
from domains.writing.context_source import RepositoryWritingContextSource
from domains.writing.contracts import WritingDomainContext
from domains.writing.story_memory import (
    SourceReference,
    StoryMemoryChange,
    StoryMemoryDeltaDraft,
    StoryMemoryKind,
    StoryMemoryOperation,
    StoryMemoryStatus,
)
from domains.writing.unified_memory_context import (
    StoryMemoryContextProvider,
    UnifiedMemoryRetriever,
    memory_context_request_from_task,
)
from domains.writing.memory_reranking import (
    MemoryRerankDecision,
    MemoryRerankResult,
)
from infrastructure.persistence.writing import (
    SqliteMemoryRecallRepository,
    SqliteAssociatedContextRepository,
    SqliteStoryMemoryRecallRepository,
    SqliteStoryMemoryRepository,
)
from infrastructure.persistence.writing.sqlite_story_memory_recall_repository import (
    _query_terms,
)


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    connection = DatabaseConnection(tmp_path)
    await connection.init()
    try:
        yield connection
    finally:
        await connection.close()


async def _apply_story(
    db: DatabaseConnection,
    *,
    key: str,
    value: str,
    chapter_id: str,
    status: StoryMemoryStatus = StoryMemoryStatus.CONFIRMED,
) -> None:
    repository = SqliteStoryMemoryRepository(db)
    delta = await repository.create_delta(StoryMemoryDeltaDraft(
        book_id="book-1",
        chapter_id=chapter_id,
        source_revision=f"rev-{chapter_id}",
        changes=(StoryMemoryChange(
            target_key=key,
            kind=StoryMemoryKind.CHARACTER_STATE,
            operation=StoryMemoryOperation.UPSERT,
            subject_id=key.rsplit(":", 1)[0],
            payload={"attribute": "location", "value": value},
            status=status,
            source=SourceReference(
                chapter_id=chapter_id,
                source_revision=f"rev-{chapter_id}",
                excerpt=f"角色抵达{value}",
            ),
        ),),
    ))
    await repository.apply_delta(delta.id)


async def _apply_relationship(
    db: DatabaseConnection,
    *,
    source_character_id: str,
    target_character_id: str,
) -> None:
    repository = SqliteStoryMemoryRepository(db)
    delta = await repository.create_delta(StoryMemoryDeltaDraft(
        book_id="book-1",
        chapter_id="chapter-relationship",
        source_revision="rev-relationship",
        changes=(StoryMemoryChange(
            target_key=(
                "relationship:directed:"
                f"{source_character_id}:{target_character_id}:trust"
            ),
            kind=StoryMemoryKind.RELATIONSHIP_STATE,
            operation=StoryMemoryOperation.UPSERT,
            subject_id=source_character_id,
            payload={
                "sourceCharacterId": source_character_id,
                "targetCharacterId": target_character_id,
                "relationType": "trust",
                "state": "动摇",
                "description": "两人表面合作",
                "directional": True,
            },
            status=StoryMemoryStatus.CONFIRMED,
            source=SourceReference(
                chapter_id="chapter-relationship",
                source_revision="rev-relationship",
                excerpt="她把备用钥匙放回桌上。",
            ),
        ),),
    ))
    await repository.apply_delta(delta.id)


def test_task_spec_semantic_hints_compile_without_becoming_authority():
    base = MemoryContextRequest(book_id="book-1", user_prompt="继续写", token_budget=8000)
    task = TaskContextRequest(
        task_spec=TaskSpec(
            goal="续写林墨与苏遥重逢",
            target={
                "storyContext": ["characters", "relationships", "timeline"],
                "entities": ["character:linmo", "character:suyao"],
                "chapterIds": ["chapter-8"],
            },
        ),
        evidence_kinds=("plot", "world"),
    )

    compiled = memory_context_request_from_task(
        base,
        task,
        current_chapter_id="chapter-9",
    )

    assert set(compiled.story_kinds) == {
        "character_state",
        "relationship_state",
        "timeline_event",
        "plot_thread",
        "world_fact",
    }
    assert set(compiled.planner_story_kinds) == {
        "character_state",
        "relationship_state",
        "timeline_event",
    }
    assert compiled.entity_refs == ("character:linmo", "character:suyao")
    assert compiled.chapter_ids == ("chapter-8", "chapter-9")


def test_long_chinese_query_terms_cover_the_end_instead_of_prefix_truncation():
    text = "".join(chr(0x4E00 + index) for index in range(150))

    terms = _query_terms(text, maximum=16)

    assert len(terms) == 16
    assert text[:2] in terms
    assert text[-3:] in terms


def test_query_term_budget_is_distributed_across_task_fields():
    long_goal = "".join(chr(0x4E00 + index) for index in range(120))
    query = f"任务目标: {long_goal}\n必须保留: 终局暗号"

    terms = _query_terms(query, maximum=16)

    assert any("终局" in value or "局暗" in value or "暗号" in value for value in terms)


@pytest.mark.asyncio
async def test_long_query_can_recall_fact_mentioned_at_the_end(db):
    await _apply_story(
        db,
        key="character:linmo:secret",
        value="终局暗号",
        chapter_id="chapter-secret",
    )
    prefix = "".join(chr(0x4E00 + index) for index in range(140))

    rows = await SqliteStoryMemoryRecallRepository(db).search_current(
        "book-1",
        prefix + "终局暗号",
        limit=20,
    )

    assert [item.memory_key for item in rows] == ["character:linmo:secret"]
    assert any(
        channel in rows[0].candidate_channels
        for channel in ("fts", "lexical")
    )


@pytest.mark.asyncio
async def test_story_recall_only_returns_confirmed_active_valid_evidence(db):
    await _apply_story(
        db,
        key="character:linmo:location",
        value="旧城区",
        chapter_id="chapter-valid",
    )
    await _apply_story(
        db,
        key="character:suyao:location",
        value="旧城区",
        chapter_id="chapter-inferred",
        status=StoryMemoryStatus.INFERRED,
    )
    await _apply_story(
        db,
        key="character:chen:location",
        value="旧城区",
        chapter_id="chapter-stale",
    )
    await db.execute(
        "UPDATE story_memory_sources SET status = 'stale' "
        "WHERE chapter_id = 'chapter-stale'",
    )

    rows = await SqliteStoryMemoryRecallRepository(db).search_current(
        "book-1",
        "旧城区的人物现在在哪里",
        kinds=("character_state",),
        limit=20,
    )

    assert [item.memory_key for item in rows] == ["character:linmo:location"]
    assert rows[0].source_excerpt == "角色抵达旧城区"


@pytest.mark.asyncio
async def test_story_recall_resolves_character_name_to_relationship_target(db):
    source_id = await db.execute_and_get_id(
        "INSERT INTO characters (book_id, name) VALUES ('book-1', '林墨')"
    )
    target_id = await db.execute_and_get_id(
        "INSERT INTO characters (book_id, name) VALUES ('book-1', '苏遥')"
    )
    assert source_id is not None and target_id is not None
    await _apply_relationship(
        db,
        source_character_id=str(source_id),
        target_character_id=str(target_id),
    )

    rows = await SqliteStoryMemoryRecallRepository(db).search_current(
        "book-1",
        "继续写苏遥接下来的反应",
        limit=20,
    )

    assert len(rows) == 1
    assert rows[0].kind == "relationship_state"
    assert "entity" in rows[0].candidate_channels


@pytest.mark.asyncio
async def test_contract_kind_is_an_independent_candidate_channel(db):
    await _apply_story(
        db,
        key="character:linmo:location",
        value="旧城区",
        chapter_id="chapter-1",
    )

    rows = await SqliteStoryMemoryRecallRepository(db).search_current(
        "book-1",
        "",
        kinds=("character_state",),
        limit=20,
    )

    assert len(rows) == 1
    assert rows[0].candidate_channels == ("contract_kind",)


class _SelectNoneReranker:
    async def rerank(self, **kwargs):
        del kwargs
        return MemoryRerankResult(model="judge", batch_count=1)


@pytest.mark.asyncio
async def test_explicit_semantic_memory_bypasses_model_filtering(db):
    forced_id = await db.execute_and_get_id(
        "INSERT INTO memory_items "
        "(book_id, kind, content, status, fingerprint) "
        "VALUES ('book-1', 'canon', '用户明确选择的设定', 'active', 'forced')"
    )
    await db.execute(
        "INSERT INTO memory_items "
        "(book_id, kind, content, status, fingerprint) "
        "VALUES ('book-1', 'plot', '普通召回内容', 'active', 'ordinary')"
    )
    assert forced_id is not None
    builder = WritingMemoryContextBuilder(
        SqliteMemoryRecallRepository(db),
        _SelectNoneReranker(),
    )

    block = await builder.build(
        MemoryContextRequest(
            book_id="book-1",
            user_prompt="普通召回内容",
            selected_memory_item_ids=(forced_id,),
        ),
        model_request=ModelRequest(provider="test", model="test-model"),
    )

    assert block.included_ids == [forced_id]
    assert "用户明确选择的设定" in block.text
    assert "普通召回内容" not in block.text
    assert block.diagnostics["rerankerStatus"] == "completed"


class _SelectStoryReranker:
    def __init__(self, record_id: str):
        self.record_id = record_id

    async def rerank(self, **kwargs):
        assert any(item.id == self.record_id for item in kwargs["candidates"])
        return MemoryRerankResult(
            decisions=(MemoryRerankDecision(
                record_id=self.record_id,
                priority="must_use",
                supports=("当前任务",),
                reason="直接相关",
            ),),
            model="judge",
            batch_count=1,
        )


@pytest.mark.asyncio
async def test_story_provider_hydrates_only_model_selected_candidate(db):
    await _apply_story(
        db,
        key="character:linmo:location",
        value="旧城区",
        chapter_id="chapter-1",
    )
    await _apply_story(
        db,
        key="character:suyao:location",
        value="北城",
        chapter_id="chapter-2",
    )
    candidates = await SqliteStoryMemoryRecallRepository(db).search_current(
        "book-1",
        "人物现在在哪里",
        kinds=("character_state",),
        limit=20,
    )
    selected = next(
        item for item in candidates
        if item.memory_key == "character:suyao:location"
    )
    provider = StoryMemoryContextProvider(
        SqliteStoryMemoryRecallRepository(db),
        _SelectStoryReranker(selected.record_id),
    )

    block = await provider.build(
        MemoryContextRequest(
            book_id="book-1",
            user_prompt="人物现在在哪里",
            story_kinds=("character_state",),
            recall_limit=10,
        ),
        model_request=ModelRequest(provider="test", model="test-model"),
    )

    assert [item.record_id for item in block.included_items] == [
        selected.record_id
    ]
    assert block.diagnostics["rerankerStatus"] == "completed"
    assert block.diagnostics["rerankerDecisions"][0]["reason"] == "直接相关"


@pytest.mark.asyncio
async def test_unified_retriever_prioritizes_story_and_deduplicates_semantic(db):
    await _apply_story(
        db,
        key="character:linmo:location",
        value="旧城区",
        chapter_id="chapter-1",
    )
    memory_id = await db.execute_and_get_id(
        "INSERT INTO memory_items "
        "(book_id, kind, content, status, source_type, source_id, fingerprint) "
        "VALUES ('book-1', 'character', '林墨的location：旧城区', "
        "'active', 'manual', 'm-1', 'fp-1')",
    )
    assert memory_id is not None
    retriever = UnifiedMemoryRetriever(
        WritingMemoryContextBuilder(SqliteMemoryRecallRepository(db)),
        StoryMemoryContextProvider(SqliteStoryMemoryRecallRepository(db)),
    )

    pack = await retriever.build(MemoryContextRequest(
        book_id="book-1",
        user_prompt="林墨在旧城区",
        token_budget=4_000,
        story_kinds=("character_state",),
    ))

    assert "权威 Story Memory" in pack.text
    assert "character:linmo:location" in pack.text
    assert memory_id in pack.semantic.suppressed_ids
    assert memory_id not in pack.semantic.included_ids
    assert [receipt.source for receipt in pack.receipts] == ["story_state"]
    assert pack.receipts[0].version == 1
    assert pack.receipts[0].chapter_id == "chapter-1"


def test_evidence_store_records_context_evidence_receipts():
    store = RunEvidenceStore()
    message = AgentMessage(
        role=MessageRole.DEVELOPER,
        content="authoritative story context",
        origin=MessageOrigin.HOST_CONTEXT,
        attributes={"context_name": "writing_retrieval"},
        host_metadata={
            CONTEXT_EVIDENCE_RECEIPTS_KEY: [{
                "evidenceId": "story:r-1:v3",
                "source": "story_state",
                "itemId": "r-1",
                "version": 3,
                "sourceId": "s-1",
                "chapterId": "chapter-4",
                "memoryKey": "plot:gate:status",
            }],
        },
    )

    receipts = store.record_context_messages((message,))

    assert len(receipts) == 1
    assert receipts[0].context_block == "writing_retrieval"
    assert receipts[0].version == 3
    assert receipts[0].metadata["chapterId"] == "chapter-4"
    assert store.context_receipts() == receipts


@pytest.mark.asyncio
async def test_context_provider_emits_reusable_pack_and_durable_usage_receipt(db):
    await _apply_story(
        db,
        key="character:linmo:location",
        value="旧城区",
        chapter_id="chapter-1",
    )
    domain = WritingDomainContext(
        book_id="book-1",
        chapter_id="chapter-1",
        current_chapter_title="第一章",
    )
    run_request = AgentRunRequest(
        messages=(AgentMessage(role=MessageRole.USER, content="续写林墨在旧城区的行动"),),
        model=ModelRequest(provider="test", model="test-model"),
        domain_context=domain.to_core_context(),
        context_window=32_000,
        tools_enabled=True,
    )
    budget = allocate_context_budget(
        window_tokens=32_000,
        output_reserve_tokens=4_096,
        claims=writing_context_claims(run_request),
    )
    provider = WritingContextProvider(RepositoryWritingContextSource(
        SqliteAssociatedContextRepository(db),
        SqliteMemoryRecallRepository(db),
        SqliteStoryMemoryRecallRepository(db),
    ))
    task = TaskContextRequest(
        task_spec=TaskSpec(
            goal="续写林墨的行动",
            target={
                "storyContext": ["characters", "timeline"],
                "entities": ["character:linmo"],
                "chapterIds": ["chapter-1"],
            },
        ),
        required_context_blocks=(WRITING_RETRIEVAL_CONTEXT,),
        evidence_kinds=("character", "plot"),
        include_response_context=True,
    )

    bundle = await provider.build_task_context(run_request, budget, task)

    retrieval = next(
        block for block in bundle.blocks
        if block.name == WRITING_RETRIEVAL_CONTEXT
    )
    receipts = retrieval.host_metadata[CONTEXT_EVIDENCE_RECEIPTS_KEY]
    assert receipts[0]["source"] == "story_state"
    assert receipts[0]["version"] == 1
    assert "memory_context_pack" in retrieval.content
    assert bundle.diagnostics["memoryContextReceipt"]["receipts"] == receipts
