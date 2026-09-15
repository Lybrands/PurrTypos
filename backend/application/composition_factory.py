"""Application bootstrap that installs product Agent profiles into Core."""

from __future__ import annotations

from functools import partial

from application.agent_composition import AgentComposition
from agents.shared.implementation_projector import (
    AgentImplementationBeginProjector,
)
from agents.shared.implementation import AgentKind
from agents.shared.implementation_registry import (
    AgentImplementationRegistry,
    AgentRolloutPolicy,
    legacy_implementation_profiles,
)
from agents.shared.composition_routing import (
    VersionedAgentProfileRegistry,
    VersionedAgentRequestRouter,
)
from agents.shared.implementation_router import (
    SqliteAgentImplementationRouter,
)
from agents.shared.cancellation import VersionRoutedRunCancellationProjector
from agents.writing.profile import (
    build_writing_replacement_profile,
    writing_replacement_implementation_profile,
)
from agents.novel_analysis.scalable_profile import (
    build_scalable_novel_analysis_profile,
    scalable_novel_analysis_implementation,
    scalable_novel_analysis_implementation_profile,
)
from agents.screenplay.profile import (
    build_screenplay_replacement_profile,
    screenplay_replacement_implementation_profile,
)
from agents.screenplay.conversation_projection import (
    ScreenplayReplacementRunBeginProjector,
    ScreenplayReplacementRunCancellationProjector,
    ScreenplayReplacementRunCommitProjector,
)


class _ChainedRunCommitProjector:
    def __init__(self, *projectors) -> None:
        self._projectors = tuple(projectors)

    async def project(self, run_id, event):
        for projector in self._projectors:
            projected = await projector.project(run_id, event)
            if projected is not None:
                raise TypeError("run commit projector must return None")


class _ChainedRunBeginProjector:
    def __init__(self, *projectors) -> None:
        self._projectors = tuple(projectors)

    async def project(self, run_id, params):
        for projector in self._projectors:
            projected = await projector.project(run_id, params)
            if projected is not None:
                raise TypeError("run begin projector must return None")


def create_agent_composition(
    db,
    **kwargs,
) -> AgentComposition:
    """Install product profiles without teaching generic composition domains."""

    rollout_policy = kwargs.pop("agent_rollout_policy", AgentRolloutPolicy())
    # None of the three legacy runtimes is a valid create target. A partial
    # host/test policy must not silently reopen a retired implementation.
    rollout_policy = AgentRolloutPolicy(
        frozenset({*rollout_policy.replacement_agent_kinds, *AgentKind}),
        tuple(
            item for item in rollout_policy.create_identity_overrides
            if item.agent_kind is not AgentKind.NOVEL_ANALYSIS
        ) + (scalable_novel_analysis_implementation(),),
    )
    implementation_profiles = [
        profile for profile in legacy_implementation_profiles()
        if profile.identity.agent_kind is not AgentKind.NOVEL_ANALYSIS
    ]
    implementation_profiles.extend((
        writing_replacement_implementation_profile(),
        scalable_novel_analysis_implementation_profile(),
        screenplay_replacement_implementation_profile(),
    ))
    implementation_registry = AgentImplementationRegistry(
        implementation_profiles
    )
    implementation_router = SqliteAgentImplementationRouter(
        db,
        implementation_registry,
        rollout_policy=rollout_policy,
    )
    for agent_kind in rollout_policy.replacement_agent_kinds:
        # A configuration flag must never claim replacement ownership unless
        # the exact implementation profile is installed in this process.
        implementation_router.for_create(agent_kind)

    supplied_projector = kwargs.pop("run_commit_projector", None)
    supplied_cancellation_projectors = tuple(
        kwargs.pop("run_cancellation_projectors", ())
    )
    screenplay_projectors = (ScreenplayReplacementRunCommitProjector(db),)
    run_commit_projector = (
        _ChainedRunCommitProjector(*screenplay_projectors, supplied_projector)
        if supplied_projector is not None
        else _ChainedRunCommitProjector(*screenplay_projectors)
    )
    composition = AgentComposition(
        db,
        run_begin_projector=_ChainedRunBeginProjector(
            AgentImplementationBeginProjector(db),
            ScreenplayReplacementRunBeginProjector(db),
        ),
        run_commit_projector=run_commit_projector,
        run_cancellation_projectors=(
            VersionRoutedRunCancellationProjector(
                implementation_router,
                {
                    "screenplay.purra-native.v1": (
                        ScreenplayReplacementRunCancellationProjector(db),
                    ),
                },
            ),
            *supplied_cancellation_projectors,
        ),
        profile_factories=(
            build_writing_replacement_profile,
            build_scalable_novel_analysis_profile,
            build_screenplay_replacement_profile,
        ),
        profile_registry_factory=partial(
            VersionedAgentProfileRegistry,
            default_profile_ids={
                "purrtypos.writing": "writing.purra-native.v1",
                "purrtypos.novel_analysis": (
                    "novel_analysis.scalable.v2"
                ),
                "purrtypos.screenplay": "screenplay.purra-native.v1",
            },
        ),
        request_profile_router=VersionedAgentRequestRouter(
            implementation_router
        ),
        **kwargs,
    )
    composition.agent_implementation_router = implementation_router
    return composition


def create_versioned_agent_composition(db, **kwargs) -> AgentComposition:
    """Install the single production runtime for every product Agent.

    Novel Analysis has no legacy runtime or registry tombstone. Its production
    read and execution paths both resolve only scalable v2.
    """

    return create_agent_composition(db, **kwargs)


__all__ = ["create_agent_composition", "create_versioned_agent_composition"]
