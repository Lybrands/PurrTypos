import pytest
from application.model_runtime import model_request_from_runtime, runtime_from_settings

BASE = {"name": "deepseek-v4-flash", "baseUrl": "https://api.deepseek.com",
        "presetId": "deepseek:deepseek-v4-flash", "contextWindow": "1m"}


def resolve(config):
    return model_request_from_runtime(runtime_from_settings(config))


def test_model_options_preserve_the_persisted_reasoning_choice():
    default = resolve(BASE)
    explicit = resolve({**BASE, "thinkingEnabled": True, "reasoningEffort": "low", "temperatureThinking": 0.7})
    disabled = resolve({**BASE, "thinkingEnabled": False, "temperatureNonThinking": 0.2})
    assert "thinking" not in default.options and "temperature" not in default.options
    assert default.max_generation_tokens is None
    assert default.capability_snapshot.max_generation_tokens == 393216
    assert explicit.options["reasoning_effort"] == "low"
    assert explicit.options["temperature"] == 0.7
    assert disabled.options["thinking"]["type"] == "disabled"
    assert disabled.options["temperature"] == 0.2


def test_disabled_temperature_customization_does_not_invent_a_value():
    request = resolve({**BASE, "thinkingEnabled": True, "customizeTemperature": False, "temperatureThinking": 0.7})
    assert "temperature" not in request.options


def test_disabled_reasoning_cannot_silently_discard_explicit_effort():
    with pytest.raises(ValueError, match="conflicts"):
        resolve({**BASE, "thinkingEnabled": False, "reasoningEffort": "max"})


CUSTOM = {"name": "custom-model", "baseUrl": "https://proxy.example/v1", "contextWindow": "256k",
          "supportsThinking": False, "thinkingOnly": False, "profileMaxGenerationTokens": 200000}


def test_custom_model_profile_capability_and_user_ceiling_are_independent():
    request = resolve({**CUSTOM, "maxGenerationTokens": 80000})
    assert request.max_generation_tokens == 80000
    assert request.capability_snapshot.max_generation_tokens == 200000


def test_custom_model_uses_profile_capability_when_user_ceiling_is_unset():
    request = resolve(CUSTOM)
    assert request.max_generation_tokens is None
    assert request.capability_snapshot.max_generation_tokens == 200000


def test_custom_model_requires_profile_capability_not_only_user_ceiling():
    with pytest.raises(ValueError, match="profile_max_generation_tokens"):
        resolve({**CUSTOM, "profileMaxGenerationTokens": None, "maxGenerationTokens": 80000})


def test_custom_user_ceiling_cannot_exceed_profile_capability():
    with pytest.raises(ValueError, match="exceeds"):
        resolve({**CUSTOM, "maxGenerationTokens": 200001})
