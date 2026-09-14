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
    progress_chunks: tuple[str, ...] | None = None,
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
        progress_text = progress
        if progress_chunks is not None:
            if not progress_chunks or any(not chunk for chunk in progress_chunks):
                raise ValueError("progress_chunks must contain nonempty chunks")
            if progress_text is not None and progress_text != "".join(progress_chunks):
                raise ValueError("progress and progress_chunks disagree")
            progress_text = "".join(progress_chunks)
        wire = planning_stream_wire(plan, progress=progress_text)

        async def chunks():
            wire_chunks = (wire,)
            if progress_chunks is not None:
                prefix = '{"v":1,"type":"progress","text":"'
                suffix = '"}\n'
                plan_wire = planning_stream_wire(plan)
                wire_chunks = tuple(
                    (
                        prefix if index == 0 else ""
                    ) + json.dumps(chunk, ensure_ascii=False)[1:-1] + (
                        suffix + plan_wire
                        if index == len(progress_chunks) - 1 else ""
                    )
                    for index, chunk in enumerate(progress_chunks)
                )
            for index, content_delta in enumerate(wire_chunks):
                is_last = index == len(wire_chunks) - 1
                yield {
                    "choices": [{
                        "delta": {"content": content_delta},
                        "finish_reason": (
                            response.get("finish_reason") or "stop"
                        ) if is_last else None,
                    }],
                    **(
                        {"usage": response["usage"]}
                        if is_last and response.get("usage") is not None
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
