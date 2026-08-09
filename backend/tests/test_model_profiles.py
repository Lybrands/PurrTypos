from __future__ import annotations

from infrastructure.models.profiles.registry import resolve_model_profile


def test_registry_resolves_each_builtin_profile_and_generic_fallback():
    glm = resolve_model_profile(
        "zai:glm-5.2",
        "glm-5.2",
        "https://open.bigmodel.cn/api/paas/v4/",
    )
    assert glm.profile_id == "zai:glm-5.2"
    assert glm.build_openai_extra_body(True) == {
        "thinking": {"type": "enabled"},
    }
    assert glm.build_openai_extra_body(False) == {
        "thinking": {"type": "disabled"},
    }
    assert glm.output_capabilities().max_output_tokens == 131_072
    deepseek_pro = resolve_model_profile(
        "deepseek:deepseek-v4-pro",
        "deepseek-v4-pro",
        "https://api.deepseek.com",
    )
    assert deepseek_pro.profile_id == "deepseek:deepseek-v4-pro"
    assert deepseek_pro.output_capabilities().max_output_tokens == 393_216
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
    assert profile.internal_output_token_floor() == 8_192


def test_generic_profile_preserves_advanced_custom_thinking_contract():
    profile = resolve_model_profile(
        None,
        "custom-model",
        "https://proxy.example/v1",
    )

    assert profile.build_openai_extra_body(False) == {
        "thinking": {"type": "disabled"},
    }
