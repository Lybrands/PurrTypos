"""Canon preview and atomic continuation creation routes."""

from fastapi import APIRouter, Query

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
        operation_id=body.operationId,
        use_source_techniques=body.useSourceTechniques,
    )}


@router.get("/continuations/{book_id}")
async def get_continuation(book_id: str):
    return {
        "success": True,
        "data": await _service().get_continuation(book_id),
    }


__all__ = ["router"]


@router.get("/continuations/{book_id}/sections")
async def list_sections(book_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
    from application.continuation_context import ContinuationContextService
    return {"success": True, "data": await ContinuationContextService(get_db()).list_source_sections(book_id=book_id, offset=offset, limit=limit)}


@router.get("/continuations/{book_id}/sections/{section_id}")
async def read_section(book_id: str, section_id: str):
    from application.continuation_context import ContinuationContextService
    return {"success": True, "data": await ContinuationContextService(get_db()).read_source_section(book_id=book_id, section_id=section_id)}


@router.get("/continuations/{book_id}/export")
async def export_continuation(book_id: str, includeHistory: bool = True):
    from application.continuation_context import ContinuationContextService
    await _service().get_continuation(book_id)
    db = get_db()
    entries = []
    if includeHistory:
        source = ContinuationContextService(db)
        offset = 0
        volume = None
        while True:
            page = await source.list_source_sections(book_id=book_id, offset=offset, limit=200)
            for item in page["items"]:
                if item["sectionType"] != "volume" and item["locator"].get("volumeId") and item["locator"]["volumeId"] != volume:
                    volume = item["locator"]["volumeId"]
                    entries.append({"title": item["locator"]["volumeTitle"], "text": "", "origin": "source"})
                if item["sectionType"] == "volume":
                    volume = item["locator"].get("volumeId")
                section = await source.read_source_section(book_id=book_id, section_id=item["sectionId"])
                entries.append({"title": section["title"], "text": section["text"], "origin": "source"})
            if page["nextOffset"] is None:
                break
            offset = page["nextOffset"]
    from utils.book_structure import get_ordered_leaf_chapters, load_chapter_texts
    rows = await get_ordered_leaf_chapters(db, book_id)
    texts = await load_chapter_texts(db, [row["id"] for row in rows])
    volume = None
    for row in rows:
        if row.get("volume_id") and row["volume_id"] != volume:
            entries.append({"title": row["volume_title"], "text": "", "origin": "continuation"})
            volume = row["volume_id"]
        entries.append({"title": row["title"], "text": texts[row["id"]], "origin": "continuation"})
    return {"success": True, "data": {"entries": entries}}


@router.get("/continuations/{book_id}/plot-materials")
async def plot_materials(book_id: str):
    import json
    rows = await get_db().fetch_all("SELECT source_key,body,records_json FROM continuation_material_baselines WHERE book_id=? AND kind IN ('plot', 'entity') ORDER BY source_key", [book_id])
    return {"success": True, "data": [{"sourceKey": row["source_key"], "body": row["body"]} for row in rows if any(record["factKind"] in {"event", "timeline", "unresolved_plot", "foreshadowing"} for record in json.loads(row["records_json"]))]}
