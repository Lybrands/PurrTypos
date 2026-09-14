"""Z.ai SDK adapter for mainland GLM chat completions."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from typing import Any, AsyncIterator

from infrastructure.models.capabilities import (
    require_supported_reasoning_mode,
)
from infrastructure.models.profiles.descriptors import profile_for_options
from infrastructure.models.profiles.base import ModelProfile
from purra.contracts import ReasoningMode
from purra.cancellation import await_with_cancellation, raise_if_stopped
from infrastructure.models.request_boundary import prepare_sdk_request, record_provider_response
from purra.model_protocol import ReasoningControl
from purra.stream_ownership import OwnedAsyncIterator, openai_chunk_is_terminal
from utils.url import normalize_base_url


_END = object()
_REAPERS: set[asyncio.Task] = set()

# The source-analysis runtime declares a 120s activity window.  The transport
# may still protect connects and pool acquisition aggressively, but its stream
# read deadline must not preempt that runtime policy.  Keep a small margin so
# cancellation/cleanup is owned by the runtime first.
_ZAI_CONNECT_TIMEOUT_SECONDS = 15.0
_ZAI_WRITE_TIMEOUT_SECONDS = 30.0
_ZAI_POOL_TIMEOUT_SECONDS = 30.0
_ZAI_STREAM_READ_TIMEOUT_SECONDS = 125.0


def _create_client(api_key: str, base_url: str | None):
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("API Key 为空")
    url = normalize_base_url(base_url)
    if not url:
        raise ValueError("请填写接口地址")

    import httpx
    from zai import ZhipuAiClient

    return ZhipuAiClient(
        api_key=key,
        base_url=url,
        max_retries=0,
        timeout=httpx.Timeout(
            connect=_ZAI_CONNECT_TIMEOUT_SECONDS,
            read=_ZAI_STREAM_READ_TIMEOUT_SECONDS,
            write=_ZAI_WRITE_TIMEOUT_SECONDS,
            pool=_ZAI_POOL_TIMEOUT_SECONDS,
        ),
    )


async def _thread_call(fn, *args, signal=None, **kwargs):
    raise_if_stopped(signal)
    task = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    try:
        return await await_with_cancellation(asyncio.shield(task), signal)
    except BaseException:
        async def reap():
            try:
                resource = await task
                await _close_in_thread(resource)
            except BaseException:
                pass
        reaper = asyncio.create_task(reap())
        _REAPERS.add(reaper)
        reaper.add_done_callback(_REAPERS.discard)
        raise


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump()
        if isinstance(result, Mapping):
            return dict(result)
    return {}


def _build_chat_params(
    messages: list[dict],
    options: dict[str, Any],
    profile: ModelProfile,
    *,
    stream: bool,
) -> dict[str, Any]:
    mode = require_supported_reasoning_mode(
        options,
        profile.protocol_capabilities(),
    )
    params: dict[str, Any] = {
        "model": str(options.get("model") or ""),
        "messages": messages,
        "stream": stream,
    }
    if profile.reasoning_control is ReasoningControl.ALWAYS_ENABLED:
        params["thinking"] = {"type": "enabled"}
    elif mode is not ReasoningMode.DEFAULT:
        params["thinking"] = {"type": mode.value}
    for key in (
        "temperature",
        "reasoning_effort",
        "max_tokens",
        "response_format",
        "tools",
        "tool_choice",
    ):
        value = options.get(key)
        if value is not None and value != []:
            params[key] = value
    return params


async def _close_in_thread(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if close is None:
        close = getattr(resource, "aclose", None)
    if close is None:
        return
    try:
        result = await asyncio.to_thread(close)
        if inspect.isawaitable(result):
            await result
    except (GeneratorExit, StopAsyncIteration):
        pass
    except Exception:
        pass


class _ThreadedCloseProxy:
    def __init__(self, resource: Any):
        self._resource = resource

    async def aclose(self) -> None:
        await _close_in_thread(self._resource)


def _next_or_end(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return _END


async def chat_no_stream(
    api_key: str,
    messages: list[dict],
    options: dict[str, Any] | None = None,
    signal: asyncio.Event | None = None,
) -> dict[str, Any]:
    opts = options or {}
    model = str(opts.get("model") or "")
    base_url = opts.get("baseURL")
    profile = profile_for_options(opts)
    client = _create_client(api_key, base_url)
    try:
        params = _build_chat_params(messages, opts, profile, stream=False)
        raise_if_stopped(signal)
        params = await prepare_sdk_request(params, opts, protocol="zai")
        response = await _thread_call(
            client.chat.completions.create,
            signal=signal,
            **params,
        )
        await record_provider_response()
        payload = _as_mapping(response)
        choices = payload.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else {}
        raw_message = (
            choice.get("message")
            if isinstance(choice, Mapping)
            else {}
        )
        message = profile.normalize_openai_message(
            raw_message if isinstance(raw_message, Mapping) else {}
        )
        usage = payload.get("usage")
        return {
            "message": message,
            "model": str(payload.get("model") or model),
            "finish_reason": (
                choice.get("finish_reason")
                if isinstance(choice, Mapping)
                else None
            ),
            "usage": dict(usage) if isinstance(usage, Mapping) else None,
            "applied_generation_limit": params.get("max_tokens"),
        }
    finally:
        await _close_in_thread(client)


async def chat_stream(
    api_key: str,
    messages: list[dict],
    options: dict[str, Any] | None = None,
    signal: asyncio.Event | None = None,
) -> dict[str, Any]:
    opts = options or {}
    model = str(opts.get("model") or "")
    base_url = opts.get("baseURL")
    profile = profile_for_options(opts)
    client = _create_client(api_key, base_url)
    try:
        params = _build_chat_params(messages, opts, profile, stream=True)
        raise_if_stopped(signal)
        params = await prepare_sdk_request(params, opts, protocol="zai")
        raw_stream = await _thread_call(
            client.chat.completions.create,
            signal=signal,
            **params,
        )
        await record_provider_response()
    except BaseException:
        await _close_in_thread(client)
        raise

    async def _generate() -> AsyncIterator[dict[str, Any]]:
        while True:
            if signal is not None and signal.is_set():
                return
            raw = await _thread_call(_next_or_end, raw_stream, signal=signal)
            if raw is _END:
                return
            chunk = profile.normalize_openai_chunk(_as_mapping(raw))
            yield chunk

    return {
        "stream": OwnedAsyncIterator(
            _generate(),
            _ThreadedCloseProxy(raw_stream),
            _ThreadedCloseProxy(client),
            terminal_predicate=openai_chunk_is_terminal,
        ),
        "model": model,
        "applied_generation_limit": params.get("max_tokens"),
    }



async def list_models(api_key: str, base_url: str | None) -> list[str]:
    client = _create_client(api_key, base_url)
    try:
        # zai-sdk 0.2.3 does not expose a models collection. Return the
        # application's supported built-in catalog without falling back to a
        # different provider SDK.
        return ["glm-5.3-flash"]
    finally:
        await _close_in_thread(client)


__all__ = ["chat_no_stream", "chat_stream", "list_models"]
