"""Screenplay transport view over the canonical PurrA output journal."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from application.sse_mapping import canonical_output_to_sse_chunk
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
        chunks = []
        for cursor, event in page:
            if event.turn_id is None:
                continue
            chunk = canonical_output_to_sse_chunk(event)
            if chunk is None:
                continue
            turn = turns.get(event.turn_id, {})
            runtime_profile = _object(turn.get("runtime_profile_json"))
            chunks.append({
                "cursor": int(cursor),
                "turnId": event.turn_id,
                "taskId": str(turn.get("long_task_id") or "") or None,
                "runId": event.run_id,
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
        }

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


def _object(value: object) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


__all__ = ["ScreenplayCanonicalOutputQuery"]
