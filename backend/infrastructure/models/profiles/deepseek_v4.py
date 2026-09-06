"""DeepSeek V4 request profiles for the OpenAI-compatible API."""

from purra.model_protocol import (
    FeatureSupport,
    ReasoningReplayPolicy,
    ThinkingTokenAccounting,
)
from infrastructure.models.profiles.base import ModelProfile


class DeepSeekV4FlashProfile(ModelProfile):
    profile_id = "deepseek:deepseek-v4-flash"
    model_names = frozenset({"deepseek-v4-flash"})
    base_urls = frozenset({
        "https://api.deepseek.com",
        "https://api.deepseek.com/v1",
    })
    max_generation_tokens = 393_216
    capability_source = "https://api-docs.deepseek.com/quick_start/pricing"
    thinking_token_accounting = ThinkingTokenAccounting.INCLUDED
    supports_json_object_output = True
    reasoning_effort_options = ("low", "high", "max")
    reasoning_replay = ReasoningReplayPolicy.REQUIRED
    # The thinking endpoint rejects forced tool choice. AUTO remains usable,
    # and PurrA keeps the logical tool requirement fail-closed.
    required_tool_choice = FeatureSupport.UNAVAILABLE


DEEPSEEK_V4_FLASH_PROFILE = DeepSeekV4FlashProfile()

__all__ = ["DEEPSEEK_V4_FLASH_PROFILE"]
