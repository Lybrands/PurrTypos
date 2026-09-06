"""Shared contract for model-specific behavior above protocol adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from purra.model_protocol import (
    FeatureSupport,
    ModelCapabilitySnapshot,
    ModelOutputCapabilities,
    ModelProtocolCapabilities,
    ReasoningControl,
    ReasoningReplayPolicy,
    ThinkingTokenAccounting,
)


class ModelProfile:
    profile_id = "generic"
    provider_protocol = "openai_compatible"
    capability_source: str | None = None
    actionable = True
    model_names: frozenset[str] = frozenset()
    base_urls: frozenset[str] = frozenset()
    native_anthropic_thinking = False
    openai_output_token_parameter = "max_tokens"
    max_generation_tokens: int | None = None
    thinking_token_accounting = ThinkingTokenAccounting.UNKNOWN
    supports_json_object_output = False
    reasoning_control = ReasoningControl.SELECTABLE
    reasoning_replay = ReasoningReplayPolicy.IGNORED
    tool_calling = FeatureSupport.SUPPORTED
    required_tool_choice = FeatureSupport.SUPPORTED
    parallel_tool_calls = FeatureSupport.SUPPORTED
    public_progress = FeatureSupport.UNAVAILABLE
    context_window_options = ("32k", "256k", "1m")
    default_context_window = "1m"
    reasoning_effort_options: tuple[str, ...] = ()
    task_reasoning_preferences: Mapping[str, str] = {}
    stream_usage = True
    default_thinking_enabled = True
    customize_temperature = False
    default_temperature_thinking = 1
    default_temperature_non_thinking = 1

    def matches(self, model: str, base_url: str | None) -> bool:
        return (
            str(model or "").strip().lower() in self.model_names
            and _normalize_base_url(base_url) in self.base_urls
        )

    def build_openai_extra_body(
        self,
        thinking_enabled: bool | None,
    ) -> dict[str, Any]:
        if (
            self.reasoning_control is ReasoningControl.UNAVAILABLE
            or thinking_enabled is None
        ):
            return {}
        return {
            "thinking": {
                "type": "enabled" if thinking_enabled else "disabled",
            },
        }

    def apply_openai_output_limit(
        self,
        params: dict[str, Any],
        max_tokens: int | None,
    ) -> None:
        if max_tokens is not None and max_tokens > 0:
            params[self.openai_output_token_parameter] = max_tokens

    def protocol_capabilities(self) -> ModelProtocolCapabilities:
        return ModelProtocolCapabilities(
            reasoning_control=self.reasoning_control,
            reasoning_replay=self.reasoning_replay,
            tool_calling=self.tool_calling,
            required_tool_choice=self.required_tool_choice,
            parallel_tool_calls=self.parallel_tool_calls,
            public_progress=self.public_progress,
            json_schema_level=(
                "json_object" if self.supports_json_object_output else "unknown"
            ),
        )

    def output_capabilities(self) -> ModelOutputCapabilities:
        return ModelOutputCapabilities(
            max_generation_tokens=self.max_generation_tokens,
            thinking_token_accounting=self.thinking_token_accounting,
        )

    def capability_snapshot(
        self,
        *,
        context_window_tokens: int,
    ) -> ModelCapabilitySnapshot:
        return ModelCapabilitySnapshot(
            schema_version=2,
            profile_id=self.profile_id,
            provider_protocol=self.provider_protocol,
            context_window_tokens=context_window_tokens,
            max_generation_tokens=self.max_generation_tokens,
            thinking_token_accounting=self.thinking_token_accounting,
            protocol=self.protocol_capabilities(),
            actionable=self.actionable,
            source=self.capability_source,
        )

    def normalize_openai_chunk(self, chunk: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(chunk)
        choices = value.get("choices")
        if not isinstance(choices, list):
            return value
        normalized_choices: list[Any] = []
        for raw_choice in choices:
            if not isinstance(raw_choice, Mapping):
                normalized_choices.append(raw_choice)
                continue
            choice = dict(raw_choice)
            delta = choice.get("delta")
            if isinstance(delta, Mapping):
                choice["delta"] = _normalize_reasoning_mapping(delta, include_details=False)
            message = choice.get("message")
            if isinstance(message, Mapping):
                choice["message"] = self.normalize_openai_message(message)
            normalized_choices.append(choice)
        value["choices"] = normalized_choices
        return value

    def normalize_openai_message(self, message: Mapping[str, Any]) -> dict[str, Any]:
        return _normalize_reasoning_mapping(message, include_details=True)


class GenericModelProfile(ModelProfile):
    provider_protocol = "custom"
    actionable = True
    default_thinking_enabled = None
    reasoning_control = ReasoningControl.UNAVAILABLE
    reasoning_replay = ReasoningReplayPolicy.FORBIDDEN
    tool_calling = FeatureSupport.SUPPORTED
    required_tool_choice = FeatureSupport.SUPPORTED
    parallel_tool_calls = FeatureSupport.SUPPORTED


def _normalize_base_url(value: str | None) -> str:
    return str(value or "").strip().lower().rstrip("/")


def _normalize_reasoning_mapping(
    value: Mapping[str, Any],
    *,
    include_details: bool,
) -> dict[str, Any]:
    normalized = dict(value)
    reasoning = normalized.get("reasoning_content")
    if not isinstance(reasoning, str) or not reasoning:
        fallback = normalized.get("reasoning")
        if isinstance(fallback, str) and fallback:
            reasoning = fallback
    if (not isinstance(reasoning, str) or not reasoning) and include_details:
        reasoning = _reasoning_details_text(normalized.get("reasoning_details"))
    if isinstance(reasoning, str) and reasoning:
        normalized["reasoning_content"] = reasoning
    return normalized


def _reasoning_details_text(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        text = item.get("text") or item.get("content")
        if isinstance(text, str) and text:
            parts.append(text)
    return "".join(parts)


__all__ = ["GenericModelProfile", "ModelProfile"]
