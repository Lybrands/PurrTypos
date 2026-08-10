"""HTTP transport for the rewritten screenplay Agent."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, Query, Request
from sse_starlette.sse import EventSourceResponse

from application.screenplay_agent_task_executor import (
    ScreenplayTaskUnitExecutor,
)
from application.screenplay_tool_calling import ScreenplayToolCallingService
from application.screenplay_agent_planner import (
    ModelScreenplayIntentPlanner,
    SqliteScreenplayTaskResolver,
)
from application.screenplay_agent_service import ScreenplayAgentService
from application.screenplay_agent_stream import ScreenplayAgentChunkStore
from application.screenplay_v2_service import ScreenplayV2ProjectService
from dependencies import get_db
from schemas.screenplay_agent import SubmitScreenplayAgentTurnRequest


router = APIRouter()
_DIAGNOSTIC_CHUNK_KEYS = frozenset({
    "model",
    "modelContentDelta",
    "reasoningDelta",
})
_STREAM_POLL_SECONDS = 0.04


def _chunk_delivery_pages(page):
    """Keep diagnostics batched while delivering public deltas individually."""

    batches = []
    diagnostics = []
    for item in page["chunks"]:
        chunk = item.get("chunk") or {}
        public = bool(set(chunk) - _DIAGNOSTIC_CHUNK_KEYS)
        if public:
            if diagnostics:
                batches.append(diagnostics)
                diagnostics = []
            batches.append([item])
        else:
            diagnostics.append(item)
    if diagnostics:
        batches.append(diagnostics)
    return tuple({
        "kind": "agent_chunks",
        "chunks": batch,
        "nextCursor": int(batch[-1]["cursor"]),
        "hasMore": bool(page["hasMore"] or index < len(batches) - 1),
    } for index, batch in enumerate(batches))


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
        planner=ModelScreenplayIntentPlanner(
            db,
            model_executor_factory=composition.create_managed_model_executor,
        ),
        resolver=SqliteScreenplayTaskResolver(db),
        unit_executor_factory=lambda runtime: ScreenplayTaskUnitExecutor(
            db,
            runtime=runtime,
            model_executor_factory=composition.create_managed_model_executor,
            tool_calling_service=ScreenplayToolCallingService(
                db,
                composition=composition,
            ),
        ),
        projects=ScreenplayV2ProjectService(db),
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
    after: int = Query(default=0, ge=0),
    chunk_after: int = Query(default=0, alias="chunkAfter", ge=0),
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
        chunk_cursor = int(chunk_after)
        chunk_replay_announced = False
        chunks = ScreenplayAgentChunkStore(get_db())
        while not await request.is_disconnected():
            emitted = False
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
                emitted = True
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
            if page["hasMore"] or chunk_page["hasMore"]:
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


@router.delete("/conversation/turns/{turn_id}/and-after")
async def truncate_screenplay_conversation_from_turn(turn_id: str):
    result = await _service().truncate_from_turn(turn_id)
    return {"success": True, "data": result}


__all__ = ["router"]
