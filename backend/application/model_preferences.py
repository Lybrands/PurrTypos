"""Typed configuration choices shared by all model request entry points."""

from dataclasses import dataclass
from collections.abc import Mapping
import math


@dataclass(frozen=True)
class SettingChoice:
    state: str
    value: object = None

    @classmethod
    def parse(cls, value):
        if not isinstance(value, Mapping) or set(value) - {"state", "value"}:
            raise ValueError("invalid model setting choice")
        state = value.get("state")
        if state not in {"inherit", "provider_default", "explicit"}:
            raise ValueError("invalid model setting choice state")
        if (state == "explicit") != ("value" in value):
            raise ValueError("only explicit model choices carry a value")
        return cls(state, value.get("value"))


def upgrade_model_config(config):
    """Lossless read migration; original values remain available to older readers."""
    if not isinstance(config, Mapping):
        return config
    if config.get("modelSettingsVersion") not in {None, 1}:
        raise ValueError("unsupported model settings version")
    if config.get("modelSettingsVersion") == 1:
        return dict(config)
    enabled = config.get("thinkingEnabled")
    mode = "enabled" if enabled is True else "disabled" if enabled is False else None
    temperature = config.get("temperatureThinking" if enabled else "temperatureNonThinking") if type(enabled) is bool and config.get("customizeTemperature") is not False else None
    values = {"reasoning_mode": mode, "reasoning_effort": config.get("reasoningEffort"), "temperature": temperature}
    preferences = {k: {"state": "explicit", "value": v} if v is not None else {"state": "provider_default"} for k, v in values.items()}
    preferences.update(config.get("modelPreferences") or {})
    return {**config, "modelSettingsVersion": 1, "modelPreferences": preferences,
            "modelPreferenceSources": {k: "legacy_persisted" if v is not None else "legacy_unspecified" for k, v in values.items()}}


def resolve_preferences(options, profile, *, task_reasoning_preference=None):
    choices = options.pop("model_preferences", {})
    if not isinstance(choices, Mapping) or set(choices) - {"reasoning_mode", "reasoning_effort", "temperature"}:
        raise ValueError("unsupported model preference")
    trace = {}
    for key in ("reasoning_mode", "reasoning_effort", "temperature"):
        wire_key = "thinking" if key == "reasoning_mode" else key
        choice = SettingChoice.parse(choices[key]) if key in choices else None
        raw = options.get(wire_key)
        if choice is None:
            trace[key] = {"state": "explicit" if raw is not None else "provider_default",
                          "source": "legacy_persisted" if raw is not None else "legacy_unspecified",
                          "requestedValue": raw, "resolvedValue": raw}
            continue
        options.pop(wire_key, None)
        source = "configured_choice"
        if choice.state == "explicit":
            if key == "reasoning_mode":
                if choice.value not in {"enabled", "disabled"}:
                    raise ValueError("invalid reasoning mode preference")
                options[wire_key] = {**(raw if isinstance(raw, Mapping) else {}), "type": choice.value}
            else:
                options[wire_key] = choice.value
        elif choice.state == "inherit":
            source = "model_default"
            if key == "reasoning_mode":
                if profile.default_thinking_enabled is not None:
                    options[wire_key] = {"type": "enabled" if profile.default_thinking_enabled else "disabled"}
            elif key == "temperature" and profile.customize_temperature:
                enabled = (options.get("thinking") or {}).get("type") == "enabled"
                options[wire_key] = profile.default_temperature_thinking if enabled else profile.default_temperature_non_thinking
            if key == "reasoning_effort" and task_reasoning_preference:
                mapped = profile.task_reasoning_preferences.get(task_reasoning_preference)
                source = "task_policy" if mapped is not None else "task_preference_unmapped"
                if mapped is not None:
                    options[wire_key] = mapped
        trace[key] = {"state": choice.state, "source": source,
                      "requestedValue": choice.value, "resolvedValue": options.get(wire_key)}
    effort = options.get("reasoning_effort")
    if effort is not None:
        if effort not in profile.reasoning_effort_options:
            raise ValueError("unsupported reasoning_effort for selected model")
        if (options.get("thinking") or {}).get("type") == "disabled":
            raise ValueError("reasoning_effort conflicts with disabled thinking")
    temperature = options.get("temperature")
    if temperature is not None and (type(temperature) not in {int, float} or not math.isfinite(temperature) or not 0 <= temperature <= 2):
        raise ValueError("temperature must be a finite number between 0 and 2")
    return trace
