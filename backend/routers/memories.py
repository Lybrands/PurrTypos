"""Spark idea & foreshadowing routes — backed by SQLite service."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query

from schemas.memories import (
    AddForeshadowingRequest,
    AddSparkIdeaRequest,
    BuildMemoryContextRequest,
    CreateMemoryRequest,
    ForeshadowingForPromptRequest,
    GetMemoryByIdsRequest,
    GetForeshadowingByBookRequest,
    GetForeshadowingByIdsRequest,
    GetSparkIdeasByBookRequest,
    GetSparkIdeasByIdsRequest,
    LinkMemoriesRequest,
    SearchMemoriesRequest,
    SearchSparkIdeasRequest,
    UpdateMemoryRequest,
    UpdateForeshadowingRequest,
    UpdateSparkIdeaRequest,
)
from services import memory_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["spark-ideas"])


def _memory_payload(body: CreateMemoryRequest) -> dict:
    return {
        "book_id": body.bookId,
        "kind": body.kind,
        "content": body.content,
        "scope_type": body.scopeType,
        "scope_id": body.scopeId,
        "summary": body.summary,
        "keywords": body.keywords,
        "importance": body.importance,
        "confidence": body.confidence,
        "status": body.status,
        "pinned": body.pinned,
        "source_type": body.sourceType,
        "source_id": body.sourceId,
    }


def _err_message(err: Exception) -> str:
    """记录异常并返回原始信息。

    （旧版 _mem0_err_message 会把错误归类成「请安装 Ollama / pip install mem0ai」
    等修复指引——但记忆栈已迁 SQLite，这些错误不可能发生，文案只会误导用户。）
    """
    logger.error("[memories] %s: %s", type(err).__name__, err, exc_info=True)
    return str(err)


# ---------------------------------------------------------------------------
# Unified long-term memories
# ---------------------------------------------------------------------------

@router.post("/memories")
async def create_memory(body: CreateMemoryRequest):
    try:
        from services import long_term_memory_service
        data = await long_term_memory_service.create_memory_item(**_memory_payload(body))
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.put("/memories/{id}")
async def update_memory(id: str, body: UpdateMemoryRequest):
    try:
        from services import long_term_memory_service
        data = await long_term_memory_service.update_memory_item(id, body.data)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/memories/search")
async def search_memories(body: SearchMemoriesRequest):
    try:
        from services import long_term_memory_service
        data = await long_term_memory_service.search_memory_items(
            body.bookId,
            body.query,
            options=body.options,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/memories/by-ids")
async def get_memories_by_ids(body: GetMemoryByIdsRequest):
    try:
        from services import long_term_memory_service
        data = await long_term_memory_service.get_memory_items_by_ids(body.ids)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/memories/{id}/archive")
async def archive_memory(id: str):
    try:
        from services import long_term_memory_service
        data = await long_term_memory_service.archive_memory_item(id)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/memories/link")
async def link_memories(body: LinkMemoriesRequest):
    try:
        from services import long_term_memory_service
        data = await long_term_memory_service.link_memory_items(
            book_id=body.bookId,
            from_memory_id=body.fromMemoryId,
            to_memory_id=body.toMemoryId,
            relation=body.relation,
            note=body.note,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/memories/context")
async def build_memory_context(body: BuildMemoryContextRequest):
    try:
        from services import memory_orchestrator
        block = await memory_orchestrator.build_memory_context(
            {
                "bookId": body.bookId,
                "selectedLongTermMemoryIds": body.selectedLongTermMemoryIds or [],
                "selectedMemoryIds": body.selectedMemoryIds or [],
                "selectedForeshadowingIds": body.selectedForeshadowingIds or [],
                "memoryBudget": body.memoryBudget,
                "memoryRecallLimit": body.memoryRecallLimit,
                "contextWindow": body.contextWindow,
            },
            body.userPrompt,
            body.mode,
        )
        return {"success": True, "data": {
            "text": block.text,
            "includedIds": block.included_ids,
            "deferredIds": block.deferred_ids,
            "tokenEstimate": block.token_estimate,
            "diagnostics": block.diagnostics,
        }}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


# ---------------------------------------------------------------------------
# Spark ideas
# ---------------------------------------------------------------------------

@router.post("/spark-ideas")
async def add_spark_idea(body: AddSparkIdeaRequest):
    try:
        data = await memory_service.add_spark_idea(
            body.bookId, body.layer, body.content,
            chapter_id=body.chapterId,
            character_id=body.characterId,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.put("/spark-ideas/{id}")
async def update_spark_idea(id: str, body: UpdateSparkIdeaRequest):
    try:
        data = await memory_service.update_spark_idea(id, body.data)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.delete("/spark-ideas/{id}")
async def delete_spark_idea(id: str):
    try:
        await memory_service.delete_spark_idea(id)
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.get("/spark-ideas/by-book")
async def get_spark_ideas_by_book(
    bookId: str = Query(...),
    layer: Optional[str] = Query(None),
):
    try:
        data = await memory_service.get_spark_ideas_by_book(bookId, layer=layer)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/spark-ideas/by-ids")
async def get_spark_ideas_by_ids(body: GetSparkIdeasByIdsRequest):
    try:
        data = await memory_service.get_spark_ideas_by_ids(body.ids)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/spark-ideas/for-prompt")
async def get_spark_ideas_for_prompt(body: SearchSparkIdeasRequest):
    try:
        data = await memory_service.get_spark_ideas_for_prompt(
            body.bookId, body.query, options=body.options,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


# ---------------------------------------------------------------------------
# Foreshadowing
# ---------------------------------------------------------------------------

@router.post("/foreshadowing")
async def add_foreshadowing(body: AddForeshadowingRequest):
    try:
        data = await memory_service.add_foreshadowing(
            body.bookId,
            body.chapterId,
            body.content,
            type_=body.type,
            expected_chapter_id=body.expectedChapterId,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.put("/foreshadowing/{id}")
async def update_foreshadowing(id: str, body: UpdateForeshadowingRequest):
    try:
        data = await memory_service.update_foreshadowing(id, body.data)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.delete("/foreshadowing/{id}")
async def delete_foreshadowing(id: str):
    try:
        await memory_service.delete_foreshadowing(id)
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.get("/foreshadowing/by-book")
async def get_foreshadowing_by_book(
    bookId: str = Query(...),
    status: Optional[str] = Query(None),
):
    try:
        data = await memory_service.get_foreshadowing_by_book(bookId, status_filter=status)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/foreshadowing/by-ids")
async def get_foreshadowing_by_ids(body: GetForeshadowingByIdsRequest):
    try:
        data = await memory_service.get_foreshadowing_by_ids(body.ids)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/foreshadowing/for-prompt")
async def get_foreshadowing_for_prompt(body: ForeshadowingForPromptRequest):
    try:
        data = await memory_service.get_foreshadowing_for_prompt(
            body.bookId, body.query, options=body.options,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}
