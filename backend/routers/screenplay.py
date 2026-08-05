"""CRUD routes for screenplay projects and their versioned documents."""

from __future__ import annotations

from fastapi import APIRouter, Query, Response

from database.crud import screenplay as screenplay_crud
from database.crud import screenplay_source_refs as source_refs_crud
from dependencies import get_db
from exceptions import AppError, NotFoundError
from schemas.screenplay import (
    CreateScreenplayDocumentRequest,
    CreateScreenplayProjectRequest,
    ScreenplayDocumentKind,
    ScreenplayDocumentStatus,
    UpdateScreenplayDocumentRequest,
    UpdateScreenplayProjectRequest,
)

router = APIRouter(tags=["screenplay"])


@router.get("/screenplay-projects")
async def list_screenplay_projects(includeArchived: bool = False):
    data = await screenplay_crud.list_projects(
        get_db(),
        include_archived=includeArchived,
    )
    return {"success": True, "data": data}


@router.post("/screenplay-projects")
async def create_screenplay_project(body: CreateScreenplayProjectRequest):
    data = await screenplay_crud.create_project(
        get_db(),
        title=body.title,
        source_kind=body.sourceKind,
        source_book_id=body.sourceBookId,
        screenplay_format=body.format,
        approach=body.approach,
        premise=body.premise,
        source_scope=(
            body.sourceScope.model_dump()
            if body.sourceScope is not None
            else None
        ),
    )
    return {"success": True, "data": data}


@router.get("/screenplay-projects/{project_id}")
async def get_screenplay_project(project_id: str):
    data = await screenplay_crud.get_project(get_db(), project_id)
    if data is None:
        raise NotFoundError("剧本项目不存在")
    return {"success": True, "data": data}


@router.post("/screenplay-projects/{project_id}/agent-session")
async def get_or_create_screenplay_agent_session(project_id: str):
    data = await screenplay_crud.get_or_create_agent_session(get_db(), project_id)
    return {"success": True, "data": data}


@router.get("/screenplay-projects/{project_id}/agent-sessions")
async def list_screenplay_agent_sessions(
    project_id: str,
    includeClosed: bool = False,
):
    data = await screenplay_crud.list_agent_sessions(
        get_db(),
        project_id,
        include_closed=includeClosed,
    )
    return {"success": True, "data": data}


@router.post("/screenplay-projects/{project_id}/agent-sessions")
async def create_screenplay_agent_session(project_id: str):
    data = await screenplay_crud.create_agent_session(get_db(), project_id)
    return {"success": True, "data": data}


@router.put("/screenplay-projects/{project_id}")
async def update_screenplay_project(
    project_id: str,
    body: UpdateScreenplayProjectRequest,
):
    data = await screenplay_crud.update_project(
        get_db(),
        project_id,
        body.model_dump(exclude_unset=True),
    )
    return {"success": True, "data": data}


@router.delete("/screenplay-projects/{project_id}")
async def delete_screenplay_project(project_id: str):
    deleted = await screenplay_crud.delete_project(get_db(), project_id)
    if not deleted:
        raise NotFoundError("剧本项目不存在")
    return {"success": True}


@router.get("/screenplay-projects/{project_id}/documents")
async def list_screenplay_documents(
    project_id: str,
    kind: ScreenplayDocumentKind | None = None,
    status: ScreenplayDocumentStatus | None = None,
):
    data = await screenplay_crud.list_documents(
        get_db(),
        project_id,
        kind=kind,
        status=status,
    )
    return {"success": True, "data": data}


@router.post("/screenplay-projects/{project_id}/documents")
async def create_screenplay_document(
    project_id: str,
    body: CreateScreenplayDocumentRequest,
):
    db = get_db()
    if body.sourceRunId:
        await screenplay_crud.require_agent_document_proposal(
            db,
            project_id=project_id,
            source_run_id=body.sourceRunId,
            kind=str(body.kind),
            title=body.title,
            content_json=body.contentJson,
            content_text=body.contentText,
            derived_from_ids=body.derivedFromIds,
        )
    data = await screenplay_crud.create_document(
        db,
        project_id=project_id,
        kind=body.kind,
        title=body.title,
        content_json=body.contentJson,
        content_text=body.contentText,
        derived_from_ids=body.derivedFromIds,
        source_run_id=body.sourceRunId,
    )
    return {"success": True, "data": data}


@router.get("/screenplay-projects/{project_id}/source-refs")
async def list_screenplay_source_refs(
    project_id: str,
    document_id: str | None = Query(default=None, alias="documentId"),
    agent_run_id: str | None = Query(default=None, alias="agentRunId"),
):
    data = await source_refs_crud.list_source_refs(
        get_db(),
        project_id=project_id,
        document_id=document_id,
        agent_run_id=agent_run_id,
    )
    return {"success": True, "data": data}


@router.post("/screenplay-projects/{project_id}/export/pdf")
async def export_screenplay_pdf(project_id: str):
    db = get_db()
    project = await screenplay_crud.get_project(db, project_id)
    if project is None:
        raise NotFoundError("剧本项目不存在")
    draft = await db.fetch_one(
        "SELECT content_text FROM screenplay_documents "
        "WHERE project_id = ? AND kind = 'scene_draft' "
        "AND status = 'accepted' ORDER BY version DESC LIMIT 1",
        [project_id],
    )
    if draft is None or not str(draft.get("content_text") or "").strip():
        raise AppError("没有可导出的已接受剧本正文", 409)
    from services.screenplay_pdf import build_screenplay_pdf

    data = build_screenplay_pdf(
        title=str(project.get("title") or "未命名剧本"),
        screenplay_format=str(project.get("format") or "剧本"),
        content=str(draft["content_text"]),
    )
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="screenplay.pdf"'},
    )


@router.get("/screenplay-documents/{document_id}")
async def get_screenplay_document(document_id: str):
    data = await screenplay_crud.get_document(get_db(), document_id)
    if data is None:
        raise NotFoundError("剧本文档不存在")
    return {"success": True, "data": data}


@router.put("/screenplay-documents/{document_id}")
async def update_screenplay_document(
    document_id: str,
    body: UpdateScreenplayDocumentRequest,
):
    data = await screenplay_crud.update_document(
        get_db(),
        document_id,
        body.model_dump(exclude_unset=True),
    )
    return {"success": True, "data": data}


@router.post("/screenplay-documents/{document_id}/accept")
async def accept_screenplay_document(document_id: str):
    data = await screenplay_crud.accept_document(get_db(), document_id)
    return {"success": True, "data": data}


@router.post("/screenplay-documents/{document_id}/restore")
async def restore_screenplay_document(document_id: str):
    data = await screenplay_crud.restore_document(get_db(), document_id)
    return {"success": True, "data": data}


@router.delete("/screenplay-documents/{document_id}")
async def delete_screenplay_document(document_id: str):
    deleted = await screenplay_crud.delete_document(get_db(), document_id)
    if not deleted:
        raise NotFoundError("剧本文档不存在")
    return {"success": True}
