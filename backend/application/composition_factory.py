"""Application bootstrap that installs product Agent profiles into Core."""

from __future__ import annotations

from application.agent_composition import AgentComposition
from application.agent_profile_registry import (
    AgentProfileRegistration,
    StaticAgentProfileExtension,
)
from application.writing_agent_profile import build_writing_profile_extension
from domains.screenplay_agent.adapter import ScreenplayDomainAdapter
from domains.screenplay_agent.agent_context import (
    SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
)
from infrastructure.screenplay import (
    ScreenplayCandidateCompletionProjector,
    build_screenplay_tool_catalog,
)


class _ChainedRunCommitProjector:
    def __init__(self, *projectors) -> None:
        self._projectors = tuple(projectors)

    async def project(self, run_id, event):
        for projector in self._projectors:
            projected = await projector.project(run_id, event)
            if projected is not None:
                raise TypeError("run commit projector must return None")


def _build_screenplay_profile_extension(*, db, **_dependencies):
    return StaticAgentProfileExtension(AgentProfileRegistration(
        id="screenplay",
        domain_namespace=SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
        adapter=ScreenplayDomainAdapter(
            tool_catalog=build_screenplay_tool_catalog(db=db),
        ),
    ))


def create_agent_composition(
    db,
    **kwargs,
) -> AgentComposition:
    """Install product profiles without teaching generic composition domains."""

    extension_factories = tuple(
        kwargs.pop("profile_extension_factories", ())
    )
    supplied_projector = kwargs.pop("run_commit_projector", None)
    screenplay_projector = ScreenplayCandidateCompletionProjector(db)
    run_commit_projector = (
        _ChainedRunCommitProjector(screenplay_projector, supplied_projector)
        if supplied_projector is not None
        else screenplay_projector
    )
    return AgentComposition(
        db,
        run_commit_projector=run_commit_projector,
        profile_extension_factories=(
            build_writing_profile_extension,
            *extension_factories,
            _build_screenplay_profile_extension,
        ),
        **kwargs,
    )


__all__ = ["create_agent_composition"]
