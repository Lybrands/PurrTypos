"""Moonshot (Kimi) vendor profile：模型名由用户填写。"""

from purra.model_protocol import FeatureSupport, ReasoningReplayPolicy
from infrastructure.models.profiles.base import VendorModelProfile


class MoonshotVendorProfile(VendorModelProfile):
    profile_id = "moonshot"
    base_urls = frozenset({
        "https://api.moonshot.ai/v1",
        "https://api.moonshot.cn/v1",
    })
    # 默认按主推 kimi-k3 登记，用户可在配置中覆盖（如 kimi-k2.6 为 262144）。
    max_generation_tokens = 1_048_576
    capability_source = "https://platform.kimi.ai/docs/guide/kimi-k3-quickstart"
    supports_json_object_output = True
    # 上下文档位取 k3/k2.6 的并集，默认选跨模型安全的 256k。
    context_window_options = ("32k", "128k", "256k", "1m")
    default_context_window = "256k"
    default_temperature_non_thinking = 0.6
    reasoning_replay = ReasoningReplayPolicy.REQUIRED
    required_tool_choice = FeatureSupport.UNAVAILABLE
    openai_output_token_parameter = "max_completion_tokens"


MOONSHOT_VENDOR_PROFILE = MoonshotVendorProfile()

__all__ = ["MOONSHOT_VENDOR_PROFILE"]
