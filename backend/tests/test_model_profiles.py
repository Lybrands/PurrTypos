from __future__ import annotations

from types import SimpleNamespace

import pytest

from application.model_runtime import model_request_from_runtime
from infrastructure.models.capabilities import normalize_thinking_enabled
from infrastructure.models.profiles.registry import (
    BUILTIN_MODEL_PROFILES,
    LEGACY_MODEL_PROFILES,
    resolve_model_profile,
)
from purra.contracts import ReasoningMode
from purra.model_protocol import (
    FeatureSupport,
    ReasoningControl,
    ReasoningReplayPolicy,
)


def test_registry_resolves_each_vendor_profile_and_generic_fallback():
    zai = resolve_model_profile(
        "zai",
        "glm-4.7",
        "https://open.bigmodel.cn/api/paas/v4/",
    )
    assert zai.profile_id == "zai"
    assert zai.build_openai_extra_body(True) == {"thinking": {"type": "enabled"}}
    assert zai.build_openai_extra_body(False) == {"thinking": {"type": "disabled"}}
    assert zai.protocol_capabilities().reasoning_control is (
        ReasoningControl.SELECTABLE
    )
    assert zai.output_capabilities().max_generation_tokens == 131_072
    assert resolve_model_profile(
        "deepseek",
        "deepseek-v4",
        "https://api.deepseek.com/v1/",
    ).profile_id == "deepseek"
    assert resolve_model_profile(
        "moonshot",
        "kimi-k3",
        "https://api.moonshot.cn/v1",
    ).profile_id == "moonshot"
    assert resolve_model_profile(
        "minimax",
        "MiniMax-M3",
        "https://api.minimaxi.com/v1",
    ).profile_id == "minimax"
    mimo = resolve_model_profile(
        "mimo",
        "mimo-v3",
        "https://api.xiaomimimo.com/v1",
    )
    assert mimo.profile_id == "mimo"
    assert mimo.output_capabilities().max_generation_tokens == 131_072
    generic = resolve_model_profile(
        None,
        "custom-model",
        "https://proxy.example/v1",
    )
    assert generic.profile_id == "generic"
    assert generic.output_capabilities().max_generation_tokens is None
    with pytest.raises(ValueError, match="unknown model profile"):
        resolve_model_profile(
            "unknown:profile",
            "custom-model",
            "https://proxy.example/v1",
        )


def test_legacy_model_profiles_stay_resolvable_with_frozen_behavior():
    glm = resolve_model_profile(
        "zai:glm-5.3-flash",
        "glm-5.3-flash",
        "https://open.bigmodel.cn/api/paas/v4/",
    )
    assert glm.profile_id == "zai:glm-5.3-flash"
    with pytest.raises(ValueError, match="does not support disabling"):
        glm.build_openai_extra_body(False)
    kimi = resolve_model_profile(
        "moonshot:kimi-k3",
        "kimi-k3",
        "https://api.moonshot.cn/v1",
    )
    assert kimi.build_openai_extra_body(True) == {"reasoning_effort": "max"}
    assert {profile.profile_id for profile in LEGACY_MODEL_PROFILES} == {
        "zai:glm-5.3-flash",
        "deepseek:deepseek-v4-flash",
        "moonshot:kimi-k3",
        "moonshot:kimi-k2.6",
        "minimax:MiniMax-M3",
        "mimo:mimo-v2.5-pro",
    }
    assert {profile.profile_id for profile in BUILTIN_MODEL_PROFILES}.isdisjoint(
        {profile.profile_id for profile in LEGACY_MODEL_PROFILES}
    )


def test_vendor_profiles_match_endpoint_only():
    zai = resolve_model_profile("zai", "any-model-name", None)
    assert zai.matches("any-model-name", "https://open.bigmodel.cn/api/paas/v4/")
    assert zai.matches("other-name", "https://open.bigmodel.cn/api/paas/v4")
    assert not zai.matches("any-model-name", "https://proxy.example/v1")


def test_minimax_profile_owns_reasoning_request_and_response_normalization():
    profile = resolve_model_profile(
        "minimax",
        "MiniMax-M3",
        "https://api.minimaxi.com/v1",
    )

    assert profile.build_openai_extra_body(True) == {
        "reasoning_split": True,
        "thinking": {"type": "adaptive"},
    }
    assert profile.build_openai_extra_body(False) == {
        "reasoning_split": True,
        "thinking": {"type": "disabled"},
    }
    assert profile.protocol_capabilities().reasoning_replay is (
        ReasoningReplayPolicy.REQUIRED
    )
    normalized = profile.normalize_openai_chunk({
        "choices": [{
            "delta": {"reasoning": "native reasoning"},
            "finish_reason": None,
        }],
    })
    assert normalized["choices"][0]["delta"]["reasoning_content"] == (
        "native reasoning"
    )


def test_generic_profile_omits_undeclared_thinking_extensions():
    profile = resolve_model_profile(
        None,
        "custom-model",
        "https://proxy.example/v1",
    )

    assert profile.build_openai_extra_body(False) == {}
    assert profile.build_openai_extra_body(True) == {}
    assert profile.protocol_capabilities().reasoning_control is (
        ReasoningControl.UNAVAILABLE
    )


def test_vendor_thinking_is_user_declared_and_never_forced():
    for profile in BUILTIN_MODEL_PROFILES:
        capabilities = profile.protocol_capabilities()
        assert capabilities.reasoning_control is ReasoningControl.SELECTABLE
        assert capabilities.reasoning_mode_is_supported(ReasoningMode.DISABLED)
        assert capabilities.reasoning_mode_is_supported(ReasoningMode.ENABLED)


def test_moonshot_vendor_declares_thinking_tool_choice_constraint():
    profile = resolve_model_profile(
        "moonshot",
        "kimi-k2.6",
        "https://api.moonshot.cn/v1",
    )

    capabilities = profile.protocol_capabilities()
    assert capabilities.reasoning_replay is ReasoningReplayPolicy.REQUIRED
    assert capabilities.required_tool_choice is FeatureSupport.UNAVAILABLE


def test_builtin_profiles_publish_stable_versioned_capability_snapshots():
    first = {
        profile.profile_id: profile.capability_snapshot(
            context_window_tokens=1_000_000,
        )
        for profile in BUILTIN_MODEL_PROFILES
    }
    second = {
        profile.profile_id: profile.capability_snapshot(
            context_window_tokens=1_000_000,
        )
        for profile in BUILTIN_MODEL_PROFILES
    }

    assert set(first) == set(second)
    assert len(first) == len(BUILTIN_MODEL_PROFILES)
    for profile_id, snapshot in first.items():
        assert snapshot.schema_version == 2
        assert snapshot.profile_id == profile_id
        assert snapshot.provider_protocol
        assert snapshot.digest() == second[profile_id].digest()
        assert len(snapshot.digest()) == 64
        if snapshot.actionable:
            assert snapshot.max_generation_tokens is not None


@pytest.mark.parametrize(
    ("profile_id", "expected_max_output"),
    [
        ("zai", 131_072),
        ("deepseek", 393_216),
        ("moonshot", 1_048_576),
        ("minimax", 524_288),
        ("mimo", 131_072),
    ],
)
def test_actionable_profile_output_limits_are_owned_by_each_profile(
    profile_id,
    expected_max_output,
):
    profile = next(
        item for item in BUILTIN_MODEL_PROFILES if item.profile_id == profile_id
    )
    snapshot = profile.capability_snapshot(context_window_tokens=1_000_000)

    assert snapshot.actionable is True
    assert snapshot.max_generation_tokens == expected_max_output

@pytest.mark.parametrize(
    ("profile_id", "expected_parameter"),
    [
        ("moonshot", "max_completion_tokens"),
        ("minimax", "max_completion_tokens"),
        ("deepseek", "max_tokens"),
        ("zai", "max_tokens"),
    ],
)
def test_profiles_translate_internal_output_limit_to_provider_parameter(
    profile_id,
    expected_parameter,
):
    profile = next(
        item for item in BUILTIN_MODEL_PROFILES if item.profile_id == profile_id
    )
    params = {}

    profile.apply_openai_output_limit(params, 2_048)

    assert params == {expected_parameter: 2_048}


def test_runtime_mapping_preserves_explicit_output_limit_and_reasoning_choice():
    runtime = SimpleNamespace(
        apiProvider="openai",
        baseURL="https://api.deepseek.com",
        contextWindow="1m",
        options={
            "model": "deepseek-v4-flash",
            "model_profile": "deepseek", "profile_binding": "compatible",
            "max_generation_tokens": 256_000,
            "thinking": {"type": "enabled"},
        },
    )

    request = model_request_from_runtime(runtime)

    assert request.max_generation_tokens == 256_000
    assert "max_tokens" not in request.options
    assert request.options["thinking"] == {"type": "enabled"}
    assert request.capability_snapshot.profile_id == "deepseek"
    assert request.capability_snapshot.context_window_tokens == 1_000_000
    assert normalize_thinking_enabled(dict(request.options)) is True


def test_vendor_runtime_overrides_default_capability_ceiling():
    runtime = SimpleNamespace(
        apiProvider="openai",
        baseURL="https://api.moonshot.cn/v1",
        contextWindow="256k",
        options={
            "model": "kimi-k2.6",
            "model_profile": "moonshot",
            "profile_max_generation_tokens": 262_144,
            "max_generation_tokens": 200_000,
        },
    )

    request = model_request_from_runtime(runtime)

    assert request.capability_snapshot.profile_id == "moonshot"
    assert request.capability_snapshot.max_generation_tokens == 262_144
    assert request.capability_snapshot.source == "user_declared"
    assert request.max_generation_tokens == 200_000


def test_vendor_runtime_rejects_user_ceiling_above_declared_override():
    runtime = SimpleNamespace(
        apiProvider="openai",
        baseURL="https://api.moonshot.cn/v1",
        contextWindow="256k",
        options={
            "model": "kimi-k2.6",
            "model_profile": "moonshot",
            "profile_max_generation_tokens": 262_144,
            "max_generation_tokens": 300_000,
        },
    )

    with pytest.raises(
        ValueError,
        match="max_generation_tokens exceeds profile_max_generation_tokens",
    ):
        model_request_from_runtime(runtime)


def test_vendor_runtime_still_rejects_thinking_capability_override():
    runtime = SimpleNamespace(
        apiProvider="openai",
        baseURL="https://api.moonshot.cn/v1",
        contextWindow="256k",
        options={
            "model": "kimi-k3",
            "model_profile": "moonshot",
            "supports_thinking": False,
        },
    )

    with pytest.raises(
        ValueError,
        match="built-in model thinking capability cannot be overridden",
    ):
        model_request_from_runtime(runtime)


def test_custom_runtime_keeps_profile_capability_separate_from_user_ceiling():
    runtime = SimpleNamespace(
        apiProvider="openai",
        baseURL="https://proxy.example/v1",
        contextWindow="256k",
        options={
            "model": "custom-model",
            "profile_max_generation_tokens": 200_000,
            "max_generation_tokens": 80_000,
            "supports_thinking": False,
            "thinking_only": False,
        },
    )

    request = model_request_from_runtime(runtime)

    assert request.capability_snapshot.profile_id == "generic"
    assert request.capability_snapshot.max_generation_tokens == 200_000
    assert request.max_generation_tokens == 80_000
    assert "profile_max_generation_tokens" not in request.options
    assert "max_generation_tokens" not in request.options


def test_custom_runtime_can_use_profile_capability_without_a_user_ceiling():
    runtime = SimpleNamespace(
        apiProvider="openai",
        baseURL="https://proxy.example/v1",
        contextWindow="256k",
        options={
            "model": "custom-model",
            "profile_max_generation_tokens": 200_000,
            "supports_thinking": False,
            "thinking_only": False,
        },
    )

    request = model_request_from_runtime(runtime)

    assert request.capability_snapshot.max_generation_tokens == 200_000
    assert request.max_generation_tokens is None


def test_custom_runtime_does_not_reinterpret_user_ceiling_as_profile_capability():
    runtime = SimpleNamespace(
        apiProvider="openai",
        baseURL="https://proxy.example/v1",
        contextWindow="256k",
        options={
            "model": "custom-model",
            "max_generation_tokens": 80_000,
            "supports_thinking": False,
            "thinking_only": False,
        },
    )

    with pytest.raises(
        ValueError,
        match="custom model requires profile_max_generation_tokens",
    ):
        model_request_from_runtime(runtime)


def test_chat_compiler_preserves_serial_tool_call_option():
    from infrastructure.models.openai_chat import _compile_chat
    from infrastructure.models.profiles.glm5_3_flash import GLM5_3_FLASH_PROFILE
    params = _compile_chat([], {'model': 'glm-5.3-flash', 'thinking_enabled': True,
                                'parallel_tool_calls': False}, GLM5_3_FLASH_PROFILE, stream=True)
    assert params['parallel_tool_calls'] is False
