"""HTTP transport for the screenplay-owned Conversation application."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, Query, Request
from sse_starlette.sse import EventSourceResponse

from application.screenplay_conversation_service import (
    ScreenplayConversationService,
)
from dependencies import get_db
from schemas.screenplay_conversation import (
    ResumeScreenplayConversationTurnRequest,
    SubmitScreenplayConversationTurnRequest,
)


router = APIRouter()


def _service() -> ScreenplayConversationService:
    from application.agent_composition import get_agent_composition

    return ScreenplayConversationService(get_db(), get_agent_composition())


@router.post("/projects/{project_id}/conversation/turns", status_code=202)
async def submit_screenplay_conversation_turn(
    project_id: str,
    body: SubmitScreenplayConversationTurnRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    service = _service()
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
    snapshot = await _service().get_snapshot(
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
    service = _service()
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
                    "data": json.dumps(event, ensure_ascii=False),
                }
            if page["hasMore"]:
                continue
            await asyncio.sleep(0.2)

    return EventSourceResponse(events())


@router.post("/conversation/turns/{turn_id}/cancel")
async def cancel_screenplay_conversation_turn(
    turn_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    turn = await _service().cancel_turn(
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
    turn = await _service().resume_turn(
        turn_id,
        command_id=idempotency_key,
        runtime=body.runtime,
    )
    return {"success": True, "data": turn}


__all__ = ["router"]
