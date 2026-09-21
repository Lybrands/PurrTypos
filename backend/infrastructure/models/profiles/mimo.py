"""Xiaomi MiMo vendor profile：模型名由用户填写。"""

from infrastructure.models.profiles.base import VendorModelProfile


class MiMoVendorProfile(VendorModelProfile):
    profile_id = "mimo"
    base_urls = frozenset({"https://api.xiaomimimo.com/v1"})
    # 默认按主推 mimo-v2.5-pro 登记，用户可在配置中覆盖。
    max_generation_tokens = 131_072
    capability_source = "https://mimo.mi.com/docs/en-US/api/chat/responses"
    supports_json_object_output = True
    default_thinking_enabled = False
    default_temperature_thinking = 0.6
    default_temperature_non_thinking = 0.6


MIMO_VENDOR_PROFILE = MiMoVendorProfile()

__all__ = ["MIMO_VENDOR_PROFILE"]
