from __future__ import annotations

from types import SimpleNamespace

import pytest

from application.model_runtime import model_request_from_runtime
from infrastructure.models.capabilities import normalize_thinking_enabled
from infrastructure.models.profiles.registry import (
    BUILTIN_MODEL_PROFILES,
    resolve_model_profile,
)
from purra.contracts import ReasoningMode
from purra.model_protocol import (
    FeatureSupport,
    ReasoningControl,
    ReasoningReplayPolicy,
)


def test_registry_resolves_each_builtin_profile_and_generic_fallback():
    glm = resolve_model_profile(
        "zai:glm-5.3-flash",
        "glm-5.3-flash",
        "https://open.bigmodel.cn/api/paas/v4/",
    )
    assert glm.profile_id == "zai:glm-5.3-flash"
    assert glm.build_openai_extra_body(True) == {
        "thinking": {"type": "enabled"},
    }
    with pytest.raises(ValueError, match="does not support disabling"):
        glm.build_openai_extra_body(False)
    assert glm.protocol_capabilities().reasoning_control is (
        ReasoningControl.ALWAYS_ENABLED
    )
    assert glm.output_capabilities().max_output_tokens == 131_072
    assert resolve_model_profile(
        "deepseek:deepseek-v4-flash",
        "deepseek-v4-flash",
        "https://api.deepseek.com/v1/",
    ).profile_id == "deepseek:deepseek-v4-flash"
    assert resolve_model_profile(
        "moonshot:kimi-k3",
        "kimi-k3",
        "https://api.moonshot.cn/v1",
    ).profile_id == "moonshot:kimi-k3"
    assert resolve_model_profile(
        "moonshot:kimi-k2.6",
        "kimi-k2.6",
        "https://api.moonshot.cn/v1",
    ).profile_id == "moonshot:kimi-k2.6"
    assert resolve_model_profile(
        "minimax:MiniMax-M3",
        "MiniMax-M3",
        "https://api.minimaxi.com/v1",
    ).profile_id == "minimax:MiniMax-M3"
    mimo = resolve_model_profile(
        "mimo:mimo-v2.5-pro",
        "mimo-v2.5-pro",
        "https://api.xiaomimimo.com/v1",
    )
    assert mimo.profile_id == "mimo:mimo-v2.5-pro"
    assert mimo.output_capabilities().max_output_tokens == 131_072
    generic = resolve_model_profile(
        None,
        "custom-model",
        "https://proxy.example/v1",
    )
    assert generic.profile_id == "generic"
    assert generic.output_capabilities().max_output_tokens is None


def test_minimax_profile_owns_reasoning_request_and_response_normalization():
    profile = resolve_model_profile(
        "minimax:MiniMax-M3",
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


def test_kimi_k3_profile_rejects_unsupported_reasoning_override():
    profile = resolve_model_profile(
        "moonshot:kimi-k3",
        "kimi-k3",
        "https://api.moonshot.cn/v1",
    )

    assert profile.build_openai_extra_body(True) == {"reasoning_effort": "max"}
    with pytest.raises(ValueError, match="does not support disabling"):
        profile.build_openai_extra_body(False)
    snapshot = profile.capability_snapshot(context_window_tokens=1_000_000)
    assert snapshot.actionable is True
    assert snapshot.max_output_tokens == 1_048_576
    assert snapshot.source == (
        "https://platform.kimi.ai/docs/guide/kimi-k3-quickstart"
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


def test_glm_profile_rejects_a_false_non_reasoning_claim():
    profile = resolve_model_profile(
        "zai:glm-5.3-flash",
        "glm-5.3-flash",
        "https://open.bigmodel.cn/api/paas/v4/",
    )

    capabilities = profile.protocol_capabilities()
    assert not capabilities.reasoning_mode_is_supported(ReasoningMode.DISABLED)
    assert capabilities.reasoning_mode_is_supported(ReasoningMode.DEFAULT)


def test_replay_required_profile_declares_protocol_without_core_model_checks():
    profile = resolve_model_profile(
        "deepseek:deepseek-v4-flash",
        "deepseek-v4-flash",
        "https://api.deepseek.com",
    )

    assert profile.protocol_capabilities().reasoning_control is (
        ReasoningControl.SELECTABLE
    )
    assert profile.protocol_capabilities().reasoning_replay is (
        ReasoningReplayPolicy.REQUIRED
    )
    assert profile.protocol_capabilities().required_tool_choice is (
        FeatureSupport.UNAVAILABLE
    )


def test_kimi_k2_6_declares_thinking_tool_choice_constraint():
    profile = resolve_model_profile(
        "moonshot:kimi-k2.6",
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
        assert snapshot.schema_version == 1
        assert snapshot.profile_id == profile_id
        assert snapshot.provider_protocol
        assert snapshot.digest() == second[profile_id].digest()
        assert len(snapshot.digest()) == 64
        if snapshot.actionable:
            assert snapshot.max_output_tokens is not None


@pytest.mark.parametrize(
    ("profile_id", "expected_max_output"),
    [
        ("deepseek:deepseek-v4-flash", 393_216),
        ("zai:glm-5.3-flash", 131_072),
        ("moonshot:kimi-k3", 1_048_576),
        ("moonshot:kimi-k2.6", 262_144),
        ("minimax:MiniMax-M3", 524_288),
        ("mimo:mimo-v2.5-pro", 131_072),
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
    assert snapshot.max_output_tokens == expected_max_output

@pytest.mark.parametrize(
    ("profile_id", "expected_parameter"),
    [
        ("moonshot:kimi-k3", "max_completion_tokens"),
        ("moonshot:kimi-k2.6", "max_completion_tokens"),
        ("minimax:MiniMax-M3", "max_completion_tokens"),
        ("deepseek:deepseek-v4-flash", "max_tokens"),
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
            "model_profile": "deepseek:deepseek-v4-flash",
            "max_tokens": 256_000,
            "thinking": {"type": "enabled"},
        },
    )

    request = model_request_from_runtime(runtime)

    assert request.options["max_tokens"] == 256_000
    assert request.options["thinking"] == {"type": "enabled"}
    assert request.capability_snapshot.profile_id == "deepseek:deepseek-v4-flash"
    assert request.capability_snapshot.context_window_tokens == 1_000_000
    assert normalize_thinking_enabled(dict(request.options)) is True
