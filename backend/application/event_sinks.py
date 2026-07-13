"""Small EventSink adapters used while transports remain unchanged."""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

from agent_core.events import AgentEvent
from agent_core.json_values import thaw_json_mapping


EventCallback = Callable[[AgentEvent], Awaitable[None] | None]
LegacyChunkCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class CallbackEventSink:
    """Forward typed Core events without introducing a transport dependency."""

    def __init__(self, callback: EventCallback):
        self._callback = callback

    async def emit(self, event: AgentEvent) -> None:
        result = self._callback(event)
        if inspect.isawaitable(result):
            await result


class LegacyChunkEventSink:
    """Preserve the existing ``{eventName: payload}`` SSE application shape."""

    def __init__(self, callback: LegacyChunkCallback):
        self._callback = callback

    async def emit(self, event: AgentEvent) -> None:
        result = self._callback({event.type: thaw_json_mapping(event.payload)})
        if inspect.isawaitable(result):
            await result
