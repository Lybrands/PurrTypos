"""Z.ai (智谱) vendor profile：模型名由用户填写。"""

from infrastructure.models.profiles.base import VendorModelProfile


class ZaiVendorProfile(VendorModelProfile):
    profile_id = "zai"
    base_urls = frozenset({"https://open.bigmodel.cn/api/paas/v4"})
    # 默认按主推 glm-5.3-flash 登记，用户可在配置中覆盖。
    max_generation_tokens = 131_072
    capability_source = "https://docs.z.ai/guides/vlm/glm-5.3-flash"
    supports_json_object_output = True
    reasoning_effort_options = ("low", "high", "max")
    task_reasoning_preferences = {"economical": "low"}
    customize_temperature = True


ZAI_VENDOR_PROFILE = ZaiVendorProfile()

__all__ = ["ZAI_VENDOR_PROFILE"]
