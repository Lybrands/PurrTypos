"""Screenplay transport view over the canonical PurrA output journal."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from application.sse_mapping import canonical_output_to_sse_chunk
from application.agent_event_stream import projection_version
from domains.screenplay_agent.tools.catalog import screenplay_tool_display_names
from exceptions import NotFoundError


class ScreenplayCanonicalOutputQuery:
    """Enrich generic session output with screenplay Turn presentation data."""

    def __init__(self, db, *, output_repository) -> None:
        self._db = db
        self._output = output_repository

    async def list_chunks(
        self,
        *,
        project_id: str,
        session_id: int,
        after: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        session = await self._db.fetch_one(
            "SELECT id FROM ai_sessions WHERE id = ? "
            "AND screenplay_project_id = ? AND scope = 'screenplay'",
            [int(session_id), str(project_id)],
        )
        if session is None:
            raise NotFoundError("剧本对话不存在")

        rows = await self._output.list_session_events(
            session_id=int(session_id),
            after_cursor=max(0, int(after)),
            limit=int(limit) + 1,
        )
        page = rows[:limit]
        turns = await self._turn_metadata(
            tuple(
                event.turn_id
                for _cursor, event in page
                if event.turn_id is not None
            )
        )
        run_roles = await self._run_roles(tuple(event.run_id for _, event in page))
        chunks = []
        for cursor, event in page:
            if event.turn_id is None:
                continue
            chunk = canonical_output_to_sse_chunk(event)
            if chunk is None:
                continue
            chunk = _with_screenplay_tool_display_names(chunk)
            turn = turns.get(event.turn_id, {})
            runtime_profile = _object(turn.get("runtime_profile_json"))
            chunks.append({
                "cursor": int(cursor),
                "turnId": event.turn_id,
                "taskId": str(turn.get("long_task_id") or "") or None,
                "runId": event.run_id,
                "runRole": run_roles.get(event.run_id, "related"),
                "userContent": str(turn.get("user_content") or ""),
                "model": str(runtime_profile.get("model") or "") or None,
                "turnCreatedAt": turn.get("turn_create_time"),
                "chunk": chunk,
                "createdAt": event.emitted_at.isoformat(),
            })
        return {
            "chunks": chunks,
            "nextCursor": int(page[-1][0]) if page else max(0, int(after)),
            "hasMore": len(rows) > limit,
            "projectionVersion": await self.projection_version(session_id),
        }

    async def projection_version(self, session_id: int) -> str:
        # Exclude assistant text, usage and heartbeat timestamps: canonical
        # text deltas must not invalidate the full business projection.
        rows = await self._db.fetch_all(
            "SELECT t.id, t.status, t.attempt, t.error_json, t.cancel_requested_at_ms, "
            "t.operation_id, t.result_revision_id AS turn_result_revision_id, "
            "r.id AS root_run_id, "
            "o.id AS operation_id, o.revision, o.status AS operation_status, "
            "o.result_revision_id, o.cancel_requested_at_ms, "
            "task.revision AS task_revision "
            "FROM screenplay_agent_turns t "
            "LEFT JOIN ai_agent_runs r ON r.binding_namespace = 'screenplay.conversation_turn' "
            "AND r.binding_aggregate_id = t.project_id AND r.binding_command_id = t.command_id "
            "LEFT JOIN screenplay_agent_operations o ON o.turn_id = t.id "
            "LEFT JOIN ai_agent_long_tasks task ON task.id = o.long_task_id "
            "WHERE t.session_id = ? ORDER BY t.rowid, r.rowid",
            [session_id],
        )
        return projection_version(rows)

    async def _turn_metadata(
        self,
        turn_ids: tuple[str, ...],
    ) -> dict[str, Mapping[str, Any]]:
        unique_ids = tuple(dict.fromkeys(turn_ids))
        if not unique_ids:
            return {}
        marks = ",".join("?" for _ in unique_ids)
        rows = await self._db.fetch_all(
            "SELECT t.id, t.user_content, t.runtime_profile_json, "
            "t.create_time AS turn_create_time, o.long_task_id "
            "FROM screenplay_agent_turns AS t "
            "LEFT JOIN screenplay_agent_operations AS o ON o.turn_id = t.id "
            f"WHERE t.id IN ({marks})",
            list(unique_ids),
        )
        return {str(row["id"]): row for row in rows}

    async def _run_roles(self, run_ids: tuple[str, ...]) -> dict[str, str]:
        unique_ids = tuple(dict.fromkeys(str(value) for value in run_ids if value))
        if not unique_ids:
            return {}
        marks = ",".join("?" for _ in unique_ids)
        rows = await self._db.fetch_all(
            "SELECT id, binding_namespace FROM ai_agent_runs "
            f"WHERE id IN ({marks})",
            list(unique_ids),
        )
        roles = {
            "screenplay.conversation_turn": "root",
            "screenplay.agent.task": "unit",
            "screenplay.agent.final_response": "final_response",
        }
        return {
            str(row["id"]): roles.get(
                str(row.get("binding_namespace") or ""),
                "related",
            )
            for row in rows
        }


def _object(value: object) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _with_screenplay_tool_display_names(
    chunk: dict[str, Any],
) -> dict[str, Any]:
    if chunk.get("kind") != "operation.started":
        return chunk
    payload = chunk.get("payload")
    if not isinstance(payload, Mapping) or payload.get("kind") != "tool":
        return chunk
    display = payload.get("display")
    if not isinstance(display, Mapping):
        return chunk
    label_params = display.get("labelParams")
    if not isinstance(label_params, Mapping):
        return chunk
    tool_name = str(label_params.get("toolName") or "").strip()
    episode_number = label_params.get("episodeNumber")
    if (
        isinstance(episode_number, bool)
        or not isinstance(episode_number, int)
        or episode_number <= 0
    ):
        episode_number = None
    task_episode_number = label_params.get("taskEpisodeNumber")
    if (
        isinstance(task_episode_number, bool)
        or not isinstance(task_episode_number, int)
        or task_episode_number <= 0
    ):
        task_episode_number = None
    role = label_params.get("deliverableRole")
    targets = label_params.get("readTargets")
    query = label_params.get("searchQuery")
    display_names = screenplay_tool_display_names(
        tool_name,
        episode_number=episode_number,
        deliverable_role=role if isinstance(role, str) else None,
        task_episode_number=task_episode_number,
        read_targets=tuple(value for value in targets if isinstance(value, str))
        if isinstance(targets, (list, tuple)) else (),
        search_query=query if isinstance(query, str) else None,
    )
    if not display_names:
        return chunk
    return {
        **chunk,
        "payload": {
            **payload,
            "display": {
                **display,
                "labelParams": {
                    **label_params,
                    "displayNames": display_names,
                },
            },
        },
    }


__all__ = ["ScreenplayCanonicalOutputQuery"]
