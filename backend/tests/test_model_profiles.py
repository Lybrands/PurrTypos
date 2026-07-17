from __future__ import annotations

from infrastructure.models.profiles.registry import resolve_model_profile


def test_registry_resolves_each_builtin_profile_and_generic_fallback():
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
    assert resolve_model_profile(
        "mimo:mimo-v2.5-pro",
        "mimo-v2.5-pro",
        "https://api.xiaomimimo.com/v1",
    ).profile_id == "mimo:mimo-v2.5-pro"
    assert resolve_model_profile(
        None,
        "custom-model",
        "https://proxy.example/v1",
    ).profile_id == "generic"


def test_minimax_profile_owns_reasoning_request_and_response_normalization():
    profile = resolve_model_profile(
        "minimax:MiniMax-M3",
        "MiniMax-M3",
        "https://api.minimaxi.com/v1",
    )

    assert profile.build_openai_extra_body(True) == {"reasoning_split": True}
    normalized = profile.normalize_openai_chunk({
        "choices": [{
            "delta": {"reasoning": "native reasoning"},
            "finish_reason": None,
        }],
    })
    assert normalized["choices"][0]["delta"]["reasoning_content"] == (
        "native reasoning"
    )


def test_kimi_k3_profile_forces_currently_supported_max_reasoning():
    profile = resolve_model_profile(
        "moonshot:kimi-k3",
        "kimi-k3",
        "https://api.moonshot.cn/v1",
    )

    assert profile.build_openai_extra_body(True) == {"reasoning_effort": "max"}
    assert profile.build_openai_extra_body(False) == {"reasoning_effort": "max"}


def test_generic_profile_preserves_advanced_custom_thinking_contract():
    profile = resolve_model_profile(
        None,
        "custom-model",
        "https://proxy.example/v1",
    )

    assert profile.build_openai_extra_body(False) == {
        "thinking": {"type": "disabled"},
    }
