"""Canon preview and atomic continuation creation routes."""

from fastapi import APIRouter

from application.continuation_service import ContinuationService
from dependencies import get_db
from schemas.continuations import CanonPreviewRequest, CreateContinuationRequest


router = APIRouter(tags=["continuations"])


def _service() -> ContinuationService:
    return ContinuationService(get_db())


@router.post("/continuations/canon-preview")
async def preview_canon(body: CanonPreviewRequest):
    return {"success": True, "data": await _service().preview_canon(
        source_revision_id=body.sourceRevisionId,
        source_analysis_id=body.sourceAnalysisId,
        fork_section_id=body.forkSectionId,
    )}


@router.post("/continuations")
async def create_continuation(body: CreateContinuationRequest):
    return {"success": True, "data": await _service().create_continuation(
        title=body.title,
        source_revision_id=body.sourceRevisionId,
        source_analysis_id=body.sourceAnalysisId,
        fork_section_id=body.forkSectionId,
        expected_snapshot_digest=body.expectedSnapshotDigest,
        enable_volume=body.enableVolume,
        writing_method_bindings=[
            item.model_dump(mode="json") for item in body.writingMethodBindings
        ],
    )}


@router.get("/continuations/{book_id}")
async def get_continuation(book_id: str):
    return {
        "success": True,
        "data": await _service().get_continuation(book_id),
    }


__all__ = ["router"]
