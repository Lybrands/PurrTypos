"""Z.ai SDK adapter for mainland GLM chat completions."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from typing import Any, AsyncIterator

from infrastructure.models.capabilities import normalize_thinking_enabled
from infrastructure.models.profiles import resolve_model_profile
from infrastructure.models.profiles.base import ModelProfile
from purra.model_protocol import ReasoningControl
from purra.stream_ownership import OwnedAsyncIterator, openai_chunk_is_terminal
from utils.session_title import SESSION_TITLE_SYSTEM_PROMPT, normalize_session_title
from utils.url import normalize_base_url


_END = object()


def _create_client(api_key: str, base_url: str | None):
    key = str(api_key or "").strip()
    if not key:
        raise ValueError("API Key 为空")
    url = normalize_base_url(base_url)
    if not url:
        raise ValueError("请填写接口地址")

    from zai import ZhipuAiClient

    return ZhipuAiClient(api_key=key, base_url=url)


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
    thinking_enabled = (
        profile.reasoning_control is ReasoningControl.ALWAYS_ENABLED
        or normalize_thinking_enabled(options)
    )
    params: dict[str, Any] = {
        "model": str(options.get("model") or ""),
        "messages": messages,
        "stream": stream,
        "thinking": {
            "type": (
                "enabled"
                if thinking_enabled
                else "disabled"
            ),
        },
    }
    for key in (
        "temperature",
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
    profile = resolve_model_profile(opts.get("model_profile"), model, base_url)
    client = _create_client(api_key, base_url)
    try:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            **_build_chat_params(messages, opts, profile, stream=False),
        )
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
    profile = resolve_model_profile(opts.get("model_profile"), model, base_url)
    client = _create_client(api_key, base_url)
    try:
        raw_stream = await asyncio.to_thread(
            client.chat.completions.create,
            **_build_chat_params(messages, opts, profile, stream=True),
        )
    except BaseException:
        await _close_in_thread(client)
        raise

    async def _generate() -> AsyncIterator[dict[str, Any]]:
        while True:
            if signal is not None and signal.is_set():
                return
            raw = await asyncio.to_thread(_next_or_end, raw_stream)
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
    }


async def generate_title(
    api_key: str,
    text: str,
    options: dict[str, Any] | None = None,
) -> str:
    opts = options or {}
    model = str(opts.get("model") or "")
    base_url = opts.get("baseURL")
    profile = resolve_model_profile(opts.get("model_profile"), model, base_url)
    client = _create_client(api_key, opts.get("baseURL"))
    try:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=model,
            messages=[
                {"role": "system", "content": SESSION_TITLE_SYSTEM_PROMPT},
                {"role": "user", "content": str(text or "").strip()},
            ],
            thinking={
                "type": (
                    "enabled"
                    if profile.reasoning_control is ReasoningControl.ALWAYS_ENABLED
                    else "disabled"
                ),
            },
            max_tokens=32,
            stream=False,
        )
        payload = _as_mapping(response)
        choices = payload.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else {}
        message = choice.get("message") if isinstance(choice, Mapping) else {}
        content = message.get("content") if isinstance(message, Mapping) else ""
        return normalize_session_title(str(content or ""))
    finally:
        await _close_in_thread(client)


async def list_models(api_key: str, base_url: str | None) -> list[str]:
    client = _create_client(api_key, base_url)
    try:
        # zai-sdk 0.2.3 does not expose a models collection. Return the
        # application's supported built-in catalog without falling back to a
        # different provider SDK.
        return ["glm-5.3-flash"]
    finally:
        await _close_in_thread(client)


__all__ = ["chat_no_stream", "chat_stream", "generate_title", "list_models"]
