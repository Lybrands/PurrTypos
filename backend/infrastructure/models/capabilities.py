"""Normalize the caller's default/enabled/disabled reasoning preference.

OpenAI-compatible parameter shapes belong to each ModelProfile. Native
Anthropic budget translation is shared here by its protocol adapter.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from purra.contracts import ReasoningMode
from purra.model_protocol import ModelProtocolCapabilities


def reasoning_mode_from_options(
    opts: Mapping[str, Any] | None,
) -> ReasoningMode:
    """Read the caller's tri-state reasoning preference without rewriting it."""
    options = opts or {}
    thinking = options.get("thinking")
    legacy = options.get("thinking_enabled")
    if thinking is None:
        if legacy is None:
            return ReasoningMode.DEFAULT
        if isinstance(legacy, bool):
            return ReasoningMode.ENABLED if legacy else ReasoningMode.DISABLED
        raise ValueError("thinking_enabled must be a boolean")
    if not isinstance(thinking, Mapping):
        raise ValueError("thinking must be an object")
    value = thinking.get("type")
    if value not in {"enabled", "adaptive", "disabled"}:
        raise ValueError("thinking.type must be enabled, adaptive or disabled")
    mode = ReasoningMode.ENABLED if value == "adaptive" else ReasoningMode(value)
    if legacy is not None and (
        not isinstance(legacy, bool)
        or legacy is not (mode is ReasoningMode.ENABLED)
    ):
        raise ValueError("thinking options conflict")
    return mode


def normalize_thinking_enabled(opts: Mapping[str, Any] | None) -> bool | None:
    """从 options 推断 thinking 开关。"""
    mode = reasoning_mode_from_options(opts)
    if mode is ReasoningMode.DEFAULT:
        return None
    return mode is ReasoningMode.ENABLED


def require_supported_reasoning_mode(
    opts: Mapping[str, Any] | None,
    capabilities: ModelProtocolCapabilities,
) -> ReasoningMode:
    mode = reasoning_mode_from_options(opts)
    if not capabilities.reasoning_mode_is_supported(mode):
        raise ValueError(
            "selected reasoning mode is incompatible with model capabilities"
        )
    return mode


# ── Anthropic Messages API ───────────────────────────────────────────

def build_anthropic_thinking_param(
    enabled: bool | None,
    max_tokens: int | None,
) -> tuple[dict[str, Any] | None, int]:
    """计算 Anthropic 的 ``thinking`` 参数 + 调整后的 ``max_tokens``。

    关闭时返回 ``(None, max_out)`` —— 调用方应整段省略 ``thinking`` 字段。
    开启时返回带 ``budget_tokens`` 的字典；budget 取 max_tokens 的一半，
    并保证 ``max_tokens > budget``。
    """
    max_out = max_tokens if isinstance(max_tokens, int) and max_tokens > 0 else 8192
    if not enabled:
        return None, max_out
    budget = min(32000, max(1024, max_out // 2))
    if budget >= max_out:
        max_out = budget + 2048
    return {"type": "enabled", "budget_tokens": budget}, max_out
