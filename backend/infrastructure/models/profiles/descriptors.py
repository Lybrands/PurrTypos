"""Versioned, serializable request behavior; SDK adapters consume frozen data."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import re
from types import SimpleNamespace

from purra.model_protocol import ReasoningControl, ModelProtocolCapabilities
from purra.json_values import thaw_json_mapping
from infrastructure.models.profiles.base import ModelProfile
from infrastructure.models.profiles.registry import BUILTIN_MODEL_PROFILES, resolve_model_profile
from infrastructure.models.profiles.legacy_profile_codecs import LEGACY_CODECS

DESCRIPTOR_KEY = "model_descriptor"
TRACE_KEY = "model_resolution"
# Immutable transport codecs for requests persisted before descriptors existed.
# New catalog changes must not rewrite this historical compatibility data.


def descriptor_digest(value: Mapping) -> str:
    return sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def describe_profile(profile: ModelProfile) -> dict:
    bodies = {}
    for name, enabled in (("default", None), ("enabled", True), ("disabled", False)):
        try:
            bodies[name] = profile.build_openai_extra_body(enabled)
        except ValueError:
            bodies[name] = None
    return {
        "schemaVersion": 1,
        "profileId": profile.profile_id,
        "modelNames": sorted(profile.model_names),
        "baseUrls": sorted(profile.base_urls),
        "contextWindowOptions": list(profile.context_window_options),
        "defaultContextWindow": profile.default_context_window,
        "defaultThinkingEnabled": profile.default_thinking_enabled,
        "customizeTemperature": profile.customize_temperature,
        "defaultTemperatureThinking": profile.default_temperature_thinking,
        "defaultTemperatureNonThinking": profile.default_temperature_non_thinking,
        "maxGenerationTokens": profile.max_generation_tokens,
        "reasoningControl": profile.reasoning_control.value,
        "reasoningEffortOptions": list(profile.reasoning_effort_options),
        "taskReasoningPreferences": dict(profile.task_reasoning_preferences),
        "outputTokenParameter": profile.openai_output_token_parameter,
        "nativeAnthropicThinking": profile.native_anthropic_thinking,
        "openaiExtraBodies": bodies,
        "streamUsage": profile.stream_usage,
        "source": profile.capability_source or "user_declared",
    }


def model_descriptors() -> list[dict]:
    return [{**(d := describe_profile(p)), "digest": descriptor_digest(d)} for p in BUILTIN_MODEL_PROFILES]


class FrozenModelProfile(ModelProfile):
    """Version 1 codec. It never consults today's model catalog."""

    def __init__(self, descriptor: Mapping, snapshot=None):
        if descriptor.get("schemaVersion") != 1:
            raise ValueError("unsupported frozen model descriptor version")
        self.profile_id = str(descriptor["profileId"])
        self.reasoning_control = ReasoningControl(descriptor["reasoningControl"])
        self.openai_output_token_parameter = str(descriptor["outputTokenParameter"])
        if self.openai_output_token_parameter not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("unsupported frozen output token parameter")
        self.native_anthropic_thinking = bool(descriptor["nativeAnthropicThinking"])
        self.stream_usage = bool(descriptor["streamUsage"])
        self._bodies = descriptor["openaiExtraBodies"]
        self._snapshot = snapshot
        if snapshot is not None:
            if snapshot.profile_id != self.profile_id:
                raise ValueError("frozen profile differs from capability snapshot")
            self.reasoning_control = snapshot.protocol.reasoning_control

    def protocol_capabilities(self):
        return self._snapshot.protocol if self._snapshot is not None else super().protocol_capabilities()

    def build_openai_extra_body(self, thinking_enabled):
        name = "default" if thinking_enabled is None else "enabled" if thinking_enabled else "disabled"
        body = self._bodies.get(name)
        if body is None:
            raise ValueError("reasoning mode conflicts with frozen model descriptor")
        return thaw_json_mapping(body)


def profile_for_options(options: Mapping) -> ModelProfile:
    descriptor = options.get(DESCRIPTOR_KEY)
    snapshot = options.get("_capability_snapshot")
    if isinstance(snapshot, Mapping):
        protocol = ModelProtocolCapabilities(**{
            re.sub(r"(?<!^)(?=[A-Z])", "_", k).lower(): v for k, v in snapshot["protocol"].items()
        })
        snapshot = SimpleNamespace(profile_id=snapshot["profileId"], protocol=protocol)
    if isinstance(descriptor, Mapping):
        return FrozenModelProfile(descriptor, snapshot)
    if snapshot is not None:
        if snapshot.profile_id == "generic":
            descriptor = {"schemaVersion": 1, "profileId": "generic",
                          "reasoningControl": snapshot.protocol.reasoning_control.value,
                          "outputTokenParameter": "max_tokens", "nativeAnthropicThinking": False,
                          "streamUsage": True, "openaiExtraBodies": {
                              "default": {}, "enabled": {"thinking": {"type": "enabled"}},
                              "disabled": {"thinking": {"type": "disabled"}}}}
            if snapshot.protocol.reasoning_control is ReasoningControl.UNAVAILABLE:
                descriptor["openaiExtraBodies"] = {"default": {}, "enabled": {}, "disabled": {}}
        else:
            descriptor = LEGACY_CODECS.get(snapshot.profile_id)
            if descriptor is None:
                raise ValueError("unsupported legacy model descriptor; start a new request")
        return FrozenModelProfile(descriptor, snapshot)
    # Raw adapter contract probes have no persisted request snapshot.
    profile_id = options.get("model_profile")
    profile = resolve_model_profile(None if profile_id == "generic" else profile_id,
                                    options.get("model", ""), options.get("baseURL"))
    descriptor = describe_profile(profile)
    return FrozenModelProfile(descriptor, snapshot)
