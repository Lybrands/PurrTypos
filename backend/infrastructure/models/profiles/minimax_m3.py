"""MiniMax M3 native reasoning profile."""

from typing import Any
from purra.model_protocol import ReasoningControl

from infrastructure.models.profiles.base import ModelProfile


class MiniMaxM3Profile(ModelProfile):
    profile_id = "minimax:MiniMax-M3"
    model_names = frozenset({"minimax-m3", "minimax-m3.0"})
    base_urls = frozenset({
        "https://api.minimax.io/v1",
        "https://api.minimaxi.com/v1",
        "https://api.minimax.io/anthropic",
        "https://api.minimaxi.com/anthropic",
    })
    native_anthropic_thinking = True
    reasoning_control = ReasoningControl.ALWAYS_ENABLED

    def build_openai_extra_body(self, thinking_enabled: bool) -> dict[str, Any]:
        # MiniMax reasons natively.  reasoning_split exposes the stream as
        # reasoning_content/reasoning_details instead of folding it into text.
        return {"reasoning_split": True}


MINIMAX_M3_PROFILE = MiniMaxM3Profile()

__all__ = ["MINIMAX_M3_PROFILE"]
