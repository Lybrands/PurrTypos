"""MiniMax M3 native reasoning profile."""

from typing import Any
from purra.model_protocol import ReasoningReplayPolicy

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
    max_call_output_tokens = 524_288
    capability_source = "https://platform.minimaxi.com/docs/api-reference/text-chat-openai"
    supports_json_object_output = True
    reasoning_replay = ReasoningReplayPolicy.REQUIRED
    openai_output_token_parameter = "max_completion_tokens"
    provider_protocol = "openai_anthropic_compatible"

    def build_openai_extra_body(
        self,
        thinking_enabled: bool | None,
    ) -> dict[str, Any]:
        # MiniMax reasons natively.  reasoning_split exposes the stream as
        # reasoning_content/reasoning_details instead of folding it into text.
        extra_body: dict[str, Any] = {"reasoning_split": True}
        if thinking_enabled is not None:
            extra_body["thinking"] = {
                "type": "adaptive" if thinking_enabled else "disabled",
            }
        return extra_body


MINIMAX_M3_PROFILE = MiniMaxM3Profile()

__all__ = ["MINIMAX_M3_PROFILE"]
