"""
ai_capabilities 行为快照。

把"reasoning_mode 抽象 → 各家 SDK 真实参数"的翻译规则锁住。
任何一家厂商改 API（例如 Anthropic 调整 thinking 字段名），这里会第一时间炸。
"""

from __future__ import annotations

import pytest

from services.ai_capabilities import (
    build_anthropic_thinking_param,
    build_openai_thinking_extra_body,
    normalize_reasoning_mode,
)


# ---------------------------------------------------------------------------
# normalize_reasoning_mode
# ---------------------------------------------------------------------------

class TestNormalizeReasoningMode:
    def test_explicit_on(self):
        assert normalize_reasoning_mode({"reasoning_mode": "on"}) == "on"

    def test_explicit_off(self):
        assert normalize_reasoning_mode({"reasoning_mode": "off"}) == "off"

    def test_legacy_thinking_enabled(self):
        assert normalize_reasoning_mode({"thinking": {"type": "enabled"}}) == "on"

    def test_legacy_thinking_disabled(self):
        assert normalize_reasoning_mode({"thinking": {"type": "disabled"}}) == "off"

    def test_new_field_overrides_legacy(self):
        # 新字段优先级高于 legacy thinking
        opts = {"reasoning_mode": "off", "thinking": {"type": "enabled"}}
        assert normalize_reasoning_mode(opts) == "off"

    def test_default_off(self):
        assert normalize_reasoning_mode({}) == "off"
        assert normalize_reasoning_mode(None) == "off"

    def test_invalid_reasoning_mode_falls_through(self):
        # reasoning_mode 不在 ("on","off") 时应继续走 thinking 兼容 / 默认
        assert normalize_reasoning_mode({"reasoning_mode": "garbage"}) == "off"
        assert normalize_reasoning_mode(
            {"reasoning_mode": "garbage", "thinking": {"type": "enabled"}}
        ) == "on"

    def test_thinking_non_dict_ignored(self):
        assert normalize_reasoning_mode({"thinking": "enabled"}) == "off"


# ---------------------------------------------------------------------------
# build_openai_thinking_extra_body
# ---------------------------------------------------------------------------

class TestBuildOpenAIThinkingExtraBody:
    def test_on(self):
        assert build_openai_thinking_extra_body("on") == {
            "thinking": {"type": "enabled"},
        }

    def test_off(self):
        assert build_openai_thinking_extra_body("off") == {
            "thinking": {"type": "disabled"},
        }


# ---------------------------------------------------------------------------
# build_anthropic_thinking_param
# ---------------------------------------------------------------------------

class TestBuildAnthropicThinkingParam:
    def test_off_returns_none_param(self):
        param, max_out = build_anthropic_thinking_param("off", 4096)
        assert param is None
        assert max_out == 4096

    def test_off_with_invalid_max_tokens_uses_default(self):
        param, max_out = build_anthropic_thinking_param("off", None)
        assert param is None
        assert max_out == 8192

    def test_on_basic(self):
        param, max_out = build_anthropic_thinking_param("on", 8192)
        assert param == {"type": "enabled", "budget_tokens": 4096}
        assert max_out == 8192
        assert max_out > param["budget_tokens"]

    def test_on_min_budget_floor_1024(self):
        # max_out=1024 → budget=max(1024, 512)=1024，因 budget>=max_out 触发膨胀
        param, max_out = build_anthropic_thinking_param("on", 1024)
        assert param["budget_tokens"] == 1024
        assert max_out == 1024 + 2048

    def test_on_max_budget_cap_32000(self):
        # max_tokens 很大时 budget 上限应被 32000 截住
        param, max_out = build_anthropic_thinking_param("on", 200000)
        assert param["budget_tokens"] == 32000
        assert max_out > 32000

    def test_on_invalid_max_tokens_uses_default(self):
        # 非 int / 非正数 → 默认 8192
        param, max_out = build_anthropic_thinking_param("on", None)
        assert param == {"type": "enabled", "budget_tokens": 4096}
        assert max_out == 8192

        param2, max_out2 = build_anthropic_thinking_param("on", -10)
        assert param2 == {"type": "enabled", "budget_tokens": 4096}
        assert max_out2 == 8192

    @pytest.mark.parametrize("max_in", [2048, 4096, 8192, 16384])
    def test_on_invariant_max_gt_budget(self, max_in: int):
        param, max_out = build_anthropic_thinking_param("on", max_in)
        assert param is not None
        # 关键约束：Anthropic 要求 max_tokens > budget
        assert max_out > param["budget_tokens"]
