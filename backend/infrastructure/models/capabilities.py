"""
Provider capability normalization and SDK parameter translation.

把模型"思考/推理"保留为 default/enabled/disabled 三态，再由
本模块等价翻译成各家 SDK 的真实参数形状：

  * OpenAI 兼容代理（Qwen/DashScope/智谱）→ ``extra_body.thinking={"type": ...}``
  * Anthropic Messages API → ``thinking={"type":"enabled","budget_tokens":N}``

调用方约定：
  * options 里**优先**读 ``thinking={"type":"enabled"|"disabled"}``。
  * 没有 thinking 字段就是 Provider default，不得改写成 disabled。
  * 调用方**不应**再直接读 ``opts.get("thinking")``——adapter 想拿底层
    形状时，请只通过 ``build_anthropic_thinking_param`` /
    ``build_openai_thinking_extra_body`` 这两个翻译函数。
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
    if value not in {"enabled", "disabled"}:
        raise ValueError("thinking.type must be enabled or disabled")
    mode = ReasoningMode(value)
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


# ── OpenAI 兼容代理（Qwen/DashScope/智谱）─────────────────────────────

def build_openai_thinking_extra_body(enabled: bool | None) -> dict[str, Any]:
    """OpenAI 兼容代理用 ``extra_body.thinking`` 控制 CoT。

    返回值需合并入 ``params['extra_body']``。标准 OpenAI 不识别此字段，会忽略。
    """
    if enabled is None:
        return {}
    return {"thinking": {"type": "enabled" if enabled else "disabled"}}


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
