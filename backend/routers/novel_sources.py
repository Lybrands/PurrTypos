"""Read-only source library and explicit immutable revision commands."""

from __future__ import annotations

from fastapi import APIRouter, Header, Query

from application.novel_analysis_service import NovelAnalysisService
from application.novel_source_service import NovelSourceService
from dependencies import get_db
from domains.novel_sources import NovelSourceError
from exceptions import AppError
from schemas.novel_sources import (
    ArchiveSourceWorkRequest,
    ConfirmSourceImportRequest,
    FreezeBookSourceRequest,
    SourceFilePayload,
    PauseNovelAnalysisRequest,
    ResumeNovelAnalysisRequest,
    ReviewNovelAnalysisRequest,
    StartNovelAnalysisRequest,
)
from domains.novel_analysis import NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX


router = APIRouter(tags=["novel-sources"])


def _service() -> NovelSourceService:
    return NovelSourceService(get_db())


def _analysis_service() -> NovelAnalysisService:
    from application.agent_composition import get_agent_composition

    return NovelAnalysisService(get_db(), get_agent_composition())


async def _call(awaitable):
    try:
        return await awaitable
    except NovelSourceError as error:
        raise AppError(str(error), error.status_code) from error


def _ok(data=None):
    return {"success": True, "data": data}


@router.post("/novel-sources/import/preview")
async def preview_import(body: SourceFilePayload):
    try:
        return _ok(_service().preview_external_import(
            file_name=body.fileName,
            extension=body.extension,
            content=body.content,
        ))
    except NovelSourceError as error:
        raise AppError(str(error), error.status_code) from error


@router.post("/novel-sources/import/confirm")
async def confirm_import(body: ConfirmSourceImportRequest):
    return _ok(await _call(_service().confirm_external_import(
        title=body.title,
        file_name=body.fileName,
        extension=body.extension,
        content=body.content,
        expected_content_digest=body.expectedContentDigest,
        confirm_single_section=body.confirmSingleSection,
        rights_confirmed=body.rightsConfirmed,
        model_data_boundary_confirmed=body.modelDataBoundaryConfirmed,
        work_id=body.workId,
    )))


@router.post("/novel-sources/freeze-book")
async def freeze_book(body: FreezeBookSourceRequest):
    return _ok(await _call(_service().freeze_book(body.bookId)))


@router.get("/novel-sources")
async def list_works(includeArchived: bool = Query(default=False)):
    return _ok(await _call(_service().list_works(include_archived=includeArchived)))


@router.get("/novel-sources/{work_id}")
async def get_work(work_id: str):
    return _ok(await _call(_service().get_work(work_id)))


@router.put("/novel-sources/{work_id}/archive")
async def archive_work(work_id: str, _body: ArchiveSourceWorkRequest):
    return _ok(await _call(_service().archive_work(work_id)))


@router.get("/novel-source-revisions/{revision_id}")
async def get_revision(revision_id: str):
    return _ok(await _call(_service().get_revision(revision_id)))


@router.delete("/novel-source-revisions/{revision_id}")
async def delete_revision(revision_id: str):
    await _call(_service().delete_revision(revision_id))
    return _ok()


@router.get("/novel-source-revisions/{revision_id}/sections/{section_id}")
async def get_section(revision_id: str, section_id: str):
    return _ok(await _call(_service().get_section(revision_id, section_id)))


@router.get("/novel-source-revisions/{revision_id}/search")
async def search_sections(
    revision_id: str,
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=12, ge=1, le=30),
):
    return _ok(await _call(_service().search_sections(revision_id, q, limit=limit)))


@router.post("/novel-source-revisions/{revision_id}/analyses", status_code=202)
async def start_analysis(
    revision_id: str,
    body: StartNovelAnalysisRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    return _ok(await _analysis_service().start(
        source_revision_id=revision_id,
        command_id=idempotency_key,
        runtime=body.runtime,
    ))


@router.get("/novel-source-revisions/{revision_id}/analysis-runs")
async def list_analysis_runs(revision_id: str):
    return _ok(await _analysis_service().list_for_revision(revision_id))


@router.post("/novel-analysis-tasks/{task_id}/pause")
async def pause_analysis(task_id: str, body: PauseNovelAnalysisRequest):
    return _ok(await _analysis_service().pause(
        task_id,
        expected_revision=body.expectedTaskRevision,
    ))


@router.post("/novel-analysis-tasks/{task_id}/resume", status_code=202)
async def resume_analysis(
    task_id: str,
    body: ResumeNovelAnalysisRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    return _ok(await _analysis_service().resume(
        task_id=task_id,
        run_command_id=idempotency_key,
        runtime=body.runtime,
        retry_failed=body.retryFailed,
    ))


@router.post("/novel-analysis-tasks/{task_id}/cancel")
async def cancel_analysis(task_id: str):
    return _ok(await _analysis_service().cancel(task_id))


@router.get("/novel-analysis-artifacts/{artifact_id}")
async def get_analysis_artifact(artifact_id: str):
    return _ok(await _analysis_service().get_artifact(
        NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX + artifact_id
    ))


@router.post("/novel-analysis-artifacts/{artifact_id}/review")
async def review_analysis_artifact(
    artifact_id: str,
    body: ReviewNovelAnalysisRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    return _ok(await _analysis_service().review(
        artifact_ref=NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX + artifact_id,
        command_id=idempotency_key,
        payload=body.model_dump(mode="json"),
    ))


@router.post("/novel-analysis-artifacts/{artifact_id}/publish")
async def publish_analysis_artifact(artifact_id: str):
    return _ok(await _analysis_service().publish(
        NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX + artifact_id
    ))


@router.get("/novel-source-revisions/{revision_id}/analyses")
async def list_published_analyses(revision_id: str):
    return _ok(await _analysis_service().list_published(revision_id))


@router.get("/novel-source-analyses/{analysis_id}")
async def get_published_analysis(analysis_id: str):
    return _ok(await _analysis_service().get_published(analysis_id))
