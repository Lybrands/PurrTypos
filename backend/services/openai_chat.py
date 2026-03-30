"""
OpenAI-compatible chat adapter — port of electron/openaiChat.js.

Uses the ``openai`` async Python SDK and exposes streaming / non-streaming
chat plus a title-generation helper.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from utils.session_title import (
    SESSION_TITLE_SYSTEM_PROMPT,
    normalize_session_title,
)
from utils.url import normalize_base_url

logger = logging.getLogger(__name__)


def _create_client(api_key: str, base_url: str | None) -> AsyncOpenAI:
    url = normalize_base_url(base_url)
    if not url:
        raise ValueError("请填写接口地址")
    return AsyncOpenAI(api_key=api_key, base_url=url)


# ── Non-streaming chat ──────────────────────────────────────────

async def chat_no_stream(
    api_key: str,
    messages: list[dict],
    options: dict[str, Any] | None = None,
    signal: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Single-shot completion.  Returns ``{"message": {...}, "model": str}``."""
    opts = options or {}
    model: str = opts.get("model", "")
    temperature = opts.get("temperature")
    thinking = opts.get("thinking")
    tools: list | None = opts.get("tools")
    max_tokens: int | None = opts.get("max_tokens")
    base_url: str | None = opts.get("baseURL")
    top_k: Any = opts.get("top_k")

    client = _create_client(api_key, base_url)

    params: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if temperature is not None:
        params["temperature"] = temperature
    if thinking:
        params.setdefault("extra_body", {})["thinking"] = thinking
    if max_tokens:
        params["max_tokens"] = max_tokens
    if tools:
        params["tools"] = tools
    if top_k is not None:
        try:
            tk = int(top_k)
            if tk > 0:
                params.setdefault("extra_body", {})["top_k"] = tk
        except (TypeError, ValueError):
            pass

    res = await client.chat.completions.create(**params)
    choice = res.choices[0] if res.choices else None
    message = choice.message.model_dump() if choice and choice.message else {}
    return {"message": message, "model": res.model or model}


# ── Streaming chat ──────────────────────────────────────────────

async def chat_stream(
    api_key: str,
    messages: list[dict],
    options: dict[str, Any] | None = None,
    signal: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Streaming completion.  Returns ``{"stream": async_generator, "model": str}``."""
    opts = options or {}
    model: str = opts.get("model", "")
    temperature = opts.get("temperature")
    thinking = opts.get("thinking")
    tools: list | None = opts.get("tools")
    max_tokens: int | None = opts.get("max_tokens")
    base_url: str | None = opts.get("baseURL")
    top_k: Any = opts.get("top_k")

    tool_names = (
        ", ".join(t.get("function", {}).get("name", "") for t in tools if t.get("function", {}).get("name"))
        if tools
        else ""
    )
    temp_log = f"temperature={temperature}" if temperature is not None else "temperature=(omit)"
    thinking_log = f" thinking={thinking.get('type')}" if thinking else ""
    tools_log = f" tools={tool_names}" if tool_names else ""
    logger.info("[OpenAI] %s %s%s%s", model, temp_log, thinking_log, tools_log)

    client = _create_client(api_key, base_url)

    params: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
    if temperature is not None:
        params["temperature"] = temperature
    if thinking:
        params.setdefault("extra_body", {})["thinking"] = thinking
    if max_tokens:
        params["max_tokens"] = max_tokens
    if tools:
        params["tools"] = tools
    if top_k is not None:
        try:
            tk = int(top_k)
            if tk > 0:
                params.setdefault("extra_body", {})["top_k"] = tk
        except (TypeError, ValueError):
            pass

    raw_stream = await client.chat.completions.create(**params)

    async def _generate() -> AsyncIterator[dict]:
        async for chunk in raw_stream:
            if signal and signal.is_set():
                break
            yield chunk.model_dump()

    return {"stream": _generate(), "model": model}


# ── Title generation ────────────────────────────────────────────

async def generate_title(
    api_key: str,
    text: str,
    options: dict[str, Any] | None = None,
) -> str:
    """Generate a short session title (≤10 chars) via a non-streaming call."""
    opts = options or {}
    model: str = opts.get("model", "")
    base_url: str | None = opts.get("baseURL")

    client = _create_client(api_key, base_url)

    res = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SESSION_TITLE_SYSTEM_PROMPT},
            {"role": "user", "content": str(text or "").strip()},
        ],
        max_tokens=32,
        extra_body={"thinking": {"type": "disabled"}},
        stream=False,
    )
    raw = (res.choices[0].message.content if res.choices and res.choices[0].message else "") or ""
    logger.info("[ai-generate-title][openai] 模型返回原文: %s", raw)
    return normalize_session_title(raw)
