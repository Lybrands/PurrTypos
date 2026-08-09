"""Host-owned projection port for opaque domain effects."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_core.contracts import RunId
from agent_core.events import AgentEvent


@runtime_checkable
class DomainEventProjector(Protocol):
    """Project one opaque host event inside the repository transaction.

    Returning ``None`` preserves the original event. Returning an event
    replaces the public/persisted event without teaching Core its semantics.
    """

    async def project(
        self,
        run_id: RunId,
        event: AgentEvent,
    ) -> AgentEvent | None: ...


__all__ = ["DomainEventProjector"]
