"""Kimi K2.6 request profile."""

from infrastructure.models.profiles.base import ModelProfile


class KimiK26Profile(ModelProfile):
    profile_id = "moonshot:kimi-k2.6"
    model_names = frozenset({"kimi-k2.6"})
    base_urls = frozenset({"https://api.moonshot.cn/v1"})
    actionable = False


KIMI_K2_6_PROFILE = KimiK26Profile()

__all__ = ["KIMI_K2_6_PROFILE"]
