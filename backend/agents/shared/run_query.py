"""Version-aware canonical Run replay without changing legacy readers."""

from __future__ import annotations

from typing import Any

from agents.shared.implementation_registry import AgentLifecycleAction
from application.agent_run_queries import AgentRunQueryService


class VersionedAgentRunQueryService:
    """Annotate replay with the exact implementation that owns the Run."""

    def __init__(
        self,
        store,
        output_repository,
        implementation_router,
        *,
        product_event_query=None,
    ) -> None:
        self._legacy_query = AgentRunQueryService(
            store,
            output_repository,
            product_event_query=product_event_query,
        )
        self._implementation_router = implementation_router

    async def get_snapshot(
        self,
        run_id: str,
        *,
        after_event_id: int = 0,
        limit: int = 100,
    ) -> dict[str, Any] | None:
        snapshot = await self._legacy_query.get_snapshot(
            run_id,
            after_event_id=after_event_id,
            limit=limit,
        )
        if snapshot is None:
            return None
        route = await self._implementation_router.for_run(
            run_id,
            action=AgentLifecycleAction.REPLAY,
        )
        return {
            **snapshot,
            "implementation": {
                **route.identity.to_mapping(),
                "identitySource": route.identity_source,
                "runtimeProfileId": route.runtime_profile_id,
            },
        }


__all__ = ["VersionedAgentRunQueryService"]
