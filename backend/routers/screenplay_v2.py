"""Public project and workspace routes for screenplay API v2."""

from __future__ import annotations

from fastapi import APIRouter, Header
from fastapi.responses import Response

from agents.screenplay.project_service import ScreenplayV2ProjectService
from dependencies import get_db
from routers.screenplay_conversations import router as conversation_router
from schemas.screenplay_v2 import (
    AdjudicateScreenplayV2ReviewRequest,
    AcceptScreenplayV2RevisionRequest,
    ChangeScreenplayV2ProjectLifecycleRequest,
    CreateScreenplayV2ProjectRequest,
    CreateScreenplayV2WorkingCopyFromRevisionRequest,
    DeleteScreenplayV2ProjectRequest,
    FinalizeScreenplayV2ProjectRequest,
    PublishScreenplayV2WorkingCopyRequest,
    UpdateScreenplayV2ProjectRequest,
    UpdateScreenplayV2WorkingCopyRequest,
)
router = APIRouter(prefix="/screenplay/v2", tags=["screenplay-v2"])
router.include_router(conversation_router)


@router.get("/projects")
async def list_screenplay_v2_projects(includeArchived: bool = False):
    projects = await ScreenplayV2ProjectService(get_db()).list_projects(
        include_archived=includeArchived,
    )
    return {"success": True, "data": projects}


@router.post("/projects", status_code=201)
async def create_screenplay_v2_project(
    body: CreateScreenplayV2ProjectRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    workspace = await ScreenplayV2ProjectService(get_db()).create_project(
        command_id=idempotency_key,
        request=body,
    )
    return {"success": True, "data": workspace}


@router.put("/projects/{project_id}/agent-sessions/current")
async def ensure_current_screenplay_v2_session(project_id: str):
    session = await ScreenplayV2ProjectService(get_db()).ensure_current_session(
        project_id
    )
    return {"success": True, "data": session}


@router.get("/projects/{project_id}/agent-sessions")
async def list_screenplay_v2_sessions(
    project_id: str,
    includeClosed: bool = False,
):
    sessions = await ScreenplayV2ProjectService(get_db()).list_sessions(
        project_id,
        include_closed=includeClosed,
    )
    return {"success": True, "data": sessions}


@router.post("/projects/{project_id}/agent-sessions", status_code=201)
async def create_screenplay_v2_session(
    project_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    session = await ScreenplayV2ProjectService(get_db()).create_session(
        command_id=idempotency_key,
        project_id=project_id,
    )
    return {"success": True, "data": session}


@router.get("/projects/{project_id}/workspace")
async def get_screenplay_v2_workspace(project_id: str):
    workspace = await ScreenplayV2ProjectService(get_db()).get_workspace(
        project_id
    )
    return {"success": True, "data": workspace}


@router.post("/projects/{project_id}/export/pdf", response_class=Response)
async def export_screenplay_v2_pdf(project_id: str):
    content = await ScreenplayV2ProjectService(get_db()).export_pdf(project_id)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="screenplay.pdf"'},
    )


@router.patch("/projects/{project_id}")
async def update_screenplay_v2_project(
    project_id: str,
    body: UpdateScreenplayV2ProjectRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    workspace = await ScreenplayV2ProjectService(get_db()).update_project(
        command_id=idempotency_key,
        project_id=project_id,
        request=body,
    )
    return {"success": True, "data": workspace}


async def _set_screenplay_v2_project_lifecycle(
    *,
    project_id: str,
    lifecycle: str,
    body: ChangeScreenplayV2ProjectLifecycleRequest,
    idempotency_key: str,
):
    workspace = (
        await ScreenplayV2ProjectService(get_db()).set_project_lifecycle(
            command_id=idempotency_key,
            project_id=project_id,
            lifecycle=lifecycle,
            request=body,
        )
    )
    return {"success": True, "data": workspace}


@router.post("/projects/{project_id}/archive")
async def archive_screenplay_v2_project(
    project_id: str,
    body: ChangeScreenplayV2ProjectLifecycleRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    return await _set_screenplay_v2_project_lifecycle(
        project_id=project_id,
        lifecycle="archived",
        body=body,
        idempotency_key=idempotency_key,
    )


@router.post("/projects/{project_id}/restore")
async def restore_screenplay_v2_project(
    project_id: str,
    body: ChangeScreenplayV2ProjectLifecycleRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    return await _set_screenplay_v2_project_lifecycle(
        project_id=project_id,
        lifecycle="active",
        body=body,
        idempotency_key=idempotency_key,
    )


@router.post("/projects/{project_id}/delete")
async def delete_screenplay_v2_project(
    project_id: str,
    body: DeleteScreenplayV2ProjectRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    result = await ScreenplayV2ProjectService(get_db()).delete_project(
        command_id=idempotency_key,
        project_id=project_id,
        request=body,
    )
    return {"success": True, "data": result}


@router.get("/revisions/{revision_id}")
async def get_screenplay_v2_revision(
    revision_id: str,
    view: str = "full",
):
    revision = await ScreenplayV2ProjectService(get_db()).get_revision(
        revision_id,
        view=view,
    )
    return {"success": True, "data": revision}


@router.get("/projects/{project_id}/deliverables/{role}/revisions")
async def list_screenplay_v2_revision_history(
    project_id: str,
    role: str,
    cursor: str | None = None,
    limit: int = 50,
):
    history = await ScreenplayV2ProjectService(get_db()).list_revision_history(
        project_id=project_id,
        role=role,
        cursor=cursor,
        limit=limit,
    )
    return {"success": True, "data": history}


@router.get(
    "/projects/{project_id}/draft-revisions/{draft_revision_id}/latest-review"
)
async def get_latest_screenplay_v2_review_for_draft(
    project_id: str,
    draft_revision_id: str,
):
    review = await ScreenplayV2ProjectService(
        get_db()
    ).get_latest_review_for_draft(
        project_id=project_id,
        draft_revision_id=draft_revision_id,
    )
    return {"success": True, "data": review}


@router.patch("/working-copies/{working_copy_id}")
async def update_screenplay_v2_working_copy(
    working_copy_id: str,
    body: UpdateScreenplayV2WorkingCopyRequest,
):
    working_copy = await ScreenplayV2ProjectService(get_db()).update_working_copy(
        working_copy_id=working_copy_id,
        request=body,
    )
    return {"success": True, "data": working_copy}


@router.post(
    "/projects/{project_id}/revisions/{revision_id}/working-copy"
)
async def create_screenplay_v2_working_copy_from_revision(
    project_id: str,
    revision_id: str,
    body: CreateScreenplayV2WorkingCopyFromRevisionRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    working_copy = (
        await ScreenplayV2ProjectService(get_db()).create_working_copy_from_revision(
            command_id=idempotency_key,
            project_id=project_id,
            revision_id=revision_id,
            request=body,
        )
    )
    return {"success": True, "data": working_copy}


@router.post("/working-copies/{working_copy_id}/publish")
async def publish_screenplay_v2_working_copy(
    working_copy_id: str,
    body: PublishScreenplayV2WorkingCopyRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    result = await ScreenplayV2ProjectService(get_db()).publish_working_copy(
        command_id=idempotency_key,
        working_copy_id=working_copy_id,
        request=body,
    )
    return {"success": True, "data": result}


@router.post("/projects/{project_id}/revisions/{revision_id}/accept")
async def accept_screenplay_v2_revision(
    project_id: str,
    revision_id: str,
    body: AcceptScreenplayV2RevisionRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    result = await ScreenplayV2ProjectService(get_db()).accept_revision(
        command_id=idempotency_key,
        project_id=project_id,
        revision_id=revision_id,
        request=body,
    )
    return {"success": True, "data": result}


@router.post("/projects/{project_id}/review-decisions")
async def adjudicate_screenplay_v2_review(
    project_id: str,
    body: AdjudicateScreenplayV2ReviewRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    workspace = await ScreenplayV2ProjectService(get_db()).adjudicate_review(
        command_id=idempotency_key,
        project_id=project_id,
        request=body,
    )
    return {"success": True, "data": workspace}


@router.post("/projects/{project_id}/finalize")
async def finalize_screenplay_v2_project(
    project_id: str,
    body: FinalizeScreenplayV2ProjectRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    workspace = await ScreenplayV2ProjectService(get_db()).finalize_project(
        command_id=idempotency_key,
        project_id=project_id,
        request=body,
    )
    return {"success": True, "data": workspace}


__all__ = [
    "adjudicate_screenplay_v2_review",
    "accept_screenplay_v2_revision",
    "archive_screenplay_v2_project",
    "create_screenplay_v2_project",
    "create_screenplay_v2_session",
    "create_screenplay_v2_working_copy_from_revision",
    "delete_screenplay_v2_project",
    "ensure_current_screenplay_v2_session",
    "export_screenplay_v2_pdf",
    "finalize_screenplay_v2_project",
    "get_latest_screenplay_v2_review_for_draft",
    "get_screenplay_v2_revision",
    "get_screenplay_v2_workspace",
    "list_screenplay_v2_projects",
    "list_screenplay_v2_sessions",
    "list_screenplay_v2_revision_history",
    "publish_screenplay_v2_working_copy",
    "restore_screenplay_v2_project",
    "router",
    "update_screenplay_v2_project",
    "update_screenplay_v2_working_copy",
]
