"""Chapter annotation (批注) routes — select-anchored manual notes."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Query

from database.crud.chapter_annotations import (
    add_annotation,
    delete_annotation,
    list_annotations,
    update_annotation,
)
from dependencies import get_db
from schemas.annotations import AddAnnotationRequest, UpdateAnnotationRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["annotations"])


def _err_message(err: Exception) -> str:
    logger.error("[annotations] %s: %s", type(err).__name__, err, exc_info=True)
    return "annotation_request_failed"


@router.post("/annotations")
async def create_annotation(body: AddAnnotationRequest):
    try:
        data = await add_annotation(
            get_db(),
            book_id=body.bookId,
            chapter_id=body.chapterId,
            start_offset=body.startOffset,
            end_offset=body.endOffset,
            quoted_text=body.quotedText,
            context_before=body.contextBefore or "",
            context_after=body.contextAfter or "",
            note=body.note,
            source=body.source,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.put("/annotations/{id}")
async def modify_annotation(id: int, body: UpdateAnnotationRequest):
    try:
        changes: dict[str, Any] = {}
        if "note" in body.model_fields_set:
            changes["note"] = body.note.strip()
        if "status" in body.model_fields_set:
            changes["status"] = body.status
        data = await update_annotation(
            get_db(), book_id=body.bookId, annotation_id=id, data=changes
        )
        if data is None:
            return {"success": False, "error": "annotation_not_found"}
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.delete("/annotations/{id}")
async def remove_annotation(id: int, bookId: str = Query(...)):
    try:
        deleted = await delete_annotation(get_db(), book_id=bookId, annotation_id=id)
        if deleted is None:
            return {"success": False, "error": "annotation_not_found"}
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}


@router.get("/annotations/by-book")
async def get_annotations_by_book(
    bookId: str = Query(...),
    chapterId: Optional[str] = Query(None),
):
    try:
        data = await list_annotations(
            get_db(), book_id=bookId, chapter_id=chapterId
        )
        return {"success": True, "data": data}
    except Exception as exc:
        return {"success": False, "error": _err_message(exc)}
