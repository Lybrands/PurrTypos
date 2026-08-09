"""Application bootstrap that installs product Agent profiles into Core."""

from __future__ import annotations

from application.agent_composition import AgentComposition
from application.screenplay_agent_composition import ScreenplayAgentComposition


def create_agent_composition(
    db,
    *,
    screenplay_adapter=None,
    **kwargs,
) -> AgentComposition:
    """Build the complete host composition without product imports in Core root."""

    def build_screenplay_extension(**dependencies):
        return ScreenplayAgentComposition(
            **dependencies,
            adapter=screenplay_adapter,
        )

    return AgentComposition(
        db,
        event_projector=ScreenplayAgentComposition.create_event_projector(db),
        profile_extension_factories=(build_screenplay_extension,),
        **kwargs,
    )


__all__ = ["create_agent_composition"]
