"""Isolated composition root for Novel Analysis replacement acceptance."""

from __future__ import annotations

from agents.novel_analysis.profile import (
    build_novel_analysis_replacement_profile,
    novel_analysis_replacement_implementation_profile,
)
from agents.shared.cancellation import VersionRoutedRunCancellationProjector
from agents.shared.implementation import AgentKind
from agents.shared.implementation_projector import AgentImplementationBeginProjector
from agents.shared.implementation_registry import (
    AgentImplementationRegistry,
    AgentRolloutPolicy,
)
from agents.shared.implementation_router import SqliteAgentImplementationRouter
from application.agent_composition import AgentComposition


def create_isolated_novel_analysis_replacement_composition(
    db,
    **kwargs,
) -> AgentComposition:
    """Install only the new profile; production routing remains unchanged."""

    registry = AgentImplementationRegistry((
        novel_analysis_replacement_implementation_profile(),
    ))
    router = SqliteAgentImplementationRouter(
        db,
        registry,
        rollout_policy=AgentRolloutPolicy(frozenset({AgentKind.NOVEL_ANALYSIS})),
    )
    supplied = tuple(kwargs.pop("run_cancellation_projectors", ()))
    composition = AgentComposition(
        db,
        run_begin_projector=AgentImplementationBeginProjector(db),
        run_cancellation_projectors=(
            VersionRoutedRunCancellationProjector(router, {}),
            *supplied,
        ),
        profile_factories=(build_novel_analysis_replacement_profile,),
        **kwargs,
    )
    composition.agent_implementation_router = router
    return composition


__all__ = ["create_isolated_novel_analysis_replacement_composition"]
