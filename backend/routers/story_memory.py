from __future__ import annotations

from dataclasses import asdict
from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder

from application.story_memory_mapping import story_setting_change_from_input
from application.story_memory_evolution import StoryMemoryEvolutionService
from dependencies import get_db
from domains.writing.story_memory import (
    StoryMemoryConflictError,
    StoryMemoryKind,
    StoryMemoryNotFoundError,
)
from domains.writing.story_memory_ledger import StoryMemoryLedger
from domains.writing.story_memory_evolution import EvolutionReviewStatus
from infrastructure.persistence.writing.sqlite_story_memory_repository import (
    SqliteStoryMemoryRepository,
)
from schemas.story_memory import (
    AnalyzeChapterStoryMemoryRequest,
    InvalidateStoryMemoryRequest,
    ResolveStoryMemoryEvolutionRequest,
    StageStoryMemoryDeltaRequest,
)
from services.story_memory_analysis_service import (
    analyze_chapter,
    receipt_dict,
)

router = APIRouter(tags=["story-memory"])


def _ledger() -> StoryMemoryLedger:
    return StoryMemoryLedger(SqliteStoryMemoryRepository(get_db()))


def _evolution() -> StoryMemoryEvolutionService:
    return StoryMemoryEvolutionService(get_db())


@router.post("/story-memory/deltas")
async def stage_story_memory_delta(body: StageStoryMemoryDeltaRequest):
    try:
        delta = await _ledger().stage_settings(
            book_id=body.bookId,
            chapter_id=body.chapterId,
            source_revision=body.sourceRevision,
            source_type=body.sourceType,
            note=body.note,
            changes=tuple(
                story_setting_change_from_input(item, body.chapterId)
                for item in body.changes
            ),
        )
        await _evolution().review_delta(delta.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _success(delta)


@router.post("/story-memory/deltas/{deltaId}/evolution-review")
async def create_story_memory_evolution_review(deltaId: str):
    try:
        return _review_success(await _evolution().review_delta(deltaId))
    except StoryMemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/story-memory/deltas/{deltaId}/evolution-review")
async def get_story_memory_evolution_review(deltaId: str):
    review = await _evolution().get_review(deltaId)
    if review is None:
        raise HTTPException(status_code=404, detail="evolution review not found")
    return _review_success(review)


@router.post("/story-memory/deltas/{deltaId}/evolution-review/resolve")
async def resolve_story_memory_evolution_review(
    deltaId: str,
    body: ResolveStoryMemoryEvolutionRequest,
):
    try:
        return _success(
            await _evolution().resolve_review(
                deltaId,
                body.resolutions,
                actor="user",
            )
        )
    except StoryMemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StoryMemoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/books/{bookId}/story-memory/evolution-reviews")
async def list_story_memory_evolution_reviews(
    bookId: str,
    status: list[EvolutionReviewStatus] | None = Query(default=None),
):
    reviews = await _evolution().list_reviews(
        bookId,
        statuses=tuple(item.value for item in (status or ())),
    )
    return {
        "success": True,
        "data": jsonable_encoder([
            {**asdict(item), "summary": item.summary}
            for item in reviews
        ]),
    }


@router.get("/story-memory/deltas/{deltaId}")
async def get_story_memory_delta(deltaId: str):
    delta = await _ledger().get_delta(deltaId)
    if delta is None:
        raise HTTPException(status_code=404, detail="story-memory delta not found")
    return _success(delta)


@router.post("/story-memory/deltas/{deltaId}/apply")
async def apply_story_memory_delta(deltaId: str):
    try:
        return _success(await _ledger().approve_delta(deltaId))
    except StoryMemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StoryMemoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/story-memory/deltas/{deltaId}/rollback")
async def rollback_story_memory_delta(deltaId: str):
    try:
        return _success(await _ledger().revert_delta(deltaId))
    except StoryMemoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StoryMemoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/books/{bookId}/story-memory")
async def get_story_memory(
    bookId: str,
    kind: list[StoryMemoryKind] | None = Query(default=None),
):
    records = await _ledger().current_state(
        bookId,
        kinds=tuple(item.value for item in (kind or ())),
    )
    return _success(records)


@router.get("/books/{bookId}/story-memory/versions")
async def get_story_memory_versions(bookId: str, memoryKey: str = Query(min_length=1)):
    return _success(await _ledger().history(bookId, memoryKey))


@router.post("/books/{bookId}/chapters/{chapterId}/story-memory/invalidate")
async def invalidate_chapter_story_memory(
    bookId: str,
    chapterId: str,
    body: InvalidateStoryMemoryRequest,
):
    try:
        receipt = await _ledger().chapter_changed(
            bookId,
            chapterId,
            current_revision=body.currentRevision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _success(receipt)


@router.post("/books/{bookId}/chapters/{chapterId}/story-memory/analyze")
async def analyze_chapter_story_memory(
    bookId: str,
    chapterId: str,
    body: AnalyzeChapterStoryMemoryRequest,
):
    try:
        receipt = await analyze_chapter(
            get_db(),
            book_id=bookId,
            chapter_id=chapterId,
            preferred_model_id=body.modelId,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "data": jsonable_encoder(receipt_dict(receipt))}


def _success(value: object) -> dict[str, object]:
    if hasattr(value, "__dataclass_fields__"):
        payload = asdict(value)  # type: ignore[arg-type]
    elif isinstance(value, tuple):
        payload = [asdict(item) for item in value]
    else:
        payload = value
    return {"success": True, "data": jsonable_encoder(payload)}


def _review_success(value) -> dict[str, object]:
    payload = asdict(value)
    payload["summary"] = value.summary
    return {"success": True, "data": jsonable_encoder(payload)}


__all__ = ["router"]
