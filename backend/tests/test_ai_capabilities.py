"""
ai_capabilities 行为快照。

把"thinking 开关抽象 → 各家 SDK 真实参数"的翻译规则锁住。
任何一家厂商改 API（例如 Anthropic 调整 thinking 字段名），这里会第一时间炸。
"""

from __future__ import annotations

import pytest

from infrastructure.models.capabilities import (
    build_anthropic_thinking_param,
    build_openai_thinking_extra_body,
    normalize_thinking_enabled,
)


# ---------------------------------------------------------------------------
# normalize_thinking_enabled
# ---------------------------------------------------------------------------

class TestNormalizeThinkingEnabled:
    def test_structured_thinking_enabled(self):
        assert normalize_thinking_enabled({"thinking": {"type": "enabled"}}) is True

    def test_structured_thinking_disabled(self):
        assert normalize_thinking_enabled({"thinking": {"type": "disabled"}}) is False

    def test_default_off(self):
        assert normalize_thinking_enabled({}) is False
        assert normalize_thinking_enabled(None) is False

    def test_thinking_non_dict_ignored(self):
        assert normalize_thinking_enabled({"thinking": "enabled"}) is False


# ---------------------------------------------------------------------------
# build_openai_thinking_extra_body
# ---------------------------------------------------------------------------

class TestBuildOpenAIThinkingExtraBody:
    def test_enabled(self):
        assert build_openai_thinking_extra_body(True) == {
            "thinking": {"type": "enabled"},
        }

    def test_disabled(self):
        assert build_openai_thinking_extra_body(False) == {
            "thinking": {"type": "disabled"},
        }


# ---------------------------------------------------------------------------
# build_anthropic_thinking_param
# ---------------------------------------------------------------------------

class TestBuildAnthropicThinkingParam:
    def test_disabled_returns_none_param(self):
        param, max_out = build_anthropic_thinking_param(False, 4096)
        assert param is None
        assert max_out == 4096

    def test_disabled_with_invalid_max_tokens_uses_default(self):
        param, max_out = build_anthropic_thinking_param(False, None)
        assert param is None
        assert max_out == 8192

    def test_enabled_basic(self):
        param, max_out = build_anthropic_thinking_param(True, 8192)
        assert param == {"type": "enabled", "budget_tokens": 4096}
        assert max_out == 8192
        assert max_out > param["budget_tokens"]

    def test_min_budget_floor_1024(self):
        # max_out=1024 → budget floor=1024，因 budget>=max_out 触发膨胀
        param, max_out = build_anthropic_thinking_param(True, 1024)
        assert param["budget_tokens"] == 1024
        assert max_out == 1024 + 2048

    def test_max_budget_cap_32000(self):
        # max_tokens 很大时 budget 上限应被 32000 截住
        param, max_out = build_anthropic_thinking_param(True, 200000)
        assert param["budget_tokens"] == 32000
        assert max_out > 32000

    def test_invalid_max_tokens_uses_default(self):
        # 非 int / 非正数 → 默认 8192
        param, max_out = build_anthropic_thinking_param(True, None)
        assert param == {"type": "enabled", "budget_tokens": 4096}
        assert max_out == 8192

        param2, max_out2 = build_anthropic_thinking_param(True, -10)
        assert param2 == {"type": "enabled", "budget_tokens": 4096}
        assert max_out2 == 8192

    @pytest.mark.parametrize("max_in", [2048, 4096, 8192, 16384])
    def test_invariant_max_gt_budget(self, max_in: int):
        param, max_out = build_anthropic_thinking_param(True, max_in)
        assert param is not None
        # 关键约束：Anthropic 要求 max_tokens > budget
        assert max_out > param["budget_tokens"]
