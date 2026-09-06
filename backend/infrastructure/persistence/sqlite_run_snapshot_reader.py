"""SQLite reader for persisted Agent Run snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from infrastructure.persistence.run_store import (
    get_run,
    get_run_events_page,
    get_run_todos,
)
from infrastructure.persistence.model_input_diagnostics import _mapping


class SqliteRunSnapshotReader:
    def __init__(self, db) -> None:
        self._db = db

    async def load(
        self,
        run_id: str,
        *,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> "PersistedRunSnapshot" | None:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("run id is required")
        normalized_after = int(after_event_id)
        normalized_limit = int(limit)
        if normalized_after < 0:
            raise ValueError("event cursor must be non-negative")
        if normalized_limit < 1 or normalized_limit > 500:
            raise ValueError("event page limit must be between 1 and 500")

        async with self._db.transaction(write=False):
            run = await get_run(self._db, normalized_run_id)
            if run is None:
                return None
            if not run.get("model_name") or not run.get("model_provider"):
                observed = await self._db.fetch_one(
                    "SELECT payload_json FROM ai_agent_run_events "
                    "WHERE run_id = ? AND event_type = 'stream.opened' ORDER BY id LIMIT 1",
                    [normalized_run_id],
                )
                receipt = _mapping(observed["payload_json"]) if observed else {}
                calls = receipt.get("callParameters") or []
                parameters = _mapping(calls[0]) if isinstance(calls, list) and calls else {}
                run = {
                    **run,
                    "model_name": run.get("model_name") or parameters.get("model") or receipt.get("model"),
                    "model_provider": run.get("model_provider") or parameters.get("provider"),
                }
            steps = await get_run_todos(self._db, normalized_run_id)
            events, has_more = await get_run_events_page(
                self._db,
                normalized_run_id,
                after_id=normalized_after,
                limit=normalized_limit,
            )
        next_cursor = (
            int(events[-1].get("id") or 0)
            if events
            else normalized_after
        )
        return PersistedRunSnapshot(
            run=run,
            steps=tuple(steps),
            events=tuple(events),
            delegations=(),
            next_cursor=next_cursor,
            has_more=has_more,
        )


@dataclass(frozen=True, slots=True)
class PersistedRunSnapshot:
    """Backend read model assembled from persisted Run state."""

    run: Mapping[str, Any]
    steps: tuple[Mapping[str, Any], ...]
    events: tuple[Mapping[str, Any], ...]
    delegations: tuple[Any, ...]
    next_cursor: int
    has_more: bool
