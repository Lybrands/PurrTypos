"""Application bootstrap that installs product Agent profiles into Core."""

from __future__ import annotations

from application.agent_composition import AgentComposition
from application.agent_profile_registry import AgentProfileRegistration
from domains.screenplay_agent.adapter import ScreenplayDomainAdapter
from domains.screenplay_agent.agent_context import (
    SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
)
from infrastructure.screenplay import (
    ScreenplayCandidateCompletionProjector,
    build_screenplay_tool_catalog,
)


class _ChainedEventProjector:
    def __init__(self, *projectors) -> None:
        self._projectors = tuple(projectors)

    async def project(self, run_id, event):
        current = event
        replaced = False
        for projector in self._projectors:
            projected = await projector.project(run_id, current)
            if projected is not None:
                current = projected
                replaced = True
        return current if replaced else None


def create_agent_composition(
    db,
    **kwargs,
) -> AgentComposition:
    """Install product profiles without teaching generic composition domains."""

    registrations = tuple(kwargs.pop("profile_registrations", ()))
    supplied_projector = kwargs.pop("event_projector", None)
    screenplay_projector = ScreenplayCandidateCompletionProjector(db)
    event_projector = (
        _ChainedEventProjector(screenplay_projector, supplied_projector)
        if supplied_projector is not None
        else screenplay_projector
    )
    return AgentComposition(
        db,
        event_projector=event_projector,
        profile_registrations=(
            *registrations,
            AgentProfileRegistration(
                id="screenplay",
                domain_namespace=SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
                adapter=ScreenplayDomainAdapter(
                    tool_catalog=build_screenplay_tool_catalog(db=db),
                ),
            ),
        ),
        **kwargs,
    )


__all__ = ["create_agent_composition"]
