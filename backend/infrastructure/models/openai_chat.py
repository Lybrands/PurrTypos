"""
OpenAI-compatible infrastructure adapter.

Uses the ``openai`` async Python SDK and exposes streaming / non-streaming
chat through a shared parameter compiler.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, AsyncIterator

from openai import AsyncOpenAI
from purra.cancellation import await_with_cancellation, raise_if_stopped
from infrastructure.models.request_boundary import prepare_sdk_request, record_provider_response

from infrastructure.models.capabilities import (
    normalize_thinking_enabled,
    require_supported_reasoning_mode,
)
from infrastructure.models.profiles.descriptors import profile_for_options
from purra.stream_ownership import (
    OwnedAsyncIterator,
    close_async_resource,
    openai_chunk_is_terminal,
)
from utils.url import normalize_base_url

logger = logging.getLogger(__name__)


def _create_client(api_key: str, base_url: str | None) -> AsyncOpenAI:
    url = normalize_base_url(base_url)
    if not url:
        raise ValueError("请填写接口地址")
    return AsyncOpenAI(api_key=api_key, base_url=url, max_retries=0)


def _compile_chat(messages, opts, profile, *, stream):
    require_supported_reasoning_mode(opts, profile.protocol_capabilities())
    params = {"model": str(opts.get("model") or ""), "messages": messages, "stream": stream}
    params["extra_body"] = profile.build_openai_extra_body(normalize_thinking_enabled(opts))
    for key in ("temperature", "reasoning_effort", "top_p", "response_format", "tools", "tool_choice"):
        if opts.get(key) is not None:
            params[key] = opts[key]
    if opts.get("top_k") is not None:
        value = opts["top_k"]
        if type(value) is not int or value <= 0:
            raise ValueError("top_k must be a positive integer")
        params["extra_body"]["top_k"] = value
    profile.apply_openai_output_limit(params, opts.get("max_tokens"))
    if stream and profile.stream_usage:
        params["stream_options"] = {"include_usage": True}
    return params


# ── Non-streaming chat ──────────────────────────────────────────

async def chat_no_stream(
    api_key: str,
    messages: list[dict],
    options: dict[str, Any] | None = None,
    signal: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Single-shot completion.  Returns ``{"message": {...}, "model": str}``."""
    opts = options or {}
    model = str(opts.get("model") or "")
    profile = profile_for_options(opts)
    params = _compile_chat(messages, opts, profile, stream=False)
    raise_if_stopped(signal)
    params = await prepare_sdk_request(params, opts, protocol="openai_compatible")
    raise_if_stopped(signal)
    client = _create_client(api_key, opts.get("baseURL"))

    try:
        res = await await_with_cancellation(client.chat.completions.create(**params), signal)
        await record_provider_response()
        choice = res.choices[0] if res.choices else None
        message = profile.normalize_openai_message(
            choice.message.model_dump() if choice and choice.message else {}
        )
        usage = res.usage.model_dump() if getattr(res, "usage", None) else None
        return {
            "message": message,
            "model": res.model or model,
            "finish_reason": getattr(choice, "finish_reason", None),
            "usage": usage,
            "applied_generation_limit": params.get(
                profile.openai_output_token_parameter
            ),
        }
    finally:
        await close_async_resource(client)


# ── Streaming chat ──────────────────────────────────────────────

async def chat_stream(
    api_key: str,
    messages: list[dict],
    options: dict[str, Any] | None = None,
    signal: asyncio.Event | None = None,
) -> dict[str, Any]:
    """Streaming completion.  Returns ``{"stream": async_generator, "model": str}``."""
    opts = options or {}
    model = str(opts.get("model") or "")
    profile = profile_for_options(opts)
    params = _compile_chat(messages, opts, profile, stream=True)
    raise_if_stopped(signal)
    params = await prepare_sdk_request(params, opts, protocol="openai_compatible")
    usage_tail_expected = profile.stream_usage
    raise_if_stopped(signal)
    client = _create_client(api_key, opts.get("baseURL"))
    try:
        raw_stream = await await_with_cancellation(client.chat.completions.create(**params), signal)
        await record_provider_response()
    except BaseException:
        await close_async_resource(client)
        raise

    async def _generate() -> AsyncIterator[dict]:
        async for chunk in raw_stream:
            if signal and signal.is_set():
                break
            normalized = profile.normalize_openai_chunk(chunk.model_dump())
            if (
                usage_tail_expected
                and openai_chunk_is_terminal(normalized)
                and normalized.get("usage") is None
            ):
                try:
                    # The protocol-defined usage record follows the finish
                    # chunk. The SDK's request/read timeout remains the
                    # transport bound; a local sub-second guess can discard a
                    # valid Provider usage record.
                    tail = await anext(raw_stream)
                except StopAsyncIteration:
                    tail = None
                except Exception:
                    # The finish chunk remains authoritative. A malformed
                    # usage tail must not turn a completed answer into a
                    # failed request.
                    tail = None
                if signal and signal.is_set():
                    return
                if tail is not None:
                    tail_value = profile.normalize_openai_chunk(
                        tail.model_dump()
                    )
                    if tail_value.get("usage") is not None:
                        normalized["usage"] = tail_value["usage"]
            yield normalized

    return {
        "stream": OwnedAsyncIterator(
            _generate(),
            raw_stream,
            client,
            terminal_predicate=openai_chunk_is_terminal,
        ),
        "model": model,
        "applied_generation_limit": params.get(
            profile.openai_output_token_parameter
        ),
    }


# ── Title generation ────────────────────────────────────────────
