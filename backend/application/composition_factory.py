"""Application bootstrap that installs product Agent profiles into Core."""

from __future__ import annotations

from functools import partial

from application.agent_composition import AgentComposition
from application.agent_profile_registry import (
    AgentProfileRegistration,
    StaticAgentProfileExtension,
)
from application.screenplay_agent_context import ScreenplayAgentContextQuery
from application.screenplay_v2_service import ScreenplayV2ProjectService
from application.writing_agent_profile import build_writing_profile_extension
from domains.screenplay_agent.adapter import (
    ScreenplayDomainAdapter,
    ScreenplayHostContextProvider,
)
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
    context_query = ScreenplayAgentContextQuery(db)
    projects = ScreenplayV2ProjectService(db)

    async def load_planning_context(project_id: str):
        workspace = await projects.get_workspace(project_id)
        return await context_query.planning_context(workspace)

    return StaticAgentProfileExtension(AgentProfileRegistration(
        id="screenplay",
        domain_namespace=SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
        adapter=ScreenplayDomainAdapter(
            tool_catalog=build_screenplay_tool_catalog(db=db),
            context_provider=ScreenplayHostContextProvider(
                planning_context_loader=load_planning_context,
            ),
        ),
    ))


def create_agent_composition(
    db,
    **kwargs,
) -> AgentComposition:
    """Install product profiles without teaching generic composition domains."""

    writing_extension_factory = partial(
        build_writing_profile_extension,
        skills_dir=kwargs.pop("skills_dir", None),
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
            writing_extension_factory,
            _build_screenplay_profile_extension,
        ),
        **kwargs,
    )


__all__ = ["create_agent_composition"]
