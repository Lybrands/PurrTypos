"""Provider fixtures for PurrA's versioned Planner stream."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any


def _message_content(message: object) -> object:
    if isinstance(message, Mapping):
        return message.get("content")
    return getattr(message, "content", None)


def is_planning_request(messages: Iterable[object]) -> bool:
    return any(
        "purra.planning-stream/v1" in str(_message_content(message) or "")
        for message in messages
    )


def planning_stream_wire(plan: Any, *, progress: str | None = None) -> str:
    records = []
    if progress is not None:
        records.append({"v": 1, "type": "progress", "text": progress})
    records.append({"v": 1, "type": "plan", "plan": plan})
    return "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in records
    )


def route_planning_stream(
    planner: Callable[..., Awaitable[dict]],
    runtime: Callable[..., Awaitable[dict]],
    *,
    progress: str | None = None,
):
    """Route the same Provider stream entry to deterministic plan/runtime fixtures."""

    async def routed(key, messages, options, provider, signal=None):
        if not is_planning_request(messages):
            return await runtime(key, messages, options, provider, signal)
        response = await planner(key, messages, options, provider, signal)
        message = response.get("message") or {}
        content = message.get("content")
        try:
            plan = json.loads(content) if isinstance(content, str) else content
        except (TypeError, ValueError):
            plan = content
        wire = planning_stream_wire(plan, progress=progress)

        async def chunks():
            yield {
                "choices": [{
                    "delta": {"content": wire},
                    "finish_reason": response.get("finish_reason") or "stop",
                }],
                **(
                    {"usage": response["usage"]}
                    if response.get("usage") is not None
                    else {}
                ),
            }

        return {
            "applied_generation_limit": response.get(
                "applied_generation_limit",
                options.get("max_tokens"),
            ),
            "stream": chunks(),
            "model": response.get("model") or options.get("model") or "model",
        }

    return routed


__all__ = [
    "is_planning_request",
    "planning_stream_wire",
    "route_planning_stream",
]
