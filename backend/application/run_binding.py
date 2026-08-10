"""Application lifecycle port for host state associated with a Core Run."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from purra.contracts import AgentRunResult


@runtime_checkable
class RunBindingLifecycle(Protocol):
    async def validate(self) -> None: ...

    async def on_run_started(self, run_id: str) -> None: ...

    async def on_run_finished(self, result: AgentRunResult) -> None: ...


__all__ = ["RunBindingLifecycle"]
