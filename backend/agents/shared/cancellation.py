"""Version-routed product projection for the shared cancellation plane."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agents.shared.implementation_registry import AgentLifecycleAction


class VersionRoutedRunCancellationProjector:
    """Dispatch domain cancellation effects by immutable Run implementation."""

    def __init__(
        self,
        implementation_router,
        projectors_by_runtime_profile: Mapping[str, Sequence[object]],
    ) -> None:
        self._router = implementation_router
        self._projectors = {
            str(profile_id): tuple(projectors)
            for profile_id, projectors in projectors_by_runtime_profile.items()
        }

    async def project(self, run_id, receipt) -> None:
        if not await self._router.should_route(run_id):
            return None
        route = await self._router.for_run(
            run_id,
            action=AgentLifecycleAction.CANCEL,
        )
        for projector in self._projectors.get(route.runtime_profile_id, ()):
            projected = await projector.project(run_id, receipt)
            if projected is not None:
                raise TypeError("Run cancellation projector must return None")
        return None


__all__ = ["VersionRoutedRunCancellationProjector"]
