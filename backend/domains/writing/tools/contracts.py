"""Value contracts shared by Writing-domain tool handlers.

This module intentionally contains no registration mechanism.  A concrete
catalog owns its handler mapping, so importing a handler never mutates process
global state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class ToolResult:
    """Text returned to the model plus the optional read-cache marker."""

    content: str
    from_cache: bool = False


ToolHandler = Callable[
    [dict, dict, Callable[[dict], None] | None],
    Awaitable[ToolResult],
]
CachePredictor = Callable[[dict, dict], bool]
ReadCacheKeyBuilder = Callable[[dict, dict], str | None]


def _err(payload: dict | str) -> ToolResult:
    """Build the structured JSON error envelope used by Writing tools."""

    body = payload if isinstance(payload, dict) else {"error": str(payload)}
    return ToolResult(json.dumps(body, ensure_ascii=False))


__all__ = [
    "CachePredictor",
    "ReadCacheKeyBuilder",
    "ToolHandler",
    "ToolResult",
    "_err",
]
