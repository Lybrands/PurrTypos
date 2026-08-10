"""Z.ai GLM-5.2 request profile."""

from infrastructure.models.profiles.base import ModelProfile


class Glm5_2Profile(ModelProfile):
    profile_id = "zai:glm-5.2"
    model_names = frozenset({"glm-5.2"})
    base_urls = frozenset({"https://open.bigmodel.cn/api/paas/v4"})
    max_output_tokens = 131_072
    supports_json_object_output = True


GLM5_2_PROFILE = Glm5_2Profile()

__all__ = ["GLM5_2_PROFILE"]
