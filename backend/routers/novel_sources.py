"""Read-only source library and explicit immutable revision commands."""

from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request
from sse_starlette.sse import EventSourceResponse

from application.novel_source_service import NovelSourceService
from dependencies import get_db
from domains.novel_sources import NovelSourceError
from exceptions import AppError
from schemas.novel_sources import (
    AnalysisSessionUpdate,
    ArchiveSourceWorkRequest,
    ConfirmSourceImportRequest,
    FollowUpNovelAnalysisRequest,
    FreezeBookSourceRequest,
    SourceFilePayload,
    PauseNovelAnalysisRequest,
    ResumeNovelAnalysisRequest,
    ReviewNovelAnalysisRequest,
    StartNovelAnalysisRequest,
)
from agents.novel_analysis.attempt_artifact import (
    NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE,
)
from agents.novel_analysis.review_projection import (
    NovelAnalysisReviewProjection,
    NovelAnalysisReviewProjectionError,
)
from agents.novel_analysis.run_projection import (
    NovelAnalysisReplacementRunProjection,
)
from agents.novel_analysis.stream_projection import (
    VersionedNovelAnalysisStreamQuery,
)
from agents.novel_analysis.review_artifact import (
    NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE,
)
from agents.novel_analysis.publication_service import (
    NovelAnalysisPublicationError,
    NovelAnalysisReplacementPublicationService,
)
from agents.novel_analysis.product_service import (
    VersionedNovelAnalysisProductService,
)
from agents.novel_analysis.sessions import NovelAnalysisSessions
from agents.novel_analysis.published_query import NovelAnalysisPublishedQuery
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)


router = APIRouter(tags=["novel-sources"])


def _service() -> NovelSourceService:
    return NovelSourceService(get_db())


def _analysis_control_service() -> VersionedNovelAnalysisProductService:
    from application.agent_composition import get_agent_composition

    composition = get_agent_composition()
    return VersionedNovelAnalysisProductService(
        get_db(),
        composition,
    )


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
            import_kind=body.importKind,
            document_count=body.documentCount,
            skipped_file_count=body.skippedFileCount,
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
        import_kind=body.importKind,
        document_count=body.documentCount,
        skipped_file_count=body.skippedFileCount,
        expected_content_digest=body.expectedContentDigest,
        confirm_single_section=body.confirmSingleSection,
        rights_confirmed=body.rightsConfirmed,
        model_data_boundary_confirmed=body.modelDataBoundaryConfirmed,
        section_layout=(
            [item.model_dump(mode="json") for item in body.sections]
            if body.sections is not None
            else None
        ),
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


@router.delete("/novel-sources/{work_id}")
async def delete_work(work_id: str):
    await _call(_service().delete_work(work_id))
    return _ok()


@router.get("/novel-source-revisions/{revision_id}")
async def get_revision(revision_id: str):
    return _ok(await _call(_service().get_revision(revision_id)))


@router.delete("/novel-source-revisions/{revision_id}")
async def delete_revision(revision_id: str):
    await _call(_service().delete_revision(revision_id))
    return _ok()


@router.get("/novel-source-revisions/{revision_id}/sections/{section_id}")
async def get_section(
    revision_id: str,
    section_id: str,
    start_character: int = Query(default=0, ge=0, alias="startCharacter"),
    character_limit: int | None = Query(
        default=None, ge=1, le=100_000, alias="characterLimit"
    ),
):
    return _ok(await _call(_service().get_section(
        revision_id,
        section_id,
        start_character=start_character,
        character_limit=character_limit,
    )))


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
    await NovelAnalysisSessions(get_db()).bind(revision_id, body.conversationId, idempotency_key)
    return _ok(await _analysis_control_service().start(
        source_revision_id=revision_id,
        command_id=idempotency_key,
        prompt=body.prompt,
        runtime=body.runtime,
    ))


@router.post(
    "/novel-source-revisions/{revision_id}/analysis-follow-ups",
    status_code=202,
)
async def follow_up_analysis(
    revision_id: str,
    body: FollowUpNovelAnalysisRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    await NovelAnalysisSessions(get_db()).bind(revision_id, body.conversationId, idempotency_key)
    if body.replaceRunId:
        return _ok(await _analysis_control_service().replace_turn(
            source_revision_id=revision_id, command_id=idempotency_key,
            target_run_id=body.replaceRunId, prompt=body.prompt, runtime=body.runtime,
        ))
    return _ok(await _analysis_control_service().follow_up(
        source_revision_id=revision_id,
        artifact_id=body.artifactId,
        prompt=body.prompt,
        command_id=idempotency_key,
        runtime=body.runtime,
    ))


@router.get("/novel-source-revisions/{revision_id}/analysis-runs")
async def list_analysis_runs(revision_id: str):
    from application.agent_composition import get_agent_composition

    composition = get_agent_composition()
    replacement = await NovelAnalysisReplacementRunProjection(
        get_db(),
        long_tasks=composition.long_task_repository,
    ).list_for_revision(revision_id)
    return _ok(sorted(
        replacement,
        key=lambda item: (
            str(item.get("createTime") or ""),
            str(item.get("runId") or ""),
        ),
        reverse=True,
    ))


@router.get("/novel-source-revisions/{revision_id}/analysis-events")
async def stream_analysis_events(
    request: Request, revision_id: str,
    after: int = Query(default=0, ge=0), limit: int = Query(default=500, ge=1, le=500),
):
    from application.agent_composition import get_agent_composition
    from application.agent_event_stream import stream_agent_pages

    composition = get_agent_composition()
    query = VersionedNovelAnalysisStreamQuery(
        get_db(), output_repository=composition.output_journal,
        long_tasks=composition.long_task_repository,
    )
    # Validate scope before opening the response so a deleted source is a 404,
    # not an endless sequence of failed SSE reconnections.
    first = await query.read_page(revision_id, after=after, limit=limit)

    async def read_page(cursor):
        nonlocal first
        if first is not None:
            page, first = first, None
            return page
        return await query.read_page(revision_id, after=cursor, limit=limit)

    return EventSourceResponse(stream_agent_pages(
        request=request, read_page=read_page,
        notifications=composition.output_notifications, after=after,
    ))


@router.post("/novel-analysis-tasks/{task_id}/pause")
async def pause_analysis(task_id: str, body: PauseNovelAnalysisRequest):
    return _ok(await _analysis_control_service().pause(
        task_id,
        expected_revision=body.expectedTaskRevision,
    ))


@router.post("/novel-analysis-tasks/{task_id}/resume", status_code=202)
async def resume_analysis(
    task_id: str,
    body: ResumeNovelAnalysisRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    return _ok(await _analysis_control_service().resume(
        task_id=task_id,
        run_command_id=idempotency_key,
        runtime=body.runtime,
    ))


@router.post("/novel-analysis-tasks/{task_id}/cancel")
async def cancel_analysis(task_id: str):
    return _ok(await _analysis_control_service().cancel(task_id))


@router.get("/novel-analysis-artifacts/{artifact_id}")
async def get_analysis_artifact(artifact_id: str):
    artifact = await SqliteArtifactRepository(get_db()).load(artifact_id)
    if (
        artifact is not None
        and artifact.namespace == NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE
    ):
        try:
            return _ok(await NovelAnalysisReviewProjection(get_db()).load(
                artifact_id
            ))
        except NovelAnalysisReviewProjectionError as error:
            raise AppError(str(error), 409) from error
    if (
        artifact is not None
        and artifact.namespace == NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE
    ):
        try:
            return _ok(
                await NovelAnalysisReplacementPublicationService(
                    get_db()
                ).load_reviewed(artifact_id)
            )
        except (NovelAnalysisPublicationError, ValueError) as error:
            raise AppError(str(error), 409) from error
    raise NotFoundError("来源分析结果不存在")


@router.post("/novel-analysis-artifacts/{artifact_id}/review")
async def review_analysis_artifact(
    artifact_id: str,
    body: ReviewNovelAnalysisRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    artifact = await SqliteArtifactRepository(get_db()).load(artifact_id)
    if (
        artifact is not None
        and artifact.namespace in {
            NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE,
            NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE,
        }
    ):
        try:
            return _ok(await NovelAnalysisReplacementPublicationService(
                get_db()
            ).review(
                source_artifact_id=artifact_id,
                command_id=idempotency_key,
                payload=body.model_dump(mode="json"),
            ))
        except (NovelAnalysisPublicationError, ValueError) as error:
            raise AppError(str(error), 422) from error
    if artifact is None:
        raise AppError("来源分析结果不存在", 404)
    raise AppError("来源分析结果类型不支持审核", 409)


@router.post("/novel-analysis-artifacts/{artifact_id}/publish")
async def publish_analysis_artifact(artifact_id: str):
    artifact = await SqliteArtifactRepository(get_db()).load(artifact_id)
    if (
        artifact is not None
        and artifact.namespace == NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE
    ):
        raise AppError("请先审核分析结果再发布", 409)
    if (
        artifact is not None
        and artifact.namespace == NOVEL_ANALYSIS_REVIEWED_ARTIFACT_NAMESPACE
    ):
        try:
            return _ok(await NovelAnalysisReplacementPublicationService(
                get_db()
            ).publish(artifact_id))
        except (NovelAnalysisPublicationError, ValueError) as error:
            raise AppError(str(error), 409) from error
    if artifact is None:
        raise AppError("来源分析结果不存在", 404)
    raise AppError("来源分析结果类型不支持发布", 409)


@router.get("/novel-source-revisions/{revision_id}/analyses")
async def list_published_analyses(revision_id: str):
    return _ok(await NovelAnalysisPublishedQuery(get_db()).list_for_revision(
        revision_id
    ))


@router.get("/novel-source-analyses/{analysis_id}")
async def get_published_analysis(analysis_id: str):
    return _ok(await NovelAnalysisPublishedQuery(get_db()).get(analysis_id))


@router.get("/novel-source-revisions/{revision_id}/conversations")
async def list_analysis_conversations(revision_id: str):
    return _ok(await NovelAnalysisSessions(get_db()).list(revision_id))


@router.post("/novel-source-revisions/{revision_id}/conversations")
async def create_analysis_conversation(revision_id: str):
    return _ok(await NovelAnalysisSessions(get_db()).create(revision_id))


@router.patch("/novel-source-revisions/{revision_id}/conversations/{identity}")
async def update_analysis_conversation(revision_id: str, identity: str, body: AnalysisSessionUpdate):
    await NovelAnalysisSessions(get_db()).update(revision_id, identity, body.title, body.closed)
    return _ok()


@router.delete("/novel-source-revisions/{revision_id}/conversations/{identity}")
async def delete_analysis_conversation(revision_id: str, identity: str):
    from application.agent_cancellation_service import AgentCancellationService
    from application.agent_composition import get_agent_composition

    db = get_db()
    sessions = NovelAnalysisSessions(get_db())
    active_run_ids = await sessions.active_run_ids(revision_id, identity)
    active_task_ids = await sessions.active_task_ids(revision_id, identity)
    cancellation = (
        AgentCancellationService(db, get_agent_composition())
        if active_run_ids else None
    )
    for run_id in active_run_ids:
        row = await db.fetch_one(
            "SELECT status FROM ai_agent_runs WHERE id = ?",
            [run_id],
        )
        if (
            cancellation is not None
            and row is not None
            and row.get("status") == "running"
        ):
            await cancellation.cancel(run_id)
    for task_id in active_task_ids:
        await _analysis_control_service().cancel(task_id)
    await sessions.delete(revision_id, identity)
    return _ok()
