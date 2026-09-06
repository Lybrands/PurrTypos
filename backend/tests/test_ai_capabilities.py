"""
ai_capabilities 行为快照。

把"thinking 开关抽象 → 各家 SDK 真实参数"的翻译规则锁住。
任何一家厂商改 API（例如 Anthropic 调整 thinking 字段名），这里会第一时间炸。
"""

from __future__ import annotations

import pytest
from purra.errors import ContractViolationError

from infrastructure.models.profiles.base import ModelProfile

from infrastructure.models.capabilities import (
    build_anthropic_thinking_param,
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

    def test_default_is_preserved(self):
        assert normalize_thinking_enabled({}) is None
        assert normalize_thinking_enabled(None) is None

    def test_invalid_thinking_is_rejected(self):
        with pytest.raises(ValueError, match="thinking must be an object"):
            normalize_thinking_enabled({"thinking": "enabled"})


# ---------------------------------------------------------------------------
# ModelProfile.build_openai_extra_body
# ---------------------------------------------------------------------------

class TestModelProfileReasoningParameters:
    def test_enabled(self):
        assert ModelProfile().build_openai_extra_body(True) == {
            "thinking": {"type": "enabled"},
        }

    def test_disabled(self):
        assert ModelProfile().build_openai_extra_body(False) == {
            "thinking": {"type": "disabled"},
        }

    def test_default_is_omitted(self):
        assert ModelProfile().build_openai_extra_body(None) == {}


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

    def test_enabled_preserves_explicit_budget_and_generation_limit(self):
        configured = {
            "type": "enabled",
            "budget_tokens": 3072,
        }
        param, max_out = build_anthropic_thinking_param(
            True,
            8192,
            configured,
        )
        assert param == configured
        assert max_out == 8192
        assert param is not configured

    @pytest.mark.parametrize(
        "thinking",
        [None, {"type": "enabled"}],
    )
    def test_enabled_without_explicit_budget_fails_closed(self, thinking):
        with pytest.raises(ContractViolationError) as caught:
            build_anthropic_thinking_param(
                True,
                8192,
                thinking,
            )

        assert caught.value.code == "anthropic_thinking_budget_required"

    @pytest.mark.parametrize("budget", [1023, 4096, True, "2048"])
    def test_invalid_explicit_budget_is_never_clamped(self, budget):
        with pytest.raises(ContractViolationError) as caught:
            build_anthropic_thinking_param(
                True,
                4096,
                {"type": "enabled", "budget_tokens": budget},
            )

        assert caught.value.code == "anthropic_thinking_budget_invalid"

    @pytest.mark.parametrize("max_tokens", [None, 0, -10, True])
    def test_enabled_requires_a_resolved_generation_limit(self, max_tokens):
        with pytest.raises(ContractViolationError) as caught:
            build_anthropic_thinking_param(
                True,
                max_tokens,
                {"type": "enabled", "budget_tokens": 1024},
            )

        assert caught.value.code == "anthropic_generation_limit_required"

    def test_adaptive_thinking_is_preserved_without_inventing_a_budget(self):
        param, max_out = build_anthropic_thinking_param(
            True,
            8192,
            {"type": "adaptive"},
        )

        assert param == {"type": "adaptive"}
        assert max_out == 8192
