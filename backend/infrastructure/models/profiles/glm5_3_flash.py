"""Z.ai GLM-5.3-Flash request profile."""

from typing import Any

from purra.model_protocol import ReasoningControl

from infrastructure.models.profiles.base import ModelProfile


class Glm5_3FlashProfile(ModelProfile):
    profile_id = "zai:glm-5.3-flash"
    model_names = frozenset({"glm-5.3-flash"})
    base_urls = frozenset({"https://open.bigmodel.cn/api/paas/v4"})
    max_generation_tokens = 131_072
    capability_source = "https://docs.z.ai/guides/vlm/glm-5.3-flash"
    supports_json_object_output = True
    reasoning_control = ReasoningControl.ALWAYS_ENABLED
    reasoning_effort_options = ("low", "high", "max")
    task_reasoning_preferences = {"economical": "low"}
    customize_temperature = True

    def build_openai_extra_body(
        self,
        thinking_enabled: bool | None,
    ) -> dict[str, Any]:
        if thinking_enabled is False:
            raise ValueError("GLM-5.3-Flash does not support disabling reasoning")
        return {"thinking": {"type": "enabled"}}


GLM5_3_FLASH_PROFILE = Glm5_3FlashProfile()

__all__ = ["GLM5_3_FLASH_PROFILE"]
