"""Xiaomi MiMo V2.5 Pro request profile."""

from infrastructure.models.profiles.base import ModelProfile


class MiMoV25ProProfile(ModelProfile):
    profile_id = "mimo:mimo-v2.5-pro"
    model_names = frozenset({"mimo-v2.5-pro"})
    base_urls = frozenset({"https://api.xiaomimimo.com/v1"})
    max_output_tokens = 131_072


MIMO_V2_5_PRO_PROFILE = MiMoV25ProProfile()

__all__ = ["MIMO_V2_5_PRO_PROFILE"]
