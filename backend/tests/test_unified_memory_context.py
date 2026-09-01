from __future__ import annotations

import pytest

from purra.contracts import TaskContextRequest, TaskSpec

from domains.writing.memory_context import (
    MemoryContextBlock,
    MemoryContextRequest,
    MemoryUsageReceipt,
)
from domains.writing.repositories import StoryMemoryRecallItem
from domains.writing.unified_memory_context import (
    MemoryContextAssembler,
    StoryMemoryContextBlock,
    StoryMemoryContextProvider,
    UnifiedMemoryRetriever,
    memory_context_request_from_task,
)


def _story(value: str = "北塔") -> StoryMemoryRecallItem:
    return StoryMemoryRecallItem(
        record_id="record-1",
        book_id="book",
        memory_key="character:hero:location",
        kind="character_state",
        subject_id="hero",
        payload={"attribute": "location", "value": value},
        version=3,
        source_id="story-source-1",
        chapter_id="chapter-8",
        source_excerpt=f"主角抵达{value}",
    )


class _StoryRepository:
    def __init__(self, items=(_story(),)):
        self.items = tuple(items)
        self.request = None

    async def search_current(self, book_id, query, **kwargs):
        self.request = (book_id, query, kwargs)
        return self.items

    async def get_current_by_ids(self, book_id, record_ids):
        selected = set(record_ids)
        return tuple(item for item in self.items if item.record_id in selected)


class _SemanticBuilder:
    def __init__(self):
        self.request = None

    async def build(self, request, **_kwargs):
        self.request = request
        return MemoryContextBlock(
            text='{"id":"memory-1","text":"一般长期记忆"}',
            included_ids=["memory-1"],
            receipts=(MemoryUsageReceipt(
                evidence_id="mem0:scope:memory-1:1",
                source="mem0/scope",
                item_id="memory-1",
                version=1,
            ),),
        )


def test_task_hints_compile_without_overwriting_host_scope():
    base = MemoryContextRequest(
        book_id="book",
        user_prompt="继续写",
        token_budget=8_000,
        selected_memory_item_ids=("manual-1",),
    )
    task = TaskContextRequest(
        task_spec=TaskSpec(
            goal="续写",
            target={
                "storyContext": ["characters", "timeline"],
                "entities": ["hero"],
                "chapterIds": ["chapter-8"],
            },
        ),
        evidence_kinds=("world_fact",),
    )
    compiled = memory_context_request_from_task(
        base,
        task,
        current_chapter_id="chapter-9",
    )
    assert compiled.book_id == "book"
    assert compiled.selected_memory_item_ids == ("manual-1",)
    assert compiled.story_kinds == (
        "character_state",
        "timeline_event",
        "world_fact",
    )
    assert compiled.chapter_ids == ("chapter-8", "chapter-9")


@pytest.mark.asyncio
async def test_story_provider_emits_versioned_source_receipt():
    repository = _StoryRepository()
    block = await StoryMemoryContextProvider(repository).build(
        MemoryContextRequest(
            book_id="book",
            user_prompt="主角在哪里",
            token_budget=2_000,
            recall_limit=4,
        )
    )
    assert [item.record_id for item in block.included_items] == ["record-1"]
    receipt, = block.receipts
    assert receipt.evidence_id == "story:record-1:v3"
    assert receipt.source_id == "story-source-1"
    assert receipt.chapter_id == "chapter-8"


@pytest.mark.asyncio
async def test_unified_retriever_gives_story_authority_to_component_builder():
    semantic = _SemanticBuilder()
    pack = await UnifiedMemoryRetriever(
        semantic,
        StoryMemoryContextProvider(_StoryRepository()),
    ).build(MemoryContextRequest(
        book_id="book",
        user_prompt="主角在哪里",
        token_budget=4_000,
        recall_limit=4,
    ))
    assert semantic.request.authoritative_fingerprints
    assert "权威 Story Memory" in pack.text
    assert "语义长期记忆" in pack.text
    assert {receipt.source for receipt in pack.receipts} == {
        "story_state",
        "mem0/scope",
    }


def test_outer_budget_truncation_drops_unverifiable_receipts():
    story_item = _story("极长地点")
    story = StoryMemoryContextBlock(
        text="故事" * 2_000,
        included_items=(story_item,),
        receipts=(MemoryUsageReceipt(
            evidence_id="story:record-1:v3",
            source="story_state",
            item_id="record-1",
            version=3,
        ),),
    )
    semantic = MemoryContextBlock(
        text="长期记忆" * 2_000,
        included_ids=["memory-1"],
        receipts=(MemoryUsageReceipt(
            evidence_id="mem0:scope:memory-1:1",
            source="mem0/scope",
            item_id="memory-1",
            version=1,
        ),),
    )
    pack = MemoryContextAssembler().assemble(story, semantic, token_budget=80)
    assert pack.outer_truncated is True
    assert pack.receipts == ()
    assert pack.semantic.receipts == ()
    assert pack.story.receipts == ()
