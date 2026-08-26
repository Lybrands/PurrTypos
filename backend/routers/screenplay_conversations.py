"""HTTP transport for the rewritten screenplay Agent."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, Query, Request
from sse_starlette.sse import EventSourceResponse

from application.screenplay_agent_task_executor import (
    ScreenplayTaskUnitExecutor,
)
from application.screenplay_agent_service import ScreenplayAgentService
from application.screenplay_agent_stream import ScreenplayCanonicalOutputQuery
from application.screenplay_v2_service import ScreenplayV2ProjectService
from dependencies import get_db
from schemas.screenplay_agent import (
    ResumeScreenplayOperationRequest,
    SubmitScreenplayAgentTurnRequest,
)


router = APIRouter()
_STREAM_POLL_SECONDS = 0.04


def _chunk_delivery_pages(page):
    """Deliver committed canonical events without host-side buffering."""

    pages = tuple({
        "kind": "agent_chunks",
        "chunks": [item],
        "nextCursor": int(item["cursor"]),
        "hasMore": bool(
            page["hasMore"] or index < len(page["chunks"]) - 1
        ),
    } for index, item in enumerate(page["chunks"]))
    if pages or int(page["nextCursor"]) <= 0:
        return pages
    return ({
        "kind": "agent_chunks",
        "chunks": [],
        "nextCursor": int(page["nextCursor"]),
        "hasMore": bool(page["hasMore"]),
    },)


def _chunk_replay_page(page):
    """Hydrate persisted history in one UI-neutral batch per database page."""

    return {
        "kind": "agent_chunks",
        "chunks": list(page["chunks"]),
        "nextCursor": int(page["nextCursor"]),
        "hasMore": bool(page["hasMore"]),
    }


def _service() -> ScreenplayAgentService:
    from application.agent_composition import get_agent_composition

    db = get_db()
    composition = get_agent_composition()
    return ScreenplayAgentService(
        db,
        owner_id=composition.execution_owner_id,
        composition=composition,
        unit_executor_factory=lambda runtime: ScreenplayTaskUnitExecutor(
            db,
            runtime=runtime,
            composition=composition,
        ),
        projects=ScreenplayV2ProjectService(db),
        output_processor=composition.output_processor,
        track_background=composition.track_background_run,
    )


@router.post("/projects/{project_id}/conversation/turns", status_code=202)
async def submit_screenplay_conversation_turn(
    project_id: str,
    body: SubmitScreenplayAgentTurnRequest,
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
    chunk_after: int = Query(default=0, alias="chunkAfter", ge=0),
    limit: int = Query(default=100, ge=1, le=500),
):
    from application.agent_composition import get_agent_composition

    chunks = ScreenplayCanonicalOutputQuery(
        get_db(),
        output_repository=get_agent_composition().output_journal,
    )
    async def events():
        chunk_cursor = int(chunk_after)
        chunk_replay_announced = False
        while not await request.is_disconnected():
            emitted = False
            chunk_page = await chunks.list_chunks(
                project_id=project_id,
                session_id=session_id,
                after=chunk_cursor,
                limit=limit,
            )
            delivered_chunk_page = False
            if chunk_page["nextCursor"] > chunk_cursor:
                delivery_pages = (
                    (_chunk_replay_page(chunk_page),)
                    if not chunk_replay_announced
                    else _chunk_delivery_pages(chunk_page)
                )
                for delivery_page in delivery_pages:
                    chunk_cursor = int(delivery_page["nextCursor"])
                    yield {
                        "data": json.dumps(
                            delivery_page,
                            ensure_ascii=False,
                        ),
                    }
                    emitted = True
                    delivered_chunk_page = True
            if not chunk_replay_announced and not chunk_page["hasMore"]:
                # The client keeps the restored conversation hidden until the
                # persisted Agent chunk backlog is fully replayed.  Sessions
                # without chunks still need an explicit catch-up marker;
                # otherwise their loading state could never settle.
                if not delivered_chunk_page:
                    yield {
                        "data": json.dumps({
                            "kind": "agent_chunks",
                            "chunks": [],
                            "nextCursor": chunk_cursor,
                            "hasMore": False,
                        }, ensure_ascii=False),
                    }
                    emitted = True
                chunk_replay_announced = True
            if chunk_page["hasMore"]:
                continue
            if not emitted:
                await asyncio.sleep(_STREAM_POLL_SECONDS)

    return EventSourceResponse(events())


@router.post("/conversation/turns/{turn_id}/cancel")
async def cancel_screenplay_conversation_turn(
    turn_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    receipt = await _service().cancel_turn(
        turn_id,
        idempotency_key=idempotency_key,
    )
    return {"success": True, "data": receipt}


@router.post("/conversation/operations/{operation_id}/resume", status_code=202)
async def resume_screenplay_operation(
    operation_id: str,
    body: ResumeScreenplayOperationRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    service = _service()
    receipt = await service.prepare_resume(
        operation_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    if receipt.get("status") == "running" and receipt.get(
        "dispatchRequired",
        True,
    ):
        service.dispatch_resumed_operation(
            operation_id,
            body.runtime,
            continuation_command=idempotency_key,
        )
    return {"success": True, "data": receipt}


@router.delete("/conversation/turns/{turn_id}/and-after")
async def truncate_screenplay_conversation_from_turn(turn_id: str):
    result = await _service().truncate_from_turn(turn_id)
    return {"success": True, "data": result}


__all__ = ["router"]
