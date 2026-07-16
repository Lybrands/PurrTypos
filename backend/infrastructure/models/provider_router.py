"""Route Core model requests to the configured provider adapter."""

from __future__ import annotations

import asyncio
from typing import Any


async def create_chat_stream(
    key: str,
    messages: list[dict],
    request_params: dict[str, Any],
    api_provider: str,
    signal: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Route a streaming chat request to the appropriate provider adapter."""
    if api_provider == "anthropic":
        from infrastructure.models.anthropic_chat import (
            chat_stream_as_openai_format,
        )

        return await chat_stream_as_openai_format(key, messages, request_params, signal)

    from infrastructure.models.openai_chat import chat_stream

    return await chat_stream(key, messages, request_params, signal)


async def create_chat_no_stream(
    key: str,
    messages: list[dict],
    options: dict[str, Any],
    api_provider: str,
    signal: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Route a non-streaming chat request to the appropriate provider adapter."""
    if api_provider == "anthropic":
        from infrastructure.models.anthropic_chat import (
            chat_no_stream_as_openai_format,
        )

        return await chat_no_stream_as_openai_format(key, messages, options, signal)

    from infrastructure.models.openai_chat import chat_no_stream

    return await chat_no_stream(key, messages, options, signal)
