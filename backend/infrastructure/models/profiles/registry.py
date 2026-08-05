"""Resolve built-in model behavior while preserving a generic custom path."""

from __future__ import annotations

from infrastructure.models.profiles.base import GenericModelProfile, ModelProfile
from infrastructure.models.profiles.glm5_2 import GLM5_2_PROFILE
from infrastructure.models.profiles.kimi_k3 import KIMI_K3_PROFILE
from infrastructure.models.profiles.kimi_k2_6 import KIMI_K2_6_PROFILE
from infrastructure.models.profiles.minimax_m3 import MINIMAX_M3_PROFILE
from infrastructure.models.profiles.mimo_v2_5_pro import MIMO_V2_5_PRO_PROFILE


BUILTIN_MODEL_PROFILES: tuple[ModelProfile, ...] = (
    GLM5_2_PROFILE,
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
        if explicit is not None:
            return explicit
    return next(
        (profile for profile in BUILTIN_MODEL_PROFILES if profile.matches(model, base_url)),
        GENERIC_MODEL_PROFILE,
    )


__all__ = ["BUILTIN_MODEL_PROFILES", "GENERIC_MODEL_PROFILE", "resolve_model_profile"]
