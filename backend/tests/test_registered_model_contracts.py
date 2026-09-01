from __future__ import annotations

from pathlib import Path

import pytest
import purra

from infrastructure.models.profiles.registry import BUILTIN_MODEL_PROFILES
from tests.support.model_profile_contracts import (
    assert_core_has_no_registered_profile_names,
    assert_profile_adapter_contract,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    BUILTIN_MODEL_PROFILES,
    ids=lambda profile: profile.profile_id,
)
async def test_registered_profile_satisfies_provider_neutral_adapter_contract(
    profile,
):
    await assert_profile_adapter_contract(profile)


def test_purra_core_does_not_branch_on_registered_profile_or_model_names():
    root = Path(purra.__file__).resolve().parent
    sources = {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in root.rglob("*.py")
    }
    assert sources, "The installed PurrA source must be inspected"
    for profile in BUILTIN_MODEL_PROFILES:
        assert_core_has_no_registered_profile_names(profile, sources)
