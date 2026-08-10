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

    def build_openai_extra_body(self, thinking_enabled: bool) -> dict[str, Any]:
        # K3 currently exposes max reasoning only.  Unknown or lower efforts
        # are rejected by the provider, so this profile deliberately ignores
        # callers that try to disable reasoning in narrow internal requests.
        return {"reasoning_effort": "max"}

    def internal_output_token_floor(self) -> int:
        # Planner, compaction, and judge calls request concise JSON and normally
        # use a small token cap.  K3 cannot disable max reasoning, so that cap
        # must also leave room for its hidden reasoning before the final JSON.
        return 8_192


KIMI_K3_PROFILE = KimiK3Profile()

__all__ = ["KIMI_K3_PROFILE"]
