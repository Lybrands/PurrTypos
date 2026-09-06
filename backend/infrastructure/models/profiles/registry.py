"""Resolve built-in model behavior while preserving a generic custom path."""

from __future__ import annotations

from infrastructure.models.profiles.base import GenericModelProfile, ModelProfile
from infrastructure.models.profiles.deepseek_v4 import DEEPSEEK_V4_FLASH_PROFILE
from infrastructure.models.profiles.glm5_3_flash import GLM5_3_FLASH_PROFILE
from infrastructure.models.profiles.kimi_k3 import KIMI_K3_PROFILE
from infrastructure.models.profiles.kimi_k2_6 import KIMI_K2_6_PROFILE
from infrastructure.models.profiles.minimax_m3 import MINIMAX_M3_PROFILE
from infrastructure.models.profiles.mimo_v2_5_pro import MIMO_V2_5_PRO_PROFILE


def _validated_profiles(*profiles: ModelProfile) -> tuple[ModelProfile, ...]:
    ids = [profile.profile_id for profile in profiles]
    if len(ids) != len(set(ids)):
        raise RuntimeError("built-in model profile ids must be unique")
    invalid = [
        profile.profile_id
        for profile in profiles
        if profile.actionable and profile.max_generation_tokens is None
    ]
    if invalid:
        raise RuntimeError(
            "actionable model profiles require max_generation_tokens: "
            + ", ".join(invalid)
        )
    return tuple(profiles)


BUILTIN_MODEL_PROFILES: tuple[ModelProfile, ...] = _validated_profiles(
    GLM5_3_FLASH_PROFILE,
    DEEPSEEK_V4_FLASH_PROFILE,
    KIMI_K3_PROFILE,
    KIMI_K2_6_PROFILE,
    MINIMAX_M3_PROFILE,
    MIMO_V2_5_PRO_PROFILE,
)
GENERIC_MODEL_PROFILE = GenericModelProfile()


def resolve_model_profile(
    profile_id: str | None,
    model: str,
    base_url: str | None,
) -> ModelProfile:
    normalized_id = str(profile_id or "").strip()
    if normalized_id:
        explicit = next(
            (profile for profile in BUILTIN_MODEL_PROFILES if profile.profile_id == normalized_id),
            None,
        )
        if explicit is None:
            raise ValueError(f"unknown model profile: {normalized_id}")
        return explicit
    return GENERIC_MODEL_PROFILE


__all__ = ["BUILTIN_MODEL_PROFILES", "GENERIC_MODEL_PROFILE", "resolve_model_profile"]
