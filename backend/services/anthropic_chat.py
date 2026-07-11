"""
Anthropic Messages API adapter — port of electron/anthropicChat.js.

Converts between OpenAI-format messages/chunks and the Anthropic SDK
so the rest of the backend can stay provider-agnostic.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from services.ai_capabilities import (
    build_anthropic_thinking_param,
    normalize_thinking_enabled,
)
from utils.session_title import (
    SESSION_TITLE_SYSTEM_PROMPT,
    normalize_session_title,
)
from utils.url import normalize_base_url

logger = logging.getLogger(__name__)


# ── Client ──────────────────────────────────────────────────────

def _create_client(api_key: str, base_url: str | None) -> AsyncAnthropic:
    url = normalize_base_url(base_url)
    return AsyncAnthropic(
        api_key=(api_key or "").strip(),
        base_url=url or None,  # SDK default when None
    )


# ── Format conversion helpers ───────────────────────────────────

def _safe_json_parse_args(s: Any) -> dict:
    raw = s if isinstance(s, str) else ""
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}


def openai_tools_to_anthropic(tools: list[dict] | None) -> list[dict] | None:
    """Convert OpenAI function-calling tool definitions to Anthropic Tool format."""
    if not tools:
        return None
    result: list[dict] = []
    for t in tools:
        fn = t.get("function") or {}
        raw = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {}
        input_schema: dict[str, Any] = (
            {"type": "object", **raw}
            if raw.get("type") == "object"
            else {"type": "object", "properties": {}, "required": [], **raw}
        )
        input_schema.setdefault("type", "object")
        result.append({
            "name": str(fn.get("name", "")).strip(),
            "description": fn.get("description") or None,
            "input_schema": input_schema,
        })
    return result or None


def openai_messages_to_anthropic(
    messages: list[dict] | None,
) -> dict[str, Any]:
    """
    Convert OpenAI-format chat history to Anthropic ``messages`` + ``system``.

    Returns ``{"system": str | None, "messages": list[dict]}``.
    """
    system_parts: list[str] = []
    out: list[dict] = []
    items = messages if isinstance(messages, list) else []
    i = 0

    while i < len(items):
        m = items[i]
        if not isinstance(m, dict):
            i += 1
            continue
        role = m.get("role", "")

        if role in ("system", "developer"):
            c = str(m.get("content") or "").strip()
            if c:
                system_parts.append(c)
            i += 1
            continue

        if role == "user":
            c = str(m.get("content") or "")
            if c:
                out.append({"role": "user", "content": c})
            i += 1
            continue

        if role == "assistant":
            blocks: list[dict] = []
            think = str(m["reasoning_content"]).strip() if m.get("reasoning_content") is not None else ""
            text = str(m.get("content") or "")
            if think:
                blocks.append({"type": "text", "text": f"（先前思考过程）\n{think}\n\n"})
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in m.get("tool_calls") or []:
                tc_id = str(tc.get("id") or "").strip()
                name = str((tc.get("function") or {}).get("name", "")).strip()
                inp = _safe_json_parse_args((tc.get("function") or {}).get("arguments"))
                if tc_id and name:
                    blocks.append({"type": "tool_use", "id": tc_id, "name": name, "input": inp})
            if blocks:
                out.append({"role": "assistant", "content": blocks})
            i += 1
            continue

        if role == "tool":
            tool_results: list[dict] = []
            while i < len(items) and isinstance(items[i], dict) and items[i].get("role") == "tool":
                tm = items[i]
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": str(tm.get("tool_call_id") or "").strip(),
                    "content": str(tm.get("content") or ""),
                })
                i += 1
            if tool_results:
                out.append({"role": "user", "content": tool_results})
            continue

        i += 1

    system = "\n\n".join(system_parts) if system_parts else None
    return {"system": system, "messages": out}


# ── OpenAI-format chunk helper ──────────────────────────────────

def _openai_chunk(
    delta: dict[str, Any] | None = None,
    finish_reason: str | None = None,
) -> dict[str, Any]:
    choice: dict[str, Any] = {"index": 0, "delta": delta or {}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return {"choices": [choice]}


# ── Thinking parameter helpers ──────────────────────────────────
# 注：``thinking={"type":"enabled","budget_tokens":N}`` 这种形状的构造
# 已搬到 ``services.ai_capabilities.build_anthropic_thinking_param``。
# 本文件只保留对错误返回的"自动降级重试"。

_THINKING_ERR_RE = re.compile(r"thinking|not support|unrecogniz|invalid", re.IGNORECASE)


# ── Streaming (Anthropic → OpenAI chunks) ───────────────────────

async def chat_stream_as_openai_format(
    api_key: str,
    messages: list[dict],
    options: dict[str, Any] | None = None,
    signal: asyncio.Event | None = None,
) -> dict[str, Any]:
    """
    Stream via Anthropic and yield OpenAI-format chunks through an async generator.

    Returns ``{"stream": async_generator, "model": str}``.
    """
    opts = options or {}
    model: str = str(opts.get("model") or "").strip()
    temperature = opts.get("temperature")
    thinking_enabled = normalize_thinking_enabled(opts)
    tools: list | None = opts.get("tools")
    max_tokens: int | None = opts.get("max_tokens")
    base_url: str | None = opts.get("baseURL")
    top_k: Any = opts.get("top_k")

    client = _create_client(api_key, base_url)
    converted = openai_messages_to_anthropic(messages)
    system: str | None = converted["system"]
    anth_messages: list[dict] = converted["messages"]
    if not anth_messages:
        raise ValueError("消息为空")

    thinking_param, max_out = build_anthropic_thinking_param(thinking_enabled, max_tokens)
    thinking_on = thinking_param is not None
    anthropic_tools = openai_tools_to_anthropic(tools)

    params: dict[str, Any] = {
        "model": model,
        "max_tokens": max_out,
        "messages": anth_messages,
        "stream": True,
    }
    if system:
        params["system"] = system
    if thinking_param:
        params["thinking"] = thinking_param
    if anthropic_tools:
        params["tools"] = anthropic_tools
    if temperature is not None:
        params["temperature"] = temperature
    if top_k is not None:
        try:
            tk = int(top_k)
            if tk > 0:
                params["top_k"] = tk
        except (TypeError, ValueError):
            pass

    raw_stream = await client.messages.create(**params)

    async def _convert() -> AsyncIterator[dict]:
        tool_accum: dict[int, dict] = {}
        last_stop_reason: str | None = None

        try:
            async for ev in raw_stream:
                if signal and signal.is_set():
                    break

                ev_type = getattr(ev, "type", None)

                if ev_type == "content_block_delta":
                    d = ev.delta
                    d_type = getattr(d, "type", None)
                    if d_type == "text_delta" and getattr(d, "text", None):
                        yield _openai_chunk({"content": d.text})
                    elif thinking_on and d_type == "thinking_delta" and getattr(d, "thinking", None):
                        yield _openai_chunk({"reasoning_content": d.thinking})
                    elif d_type == "input_json_delta" and getattr(d, "partial_json", None) is not None:
                        idx = ev.index
                        cur = tool_accum.get(idx, {"json": ""})
                        cur["json"] += d.partial_json
                        tool_accum[idx] = cur

                elif ev_type == "content_block_start":
                    block = ev.content_block
                    if block and getattr(block, "type", None) == "tool_use":
                        tool_accum[ev.index] = {
                            "id": block.id,
                            "name": block.name,
                            "json": "",
                        }

                elif ev_type == "content_block_stop":
                    idx = ev.index
                    t = tool_accum.get(idx)
                    if t and t.get("name"):
                        args_str = t["json"].strip() if t["json"] and t["json"].strip() else "{}"
                        yield _openai_chunk({
                            "tool_calls": [{
                                "index": idx,
                                "id": str(t.get("id") or ""),
                                "type": "function",
                                "function": {
                                    "name": str(t.get("name") or ""),
                                    "arguments": args_str,
                                },
                            }],
                        })

                elif ev_type == "message_delta":
                    sr = getattr(getattr(ev, "delta", None), "stop_reason", None)
                    if sr:
                        last_stop_reason = sr

        except Exception:
            if signal and signal.is_set():
                yield _openai_chunk({}, "stop")
                return
            raise

        if last_stop_reason == "tool_use":
            yield _openai_chunk({}, "tool_calls")
        elif last_stop_reason == "max_tokens":
            yield _openai_chunk({}, "length")
        else:
            yield _openai_chunk({}, "stop")

    return {"stream": _convert(), "model": model}


# ── Non-streaming (Anthropic → OpenAI message) ─────────────────

async def chat_no_stream_as_openai_format(
    api_key: str,
    messages: list[dict],
    options: dict[str, Any] | None = None,
    signal: asyncio.Event | None = None,
) -> dict[str, Any]:
    """
    Non-streaming call via Anthropic, returning an OpenAI-shaped
    ``{"message": {...}, "model": str}``.
    """
    opts = options or {}
    model: str = str(opts.get("model") or "").strip()
    temperature = opts.get("temperature")
    thinking_enabled = normalize_thinking_enabled(opts)
    tools: list | None = opts.get("tools")
    max_tokens: int | None = opts.get("max_tokens")
    base_url: str | None = opts.get("baseURL")
    top_k: Any = opts.get("top_k")

    client = _create_client(api_key, base_url)
    converted = openai_messages_to_anthropic(messages)
    system: str | None = converted["system"]
    anth_messages: list[dict] = converted["messages"]
    if not anth_messages:
        raise ValueError("消息为空")

    thinking_param, max_out = build_anthropic_thinking_param(thinking_enabled, max_tokens)
    anthropic_tools = openai_tools_to_anthropic(tools)

    params: dict[str, Any] = {
        "model": model,
        "max_tokens": max_out,
        "messages": anth_messages,
        "stream": False,
    }
    if system:
        params["system"] = system
    if thinking_param:
        params["thinking"] = thinking_param
    if anthropic_tools:
        params["tools"] = anthropic_tools
    if temperature is not None:
        params["temperature"] = temperature
    if top_k is not None:
        try:
            tk = int(top_k)
            if tk > 0:
                params["top_k"] = tk
        except (TypeError, ValueError):
            pass

    try:
        msg = await client.messages.create(**params)
    except Exception as err:
        err_msg = str(err)
        if _THINKING_ERR_RE.search(err_msg) and "thinking" in params:
            params.pop("thinking")
            msg = await client.messages.create(**params)
        else:
            raise

    blocks = msg.content if isinstance(msg.content, list) else []
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls_openai: list[dict] = []

    for block in blocks:
        b_type = getattr(block, "type", None)
        if b_type == "text" and isinstance(getattr(block, "text", None), str):
            text_parts.append(block.text)
        elif b_type == "thinking" and isinstance(getattr(block, "thinking", None), str):
            thinking_parts.append(block.thinking)
        elif b_type == "tool_use":
            inp = block.input if isinstance(block.input, dict) else {}
            tool_calls_openai.append({
                "id": str(getattr(block, "id", "") or ""),
                "type": "function",
                "function": {
                    "name": str(getattr(block, "name", "") or ""),
                    "arguments": json.dumps(inp),
                },
            })

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts),
    }
    if thinking_parts:
        message["reasoning_content"] = "\n".join(thinking_parts)
    if tool_calls_openai:
        message["tool_calls"] = tool_calls_openai

    return {"message": message, "model": getattr(msg, "model", None) or model}


# ── Title extraction helper ─────────────────────────────────────

def _extract_anthropic_title_plain_text(msg: Any) -> str:
    if msg is None:
        return ""
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return re.sub(r"[\r\n]+", " ", content).strip()

    blocks = content if isinstance(content, list) else []

    from_text = "".join(
        getattr(b, "text", "") if getattr(b, "type", None) == "text" else ""
        for b in blocks
    )
    from_text = re.sub(r"[\r\n]+", " ", from_text).strip()
    if from_text:
        return from_text

    fallback_parts: list[str] = []
    for b in blocks:
        b_type = getattr(b, "type", None)
        if b_type == "thinking" and isinstance(getattr(b, "thinking", None), str):
            fallback_parts.append(b.thinking)
        elif isinstance(getattr(b, "text", None), str):
            fallback_parts.append(b.text)
        elif isinstance(getattr(b, "content", None), str):
            fallback_parts.append(b.content)
    return re.sub(r"[\r\n]+", " ", "".join(fallback_parts)).strip()


# ── Title generation ────────────────────────────────────────────

async def generate_title(
    api_key: str,
    text: str,
    options: dict[str, Any] | None = None,
) -> str:
    """Generate a short session title (≤10 chars) via Anthropic."""
    opts = options or {}
    model: str = opts.get("model", "")
    base_url: str | None = opts.get("baseURL")

    client = _create_client(api_key, base_url)
    user_text = str(text or "").strip()[:4000]

    # 标题生成走窄任务：禁用思考。Anthropic 的 none → 不传 thinking 字段。
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 512,
        "system": SESSION_TITLE_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_text}],
    }
    try:
        msg = await client.messages.create(**payload)
    except Exception as err:
        err_msg = str(err)
        if _THINKING_ERR_RE.search(err_msg) and "thinking" in payload:
            payload.pop("thinking")
            msg = await client.messages.create(**payload)
        else:
            raise

    raw = _extract_anthropic_title_plain_text(msg)
    logger.info("[ai-generate-title][anthropic] 模型返回原文: %s", raw)

    if not raw:
        block_types: Any
        if isinstance(getattr(msg, "content", None), list):
            block_types = [getattr(b, "type", type(b).__name__) for b in msg.content]
        else:
            block_types = type(getattr(msg, "content", None)).__name__
        logger.warning(
            "[ai-generate-title][anthropic] 无可见正文（非抛错）: stop_reason=%s blockTypes=%s model=%s",
            getattr(msg, "stop_reason", None),
            block_types,
            model,
        )

    return normalize_session_title(raw)
