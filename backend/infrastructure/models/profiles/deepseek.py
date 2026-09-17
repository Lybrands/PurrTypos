"""DeepSeek vendor profile for the OpenAI-compatible API."""

from purra.model_protocol import (
    FeatureSupport,
    ReasoningReplayPolicy,
    ThinkingTokenAccounting,
)
from infrastructure.models.profiles.base import VendorModelProfile


class DeepSeekVendorProfile(VendorModelProfile):
    profile_id = "deepseek"
    base_urls = frozenset({
        "https://api.deepseek.com",
        "https://api.deepseek.com/v1",
    })
    # 默认按主推 deepseek-v4-flash 登记，用户可在配置中覆盖。
    max_generation_tokens = 393_216
    capability_source = "https://api-docs.deepseek.com/quick_start/pricing"
    thinking_token_accounting = ThinkingTokenAccounting.INCLUDED
    supports_json_object_output = True
    reasoning_effort_options = ("low", "high", "max")
    # The thinking endpoint rejects forced tool choice. AUTO remains usable,
    # and PurrA keeps the logical tool requirement fail-closed.
    required_tool_choice = FeatureSupport.UNAVAILABLE
    reasoning_replay = ReasoningReplayPolicy.REQUIRED


DEEPSEEK_VENDOR_PROFILE = DeepSeekVendorProfile()

__all__ = ["DEEPSEEK_VENDOR_PROFILE"]
