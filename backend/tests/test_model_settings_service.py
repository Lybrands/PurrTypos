from services.model_settings_service import build_model_options


def test_model_options_preserve_the_persisted_reasoning_choice():
    base = {
        "name": "model",
        "baseUrl": "https://provider.test/v1",
        "presetId": "provider:model",
    }

    provider_default = build_model_options(base, max_tokens=1_200)
    explicitly_enabled = build_model_options(
        {
            **base,
            "thinkingEnabled": True,
            "temperatureThinking": 0.7,
        },
        max_tokens=1_200,
    )
    explicitly_disabled = build_model_options(
        {
            **base,
            "thinkingEnabled": False,
            "temperatureNonThinking": 0.2,
        },
        max_tokens=1_200,
    )

    assert "thinking" not in provider_default
    assert "temperature" not in provider_default
    assert explicitly_enabled["thinking"] == {"type": "enabled"}
    assert explicitly_enabled["temperature"] == 0.7
    assert explicitly_disabled["thinking"] == {"type": "disabled"}
    assert explicitly_disabled["temperature"] == 0.2


def test_disabled_temperature_customization_does_not_invent_a_value():
    options = build_model_options(
        {
            "name": "model",
            "thinkingEnabled": True,
            "customizeTemperature": False,
            "temperatureThinking": 0.7,
        },
        max_tokens=4_000,
    )

    assert options["thinking"] == {"type": "enabled"}
    assert "temperature" not in options
