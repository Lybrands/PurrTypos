"""Backend projection into the shared Agent conversation chunk protocol."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from purra.events import AgentEvent, CoreEventType
from purra.output import AgentOutputEvent
from application.sse_mapping import canonical_output_to_sse_chunk


class ScreenplayAgentChunkStore:
    def __init__(self, db) -> None:
        self._db = db

    async def append(
        self,
        *,
        project_id: str,
        session_id: int,
        turn_id: str,
        chunk: Mapping[str, Any],
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        await self._db.execute(
            "INSERT INTO screenplay_agent_chunks "
            "(project_id, session_id, turn_id, task_id, run_id, "
            "protocol_version, chunk_json) VALUES (?, ?, ?, ?, ?, 2, ?)",
            [
                project_id,
                int(session_id),
                turn_id,
                task_id,
                run_id,
                json.dumps(dict(chunk), ensure_ascii=False, separators=(",", ":")),
            ],
        )

    async def append_output_event(
        self,
        *,
        project_id: str,
        session_id: int,
        turn_id: str,
        event: AgentOutputEvent,
        task_id: str | None = None,
    ) -> None:
        """Persist one raw public PurrA event for live delivery and replay."""

        chunk = canonical_output_to_sse_chunk(event)
        if chunk is None:
            return
        await self.append(
            project_id=project_id,
            session_id=session_id,
            turn_id=turn_id,
            task_id=task_id,
            run_id=event.run_id,
            chunk=chunk,
        )

    async def list_chunks(
        self,
        *,
        project_id: str,
        session_id: int,
        after: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        rows = await self._db.fetch_all(
            "SELECT c.*, t.user_content, t.runtime_profile_json, "
            "t.create_time AS turn_create_time "
            "FROM screenplay_agent_chunks AS c "
            "JOIN screenplay_agent_turns AS t ON t.id = c.turn_id "
            "WHERE c.project_id = ? AND c.session_id = ? "
            "AND c.protocol_version = 2 AND c.id > ? "
            "ORDER BY c.id LIMIT ?",
            [project_id, int(session_id), max(0, int(after)), int(limit) + 1],
        )
        page = rows[:limit]
        return {
            "chunks": [self._view(row) for row in page],
            "nextCursor": int(page[-1]["id"]) if page else max(0, int(after)),
            "hasMore": len(rows) > limit,
        }

    @staticmethod
    def _view(row: Mapping[str, Any]) -> dict[str, Any]:
        try:
            chunk = json.loads(str(row.get("chunk_json") or "{}"))
        except (TypeError, json.JSONDecodeError):
            chunk = {}
        runtime_profile = _object(row.get("runtime_profile_json"))
        return {
            "cursor": int(row["id"]),
            "turnId": str(row["turn_id"]),
            "taskId": str(row.get("task_id") or "") or None,
            "runId": str(row.get("run_id") or "") or None,
            "userContent": str(row.get("user_content") or ""),
            "model": str(runtime_profile.get("model") or "") or None,
            "turnCreatedAt": row.get("turn_create_time"),
            "chunk": chunk if isinstance(chunk, dict) else {},
            "createdAt": row.get("create_time"),
        }


class ScreenplayAgentChunkProjector:
    """Project Turn and PurrA task state into shared Agent chunks."""

    def __init__(self, db) -> None:
        self._db = db
        self._chunks = ScreenplayAgentChunkStore(db)

    async def started(self, turn_id: str) -> None:
        # A Turn is a durable host container, not a synthetic Agent Run.
        # The first visible execution event must come from a submitted PurrA Run.
        await self._state(turn_id)

    async def plan(self, turn_id: str) -> None:
        # PurrA runtime events are the only source of Agent plan presentation.
        await self._state(turn_id)

    async def task_progress(self, turn_id: str, event: AgentEvent) -> None:
        if event.type != CoreEventType.LONG_TASK_PROGRESS:
            return
        turn, task, units = await self._state(turn_id)
        if task is None:
            raise RuntimeError("screenplay task progress has no durable task")
        task_id = str(task["id"])
        event_task_id = str(event.payload.get("taskId") or "")
        if event_task_id != task_id:
            raise RuntimeError("screenplay task progress belongs to another task")
        labels = {
            str(unit["unit_id"]): _unit_label(unit, task)
            for unit in units
        }
        raw_units = event.payload.get("units")
        progress_units = [
            {
                **dict(raw),
                "title": labels.get(str(raw.get("id") or ""), "执行剧本任务"),
            }
            for raw in raw_units
            if isinstance(raw, Mapping)
        ] if isinstance(raw_units, Sequence) and not isinstance(
            raw_units,
            (str, bytes, bytearray),
        ) else []
        await self._append(
            turn,
            {
                "longTaskProgress": {
                    "runId": turn_id,
                    **dict(event.payload),
                    "units": progress_units,
                },
            },
            task_id=task_id,
        )

    async def terminal(self, turn_id: str) -> None:
        turn, task, _units = await self._state(turn_id)
        status = _effective_status(turn, task)
        if status == "failed":
            await self._append(turn, {
                "done": True,
                "runResult": {
                    "runId": turn_id,
                    "status": "failed",
                    "errorCode": _error_code(
                        (task or {}).get("error_json")
                        or turn.get("operation_error_json")
                    ),
                },
            }, task_id=str((task or {}).get("id") or "") or None)
            return
        await self._append(
            turn,
            {
                "done": True,
                "runResult": {
                    "runId": turn_id,
                    "status": (
                        "done" if status == "completed" else status
                    ),
                    "errorCode": None,
                },
                **(
                    {"finalResponseExpected": False}
                    if status == "paused" else {}
                ),
                **({"aborted": True} if status == "canceled" else {}),
            },
            task_id=str((task or {}).get("id") or "") or None,
        )

    async def _state(self, turn_id: str):
        turn = await self._db.fetch_one(
            "SELECT t.*, o.status AS operation_status, "
            "o.long_task_id AS authoritative_task_id, "
            "o.target_role AS operation_target_role, "
            "COALESCE(o.error_json, (SELECT json_extract(e.payload_json, '$.error') "
            "FROM screenplay_agent_events AS e WHERE e.turn_id = t.id "
            "AND e.event_type IN ('screenplay.agent.turn.failed', "
            "'screenplay.agent.task.failed', 'screenplay.agent.task.paused') "
            "ORDER BY e.id DESC LIMIT 1)) AS operation_error_json "
            "FROM screenplay_agent_turns AS t "
            "LEFT JOIN screenplay_agent_operations AS o ON o.turn_id = t.id "
            "WHERE t.id = ?",
            [turn_id],
        )
        if turn is None:
            raise RuntimeError("screenplay Agent Turn does not exist")
        task_id = str(turn.get("authoritative_task_id") or "").strip()
        if task_id:
            task = await self._db.fetch_one(
                "SELECT * FROM ai_agent_long_tasks WHERE id = ?",
                [task_id],
            )
            if task is None:
                raise RuntimeError("screenplay Agent durable task does not exist")
            metadata = _object(task.get("metadata_json"))
            task_view = {
                **task,
                "status": (
                    "completed"
                    if turn.get("operation_status") == "succeeded"
                    else str(turn.get("operation_status") or task["status"])
                ),
                "target_role": (
                    turn.get("operation_target_role")
                    or metadata.get("targetRole")
                ),
                "error_json": turn.get("operation_error_json"),
            }
            unit_rows = await self._db.fetch_all(
                "SELECT * FROM ai_agent_long_task_units "
                "WHERE task_id = ? ORDER BY position",
                [task_id],
            )
            units = []
            for unit in unit_rows:
                unit_metadata = _object(unit.get("metadata_json"))
                error_code = str(unit.get("error_code") or "")
                units.append({
                    "unit_id": unit["unit_id"],
                    "kind": unit_metadata.get("unitKind"),
                    "status": unit["status"],
                    "input_json": json.dumps(
                        unit_metadata.get("input") or {},
                        ensure_ascii=False,
                    ),
                    "error_json": (
                        json.dumps({
                            "code": error_code,
                            "message": _unit_failure_message(
                                unit_metadata,
                                error_code,
                            ),
                        }, ensure_ascii=False)
                        if error_code
                        else None
                    ),
                })
            return turn, task_view, units
        return turn, None, []

    async def _append(
        self,
        turn: Mapping[str, Any],
        chunk: Mapping[str, Any],
        *,
        task_id: str | None = None,
    ) -> None:
        await self._chunks.append(
            project_id=str(turn["project_id"]),
            session_id=int(turn["session_id"]),
            turn_id=str(turn["id"]),
            task_id=task_id,
            chunk=chunk,
        )


def _effective_status(
    turn: Mapping[str, Any],
    task: Mapping[str, Any] | None,
) -> str:
    turn_status = str(turn["status"])
    return (
        turn_status
        if turn_status in {"failed", "canceled"}
        else str((task or turn)["status"])
    )


def _error_code(value: Any) -> str:
    payload = _object(value)
    return str(payload.get("code") or "screenplay_task_failed")


_ROLE_LABELS = {
    "sourceAnalysis": "原作分析",
    "creativeBrief": "创作简报",
    "structure": "分集结构",
    "sceneList": "场景表",
    "screenplayDraft": "剧本正文",
    "review": "审阅报告",
}


def _unit_label(unit: Mapping[str, Any], task: Mapping[str, Any] | None) -> str:
    kind = str(unit["kind"])
    payload = _object(unit.get("input_json"))
    episode_number = int(payload.get("episodeNumber") or 0)
    role = str((task or {}).get("target_role") or "")
    target = _ROLE_LABELS.get(role, "剧本交付物")
    if kind == "compose_final_response":
        return "整理最终答复"
    if kind == "collect_evidence":
        return (
            f"整理第 {episode_number} 集创作依据"
            if episode_number
            else f"整理{target}创作依据"
        )
    if kind == "generate_draft_scene":
        return f"创作第 {episode_number} 集场景 {payload.get('sceneId') or ''}".strip()
    if kind == "generate_episode_metadata":
        return f"整理第 {episode_number} 集连续性"
    if kind == "generate_review_dimension":
        dimensions = {
            "continuity": "连贯性",
            "character_arc": "人物弧光",
            "structure_rhythm": "结构节奏",
            "dialogue": "对白",
            "format": "格式",
        }
        dimension = dimensions.get(
            str(payload.get("reviewDimension") or ""),
            "专项",
        )
        return f"审阅第 {episode_number} 集{dimension}"
    if kind == "generate_document_section":
        return f"生成{target}章节 {payload.get('sectionKey') or ''}".strip()
    if kind == "validate_manifest_part":
        return (
            f"校验第 {episode_number} 集完整性"
            if episode_number
            else f"校验{target}完整性"
        )
    return "执行剧本任务"


def _unit_failure_message(metadata: Mapping[str, Any], error_code: str) -> str:
    unit_input = metadata.get("input")
    episode_number = (
        int(unit_input.get("episodeNumber") or 0)
        if isinstance(unit_input, Mapping)
        else 0
    )
    kind = str(metadata.get("unitKind") or "")
    if episode_number > 0 and kind in {
        "generate_review_dimension",
        "validate_manifest_part",
    }:
        return f"第 {episode_number} 集审阅失败"
    return error_code


def _object(value: object) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


__all__ = [
    "ScreenplayAgentChunkProjector",
    "ScreenplayAgentChunkStore",
]
