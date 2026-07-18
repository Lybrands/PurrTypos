"""SQLite adapter for durable Run checkpoint reads."""

from __future__ import annotations

from agent_core.contracts import RunCheckpoint
from infrastructure.persistence.run_store import (
    get_run,
    get_run_events_page,
    get_run_todos,
)
from infrastructure.persistence.sqlite_delegation_repository import (
    SqliteDelegationRepository,
)


class SqliteCheckpointStore:
    def __init__(self, db) -> None:
        self._db = db
        self._delegations = SqliteDelegationRepository(db)

    async def load(
        self,
        run_id: str,
        *,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> RunCheckpoint | None:
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
            steps = await get_run_todos(self._db, normalized_run_id)
            events, has_more = await get_run_events_page(
                self._db,
                normalized_run_id,
                after_id=normalized_after,
                limit=normalized_limit,
            )
            delegations = await self._delegations.list_for_parent(
                normalized_run_id,
            )
        next_cursor = (
            int(events[-1].get("id") or 0)
            if events
            else normalized_after
        )
        return RunCheckpoint(
            run=run,
            steps=tuple(steps),
            events=tuple(events),
            delegations=delegations,
            next_cursor=next_cursor,
            has_more=has_more,
        )
