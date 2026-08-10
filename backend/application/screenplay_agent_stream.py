"""Backend projection into the shared Agent conversation chunk protocol."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from purra.events import AgentEvent, CoreEventType
from application.screenplay_progress_stream import visible_stream_chunks


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
        if await self._db.fetch_one(
            "SELECT 1 FROM screenplay_agent_chunks WHERE turn_id = ? LIMIT 1",
            [turn_id],
        ):
            return
        turn, _task, _units = await self._state(turn_id)
        await self._append(turn, {
            "agentRunStarted": {
                "runId": turn_id,
                "status": "running",
                "goal": str(turn["user_content"]),
            },
        })

    async def plan(self, turn_id: str) -> None:
        turn, task, units = await self._state(turn_id)
        if task is None:
            return
        await self._append_plan(turn, task, units)

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
        await self._append_plan(turn, task, units)
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
        await self.plan(turn_id)
        status = _effective_status(turn, task)
        if status == "failed":
            error = _error_message(
                (task or {}).get("error_json")
                or turn.get("operation_error_json")
            )
            await self._append(turn, {"error": error or "剧本任务执行失败。"}, task_id=(
                str((task or {}).get("id") or "") or None
            ))
            return
        if task is not None and status == "completed":
            summary = str(turn.get("assistant_content") or "").strip()
            if summary:
                for fragment in visible_stream_chunks(summary):
                    await self._append(
                        turn,
                        {"delta": fragment},
                        task_id=str(task["id"]),
                    )
        await self._append(
            turn,
            {
                "done": True,
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
                            "code": unit.get("error_code"),
                            "message": unit.get("error_code"),
                        }, ensure_ascii=False)
                        if unit.get("error_code")
                        else None
                    ),
                })
            return turn, task_view, units
        return turn, None, []

    async def _append_plan(self, turn, task, units) -> None:
        await self._append(
            turn,
            {
                "agentRunTodosUpdated": {
                    "runId": str(turn["id"]),
                    "title": str(turn["user_content"]),
                    "goal": str(turn["user_content"]),
                    "status": _plan_status(_effective_status(turn, task)),
                    "steps": [
                        *[
                            {
                                "id": str(unit["unit_id"]),
                                "title": _unit_label(unit, task),
                                "type": (
                                    "review"
                                    if unit["kind"] == "publish_candidate_revision"
                                    else "write"
                                ),
                                "status": _unit_status(str(unit["status"])),
                                "executor": (
                                    "tool"
                                    if unit["kind"] == "publish_candidate_revision"
                                    else "model"
                                ),
                                **(
                                    {"error": _error_message(unit.get("error_json"))}
                                    if unit.get("error_json")
                                    else {}
                                ),
                            }
                            for unit in units
                        ],
                    ],
                },
            },
            task_id=str((task or {}).get("id") or "") or None,
        )

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


def _plan_status(status: str) -> str:
    return {
        "queued": "planned",
        "paused": "paused",
        "completed": "done",
        "failed": "failed",
        "canceled": "canceled",
    }.get(status, "running")


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


def _unit_status(status: str) -> str:
    return {
        "claimed": "running",
        "completed": "done",
        "failed": "failed",
        "canceled": "blocked",
    }.get(status, status)


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
    if kind == "generate_candidate":
        return (
            f"创作第 {episode_number} 集候选稿"
            if episode_number
            else f"生成{target}候选稿"
        )
    if kind == "validate_candidate":
        return (
            f"校验第 {episode_number} 集候选稿"
            if episode_number
            else f"校验{target}候选稿"
        )
    if kind == "generate_episode_draft":
        return f"创作第 {episode_number} 集正文"
    if kind == "generate_deliverable":
        return f"生成{target}"
    if kind == "publish_candidate_revision":
        return "整理并发布候选稿"
    return "执行剧本任务"

def _error_message(value: object) -> str:
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return ""
    return str(payload.get("message") or "") if isinstance(payload, dict) else ""


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
