"""Public project and workspace routes for screenplay API v2."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, Query, Request
from sse_starlette.sse import EventSourceResponse

from application.screenplay_conversation_service import (
    ScreenplayConversationService,
)
from application.screenplay_v2_service import ScreenplayV2ProjectService
from dependencies import get_db
from schemas.screenplay_v2 import (
    AcceptScreenplayV2RevisionRequest,
    ChangeScreenplayV2ProjectLifecycleRequest,
    CreateScreenplayV2ProjectRequest,
    CreateScreenplayV2WorkingCopyFromRevisionRequest,
    DeleteScreenplayV2ProjectRequest,
    PublishScreenplayV2WorkingCopyRequest,
    StartScreenplayV2OperationRequest,
    UpdateScreenplayV2ProjectRequest,
    UpdateScreenplayV2WorkingCopyRequest,
)
from schemas.screenplay_conversation import (
    ResumeScreenplayConversationTurnRequest,
    SubmitScreenplayConversationTurnRequest,
)


router = APIRouter(prefix="/screenplay/v2", tags=["screenplay-v2"])


def _conversation_service() -> ScreenplayConversationService:
    from application.agent_composition import get_agent_composition

    return ScreenplayConversationService(get_db(), get_agent_composition())


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


@router.post("/projects/{project_id}/conversation/turns", status_code=202)
async def submit_screenplay_conversation_turn(
    project_id: str,
    body: SubmitScreenplayConversationTurnRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    service = _conversation_service()
    turn = await service.submit_turn(
        command_id=idempotency_key,
        project_id=project_id,
        request=body,
    )
    service.dispatch_turn(str(turn["id"]), body.runtime)
    return {"success": True, "data": turn}


@router.get("/projects/{project_id}/conversation/snapshot")
async def get_screenplay_conversation_snapshot(
    project_id: str,
    session_id: int = Query(alias="sessionId", ge=1),
):
    snapshot = await _conversation_service().get_snapshot(
        project_id=project_id,
        session_id=session_id,
    )
    return {"success": True, "data": snapshot}


@router.get("/projects/{project_id}/conversation/events")
async def stream_screenplay_conversation_events(
    request: Request,
    project_id: str,
    session_id: int = Query(alias="sessionId", ge=1),
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    follow: bool = True,
):
    service = _conversation_service()
    if not follow:
        page = await service.list_events(
            project_id=project_id,
            session_id=session_id,
            after=after,
            limit=limit,
        )
        return {"success": True, "data": page}

    async def events():
        cursor = int(after)
        while not await request.is_disconnected():
            page = await service.list_events(
                project_id=project_id,
                session_id=session_id,
                after=cursor,
                limit=limit,
            )
            for event in page["events"]:
                cursor = int(event["cursor"])
                yield {
                    "id": str(cursor),
                    "event": str(event["type"]),
                    "data": json.dumps(event, ensure_ascii=False),
                }
            if page["hasMore"]:
                continue
            await asyncio.sleep(0.2)

    return EventSourceResponse(events(), media_type="text/event-stream")


@router.post("/conversation/turns/{turn_id}/cancel")
async def cancel_screenplay_conversation_turn(
    turn_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    turn = await _conversation_service().cancel_turn(
        turn_id,
        command_id=idempotency_key,
    )
    return {"success": True, "data": turn}


@router.post("/conversation/turns/{turn_id}/resume", status_code=202)
async def resume_screenplay_conversation_turn(
    turn_id: str,
    body: ResumeScreenplayConversationTurnRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    turn = await _conversation_service().resume_turn(
        turn_id,
        command_id=idempotency_key,
        runtime=body.runtime,
    )
    return {"success": True, "data": turn}


@router.get("/projects/{project_id}/workspace")
async def get_screenplay_v2_workspace(project_id: str):
    workspace = await ScreenplayV2ProjectService(get_db()).get_workspace(
        project_id
    )
    return {"success": True, "data": workspace}


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


@router.get("/operations/{operation_id}")
async def get_screenplay_v2_operation(operation_id: str):
    operation = await ScreenplayV2ProjectService(get_db()).get_operation(
        operation_id
    )
    return {"success": True, "data": operation}


@router.post("/projects/{project_id}/operations", status_code=202)
async def start_screenplay_v2_operation(
    project_id: str,
    body: StartScreenplayV2OperationRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    result = await ScreenplayV2ProjectService(get_db()).start_operation(
        command_id=idempotency_key,
        project_id=project_id,
        request=body,
    )
    return {"success": True, "data": result}


async def _control_screenplay_v2_operation(
    operation_id: str,
    action: str,
    idempotency_key: str,
):
    result = await ScreenplayV2ProjectService(get_db()).control_operation(
        command_id=idempotency_key,
        operation_id=operation_id,
        action=action,
    )
    return {"success": True, "data": result}


@router.post("/operations/{operation_id}/pause")
async def pause_screenplay_v2_operation(
    operation_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    return await _control_screenplay_v2_operation(
        operation_id,
        "pause",
        idempotency_key,
    )


@router.post("/operations/{operation_id}/resume")
async def resume_screenplay_v2_operation(
    operation_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    return await _control_screenplay_v2_operation(
        operation_id,
        "resume",
        idempotency_key,
    )


@router.post("/operations/{operation_id}/cancel")
async def cancel_screenplay_v2_operation(
    operation_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    return await _control_screenplay_v2_operation(
        operation_id,
        "cancel",
        idempotency_key,
    )


@router.get("/operations/{operation_id}/events")
async def list_screenplay_v2_operation_events(
    operation_id: str,
    after: int = 0,
    limit: int = 100,
):
    events = await ScreenplayV2ProjectService(get_db()).list_operation_events(
        operation_id,
        after=after,
        limit=limit,
    )
    return {"success": True, "data": events}


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


__all__ = [
    "accept_screenplay_v2_revision",
    "archive_screenplay_v2_project",
    "cancel_screenplay_v2_operation",
    "create_screenplay_v2_project",
    "create_screenplay_v2_session",
    "create_screenplay_v2_working_copy_from_revision",
    "delete_screenplay_v2_project",
    "ensure_current_screenplay_v2_session",
    "get_screenplay_v2_operation",
    "get_screenplay_v2_revision",
    "get_screenplay_v2_workspace",
    "list_screenplay_v2_projects",
    "list_screenplay_v2_sessions",
    "list_screenplay_v2_revision_history",
    "list_screenplay_v2_operation_events",
    "pause_screenplay_v2_operation",
    "publish_screenplay_v2_working_copy",
    "restore_screenplay_v2_project",
    "resume_screenplay_v2_operation",
    "router",
    "start_screenplay_v2_operation",
    "update_screenplay_v2_project",
    "update_screenplay_v2_working_copy",
]
