"""Process-local wakeups for the durable canonical output journal."""

from __future__ import annotations

import asyncio

from purra.output import AgentOutputEvent


class InProcessAgentOutputPublisher:
    """Wake subscribers after persistence without becoming an event store."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._sequences: dict[str, int] = {}
        self._revision = 0

    async def publish_committed(self, event: AgentOutputEvent) -> None:
        if not isinstance(event, AgentOutputEvent):
            raise TypeError("output publisher requires an AgentOutputEvent")
        async with self._condition:
            self._revision += 1
            self._sequences[event.run_id] = max(
                event.sequence,
                self._sequences.get(event.run_id, 0),
            )
            self._condition.notify_all()

    @property
    def revision(self) -> int:
        return self._revision

    async def wait_for_change(self, after: int, *, timeout: float = 2.0) -> None:
        # Durable queries remain authoritative, including writes by another
        # process and product commits without a public output event.
        async with self._condition:
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self._revision > after),
                    timeout=timeout,
                )
            except TimeoutError:
                pass

    async def wait_for_sequence(
        self,
        run_id: str,
        *,
        after_sequence: int,
    ) -> None:
        async with self._condition:
            await self._condition.wait_for(
                lambda: self._sequences.get(run_id, 0) > after_sequence
            )


__all__ = ["InProcessAgentOutputPublisher"]
