from __future__ import annotations

from fastapi import APIRouter, HTTPException

from application.book_conversation_product_projection import (
    BookSettingResolutionConflictError,
    backfill_setting_diff_mutation_digest,
    legacy_setting_diff_reviewed_final,
    persist_setting_diff_resolution,
    setting_diff_mutation_digest,
    validate_setting_diff_mutation,
)
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


async def _persist_resolution(*args, **kwargs):
    try:
        return await persist_setting_diff_resolution(*args, **kwargs)
    except BookSettingResolutionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


async def _validate_mutation(*args, **kwargs) -> None:
    try:
        await validate_setting_diff_mutation(*args, **kwargs)
    except BookSettingResolutionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


async def _resolution_with_mutation(
    db,
    resolution,
    *,
    request_before: dict,
    request_proposed: dict,
    final: dict,
) -> dict:
    value = resolution.model_dump()
    try:
        digest = await setting_diff_mutation_digest(
            db,
            run_id=resolution.agentRunId,
            resolution=value,
            request_before=request_before,
            request_proposed=request_proposed,
            final=final,
        )
    except BookSettingResolutionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {**value, "mutationDigest": digest}


async def _backfill_legacy_replay(
    db,
    *,
    write,
    resolution,
    resolution_value: dict,
    proven: bool,
) -> None:
    if not proven:
        raise HTTPException(
            status_code=409,
            detail="legacy setting proposal replay cannot be proven",
        )
    try:
        await backfill_setting_diff_mutation_digest(
            db,
            conversation_id=write.conversation_id,
            session_id=resolution.sessionId,
            proposal_id=resolution.proposalId,
            expected_resolution=resolution_value,
        )
    except BookSettingResolutionConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _legacy_replay_is_proven(
    *,
    kind: str,
    request_before: dict,
    request_proposed: dict,
    resolution: dict,
    current: dict | None,
    final: dict,
    history: dict | None,
) -> bool:
    expected = legacy_setting_diff_reviewed_final(
        kind=kind,
        request_before=request_before,
        request_proposed=request_proposed,
        resolution=resolution,
    )
    return bool(history and expected is not None and current == expected and final == expected)


async def _check_file_proposal(db, resolution, expected):
    if resolution is None or expected is None:
        return
    from application.writing_proposal_read_model import SqliteWritingProposalReadModel
    events = await SqliteWritingProposalReadModel(db).list_for_run(resolution.agentRunId)
    proposal = next((event['payload'] for event in events if event['proposalId'] == resolution.proposalId), {})
    if proposal.get('baseRevision') != expected:
        raise HTTPException(status_code=409, detail="提案文件版本与原始提案不一致，请重新生成提案")


def _atomic_history(function):
    from functools import wraps
    @wraps(function)
    async def wrapped(*args, **kwargs):
        async with get_db().transaction():
            return await function(*args, **kwargs)
    return wrapped


async def _rollback_revision(db, kind, identity):
    from application.creation_material_service import materials, TABLES
    table, key = TABLES[kind]
    row = await db.fetch_one(f'SELECT book_id FROM {table} WHERE {key}=?', [identity])
    if not row:
        return None
    await materials(db).synchronize(row['book_id'])
    mapping = await db.fetch_one('SELECT revision FROM creation_material_files WHERE book_id=? AND kind=? AND entity_id=?', [row['book_id'], kind, str(identity)])
    return mapping['revision'] if mapping else None


@router.post("/setting-diff/character/{characterId}/commit")
async def commit_character_diff(characterId: str, body: CommitCharacterDiffRequest):
    db = get_db()
    try:
        cid = int(characterId)
    except (TypeError, ValueError):
        return {"success": False, "error": "无效的人物 ID"}

    async with db.transaction(cancellation_linearizable=True):
        if body.resolution is not None:
            if (
                body.resolution.kind != "character"
                or body.resolution.sessionKey != f"character:{cid}"
            ):
                raise ValueError("setting proposal target does not match character")
            request_before = body.before.model_dump()
            request_proposed = body.after.model_dump()
            final = {
                "name": body.name,
                "tags": body.tags,
                "profileMd": body.profileMd,
            }
            resolution_value = await _resolution_with_mutation(
                db,
                body.resolution,
                request_before=request_before,
                request_proposed=request_proposed,
                final=final,
            )
            resolution_write = await _persist_resolution(
                db,
                session_id=body.resolution.sessionId,
                run_id=body.resolution.agentRunId,
                resolution=resolution_value,
                allow_new_committed=True,
            )
            if resolution_write.replayed:
                return {
                    "success": True,
                    "data": {"characterId": cid, "replayed": True},
                }
            current = await db.fetch_one(
                "SELECT name, tags, profile_md FROM characters WHERE id = ?",
                [cid],
            )
            if resolution_write.needs_digest_backfill:
                history = await db.fetch_one(
                    "SELECT id FROM character_history WHERE character_id = ? "
                    "AND before_name = ? AND before_tags = ? "
                    "AND before_profile_md = ? AND after_name = ? "
                    "AND after_tags = ? AND after_profile_md = ? "
                    "AND accepted_segments = ? AND rejected_segments = ? LIMIT 1",
                    [
                        cid,
                        body.before.name,
                        body.before.tags,
                        body.before.profileMd,
                        body.after.name,
                        body.after.tags,
                        body.after.profileMd,
                        body.resolution.acceptedSegments,
                        body.resolution.rejectedSegments,
                    ],
                )
                current_value = ({
                    "name": str(current.get("name") or ""),
                    "tags": str(current.get("tags") or ""),
                    "profileMd": str(current.get("profile_md") or ""),
                } if current else None)
                await _backfill_legacy_replay(
                    db,
                    write=resolution_write,
                    resolution=body.resolution,
                    resolution_value=resolution_value,
                    proven=_legacy_replay_is_proven(
                        kind="character",
                        request_before=request_before,
                        request_proposed=request_proposed,
                        resolution=resolution_value,
                        current=current_value,
                        final=final,
                        history=history,
                    ),
                )
                return {
                    "success": True,
                    "data": {"characterId": cid, "replayed": True},
                }
            if current is None:
                raise HTTPException(status_code=409, detail="character target is missing")
            await _validate_mutation(
                db,
                run_id=body.resolution.agentRunId,
                resolution=resolution_value,
                request_before=request_before,
                request_proposed=request_proposed,
                current={
                    "name": str((current or {}).get("name") or ""),
                    "tags": str((current or {}).get("tags") or ""),
                    "profileMd": str((current or {}).get("profile_md") or ""),
                },
                final=final,
            )
        await _check_file_proposal(db, body.resolution, body.baseRevision)
        row = await characters_crud.update_character(
            db,
            cid,
            {
                "name": body.name,
                "tags": body.tags,
                "profile_md": body.profileMd,
                "baseRevision": body.baseRevision,
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
        from services import memory_deposition_service

        manual_keys = await memory_deposition_service.record_manual_character(db, row)
        diff_keys = await memory_deposition_service.record_character_diff_candidate(
            db,
            book_id=str(row["book_id"]),
            character_id=cid,
            character_name=body.after.name or body.name,
            after_profile_md=body.after.profileMd or body.profileMd,
            source_id=hist_id,
            source=body.source,
            accepted_segments=body.accepted_segments,
        )
        delivery_keys = (*manual_keys, *diff_keys)
    deliveries = await memory_deposition_service.deliver_recorded(db, delivery_keys)
    return {
        "success": True,
        "data": {
            "id": hist_id,
            "characterId": cid,
            "memoryDelivery": [item.to_dict() for item in deliveries],
        },
    }


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
@_atomic_history
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

    row = await characters_crud.update_character(db, cid, rollback_to, base_revision=await _rollback_revision(db, "character", cid))
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

    async with db.transaction(cancellation_linearizable=True):
        if body.resolution is not None:
            if (
                body.resolution.kind != "entity"
                or body.resolution.sessionKey != f"entity:{eid}"
            ):
                raise ValueError("setting proposal target does not match entity")
            request_before = body.before.model_dump()
            request_proposed = body.after.model_dump()
            final = {
                "name": body.name,
                "tags": body.tags,
                "profileMd": body.profileMd,
            }
            resolution_value = await _resolution_with_mutation(
                db,
                body.resolution,
                request_before=request_before,
                request_proposed=request_proposed,
                final=final,
            )
            resolution_write = await _persist_resolution(
                db,
                session_id=body.resolution.sessionId,
                run_id=body.resolution.agentRunId,
                resolution=resolution_value,
                allow_new_committed=True,
            )
            if resolution_write.replayed:
                return {
                    "success": True,
                    "data": {"entityId": eid, "replayed": True},
                }
            current = await db.fetch_one(
                "SELECT name, tags, profile_md FROM setting_entities WHERE id = ?",
                [eid],
            )
            if resolution_write.needs_digest_backfill:
                history = await db.fetch_one(
                    "SELECT id FROM setting_entity_history WHERE entity_id = ? "
                    "AND before_name = ? AND before_tags = ? "
                    "AND before_profile_md = ? AND after_name = ? "
                    "AND after_tags = ? AND after_profile_md = ? "
                    "AND accepted_segments = ? AND rejected_segments = ? LIMIT 1",
                    [
                        eid,
                        body.before.name,
                        body.before.tags,
                        body.before.profileMd,
                        body.after.name,
                        body.after.tags,
                        body.after.profileMd,
                        body.resolution.acceptedSegments,
                        body.resolution.rejectedSegments,
                    ],
                )
                current_value = ({
                    "name": str(current.get("name") or ""),
                    "tags": str(current.get("tags") or ""),
                    "profileMd": str(current.get("profile_md") or ""),
                } if current else None)
                await _backfill_legacy_replay(
                    db,
                    write=resolution_write,
                    resolution=body.resolution,
                    resolution_value=resolution_value,
                    proven=_legacy_replay_is_proven(
                        kind="entity",
                        request_before=request_before,
                        request_proposed=request_proposed,
                        resolution=resolution_value,
                        current=current_value,
                        final=final,
                        history=history,
                    ),
                )
                return {
                    "success": True,
                    "data": {"entityId": eid, "replayed": True},
                }
            if current is None:
                raise HTTPException(status_code=409, detail="entity target is missing")
            await _validate_mutation(
                db,
                run_id=body.resolution.agentRunId,
                resolution=resolution_value,
                request_before=request_before,
                request_proposed=request_proposed,
                current={
                    "name": str((current or {}).get("name") or ""),
                    "tags": str((current or {}).get("tags") or ""),
                    "profileMd": str((current or {}).get("profile_md") or ""),
                },
                final=final,
            )
        await _check_file_proposal(db, body.resolution, body.baseRevision)
        row = await entities_crud.update_setting_entity(
            db,
            eid,
            {
                "name": body.name,
                "tags": body.tags,
                "profile_md": body.profileMd,
                "baseRevision": body.baseRevision,
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
        from services import memory_deposition_service

        manual_keys = await memory_deposition_service.record_manual_entity(db, row)
        diff_keys = await memory_deposition_service.record_entity_diff_candidate(
            db,
            book_id=str(row["book_id"]),
            entity_id=eid,
            entity_name=body.after.name or body.name,
            after_profile_md=body.after.profileMd or body.profileMd,
            source_id=hist_id,
            source=body.source,
            accepted_segments=body.accepted_segments,
        )
        delivery_keys = (*manual_keys, *diff_keys)
    deliveries = await memory_deposition_service.deliver_recorded(db, delivery_keys)
    return {
        "success": True,
        "data": {
            "id": hist_id,
            "entityId": eid,
            "memoryDelivery": [item.to_dict() for item in deliveries],
        },
    }


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
@_atomic_history
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

    row = await entities_crud.update_setting_entity(db, eid, rollback_to, base_revision=await _rollback_revision(db, "entity", eid))
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
    async with db.transaction(cancellation_linearizable=True):
        if body.resolution is not None:
            if (
                body.resolution.kind != "background"
                or body.resolution.sessionKey != f"background:{bookId}"
            ):
                raise ValueError("setting proposal target does not match background")
            request_before = {"content": body.before_content}
            request_proposed = {"content": body.after_content}
            final = {"content": body.content}
            resolution_value = await _resolution_with_mutation(
                db,
                body.resolution,
                request_before=request_before,
                request_proposed=request_proposed,
                final=final,
            )
            resolution_write = await _persist_resolution(
                db,
                session_id=body.resolution.sessionId,
                run_id=body.resolution.agentRunId,
                resolution=resolution_value,
                allow_new_committed=True,
            )
            if resolution_write.replayed:
                return {
                    "success": True,
                    "data": {"bookId": bookId, "replayed": True},
                }
            current = await db.fetch_one(
                "SELECT content FROM story_background WHERE book_id = ?",
                [bookId],
            )
            if resolution_write.needs_digest_backfill:
                history = await db.fetch_one(
                    "SELECT id FROM story_background_history WHERE book_id = ? "
                    "AND before_content = ? AND after_content = ? "
                    "AND accepted_segments = ? AND rejected_segments = ? LIMIT 1",
                    [
                        bookId,
                        body.before_content,
                        body.after_content,
                        body.resolution.acceptedSegments,
                        body.resolution.rejectedSegments,
                    ],
                )
                current_value = (
                    {"content": str(current.get("content") or "")}
                    if current else None
                )
                await _backfill_legacy_replay(
                    db,
                    write=resolution_write,
                    resolution=body.resolution,
                    resolution_value=resolution_value,
                    proven=_legacy_replay_is_proven(
                        kind="background",
                        request_before=request_before,
                        request_proposed=request_proposed,
                        resolution=resolution_value,
                        current=current_value,
                        final=final,
                        history=history,
                    ),
                )
                return {
                    "success": True,
                    "data": {"bookId": bookId, "replayed": True},
                }
            await _validate_mutation(
                db,
                run_id=body.resolution.agentRunId,
                resolution=resolution_value,
                request_before=request_before,
                request_proposed=request_proposed,
                current={"content": str((current or {}).get("content") or "")},
                final=final,
            )
        await _check_file_proposal(db, body.resolution, body.baseRevision)
        await bg_crud.save_story_background(db, bookId, body.content, base_revision=body.baseRevision)
        hist_id = await bg_hist_crud.insert_story_background_history(
            db,
            book_id=bookId,
            before_content=body.before_content,
            after_content=body.after_content,
            source=body.source,
            accepted_segments=body.accepted_segments,
            rejected_segments=body.rejected_segments,
        )
        from services import memory_deposition_service

        manual_keys = await memory_deposition_service.record_manual_background(
            db, bookId,
            body.content or body.after_content,
        )
        diff_keys = await memory_deposition_service.record_background_diff_candidate(
            db,
            book_id=bookId,
            after_content=body.after_content or body.content,
            source_id=hist_id,
            source=body.source,
            accepted_segments=body.accepted_segments,
        )
        delivery_keys = (*manual_keys, *diff_keys)
    deliveries = await memory_deposition_service.deliver_recorded(db, delivery_keys)
    return {
        "success": True,
        "data": {
            "id": hist_id,
            "bookId": bookId,
            "memoryDelivery": [item.to_dict() for item in deliveries],
        },
    }


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
@_atomic_history
async def rollback_background_history(historyId: int):
    db = get_db()
    target = await bg_hist_crud.get_story_background_history(db, historyId)
    if not target:
        return {"success": False, "error": "history not found"}

    book_id = str(target["book_id"])
    rollback_to = target.get("before_content") or ""
    rollback_from = target.get("after_content") or ""

    await bg_crud.save_story_background(db, book_id, rollback_to, base_revision=await _rollback_revision(db, "background", book_id))
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
