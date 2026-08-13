"""Application bootstrap that installs product Agent profiles into Core."""

from __future__ import annotations

from functools import partial

from application.agent_composition import AgentComposition
from application.screenplay_agent_profile import (
    build_screenplay_profile_extension,
)
from application.writing_agent_profile import build_writing_profile_extension
from infrastructure.screenplay import ScreenplayCandidateCompletionProjector
from infrastructure.screenplay.agent_root_completion_projector import (
    ScreenplayAgentRootCompletionProjector,
)


class _ChainedRunCommitProjector:
    def __init__(self, *projectors) -> None:
        self._projectors = tuple(projectors)

    async def project(self, run_id, event):
        for projector in self._projectors:
            projected = await projector.project(run_id, event)
            if projected is not None:
                raise TypeError("run commit projector must return None")


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
    screenplay_projectors = (
        ScreenplayCandidateCompletionProjector(db),
        ScreenplayAgentRootCompletionProjector(db),
    )
    run_commit_projector = (
        _ChainedRunCommitProjector(*screenplay_projectors, supplied_projector)
        if supplied_projector is not None
        else _ChainedRunCommitProjector(*screenplay_projectors)
    )
    return AgentComposition(
        db,
        run_commit_projector=run_commit_projector,
        profile_extension_factories=(
            writing_extension_factory,
            build_screenplay_profile_extension,
        ),
        **kwargs,
    )


__all__ = ["create_agent_composition"]
