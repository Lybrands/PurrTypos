"""Isolated composition root for Screenplay replacement acceptance."""

from __future__ import annotations

from agents.screenplay.profile import (
    build_screenplay_replacement_profile,
    screenplay_replacement_implementation_profile,
)
from agents.shared.cancellation import VersionRoutedRunCancellationProjector
from agents.shared.implementation import AgentKind
from agents.shared.implementation_projector import AgentImplementationBeginProjector
from agents.shared.implementation_registry import (
    AgentImplementationRegistry,
    AgentRolloutPolicy,
)
from agents.shared.implementation_router import SqliteAgentImplementationRouter
from agents.screenplay.conversation_projection import (
    ScreenplayReplacementRunBeginProjector,
    ScreenplayReplacementRunCancellationProjector,
    ScreenplayReplacementRunCommitProjector,
)
from application.agent_composition import AgentComposition


class _ProjectorChain:
    def __init__(self, *projectors) -> None:
        self._projectors = projectors

    async def project(self, run_id, value):
        for projector in self._projectors:
            result = await projector.project(run_id, value)
            if result is not None:
                raise TypeError("Screenplay replacement projector must return None")


def create_isolated_screenplay_replacement_composition(
    db,
    **kwargs,
) -> AgentComposition:
    """Install only the new profile; production rollout remains unchanged."""

    registry = AgentImplementationRegistry((
        screenplay_replacement_implementation_profile(),
    ))
    router = SqliteAgentImplementationRouter(
        db,
        registry,
        rollout_policy=AgentRolloutPolicy(frozenset({AgentKind.SCREENPLAY})),
    )
    supplied = tuple(kwargs.pop("run_cancellation_projectors", ()))
    composition = AgentComposition(
        db,
        run_begin_projector=_ProjectorChain(
            AgentImplementationBeginProjector(db),
            ScreenplayReplacementRunBeginProjector(db),
        ),
        run_commit_projector=ScreenplayReplacementRunCommitProjector(db),
        run_cancellation_projectors=(
            VersionRoutedRunCancellationProjector(
                router,
                {
                    "screenplay.purra-native.v1": (
                        ScreenplayReplacementRunCancellationProjector(db),
                    ),
                },
            ),
            *supplied,
        ),
        profile_factories=(build_screenplay_replacement_profile,),
        **kwargs,
    )
    composition.agent_implementation_router = router
    return composition


__all__ = ["create_isolated_screenplay_replacement_composition"]
