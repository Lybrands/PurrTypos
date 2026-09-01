"""Kimi K2.6 request profile."""

from purra.model_protocol import FeatureSupport, ReasoningReplayPolicy

from infrastructure.models.profiles.base import ModelProfile


class KimiK26Profile(ModelProfile):
    profile_id = "moonshot:kimi-k2.6"
    model_names = frozenset({"kimi-k2.6"})
    base_urls = frozenset({
        "https://api.moonshot.ai/v1",
        "https://api.moonshot.cn/v1",
    })
    max_call_output_tokens = 262_144
    capability_source = "https://platform.kimi.ai/docs/guide/kimi-k2-6-quickstart"
    supports_json_object_output = True
    reasoning_replay = ReasoningReplayPolicy.REQUIRED
    required_tool_choice = FeatureSupport.UNAVAILABLE
    openai_output_token_parameter = "max_completion_tokens"


KIMI_K2_6_PROFILE = KimiK26Profile()

__all__ = ["KIMI_K2_6_PROFILE"]
