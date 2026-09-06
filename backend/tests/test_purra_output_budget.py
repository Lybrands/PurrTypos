from __future__ import annotations

from dataclasses import replace

import pytest

from purra.errors import UnsupportedModelFeatureError
from purra.model_protocol import generic_capability_snapshot
from purra.model_protocol.output_limits import (
    GenerationBudgetSource,
    resolve_invocation_output_budget,
)


def _snapshot(max_generation_tokens: int | None = 393_216):
    return replace(
        generic_capability_snapshot(),
        profile_id="fixture:model",
        max_generation_tokens=max_generation_tokens,
    )


def test_profile_limit_is_the_default_invocation_output_limit():
    limit = resolve_invocation_output_budget(
        _snapshot(),
        max_generation_tokens=None,
    )

    assert limit.max_generation_tokens == 393_216
    assert limit.profile_max_generation_tokens == 393_216
    assert limit.generation_source is GenerationBudgetSource.MODEL_PROFILE
    assert limit.to_mapping() == {
        "maxGenerationTokens": 393_216,
        "generationSource": "model_profile",
        "profileMaxGenerationTokens": 393_216,
        "requestedUserMaxGenerationTokens": None,
        "resultCapacityTargetTokens": None,
        "resultCapacitySource": None,
        "nonResultHeadroomTokens": None,
    }


def test_explicit_user_override_is_preserved_without_rescaling():
    limit = resolve_invocation_output_budget(
        _snapshot(),
        max_generation_tokens=256_000,
    )

    assert limit.max_generation_tokens == 256_000
    assert limit.profile_max_generation_tokens == 393_216
    assert limit.generation_source is GenerationBudgetSource.USER


@pytest.mark.parametrize("reasoning_mode", ["enabled", "disabled"])
def test_reasoning_mode_cannot_change_the_resolved_limit(reasoning_mode):
    del reasoning_mode

    assert resolve_invocation_output_budget(
        _snapshot(),
        max_generation_tokens=256_000,
    ).max_generation_tokens == 256_000


def test_user_override_above_profile_limit_fails_before_provider_call():
    with pytest.raises(UnsupportedModelFeatureError) as captured:
        resolve_invocation_output_budget(
            _snapshot(),
            max_generation_tokens=400_000,
        )

    assert captured.value.code == "model_generation_limit_exceeded"
    assert captured.value.retryable is False


def test_profile_without_verified_output_limit_is_not_inherited_or_guessed():
    with pytest.raises(UnsupportedModelFeatureError) as captured:
        resolve_invocation_output_budget(
            _snapshot(max_generation_tokens=None),
            max_generation_tokens=10_000,
        )

    assert captured.value.code == "model_generation_limit_unknown"
    assert captured.value.retryable is False
