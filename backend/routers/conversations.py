from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from application.book_conversation_product_projection import (
    BookSettingResolutionConflictError,
    merge_product_agent_process,
    persist_setting_diff_resolution,
)
from dependencies import get_db
from infrastructure.persistence.run_store import ROOT_RUN_SESSION_FILTER
from schemas.conversations import SaveConversationRequest

router = APIRouter(tags=["conversations"])

TERMINAL_RUN_STATUSES = frozenset({"done", "failed", "blocked", "canceled"})


@router.post("/conversations")
async def save_conversation(body: SaveConversationRequest):
    db = get_db()
    conversation_id: int | None = None
    memory_book_id: str | None = None
    memory_prompt = body.prompt
    deposit_memory = True
    delivery_keys: tuple[str, ...] = ()
    async with db.transaction(cancellation_linearizable=True):
        session = await db.fetch_one(
            "SELECT id, book_id, chapter_id FROM ai_sessions WHERE id = ?",
            [body.sessionId],
        )
        if session is None:
            raise HTTPException(status_code=409, detail="对话会话不存在或已删除")
        memory_book_id = str(session.get("book_id") or "").strip() or None
        session_chapter_id = str(session.get("chapter_id") or "").strip() or None
        request_book_id = str(body.bookId or "").strip() or None
        request_chapter_id = str(body.chapterId or "").strip() or None
        if body.agentRunId:
            run_id = str(body.agentRunId)
            run = await db.fetch_one(
                "SELECT conversation_id, session_id, status, prompt, model_name, parent_run_id, root_run_id "
                "FROM ai_agent_runs WHERE id = ?",
                [run_id],
            )
            if run is None or int(run.get("session_id") or 0) != body.sessionId:
                raise HTTPException(status_code=409, detail="Agent Run 不属于当前会话")
            if run.get("parent_run_id") or run.get("root_run_id") not in (None, "", run_id):
                raise HTTPException(status_code=409, detail="Child Run 不能保存为独立对话")
            run_status = str(run.get("status") or "")
            running_resolution = (
                run_status == "running"
                and _has_setting_resolution(body.agentProcess)
            )
            if run_status not in TERMINAL_RUN_STATUSES and not running_resolution:
                raise HTTPException(status_code=409, detail="Agent Run 尚未终态，不能保存投影")
            existing_id = run.get("conversation_id")
            incoming_resolutions = _setting_resolutions(body.agentProcess)
            if incoming_resolutions:
                try:
                    for resolution in incoming_resolutions.values():
                        write = await persist_setting_diff_resolution(
                            db,
                            session_id=body.sessionId,
                            run_id=run_id,
                            resolution=resolution,
                        )
                        existing_id = write.conversation_id
                except BookSettingResolutionConflictError as error:
                    raise HTTPException(status_code=409, detail=str(error)) from error
                if running_resolution:
                    deposit_memory = False
            if existing_id is None:
                from infrastructure.persistence.run_conversation_store import (
                    ensure_terminal_run_conversation,
                )

                conversation_id = await ensure_terminal_run_conversation(db, run_id)
            else:
                conversation_id = int(existing_id)
            if conversation_id is None:
                raise RuntimeError("terminal Agent Run conversation was not materialized")
            existing = await db.fetch_one(
                "SELECT prompt, agent_process FROM ai_conversations "
                "WHERE id = ? AND session_id = ?",
                [conversation_id, body.sessionId],
            )
            if existing is None:
                raise HTTPException(status_code=409, detail="Agent Run 会话投影归属不一致")
            if body.clientTurnId:
                turn_id = str(body.clientTurnId).strip()
                collision = await db.fetch_one(
                    "SELECT id FROM ai_conversations WHERE session_id = ? "
                    "AND client_turn_id = ? AND id <> ?",
                    [body.sessionId, turn_id, conversation_id],
                )
                if collision is not None:
                    raise HTTPException(status_code=409, detail="对话回合标识已被占用")
                await db.execute(
                    "UPDATE ai_conversations SET client_turn_id = "
                    "COALESCE(client_turn_id, ?) WHERE id = ? AND session_id = ?",
                    [turn_id, conversation_id, body.sessionId],
                )
            memory_prompt = str(existing.get("prompt") or "")
            merged = merge_product_agent_process(
                _json_object(existing.get("agent_process")),
                _without_setting_resolutions(body.agentProcess),
                run_id=run_id,
                conversation_id=conversation_id,
            )
            if merged:
                await db.execute(
                    "UPDATE ai_conversations SET agent_process = ? "
                    "WHERE id = ? AND session_id = ?",
                    [
                        json.dumps(merged, ensure_ascii=False),
                        conversation_id,
                        body.sessionId,
                    ],
                )
        else:
            enhanced_local_save = (
                body.clientTurnId is not None
                or body.expectedConversationIds is not None
            )
            if enhanced_local_save and (
                (body.bookId is not None and request_book_id != memory_book_id)
                or (
                    body.chapterId is not None
                    and request_chapter_id != session_chapter_id
                )
            ):
                raise HTTPException(
                    status_code=409,
                    detail="对话请求范围不属于当前会话",
                )
            turn_id = str(body.clientTurnId or "").strip() or None
            turn_digest = (
                _local_conversation_digest(
                    body,
                    chapter_id=session_chapter_id,
                )
                if turn_id
                else None
            )
            receipt = None
            if turn_id:
                receipt = await db.fetch_one(
                    "SELECT payload_digest, status, conversation_id "
                    "FROM ai_local_conversation_turn_receipts "
                    "WHERE session_id = ? AND client_turn_id = ?",
                    [body.sessionId, turn_id],
                )
            replay = None
            if receipt is not None:
                if str(receipt.get("status") or "") == "retired":
                    raise HTTPException(
                        status_code=409,
                        detail="该本地对话回合已被截断，不能重新保存。",
                    )
                if str(receipt.get("payload_digest") or "") != turn_digest:
                    raise HTTPException(
                        status_code=409,
                        detail="对话回合标识与既有内容冲突",
                    )
                receipt_conversation_id = receipt.get("conversation_id")
                receipt_projection = await db.fetch_one(
                    "SELECT id FROM ai_conversations WHERE id = ? "
                    "AND session_id = ? AND client_turn_id = ?",
                    [receipt_conversation_id, body.sessionId, turn_id],
                )
                if receipt_projection is None:
                    raise HTTPException(
                        status_code=409,
                        detail="本地对话回合投影缺失，请刷新后重试。",
                    )
                conversation_id = int(receipt_projection["id"])
            elif turn_id:
                replay = await db.fetch_one(
                    "SELECT id, chapter_id, prompt, response, model, commentary, "
                    "tool_call_segments, commentary_blocks, commentary_durations_ms, "
                    "duration_ms, task_plan, context_compaction, context_budget, "
                    "agent_process FROM ai_conversations "
                    "WHERE session_id = ? AND client_turn_id = ?",
                    [body.sessionId, turn_id],
                )
            if conversation_id is None and replay is not None:
                if not _local_conversation_replay_matches(
                    replay,
                    body,
                    chapter_id=session_chapter_id,
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="对话回合标识与既有内容冲突",
                    )
                conversation_id = int(replay["id"])
                await db.execute(
                    "INSERT INTO ai_local_conversation_turn_receipts "
                    "(session_id, client_turn_id, payload_digest, status, "
                    "conversation_id) VALUES (?, ?, ?, 'persisted', ?)",
                    [
                        body.sessionId,
                        turn_id,
                        turn_digest,
                        conversation_id,
                    ],
                )
            elif conversation_id is None:
                if body.expectedConversationIds is not None:
                    current_conversation_ids = [
                        int(row["id"])
                        for row in await db.fetch_all(
                            "SELECT id FROM ai_conversations "
                            "WHERE session_id = ? ORDER BY id ASC",
                            [body.sessionId],
                        )
                    ]
                    if current_conversation_ids != body.expectedConversationIds:
                        raise HTTPException(
                            status_code=409,
                            detail="对话历史已变化，请刷新后重试。",
                        )
                pending_agent_projection = await db.fetch_one(
                    "SELECT id FROM ai_agent_runs WHERE session_id = ? "
                    f"AND {ROOT_RUN_SESSION_FILTER} "
                    "AND ("
                    "conversation_id IS NULL OR "
                    "status IN ('pending', 'queued', 'running', 'paused')"
                    ") LIMIT 1",
                    [body.sessionId],
                )
                pending_agent_request = await db.fetch_one(
                    "SELECT request_id FROM ai_writing_chat_requests "
                    "WHERE session_id = ? AND status IN ('accepted', 'starting') "
                    "LIMIT 1",
                    [body.sessionId],
                )
                if pending_agent_projection is not None or pending_agent_request is not None:
                    raise HTTPException(
                        status_code=409,
                        detail="Agent 对话仍在运行或等待投影，请稍后重试。",
                    )
                values = _local_conversation_values(
                    body,
                    chapter_id=session_chapter_id,
                )
                conversation_id = await db.execute_and_get_id(
                    """INSERT INTO ai_conversations
                       (session_id, chapter_id, prompt, response, model, commentary,
                        tool_call_segments, commentary_blocks, commentary_durations_ms,
                        duration_ms, task_plan, context_compaction, context_budget,
                        agent_process, client_turn_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [body.sessionId, *values, turn_id],
                )
                if turn_id:
                    await db.execute(
                        "INSERT INTO ai_local_conversation_turn_receipts "
                        "(session_id, client_turn_id, payload_digest, status, "
                        "conversation_id) VALUES (?, ?, ?, 'persisted', ?)",
                        [
                            body.sessionId,
                            turn_id,
                            turn_digest,
                            conversation_id,
                        ],
                    )
        if deposit_memory:
            from services import memory_deposition_service

            source = await db.fetch_one(
                "SELECT id FROM ai_conversations WHERE id = ? AND session_id = ?",
                [conversation_id, body.sessionId],
            )
            if source is not None:
                delivery_keys = await memory_deposition_service.record_explicit_conversation_memory(
                    db,
                    book_id=memory_book_id,
                    conversation_id=conversation_id,
                    prompt=memory_prompt,
                )
    from services import memory_deposition_service

    deliveries = await memory_deposition_service.deliver_recorded(db, delivery_keys)
    return {
        "success": True,
        "data": {
            "id": conversation_id,
            "memoryDelivery": [item.to_dict() for item in deliveries],
        },
    }


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_value(value: object) -> str | None:
    return json.dumps(value, ensure_ascii=False) if value is not None else None


def _has_setting_resolution(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"settingDiff"}:
        return False
    setting = value.get("settingDiff")
    if not isinstance(setting, dict):
        return False
    resolutions = setting.get("resolutions")
    if not isinstance(resolutions, dict) or not resolutions:
        return False
    return all(
        isinstance(resolution, dict)
        and resolution.get("status") in {"committed", "rejected"}
        for resolution in resolutions.values()
    )


def _setting_resolutions(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    setting = value.get("settingDiff")
    resolutions = setting.get("resolutions") if isinstance(setting, dict) else None
    if not isinstance(resolutions, dict):
        return {}
    return {
        str(proposal_id): resolution
        for proposal_id, resolution in resolutions.items()
        if isinstance(resolution, dict)
    }


def _without_setting_resolutions(value: object) -> object:
    if not isinstance(value, dict):
        return value
    setting = value.get("settingDiff")
    if not isinstance(setting, dict) or "resolutions" not in setting:
        return value
    return {
        **value,
        "settingDiff": {
            **setting,
            "resolutions": {},
        },
    }


def _local_conversation_values(
    body: SaveConversationRequest,
    *,
    chapter_id: str | None = None,
) -> list[Any]:
    return [
        chapter_id if chapter_id is not None else body.chapterId,
        body.prompt,
        body.response,
        body.model,
        body.commentary,
        _json_value(body.toolCallSegments),
        _json_value(body.commentaryBlocks),
        _json_value(body.commentaryDurationsMs),
        body.durationMs,
        _json_value(body.taskPlan),
        _json_value(body.contextCompaction),
        _json_value(body.contextBudget),
        _json_value(body.agentProcess),
    ]


def _local_conversation_digest(
    body: SaveConversationRequest,
    *,
    chapter_id: str | None,
) -> str:
    return _local_payload_digest({
        "chapterId": chapter_id,
        "prompt": body.prompt,
        "response": body.response,
        "model": body.model,
        "commentary": body.commentary,
        "toolCallSegments": body.toolCallSegments,
        "commentaryBlocks": body.commentaryBlocks,
        "commentaryDurationsMs": body.commentaryDurationsMs,
        "durationMs": body.durationMs,
        "taskPlan": body.taskPlan,
        "contextCompaction": body.contextCompaction,
        "contextBudget": body.contextBudget,
        "agentProcess": body.agentProcess,
    })


def _local_payload_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{sha256(canonical.encode('utf-8')).hexdigest()}"


def _local_conversation_replay_matches(
    row: dict[str, Any],
    body: SaveConversationRequest,
    *,
    chapter_id: str | None,
) -> bool:
    json_fields = {
        "toolCallSegments": "tool_call_segments",
        "commentaryBlocks": "commentary_blocks",
        "commentaryDurationsMs": "commentary_durations_ms",
        "taskPlan": "task_plan",
        "contextCompaction": "context_compaction",
        "contextBudget": "context_budget",
        "agentProcess": "agent_process",
    }
    decoded: dict[str, Any] = {}
    for payload_field, row_field in json_fields.items():
        raw = row.get(row_field)
        if raw is None:
            decoded[payload_field] = None
            continue
        try:
            decoded[payload_field] = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
    persisted_digest = _local_payload_digest({
        "chapterId": row.get("chapter_id"),
        "prompt": row.get("prompt"),
        "response": row.get("response"),
        "model": row.get("model"),
        "commentary": row.get("commentary"),
        "durationMs": row.get("duration_ms"),
        **decoded,
    })
    return persisted_digest == _local_conversation_digest(
        body,
        chapter_id=chapter_id,
    )


@router.get("/conversations/{sessionId}")
async def get_conversations(sessionId: str):
    db = get_db()
    rows = await db.fetch_all(
        "SELECT c.id, c.session_id, c.chapter_id, c.prompt, c.response, "
        "c.create_time, c.model, c.commentary, c.tool_call_segments, "
        "c.commentary_blocks, c.commentary_durations_ms, c.duration_ms, "
        "c.task_plan, c.context_compaction, c.context_budget, c.agent_process, "
        "c.client_turn_id, "
        "(SELECT r.id FROM ai_agent_runs AS r "
        "  WHERE r.conversation_id = c.id "
        "  ORDER BY r.update_time DESC LIMIT 1) AS agent_run_id, "
        "(SELECT lt.id FROM ai_agent_runs AS r "
        "  JOIN ai_agent_long_task_runs AS ltr ON ltr.run_id = r.id "
        "  JOIN ai_agent_long_tasks AS lt ON lt.id = ltr.task_id "
        "  WHERE r.conversation_id = c.id "
        "    AND ((json_extract(lt.metadata_json, '$.sessionId') IS NOT NULL "
        "      AND CAST(json_extract(lt.metadata_json, '$.sessionId') AS INTEGER) = c.session_id) "
        "      OR (json_extract(lt.metadata_json, '$.sessionId') IS NULL "
        "        AND lt.created_by_run_id = r.id)) "
        "  ORDER BY lt.update_time DESC LIMIT 1) AS long_task_id "
        "FROM ai_conversations AS c WHERE c.session_id = ? ORDER BY c.id ASC",
        [sessionId],
    )
    return {"success": True, "data": rows}


@router.delete("/conversations/{sessionId}/after-turn")
async def delete_after_turn(
    sessionId: str,
    keepTurnCount: int = Query(...),
    expectedConversationIds: str | None = None,
    retireConversationIds: str | None = None,
    retireRunIds: str | None = None,
    expectedRunIds: str | None = None,
    retireClientTurnIds: str | None = None,
):
    db = get_db()
    memory_delivery_keys = ()
    async with db.transaction(cancellation_linearizable=True):
        exact_boundary = (
            retireConversationIds is not None
            or retireRunIds is not None
            or expectedRunIds is not None
            or retireClientTurnIds is not None
        )
        expected_ids = _csv_ints(expectedConversationIds)
        requested_ids = _csv_ints(retireConversationIds)
        requested_conversation_ids = set(requested_ids)
        explicit_run_ids = _csv_text(retireRunIds)
        expected_run_ids = _csv_text(expectedRunIds)
        requested_turn_ids = _csv_text(retireClientTurnIds)
        if requested_turn_ids:
            session = await db.fetch_one(
                "SELECT id FROM ai_sessions WHERE id = ?",
                [sessionId],
            )
            if session is None:
                raise HTTPException(status_code=409, detail="对话会话不存在或已删除")
        normal_state = True
        replay_state = True
        if exact_boundary and expectedConversationIds is not None:
            current_ids = [
                int(row["id"])
                for row in await db.fetch_all(
                    "SELECT id FROM ai_conversations "
                    "WHERE session_id = ? ORDER BY id ASC",
                    [sessionId],
                )
            ]
            normal_state = normal_state and current_ids == expected_ids
            replay_state = replay_state and current_ids == [
                value for value in expected_ids
                if value not in requested_conversation_ids
            ]
        if exact_boundary and expectedRunIds is not None:
            current_run_ids = {
                str(row["id"])
                for row in await db.fetch_all(
                    "SELECT id FROM ai_agent_runs WHERE session_id = ? "
                    f"AND {ROOT_RUN_SESSION_FILTER}",
                    [sessionId],
                )
            }
            expected_run_set = set(expected_run_ids)
            normal_state = normal_state and current_run_ids == expected_run_set
            replay_state = replay_state and current_run_ids == (
                expected_run_set - set(explicit_run_ids)
            )
        if exact_boundary and not normal_state and not replay_state:
            raise HTTPException(
                status_code=409,
                detail="对话历史已变化，请刷新后重试编辑。",
            )
        already_applied = replay_state and not normal_state
        ids_to_delete = list(requested_ids)
        if exact_boundary:
            if ids_to_delete:
                placeholders = ",".join("?" for _ in ids_to_delete)
                owned = await db.fetch_all(
                    "SELECT id FROM ai_conversations "
                    f"WHERE id IN ({placeholders}) AND session_id = ?",
                    [*ids_to_delete, sessionId],
                )
                ids_to_delete = [int(row["id"]) for row in owned]
                if (
                    set(ids_to_delete) != requested_conversation_ids
                    and not already_applied
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="截断对话目标不存在或不属于当前会话。",
                    )
                if already_applied:
                    placeholders = ",".join(
                        "?" for _ in requested_conversation_ids
                    )
                    foreign = await db.fetch_one(
                        "SELECT id FROM ai_conversations "
                        f"WHERE id IN ({placeholders}) AND session_id <> ? LIMIT 1",
                        [*requested_conversation_ids, sessionId],
                    )
                    if foreign is not None:
                        raise HTTPException(
                            status_code=409,
                            detail="截断对话目标已被其他会话占用。",
                        )
        else:
            all_rows = await db.fetch_all(
                "SELECT id FROM ai_conversations "
                "WHERE session_id = ? ORDER BY id ASC",
                [sessionId],
            )
            ids_to_delete = [
                int(row["id"])
                for row in all_rows[max(0, keepTurnCount):]
            ]

        tail_runs: list[dict[str, Any]] = []
        if ids_to_delete:
            placeholders = ",".join("?" for _ in ids_to_delete)
            tail_runs.extend(await db.fetch_all(
                "SELECT id, session_id, conversation_id, status, "
                "binding_aggregate_id FROM ai_agent_runs "
                f"WHERE conversation_id IN ({placeholders})",
                ids_to_delete,
            ))
        if explicit_run_ids:
            run_placeholders = ",".join("?" for _ in explicit_run_ids)
            explicit_rows = await db.fetch_all(
                "SELECT id, session_id, conversation_id, status, "
                "binding_aggregate_id FROM ai_agent_runs "
                f"WHERE id IN ({run_placeholders})",
                explicit_run_ids,
            )
            if {str(row["id"]) for row in explicit_rows} != set(explicit_run_ids):
                raise HTTPException(
                    status_code=409,
                    detail="截断 Run 目标不存在。",
                )
            for row in explicit_rows:
                session_owner = row.get("session_id")
                binding_owner = str(row.get("binding_aggregate_id") or "")
                if (
                    session_owner is not None
                    and int(session_owner) != int(sessionId)
                ) or (
                    session_owner is None
                    and binding_owner != str(sessionId)
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="截断目标不属于当前对话会话。",
                    )
                if not any(run["id"] == row["id"] for run in tail_runs):
                    tail_runs.append(row)
                conversation_id = row.get("conversation_id")
                if conversation_id is not None:
                    ids_to_delete.append(int(conversation_id))

        active_request = await db.fetch_one(
            "SELECT request_id FROM ai_writing_chat_requests "
            "WHERE session_id = ? AND status IN ('accepted', 'starting') LIMIT 1",
            [sessionId],
        )
        if active_request is not None:
            raise HTTPException(
                status_code=409,
                detail="会话已有待启动请求，请先完成或终止后再编辑。",
            )

        ids_to_delete = list(dict.fromkeys(ids_to_delete))
        retired_turn_ids = list(requested_turn_ids)
        if ids_to_delete:
            placeholders = ",".join("?" for _ in ids_to_delete)
            turn_rows = await db.fetch_all(
                "SELECT client_turn_id FROM ai_conversations "
                f"WHERE id IN ({placeholders}) AND session_id = ? "
                "AND client_turn_id IS NOT NULL",
                [*ids_to_delete, sessionId],
            )
            retired_turn_ids.extend(
                str(row["client_turn_id"])
                for row in turn_rows
                if str(row.get("client_turn_id") or "").strip()
            )
        retired_turn_ids = list(dict.fromkeys(retired_turn_ids))
        if requested_turn_ids:
            turn_placeholders = ",".join("?" for _ in requested_turn_ids)
            protected_conversation_turn = await db.fetch_one(
                "SELECT client_turn_id FROM ai_conversations "
                "WHERE session_id = ? "
                f"AND client_turn_id IN ({turn_placeholders}) "
                + (
                    f"AND id NOT IN ({','.join('?' for _ in ids_to_delete)}) "
                    if ids_to_delete
                    else ""
                )
                + "LIMIT 1",
                [sessionId, *requested_turn_ids, *ids_to_delete],
            )
            protected_turn = await db.fetch_one(
                "SELECT client_turn_id FROM ai_local_conversation_turn_receipts "
                "WHERE session_id = ? AND status = 'persisted' "
                f"AND client_turn_id IN ({turn_placeholders}) "
                + (
                    f"AND conversation_id NOT IN ({','.join('?' for _ in ids_to_delete)}) "
                    if ids_to_delete
                    else ""
                )
                + "LIMIT 1",
                [sessionId, *requested_turn_ids, *ids_to_delete],
            )
            if protected_conversation_turn is not None or protected_turn is not None:
                raise HTTPException(
                    status_code=409,
                    detail="本地对话截断目标不属于待删除历史。",
                )
        active_run = next(
            (
                run for run in tail_runs
                if str(run.get("status") or "") not in TERMINAL_RUN_STATUSES
            ),
            None,
        )
        if active_run is not None:
            raise HTTPException(
                status_code=409,
                detail="运行中的对话不能截断，请先完成或终止任务。",
            )
        tail_run_ids = [str(run["id"]) for run in tail_runs]
        if tail_run_ids:
            run_placeholders = ",".join("?" for _ in tail_run_ids)
            active_task = await db.fetch_one(
                "SELECT lt.id FROM ai_agent_long_tasks AS lt "
                "WHERE lt.status IN ('pending', 'queued', 'running', 'paused') "
                "AND ("
                f"lt.created_by_run_id IN ({run_placeholders}) OR EXISTS ("
                " SELECT 1 FROM ai_agent_long_task_runs AS ltr "
                " WHERE ltr.task_id = lt.id "
                f" AND ltr.run_id IN ({run_placeholders})"
                ")) LIMIT 1",
                [*tail_run_ids, *tail_run_ids],
            )
            if active_task is not None:
                raise HTTPException(
                    status_code=409,
                    detail="仍有长任务关联该对话，不能截断。",
                )
            await db.execute(
                "UPDATE ai_agent_long_tasks SET "
                "metadata_json = json_remove(metadata_json, '$.sessionId') "
                f"WHERE created_by_run_id IN ({run_placeholders}) OR EXISTS ("
                " SELECT 1 FROM ai_agent_long_task_runs AS ltr "
                " WHERE ltr.task_id = ai_agent_long_tasks.id "
                f" AND ltr.run_id IN ({run_placeholders})"
                ")",
                [*tail_run_ids, *tail_run_ids],
            )
            await db.execute(
                "UPDATE ai_writing_chat_requests SET status = 'canceled', "
                "cancel_requested_at_ms = COALESCE("
                "cancel_requested_at_ms, unixepoch('now') * 1000), "
                "rejection_code = COALESCE(rejection_code, 'history_truncated'), "
                "revision = revision + 1, update_time = CURRENT_TIMESTAMP "
                f"WHERE run_id IN ({run_placeholders}) "
                "AND status <> 'canceled'",
                tail_run_ids,
            )
        for turn_id in retired_turn_ids:
            await db.execute(
                "INSERT INTO ai_local_conversation_turn_receipts "
                "(session_id, client_turn_id, payload_digest, status, "
                "conversation_id) VALUES (?, ?, NULL, 'retired', NULL) "
                "ON CONFLICT(session_id, client_turn_id) DO UPDATE SET "
                "status = 'retired', conversation_id = NULL, "
                "revision = CASE WHEN status = 'retired' THEN revision "
                "ELSE revision + 1 END, "
                "update_time = CASE WHEN status = 'retired' THEN update_time "
                "ELSE CURRENT_TIMESTAMP END",
                [sessionId, turn_id],
            )
        report_clauses: list[str] = []
        report_params: list[Any] = []
        if ids_to_delete:
            placeholders = ",".join("?" for _ in ids_to_delete)
            report_clauses.append(f"conversation_id IN ({placeholders})")
            report_params.extend(ids_to_delete)
        if tail_run_ids:
            run_placeholders = ",".join("?" for _ in tail_run_ids)
            report_clauses.append(f"agent_run_id IN ({run_placeholders})")
            report_params.extend(tail_run_ids)
        if report_clauses:
            await db.execute(
                "UPDATE ai_error_reports SET session_id = NULL, "
                "conversation_id = NULL WHERE " + " OR ".join(report_clauses),
                report_params,
            )
        if tail_run_ids:
            run_placeholders = ",".join("?" for _ in tail_run_ids)
            await db.execute(
                "UPDATE ai_agent_runs SET session_id = NULL, "
                "conversation_id = NULL, update_time = CURRENT_TIMESTAMP "
                f"WHERE id IN ({run_placeholders})",
                tail_run_ids,
            )
        if ids_to_delete:
            placeholders = ",".join("?" for _ in ids_to_delete)
            from services.memory_deposition_service import (
                record_deleted_conversation_sources,
            )

            memory_delivery_keys = await record_deleted_conversation_sources(
                db,
                ids_to_delete,
            )
            await db.execute(
                f"DELETE FROM ai_conversations WHERE id IN ({placeholders}) "
                "AND session_id = ?",
                [*ids_to_delete, sessionId],
            )
        if ids_to_delete or tail_run_ids or retired_turn_ids:
            await db.execute(
                "DELETE FROM ai_conversation_summaries WHERE session_id = ?",
                [sessionId],
            )
    from services.memory_deposition_service import deliver_recorded

    deliveries = await deliver_recorded(db, memory_delivery_keys)
    return {
        "success": True,
        "memoryDelivery": [item.to_dict() for item in deliveries],
    }


def _csv_text(value: str | None) -> list[str]:
    if not isinstance(value, str):
        return []
    return list(dict.fromkeys(
        item.strip() for item in value.split(",") if item.strip()
    ))


def _csv_ints(value: str | None) -> list[int]:
    result: list[int] = []
    for item in _csv_text(value):
        try:
            parsed = int(item)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="无效的对话截断边界") from error
        if parsed > 0 and parsed not in result:
            result.append(parsed)
    return result
