"""Agent-skill library API: view, install, archive and delete.

技能与写作技法是两个概念：不参与按书授权、手动/自动模式与检索选择；
是否允许 Agent 自动使用由包内 metadata.autoUse 声明。内置技能只读，
已安装技能可归档、删除或重装升级。
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from application.writing_technique_service import WritingTechniqueService
from dependencies import get_db
from domains.writing.techniques import TechniqueError
from schemas.skills import DeleteSkillRequest, ImportSkillRequest, SkillStatusRequest

router = APIRouter(prefix="/skills", tags=["skills"])


def _service():
    return WritingTechniqueService(get_db())


async def _call(awaitable):
    try:
        return {"success": True, "data": await awaitable}
    except TechniqueError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": str(exc), "code": exc.code})


@router.get("/objects")
async def list_objects(includeArchived: bool = False):
    return await _call(_service().list_objects("skill", include_archived=includeArchived))


@router.get("/objects/{object_id}")
async def get_object(object_id: str):
    return await _call(_service().get_object("skill", object_id))


@router.put("/objects/{object_id}/status")
async def status(object_id: str, body: SkillStatusRequest):
    return await _call(_service().set_status("skill", object_id, body.status, operation_id=body.operationId))


@router.get("/objects/{object_id}/deletion-preview")
async def deletion_preview(object_id: str):
    return await _call(_service().deletion_preview("skill", object_id))


@router.post("/objects/{object_id}/delete")
async def delete_object(object_id: str, body: DeleteSkillRequest):
    return await _call(_service().delete_object("skill", object_id,
                      operation_id=body.operationId, revision_token=body.revisionToken))


@router.get("/{object_id}/versions/{version_id}/file")
async def version_file(object_id: str, version_id: str, path: str = "SKILL.md"):
    return await _call(_service().read_version_file(
        {"kind": "skill", "id": object_id, "versionId": version_id}, path))


@router.get("/{object_id}/versions/{version_id}/manifest")
async def version_manifest(object_id: str, version_id: str):
    return await _call(asyncio.to_thread(_service().store("skill").get_version_manifest,
        {"kind": "skill", "id": object_id, "versionId": version_id}))


@router.post("/import-preview")
async def import_preview(request: Request, filename: str):
    from application.writing_technique_exchange import preview_technique_upload

    async def preview():
        service = _service()
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > service.store("skill").limits.package_bytes * 2:
                raise TechniqueError("file_exceeds_budget", "上传内容超过限制")
            chunks.append(chunk)
        return await asyncio.to_thread(
            preview_technique_upload, b"".join(chunks), filename,
            service.store("skill").limits)

    return await _call(preview())


@router.post("/import")
async def import_files(body: ImportSkillRequest):
    """安装技能：校验、封存并发布，返回安装结果（含是否允许自动使用）。"""

    async def install():
        from application.writing_technique_exchange import import_skill

        service = _service()
        draft = await import_skill(service, files=body.files, operation_id=body.operationId)
        sealed = await service.seal("skill", draft["techniqueId"], draft["draftId"],
            expected_revision=draft["draftRevision"], expected_tree_digest=draft["treeDigest"],
            operation_id=body.operationId + ":seal")
        published = await service.publish("skill", draft["techniqueId"], ref=sealed["sealedRef"],
            expected_published_head=None, operation_id=body.operationId + ":publish")
        metadata = published.get("metadata") or {}
        return {
            "skillId": draft["techniqueId"],
            "ref": sealed["sealedRef"],
            "metadata": metadata,
            "autoUse": metadata.get("autoUse") is True,
        }

    return await _call(install())


@router.get("/{object_id}/versions/{version_id}/export")
async def export_files(object_id: str, version_id: str, format: Literal["zip", "markdown"] = "zip"):
    from application.writing_technique_exchange import export_technique

    service = _service()
    ref = {"kind": "skill", "id": object_id, "versionId": version_id}
    try:
        if format == "markdown":
            manifest = await asyncio.to_thread(service.store("skill").get_version_manifest, ref)
            if len(manifest["files"]) != 1:
                raise TechniqueError("invalid_reference", "多文件技能请导出 ZIP")
            content = (await service.read_version_file(ref, "SKILL.md"))["content"].encode("utf-8")
        else:
            content = await asyncio.to_thread(export_technique, service.store("skill"), ref)
        return Response(content, media_type="application/zip" if format == "zip" else "text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="skill.{"zip" if format == "zip" else "md"}"'})
    except TechniqueError as exc:
        return JSONResponse(status_code=exc.status_code, content={"success": False, "error": str(exc), "code": exc.code})
