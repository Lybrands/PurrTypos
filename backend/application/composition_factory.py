"""Application bootstrap that installs product Agent profiles into Core."""

from __future__ import annotations

from functools import partial

from application.agent_composition import AgentComposition
from application.screenplay_agent_profile import (
    build_screenplay_agent_profile,
)
from application.screenplay_agent_task_executor import (
    normalize_screenplay_candidate,
)
from application.writing_agent_profile import build_writing_agent_profile
from infrastructure.screenplay.agent_root_completion_projector import (
    ScreenplayAgentRootCompletionProjector,
)
from infrastructure.screenplay.agent_continuation_begin_projector import (
    ScreenplayContinuationBeginProjector,
)
from infrastructure.screenplay.agent_run_cancellation_projector import (
    ScreenplayRunCancellationProjector,
)
from infrastructure.screenplay.candidate_completion_projector import (
    ScreenplayCandidateCompletionProjector,
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

    writing_profile_factory = partial(
        build_writing_agent_profile,
        skills_dir=kwargs.pop("skills_dir", None),
    )
    supplied_projector = kwargs.pop("run_commit_projector", None)
    supplied_cancellation_projectors = tuple(
        kwargs.pop("run_cancellation_projectors", ())
    )
    screenplay_projectors = (
        ScreenplayCandidateCompletionProjector(
            db,
            candidate_normalizer=normalize_screenplay_candidate,
        ),
        ScreenplayAgentRootCompletionProjector(db),
    )
    run_commit_projector = (
        _ChainedRunCommitProjector(*screenplay_projectors, supplied_projector)
        if supplied_projector is not None
        else _ChainedRunCommitProjector(*screenplay_projectors)
    )
    return AgentComposition(
        db,
        run_begin_projector=ScreenplayContinuationBeginProjector(db),
        run_commit_projector=run_commit_projector,
        run_cancellation_projectors=(
            ScreenplayRunCancellationProjector(db),
            *supplied_cancellation_projectors,
        ),
        profile_factories=(
            writing_profile_factory,
            partial(
                build_screenplay_agent_profile,
                candidate_normalizer=normalize_screenplay_candidate,
            ),
        ),
        **kwargs,
    )


__all__ = ["create_agent_composition"]
