"""Isolated composition root for scalable Novel Analysis acceptance."""

from __future__ import annotations

from agents.novel_analysis.scalable_profile import (
    build_scalable_novel_analysis_profile,
    scalable_novel_analysis_implementation,
    scalable_novel_analysis_implementation_profile,
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


def create_isolated_scalable_novel_analysis_composition(
    db,
    **kwargs,
) -> AgentComposition:
    """Install only scalable v2 without changing the production registry."""

    registry = AgentImplementationRegistry((
        scalable_novel_analysis_implementation_profile(),
    ))
    router = SqliteAgentImplementationRouter(
        db,
        registry,
        rollout_policy=AgentRolloutPolicy(
            frozenset({AgentKind.NOVEL_ANALYSIS}),
            (scalable_novel_analysis_implementation(),),
        ),
    )
    supplied = tuple(kwargs.pop("run_cancellation_projectors", ()))
    composition = AgentComposition(
        db,
        run_begin_projector=AgentImplementationBeginProjector(db),
        run_cancellation_projectors=(
            VersionRoutedRunCancellationProjector(router, {}),
            *supplied,
        ),
        profile_factories=(build_scalable_novel_analysis_profile,),
        **kwargs,
    )
    composition.agent_implementation_router = router
    return composition


__all__ = [
    "create_isolated_scalable_novel_analysis_composition",
]
