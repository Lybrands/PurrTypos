"""Isolated composition root for deterministic Writing replacement acceptance."""

from __future__ import annotations

from agents.shared.cancellation import VersionRoutedRunCancellationProjector
from agents.shared.implementation import AgentKind
from agents.shared.implementation_projector import AgentImplementationBeginProjector
from agents.shared.implementation_registry import (
    AgentImplementationRegistry,
    AgentRolloutPolicy,
)
from agents.shared.implementation_router import SqliteAgentImplementationRouter
from agents.writing.profile import (
    build_writing_replacement_profile,
    writing_replacement_implementation_profile,
)
from application.agent_composition import AgentComposition


def create_isolated_writing_replacement_composition(db, **kwargs) -> AgentComposition:
    """Build only the new Writing profile; never use this to open legacy Runs."""

    registry = AgentImplementationRegistry((
        writing_replacement_implementation_profile(),
    ))
    router = SqliteAgentImplementationRouter(
        db,
        registry,
        rollout_policy=AgentRolloutPolicy(frozenset({AgentKind.WRITING})),
    )
    supplied_cancellation_projectors = tuple(
        kwargs.pop("run_cancellation_projectors", ())
    )
    composition = AgentComposition(
        db,
        run_begin_projector=AgentImplementationBeginProjector(db),
        run_cancellation_projectors=(
            VersionRoutedRunCancellationProjector(router, {}),
            *supplied_cancellation_projectors,
        ),
        profile_factories=(build_writing_replacement_profile,),
        **kwargs,
    )
    composition.agent_implementation_router = router
    return composition


__all__ = ["create_isolated_writing_replacement_composition"]
