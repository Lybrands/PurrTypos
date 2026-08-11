"""Host-owned projection port for opaque domain effects."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from purra.contracts import RunId
from purra.output.contracts import DomainEffectOutput


@runtime_checkable
class DomainEventProjector(Protocol):
    """Project one typed domain effect inside the output transaction.

    The projector never receives or returns the canonical event envelope.
    """

    async def project(
        self,
        run_id: RunId,
        effect: DomainEffectOutput,
    ) -> None: ...


__all__ = ["DomainEventProjector"]
