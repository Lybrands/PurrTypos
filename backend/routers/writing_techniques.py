"""Writing-technique authoring API; every editor uses the same draft service."""

import asyncio
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from application.writing_technique_service import WritingTechniqueService
from dependencies import get_db
from domains.writing.techniques import TechniqueError
from schemas.writing_techniques import (
    ApplyTechniqueChangesRequest, CreateTechniqueDraftRequest, CreateSchemeDraftRequest,
    PublishTechniqueRequest, SealTechniqueRequest, TechniqueStatusRequest,
    UpdateSchemeDraftRequest, TechniqueModeRequest,
    ImportTechniqueRequest,
    ReserveTechniqueInputRequest, VersionRef, UploadTechniqueRequest,
    TechniqueRequest, DeleteTechniqueRequest,
)

router = APIRouter(prefix="/writing-techniques", tags=["writing-techniques"])
Kind = Literal["technique", "scheme"]


def _service():
    return WritingTechniqueService(get_db())


@router.get("/runs/{run_id}/usage")
async def run_usage(run_id: str):
    from agents.writing.technique_usage import (
        project_replacement_writing_technique_usage,
    )
    from application.writing_technique_runs import WritingTechniqueRuns

    db = get_db()
    replacement = await project_replacement_writing_technique_usage(
        db, run_id
    )
    if replacement is not None:
        return {"success": True, "data": replacement}
    return await _call(WritingTechniqueRuns(db).usage(run_id))


@router.post("/analyses/{analysis_id}/save")
async def save_analysis_technique(analysis_id: str, body: TechniqueRequest):
    async def save():
        import json
        db = get_db()
        row = await db.fetch_one("SELECT source_revision_id,schema_version,summary_json FROM novel_source_analyses WHERE id=?", [analysis_id])
        if not row or row["schema_version"] != 3:
            raise TechniqueError("invalid_reference", "请选择新版本的已保存分析")
        result = json.loads(row["summary_json"]).get("techniqueResult") or {}
        if result.get("status") != "generated":
            raise TechniqueError("invalid_reference", "这次分析没有可保存的写作技法")
        candidate = result["candidate"]
        service = _service()
        draft = await asyncio.to_thread(service.techniques.get_draft, candidate["techniqueId"], candidate["draftId"])
        ref = {"kind": "technique", "id": candidate["techniqueId"], "versionId": candidate["versionId"]}
        if draft.get("sealedRef") != ref or draft["owner"].get("sourceRevisionId") != row["source_revision_id"]:
            raise TechniqueError("invalid_reference", "来源分析与技法版本不一致")
        return await service.create_draft(operation_id=body.operationId, from_version=ref,
            owner={"analysisId": analysis_id, "sourceRevisionId": row["source_revision_id"], "sourceTechnique": ref})
    return await _call(save())


@router.get("/analyses/{analysis_id}/library")
async def analysis_library(analysis_id: str):
    async def lookup():
        service = _service()
        items = []
        for record in await service.list_objects("technique", include_archived=True):
            for draft_id in record["draftIds"]:
                draft = await asyncio.to_thread(service.techniques.get_draft, record["id"], draft_id)
                if draft.get("owner", {}).get("analysisId") == analysis_id:
                    items.append({"id": record["id"], "status": record["status"],
                                  "name": (record.get("metadata") or {}).get("name", "")})
                    break
        return items
    return await _call(lookup())


@router.post("/request-inputs")
async def reserve_input(body: ReserveTechniqueInputRequest):
    from application.writing_technique_access import WritingTechniqueAccess
    return await _call(WritingTechniqueAccess(get_db()).reserve_input(operation_id=body.operationId,
        book_id=body.bookId, session_id=body.sessionId, mode=body.mode, manual=[ref.model_dump() for ref in body.manual] if body.manual is not None else None))


@router.post("/uploads")
async def upload(body: UploadTechniqueRequest):
    from application.writing_technique_exchange import upload_technique
    return await _call(upload_technique(_service(), files=body.files, operation_id=body.operationId,
        book_id=body.bookId, session_id=body.sessionId))


@router.get("/books/{book_id}/grants")
async def grants(book_id: str):
    from application.writing_technique_access import WritingTechniqueAccess
    return await _call(WritingTechniqueAccess(get_db()).list_grants(book_id))


@router.post("/books/{book_id}/grants")
async def grant(book_id: str, body: VersionRef):
    from application.writing_technique_access import WritingTechniqueAccess
    return await _call(WritingTechniqueAccess(get_db()).grant(book_id, body.model_dump()))


@router.delete("/books/{book_id}/grants/{grant_id}")
async def revoke(book_id: str, grant_id: str):
    from application.writing_technique_access import WritingTechniqueAccess
    return await _call(WritingTechniqueAccess(get_db()).revoke(book_id, grant_id))


async def _call(awaitable):
    try:
        return {"success": True, "data": await awaitable}
    except TechniqueError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": str(exc), "code": exc.code})


@router.get("/objects/{kind}")
async def list_objects(kind: Kind, includeArchived: bool = False):
    return await _call(_service().list_objects(kind, include_archived=includeArchived))


@router.get("/objects/{kind}/{object_id}")
async def get_object(kind: Kind, object_id: str):
    return await _call(_service().get_object(kind, object_id))


@router.post("/drafts")
async def create_draft(body: CreateTechniqueDraftRequest):
    return await _call(_service().create_draft(operation_id=body.operationId, technique_id=body.techniqueId,
                      from_version=body.fromVersion.model_dump() if body.fromVersion else None))


@router.get("/{object_id}/drafts/{draft_id}/file")
async def read_draft_file(object_id: str, draft_id: str, revision: int = Query(ge=0), path: str = "SKILL.md"):
    return await _call(_service().read_draft_file(object_id, draft_id, revision, path))


@router.put("/{object_id}/drafts/{draft_id}")
async def apply_changes(object_id: str, draft_id: str, body: ApplyTechniqueChangesRequest):
    return await _call(_service().apply_changes(object_id, draft_id, expected_revision=body.expectedDraftRevision,
                      operation_id=body.operationId, changes=[c.model_dump(exclude_none=True) for c in body.changes]))


@router.post("/scheme-drafts")
async def create_scheme(body: CreateSchemeDraftRequest):
    return await _call(_service().create_draft(kind="scheme", operation_id=body.operationId,
                      scheme_id=body.schemeId, content=body.content.model_dump()))


@router.put("/schemes/{object_id}/drafts/{draft_id}")
async def update_scheme(object_id: str, draft_id: str, body: UpdateSchemeDraftRequest):
    return await _call(_service().update_scheme(object_id, draft_id, expected_revision=body.expectedDraftRevision,
                      operation_id=body.operationId, content=body.content.model_dump()))


@router.post("/objects/{kind}/{object_id}/drafts/{draft_id}/seal")
async def seal(kind: Kind, object_id: str, draft_id: str, body: SealTechniqueRequest):
    return await _call(_service().seal(kind, object_id, draft_id, expected_revision=body.expectedDraftRevision,
                      expected_tree_digest=body.expectedTreeDigest, operation_id=body.operationId))


@router.post("/objects/{kind}/{object_id}/publish")
async def publish(kind: Kind, object_id: str, body: PublishTechniqueRequest):
    return await _call(_service().publish(kind, object_id, ref=body.ref.model_dump(),
                      expected_published_head=body.expectedPublishedHead, operation_id=body.operationId))


@router.put("/objects/{kind}/{object_id}/status")
async def status(kind: Kind, object_id: str, body: TechniqueStatusRequest):
    return await _call(_service().set_status(kind, object_id, body.status, operation_id=body.operationId))


@router.get("/{object_id}/versions/{version_id}/file")
async def version_file(object_id: str, version_id: str, path: str = "SKILL.md"):
    return await _call(_service().read_version_file({"kind": "technique", "id": object_id, "versionId": version_id}, path))


@router.get("/{object_id}/versions/{version_id}/manifest")
async def version_manifest(object_id: str, version_id: str):
    return await _call(asyncio.to_thread(_service().techniques.get_version_manifest,
        {"kind": "technique", "id": object_id, "versionId": version_id}))


@router.get("/modes/{scope_kind}/{scope_id}")
async def mode(scope_kind: Literal["book", "session"], scope_id: str):
    return await _call(_service().get_mode(scope_kind, scope_id))


@router.put("/modes/{scope_kind}/{scope_id}")
async def set_mode(scope_kind: Literal["book", "session"], scope_id: str, body: TechniqueModeRequest):
    return await _call(_service().set_mode(scope_kind, scope_id, body.mode))


@router.post("/import-preview")
async def import_preview(request: Request, filename: str):
    from application.writing_technique_exchange import preview_technique_upload

    async def preview():
        service = _service()
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > service.techniques.limits.package_bytes * 2:
                raise TechniqueError("file_exceeds_budget", "上传内容超过限制")
            chunks.append(chunk)
        return await asyncio.to_thread(preview_technique_upload, b"".join(chunks), filename, service.techniques.limits)

    return await _call(preview())


@router.post("/import")
async def import_files(body: ImportTechniqueRequest):
    from application.writing_technique_exchange import import_technique
    return await _call(import_technique(_service(), files=body.files, operation_id=body.operationId, technique_id=body.techniqueId))


@router.get("/{object_id}/versions/{version_id}/export")
async def export_files(object_id: str, version_id: str, format: Literal["zip", "markdown"] = "zip"):
    from application.writing_technique_exchange import export_technique
    service = _service()
    ref = {"kind": "technique", "id": object_id, "versionId": version_id}
    try:
        if format == "markdown":
            manifest = await asyncio.to_thread(service.techniques.get_version_manifest, ref)
            if len(manifest["files"]) != 1:
                raise TechniqueError("invalid_reference", "多文件技法请导出 ZIP")
            content = (await service.read_version_file(ref, "SKILL.md"))["content"].encode("utf-8")
        else:
            content = await asyncio.to_thread(export_technique, service.techniques, ref)
        return Response(content, media_type="application/zip" if format == "zip" else "text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="technique.{"zip" if format == "zip" else "md"}"'})
    except TechniqueError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": str(exc), "code": exc.code})


@router.post('/scheme-import-preview')
async def scheme_import_preview(request: Request):
    from application.writing_technique_exchange import preview_scheme_bundle, SCHEME_BUNDLE_BYTES
    async def preview():
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > SCHEME_BUNDLE_BYTES:
                raise TechniqueError('file_exceeds_budget', '方案导入内容超过限制')
            chunks.append(chunk)
        return await asyncio.to_thread(preview_scheme_bundle, b''.join(chunks), _service().techniques.limits)
    return await _call(preview())


from schemas.writing_techniques import ImportSchemeRequest


@router.post('/scheme-import')
async def scheme_import(body: ImportSchemeRequest):
    from application.writing_technique_exchange import import_scheme_bundle
    return await _call(import_scheme_bundle(_service(), bundle=body.bundle, operation_id=body.operationId))


@router.get('/schemes/{object_id}/versions/{version_id}')
async def scheme_version(object_id: str, version_id: str):
    return await _call(asyncio.to_thread(_service().schemes.read_scheme,
        {'kind': 'scheme', 'id': object_id, 'versionId': version_id}, verify_members=False))


@router.get('/schemes/{object_id}/versions/{version_id}/export')
async def scheme_export(object_id: str, version_id: str):
    from application.writing_technique_exchange import export_scheme_bundle
    try:
        raw = await asyncio.to_thread(export_scheme_bundle, _service(), {'kind': 'scheme', 'id': object_id, 'versionId': version_id})
        return Response(raw, media_type='application/json', headers={'Content-Disposition': 'attachment; filename="writing-scheme.json"'})
    except TechniqueError as exc:
        return JSONResponse(status_code=exc.status_code, content={'success': False, 'error': str(exc), 'code': exc.code})


@router.get('/objects/{kind}/{object_id}/deletion-preview')
async def deletion_preview(kind: Kind, object_id: str):
    return await _call(_service().deletion_preview(kind, object_id))


@router.post('/objects/{kind}/{object_id}/delete')
async def delete_object(kind: Kind, object_id: str, body: DeleteTechniqueRequest):
    return await _call(_service().delete_object(kind, object_id, operation_id=body.operationId, revision_token=body.revisionToken))


from schemas.writing_techniques import TechniqueSelectionRequest


@router.get('/selections/{scope_kind}/{scope_id}')
async def get_selection(scope_kind: Literal['book', 'session'], scope_id: str):
    return await _call(_service().get_selection(scope_kind, scope_id))


@router.put('/selections/{scope_kind}/{scope_id}')
async def set_selection(scope_kind: Literal['book', 'session'], scope_id: str, body: TechniqueSelectionRequest):
    return await _call(_service().set_selection(scope_kind, scope_id, [ref.model_dump() for ref in body.refs]))


@router.get('/analyses/{analysis_id}/results')
async def analysis_results(analysis_id: str):
    from application.source_analysis_techniques import results
    return await _call(results(get_db(), analysis_id))
