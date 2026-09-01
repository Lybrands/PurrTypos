"""Spark idea & foreshadowing routes — backed by SQLite service."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query

from application.unified_memory import (
    UnifiedMemoryQueryService,
    page_to_response,
)
from dependencies import get_db
from domains.writing.unified_memory import (
    UnifiedMemorySource,
    UnifiedMemoryStatus,
)

from schemas.memories import (
    AddForeshadowingRequest,
    AddSparkIdeaRequest,
    BuildMemoryContextRequest,
    CreateMemoryRequest,
    ForeshadowingForPromptRequest,
    LinkMemoriesRequest,
    DeleteMemoryRequest,
    ResolveMemoriesRequest,
    ReviewMemoryRequest,
    SearchSparkIdeasRequest,
    SetMemoryStateRequest,
    UpdateMemoryRequest,
    UpdateForeshadowingRequest,
    UpdateSparkIdeaRequest,
)
from infrastructure.persistence.writing import SqliteWritingSourceRepository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["spark-ideas"])


def _memory_operations():
    from application.agent_composition import get_agent_composition
    from application.memory_operations import MemoryApplicationService

    composition = get_agent_composition()
    return MemoryApplicationService(
        composition.database,
        composition.memory_resource,
    )


def _writing_sources() -> SqliteWritingSourceRepository:
    return SqliteWritingSourceRepository(get_db())


def _explicit_source_fields(body, fields: dict[str, str]) -> dict:
    return {
        target: getattr(body, source)
        for source, target in fields.items()
        if source in body.model_fields_set
    }


def _metadata_from_create(body: CreateMemoryRequest) -> dict:
    from application.memory_operations import memory_metadata

    return memory_metadata(
        kind=body.kind,
        scope_type=body.scopeType,
        scope_id=body.scopeId,
        summary=body.summary,
        keywords=body.keywords,
        importance=body.importance,
        confidence=body.confidence,
        pinned=body.pinned,
    )


def _metadata_from_update(body: UpdateMemoryRequest, current: dict) -> dict | None:
    from application.memory_operations import memory_metadata

    changes = body.model_dump(exclude_none=True)
    metadata_fields = {
        "kind",
        "scopeType",
        "scopeId",
        "summary",
        "keywords",
        "importance",
        "confidence",
        "pinned",
    }
    if not metadata_fields.intersection(changes):
        return None
    existing = dict(current.get("metadata") or {})
    return memory_metadata(
        kind=changes.get("kind", existing.get("kind")),
        scope_type=changes.get("scopeType", existing.get("scopeType", "book")),
        scope_id=(
            changes["scopeId"]
            if "scopeId" in changes
            else existing.get("scopeId")
        ),
        summary=changes.get("summary", existing.get("summary", "")),
        keywords=changes.get("keywords", existing.get("keywords", "")),
        importance=changes.get("importance", existing.get("importance", 3)),
        confidence=changes.get("confidence", existing.get("confidence", 1.0)),
        pinned=changes.get("pinned", existing.get("pinned", False)),
    )


def _err_message(err: Exception) -> str:
    """Record private diagnostics and return only a stable public code."""
    logger.error("[memories] %s: %s", type(err).__name__, err, exc_info=True)
    code = getattr(err, "code", None)
    return str(code) if isinstance(code, str) and code else "memory_request_failed"


# ---------------------------------------------------------------------------
# Unified long-term memories
# ---------------------------------------------------------------------------

@router.get("/books/{bookId}/memories/unified")
async def list_unified_memories(
    bookId: str,
    q: str = Query(default="", max_length=500),
    status: list[UnifiedMemoryStatus] | None = Query(default=None),
    kind: list[str] | None = Query(default=None),
    source: list[UnifiedMemorySource] | None = Query(default=None),
    limit: int = Query(default=120, ge=1, le=500),
):
    try:
        page = await UnifiedMemoryQueryService(
            get_db(),
            _memory_operations(),
        ).list_items(
            bookId,
            query=q,
            statuses=tuple(item.value for item in (status or ())),
            kinds=tuple(kind or ()),
            sources=tuple(item.value for item in (source or ())),
            limit=limit,
        )
        return {"success": True, "data": page_to_response(page)}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}

@router.post("/memories")
async def create_memory(body: CreateMemoryRequest):
    try:
        data = await _memory_operations().create_manual(
            book_id=body.bookId,
            operation_key=body.operationKey,
            text=body.text,
            metadata=_metadata_from_create(body),
            state=body.state,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.put("/memories/{id}")
async def update_memory(id: str, body: UpdateMemoryRequest):
    try:
        operations = _memory_operations()
        current = await operations.get(
            book_id=body.bookId,
            item_id=id,
            include_inactive=True,
        )
        if current is None:
            return {"success": False, "error": "memory_not_found"}
        data = await operations.update(
            book_id=body.bookId,
            item_id=id,
            version=body.version,
            operation_key=f"api-update:{body.operationKey}",
            text=body.text,
            metadata=_metadata_from_update(body, current),
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/memories/{id}/state")
async def set_memory_state(id: str, body: SetMemoryStateRequest):
    try:
        data = await _memory_operations().set_state(
            book_id=body.bookId,
            item_id=id,
            version=body.version,
            operation_key=f"api-state:{body.operationKey}",
            state=body.state,
            reason=body.reason,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/memories/link")
async def link_memories(body: LinkMemoriesRequest):
    try:
        from purra_mem0 import MemoryRef

        data = await _memory_operations().link(
            book_id=body.bookId,
            from_ref=MemoryRef(body.fromMemory.id, body.fromMemory.version),
            to_ref=MemoryRef(body.toMemory.id, body.toMemory.version),
            relation=body.relation,
            note=body.note,
            operation_key=f"api-link:{body.operationKey}",
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/memories/{id}/review")
async def review_memory(id: str, body: ReviewMemoryRequest):
    try:
        from purra_mem0 import MemoryRef

        data = await _memory_operations().review(
            book_id=body.bookId,
            candidate=MemoryRef(id, body.version),
            operation_key=body.operationKey,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/memories/resolve")
async def resolve_memories(body: ResolveMemoriesRequest):
    try:
        from purra_mem0 import MemoryRef, MemoryResolution

        data = await _memory_operations().resolve(
            book_id=body.bookId,
            resolution=MemoryResolution(
                body.kind,
                tuple(MemoryRef(item.id, item.version) for item in body.items),
                body.keep,
                body.reviewKey,
            ),
            operation_key=body.operationKey,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/memories/{id}/delete")
async def delete_memory(id: str, body: DeleteMemoryRequest):
    try:
        data = await _memory_operations().delete(
            book_id=body.bookId,
            item_id=id,
            version=body.version,
            operation_key=body.operationKey,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.get("/memories/{id}/history")
async def memory_history(id: str, bookId: str = Query(...)):
    try:
        data = await _memory_operations().history(book_id=bookId, item_id=id)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.get("/memories/{id}/links")
async def memory_links(
    id: str,
    bookId: str = Query(...),
    limit: int = Query(default=20, ge=1, le=32),
    after: str | None = Query(default=None),
):
    try:
        data = await _memory_operations().links(
            book_id=bookId,
            item_id=id,
            limit=limit,
            after=after,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/memories/context")
async def build_memory_context(body: BuildMemoryContextRequest):
    try:
        from application.writing_memory_context import (
            build_writing_memory_context,
        )
        from dependencies import get_db

        result = await build_writing_memory_context(body, db=get_db())
        return {"success": True, "data": result.to_response_data()}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


# ---------------------------------------------------------------------------
# Spark ideas
# ---------------------------------------------------------------------------

@router.post("/spark-ideas")
async def add_spark_idea(body: AddSparkIdeaRequest):
    try:
        data = await _writing_sources().add_spark_idea(
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
        data = await _writing_sources().update_spark_idea(
            body.bookId,
            id,
            _explicit_source_fields(body, {
                "content": "content",
                "layer": "layer",
                "chapterId": "chapter_id",
                "characterId": "character_id",
            }),
        )
        if data is None:
            return {"success": False, "error": "spark_idea_not_found"}
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.delete("/spark-ideas/{id}")
async def delete_spark_idea(id: str, bookId: str = Query(...)):
    try:
        deleted = await _writing_sources().delete_spark_idea(bookId, id)
        if deleted is None:
            return {"success": False, "error": "spark_idea_not_found"}
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.get("/spark-ideas/by-book")
async def get_spark_ideas_by_book(
    bookId: str = Query(...),
    layer: Optional[str] = Query(None),
):
    try:
        data = await _writing_sources().list_spark_ideas(bookId, layer=layer)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/spark-ideas/for-prompt")
async def get_spark_ideas_for_prompt(body: SearchSparkIdeasRequest):
    try:
        data = await _writing_sources().get_spark_ideas_for_prompt(
            body.bookId,
            body.query,
            options=(body.options.model_dump(exclude_none=True) if body.options else None),
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
        data = await _writing_sources().add_foreshadowing(
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
        data = await _writing_sources().update_foreshadowing(
            body.bookId,
            id,
            _explicit_source_fields(body, {
                "content": "content",
                "type": "type",
                "expectedChapterId": "expected_chapter_id",
                "status": "status",
                "resolvedChapterId": "resolved_chapter_id",
            }),
        )
        if data is None:
            return {"success": False, "error": "foreshadowing_not_found"}
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.delete("/foreshadowing/{id}")
async def delete_foreshadowing(id: str, bookId: str = Query(...)):
    try:
        deleted = await _writing_sources().delete_foreshadowing(bookId, id)
        if deleted is None:
            return {"success": False, "error": "foreshadowing_not_found"}
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.get("/foreshadowing/by-book")
async def get_foreshadowing_by_book(
    bookId: str = Query(...),
    status: Optional[str] = Query(None),
):
    try:
        data = await _writing_sources().list_foreshadowing(bookId, status=status)
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.post("/foreshadowing/for-prompt")
async def get_foreshadowing_for_prompt(body: ForeshadowingForPromptRequest):
    try:
        data = await _writing_sources().get_foreshadowing_for_prompt(
            body.bookId,
            body.query,
            options=(body.options.model_dump(exclude_none=True) if body.options else None),
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}
