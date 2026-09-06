"""Kimi K3 max-reasoning request profile."""

from typing import Any
from purra.model_protocol import ReasoningControl, ReasoningReplayPolicy

from infrastructure.models.profiles.base import ModelProfile


class KimiK3Profile(ModelProfile):
    profile_id = "moonshot:kimi-k3"
    model_names = frozenset({"kimi-k3"})
    base_urls = frozenset({
        "https://api.moonshot.ai/v1",
        "https://api.moonshot.cn/v1",
    })
    reasoning_control = ReasoningControl.ALWAYS_ENABLED
    reasoning_replay = ReasoningReplayPolicy.REQUIRED
    max_generation_tokens = 1_048_576
    capability_source = "https://platform.kimi.ai/docs/guide/kimi-k3-quickstart"
    supports_json_object_output = True
    openai_output_token_parameter = "max_completion_tokens"

    def build_openai_extra_body(
        self,
        thinking_enabled: bool | None,
    ) -> dict[str, Any]:
        if thinking_enabled is False:
            raise ValueError("Kimi K3 does not support disabling reasoning")
        return {"reasoning_effort": "max"}

KIMI_K3_PROFILE = KimiK3Profile()

__all__ = ["KIMI_K3_PROFILE"]
