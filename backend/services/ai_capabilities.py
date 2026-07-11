"""
Provider-agnostic capability flags + per-provider translation helpers.

把模型"思考/推理"这件事抽象成统一布尔开关，再由
本模块翻译成各家 SDK 的真实参数形状：

  * OpenAI 兼容代理（Qwen/DashScope/智谱）→ ``extra_body.thinking={"type": ...}``
  * Anthropic Messages API → ``thinking={"type":"enabled","budget_tokens":N}``

调用方约定：
  * options 里**优先**读 ``thinking={"type":"enabled"|"disabled"}``。
  * 调用方**不应**再直接读 ``opts.get("thinking")``——adapter 想拿底层
    形状时，请只通过 ``build_anthropic_thinking_param`` /
    ``build_openai_thinking_extra_body`` 这两个翻译函数。
"""

from __future__ import annotations

from typing import Any


def normalize_thinking_enabled(opts: dict[str, Any] | None) -> bool:
    """从 options 推断 thinking 开关。"""
    o = opts or {}

    # 1) 新字段：thinking={"type":"enabled"|"disabled"}
    th = o.get("thinking")
    if isinstance(th, dict):
        t = th.get("type")
        if t == "enabled":
            return True
        if t == "disabled":
            return False

    # 2) 默认关。子专家 / 标题生成等窄任务保持向后兼容。
    return False


# ── OpenAI 兼容代理（Qwen/DashScope/智谱）─────────────────────────────

def build_openai_thinking_extra_body(enabled: bool) -> dict[str, Any]:
    """OpenAI 兼容代理用 ``extra_body.thinking`` 控制 CoT。

    返回值需合并入 ``params['extra_body']``。标准 OpenAI 不识别此字段，会忽略。
    """
    return {"thinking": {"type": "enabled" if enabled else "disabled"}}


# ── Anthropic Messages API ───────────────────────────────────────────

def build_anthropic_thinking_param(
    enabled: bool,
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
