"""DeepSeek V4 request profiles for the OpenAI-compatible API."""

from purra.output_budget import ThinkingTokenAccounting
from infrastructure.models.profiles.base import ModelProfile


class DeepSeekV4ProProfile(ModelProfile):
    profile_id = "deepseek:deepseek-v4-pro"
    model_names = frozenset({"deepseek-v4-pro"})
    base_urls = frozenset({
        "https://api.deepseek.com",
        "https://api.deepseek.com/v1",
    })
    max_output_tokens = 393_216
    thinking_token_accounting = ThinkingTokenAccounting.INCLUDED
    supports_json_object_output = True


class DeepSeekV4FlashProfile(ModelProfile):
    profile_id = "deepseek:deepseek-v4-flash"
    model_names = frozenset({"deepseek-v4-flash"})
    base_urls = frozenset({
        "https://api.deepseek.com",
        "https://api.deepseek.com/v1",
    })
    max_output_tokens = 393_216
    thinking_token_accounting = ThinkingTokenAccounting.INCLUDED
    supports_json_object_output = True


DEEPSEEK_V4_PRO_PROFILE = DeepSeekV4ProProfile()
DEEPSEEK_V4_FLASH_PROFILE = DeepSeekV4FlashProfile()

__all__ = ["DEEPSEEK_V4_FLASH_PROFILE", "DEEPSEEK_V4_PRO_PROFILE"]
