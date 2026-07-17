from __future__ import annotations

from fastapi import APIRouter

from database.crud import character_history as char_hist_crud
from database.crud import characters as characters_crud
from database.crud import setting_entities as entities_crud
from database.crud import setting_entity_history as ent_hist_crud
from database.crud import story_background as bg_crud
from database.crud import story_background_history as bg_hist_crud
from dependencies import get_db
from schemas.setting_diff import (
    CommitBackgroundDiffRequest,
    CommitCharacterDiffRequest,
    CommitEntityDiffRequest,
)

router = APIRouter(tags=["setting-diff"])


@router.post("/setting-diff/character/{characterId}/commit")
async def commit_character_diff(characterId: str, body: CommitCharacterDiffRequest):
    db = get_db()
    try:
        cid = int(characterId)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的人物 ID"}

    row = await characters_crud.update_character(
        db,
        cid,
        {
            "name": body.name,
            "tags": body.tags,
            "profile_md": body.profileMd,
        },
    )
    if not row:
        return {"success": False, "error": "人物不存在"}

    hist_id = await char_hist_crud.insert_character_history(
        db,
        character_id=cid,
        before_name=body.before.name,
        before_tags=body.before.tags,
        before_profile_md=body.before.profileMd,
        after_name=body.after.name,
        after_tags=body.after.tags,
        after_profile_md=body.after.profileMd,
        source=body.source,
        accepted_segments=body.accepted_segments,
        rejected_segments=body.rejected_segments,
    )
    try:
        from services import memory_deposition_service
        await memory_deposition_service.deposit_manual_character_memory(row)
        await memory_deposition_service.deposit_character_diff_candidate(
            book_id=str(row["book_id"]),
            character_id=cid,
            character_name=body.after.name or body.name,
            after_profile_md=body.after.profileMd or body.profileMd,
            source_id=hist_id,
            source=body.source,
            accepted_segments=body.accepted_segments,
        )
    except Exception:
        pass
    return {"success": True, "data": {"id": hist_id, "characterId": cid}}


@router.get("/setting-diff/character/{characterId}/history")
async def list_character_history(characterId: str, limit: int = 50):
    db = get_db()
    try:
        cid = int(characterId)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的人物 ID"}
    rows = await char_hist_crud.list_character_history(db, cid, limit=limit)
    return {"success": True, "data": rows}


@router.get("/setting-diff/character/history/{historyId}")
async def get_character_history(historyId: int):
    db = get_db()
    row = await char_hist_crud.get_character_history(db, historyId)
    return {"success": True, "data": row}


@router.post("/setting-diff/character/history/{historyId}/rollback")
async def rollback_character_history(historyId: int):
    db = get_db()
    target = await char_hist_crud.get_character_history(db, historyId)
    if not target:
        return {"success": False, "error": "history not found"}

    cid = int(target["character_id"])
    rollback_to = {
        "name": target.get("before_name") or "",
        "tags": target.get("before_tags") or "",
        "profile_md": target.get("before_profile_md") or "",
    }
    rollback_from = {
        "name": target.get("after_name") or "",
        "tags": target.get("after_tags") or "",
        "profile_md": target.get("after_profile_md") or "",
    }

    row = await characters_crud.update_character(db, cid, rollback_to)
    if not row:
        return {"success": False, "error": "人物不存在"}

    new_id = await char_hist_crud.insert_character_history(
        db,
        character_id=cid,
        before_name=rollback_from["name"],
        before_tags=rollback_from["tags"],
        before_profile_md=rollback_from["profile_md"],
        after_name=rollback_to["name"],
        after_tags=rollback_to["tags"],
        after_profile_md=rollback_to["profile_md"],
        source=f"rollback_of:{historyId}",
        accepted_segments=0,
        rejected_segments=0,
    )
    return {"success": True, "data": {"id": new_id, "characterId": cid}}


@router.post("/setting-diff/entity/{entityId}/commit")
async def commit_entity_diff(entityId: str, body: CommitEntityDiffRequest):
    db = get_db()
    try:
        eid = int(entityId)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的实体 ID"}

    row = await entities_crud.update_setting_entity(
        db,
        eid,
        {
            "name": body.name,
            "tags": body.tags,
            "profile_md": body.profileMd,
        },
    )
    if not row:
        return {"success": False, "error": "实体不存在"}

    hist_id = await ent_hist_crud.insert_entity_history(
        db,
        entity_id=eid,
        before_name=body.before.name,
        before_tags=body.before.tags,
        before_profile_md=body.before.profileMd,
        after_name=body.after.name,
        after_tags=body.after.tags,
        after_profile_md=body.after.profileMd,
        source=body.source,
        accepted_segments=body.accepted_segments,
        rejected_segments=body.rejected_segments,
    )
    try:
        from services import memory_deposition_service
        await memory_deposition_service.deposit_manual_entity_memory(row)
        await memory_deposition_service.deposit_entity_diff_candidate(
            book_id=str(row["book_id"]),
            entity_id=eid,
            entity_name=body.after.name or body.name,
            after_profile_md=body.after.profileMd or body.profileMd,
            source_id=hist_id,
            source=body.source,
            accepted_segments=body.accepted_segments,
        )
    except Exception:
        pass
    return {"success": True, "data": {"id": hist_id, "entityId": eid}}


@router.get("/setting-diff/entity/{entityId}/history")
async def list_entity_history(entityId: str, limit: int = 50):
    db = get_db()
    try:
        eid = int(entityId)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的实体 ID"}
    rows = await ent_hist_crud.list_entity_history(db, eid, limit=limit)
    return {"success": True, "data": rows}


@router.get("/setting-diff/entity/history/{historyId}")
async def get_entity_history(historyId: int):
    db = get_db()
    row = await ent_hist_crud.get_entity_history(db, historyId)
    return {"success": True, "data": row}


@router.post("/setting-diff/entity/history/{historyId}/rollback")
async def rollback_entity_history(historyId: int):
    db = get_db()
    target = await ent_hist_crud.get_entity_history(db, historyId)
    if not target:
        return {"success": False, "error": "history not found"}

    eid = int(target["entity_id"])
    rollback_to = {
        "name": target.get("before_name") or "",
        "tags": target.get("before_tags") or "",
        "profile_md": target.get("before_profile_md") or "",
    }
    rollback_from = {
        "name": target.get("after_name") or "",
        "tags": target.get("after_tags") or "",
        "profile_md": target.get("after_profile_md") or "",
    }

    row = await entities_crud.update_setting_entity(db, eid, rollback_to)
    if not row:
        return {"success": False, "error": "实体不存在"}

    new_id = await ent_hist_crud.insert_entity_history(
        db,
        entity_id=eid,
        before_name=rollback_from["name"],
        before_tags=rollback_from["tags"],
        before_profile_md=rollback_from["profile_md"],
        after_name=rollback_to["name"],
        after_tags=rollback_to["tags"],
        after_profile_md=rollback_to["profile_md"],
        source=f"rollback_of:{historyId}",
        accepted_segments=0,
        rejected_segments=0,
    )
    return {"success": True, "data": {"id": new_id, "entityId": eid}}


@router.post("/setting-diff/background/{bookId}/commit")
async def commit_background_diff(bookId: str, body: CommitBackgroundDiffRequest):
    db = get_db()
    await bg_crud.save_story_background(db, bookId, body.content)
    hist_id = await bg_hist_crud.insert_story_background_history(
        db,
        book_id=bookId,
        before_content=body.before_content,
        after_content=body.after_content,
        source=body.source,
        accepted_segments=body.accepted_segments,
        rejected_segments=body.rejected_segments,
    )
    try:
        from services import memory_deposition_service
        await memory_deposition_service.deposit_manual_background_memory(
            bookId,
            body.content or body.after_content,
        )
        await memory_deposition_service.deposit_background_diff_candidate(
            book_id=bookId,
            after_content=body.after_content or body.content,
            source_id=hist_id,
            source=body.source,
            accepted_segments=body.accepted_segments,
        )
    except Exception:
        pass
    return {"success": True, "data": {"id": hist_id, "bookId": bookId}}


@router.get("/setting-diff/background/{bookId}/history")
async def list_background_history(bookId: str, limit: int = 50):
    db = get_db()
    rows = await bg_hist_crud.list_story_background_history(db, bookId, limit=limit)
    return {"success": True, "data": rows}


@router.get("/setting-diff/background/history/{historyId}")
async def get_background_history(historyId: int):
    db = get_db()
    row = await bg_hist_crud.get_story_background_history(db, historyId)
    return {"success": True, "data": row}


@router.post("/setting-diff/background/history/{historyId}/rollback")
async def rollback_background_history(historyId: int):
    db = get_db()
    target = await bg_hist_crud.get_story_background_history(db, historyId)
    if not target:
        return {"success": False, "error": "history not found"}

    book_id = str(target["book_id"])
    rollback_to = target.get("before_content") or ""
    rollback_from = target.get("after_content") or ""

    await bg_crud.save_story_background(db, book_id, rollback_to)
    new_id = await bg_hist_crud.insert_story_background_history(
        db,
        book_id=book_id,
        before_content=rollback_from,
        after_content=rollback_to,
        source=f"rollback_of:{historyId}",
        accepted_segments=0,
        rejected_segments=0,
    )
    return {"success": True, "data": {"id": new_id, "bookId": book_id}}
