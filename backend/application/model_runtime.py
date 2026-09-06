"""Map product runtime configuration into provider-neutral PurrA contracts."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from purra.contracts import ModelRequest, ReasoningMode, RunExecutionIntent
from purra.model_protocol import (
    FeatureSupport,
    FeatureRequirement,
    ReasoningControl,
    TaskCapabilityRequirements,
    preflight_capabilities,
)
from infrastructure.models.capabilities import reasoning_mode_from_options
from infrastructure.models.profiles.registry import resolve_model_profile
from infrastructure.models.profiles.descriptors import DESCRIPTOR_KEY, TRACE_KEY, describe_profile, descriptor_digest
from application.model_preferences import resolve_preferences, upgrade_model_config


_CONTEXT_WINDOWS = {
    "32k": 32_000,
    "64k": 64_000,
    "128k": 128_000,
    "200k": 200_000,
    "256k": 256_000,
    "300k": 300_000,
    "1m": 1_000_000,
}


def with_adapter_public_progress(request: ModelRequest) -> ModelRequest:
    """Declare the public progress records produced by the host gateway."""

    protocol = request.capability_snapshot.protocol
    if protocol.public_progress is FeatureSupport.SUPPORTED:
        return request
    return replace(
        request,
        capability_snapshot=replace(
            request.capability_snapshot,
            protocol=replace(
                protocol,
                public_progress=FeatureSupport.SUPPORTED,
            ),
        ),
    )


def model_request_from_runtime(
    runtime,
    *,
    json_object_output: bool = False,
    requirements: TaskCapabilityRequirements | None = None,
    task_reasoning_preference: str | None = None,
) -> ModelRequest:
    options = dict(runtime.options)
    provider = str(runtime.apiProvider or "openai").strip().lower()
    if provider not in {"openai", "anthropic", "zai"}:
        raise ValueError("unsupported model provider")
    if DESCRIPTOR_KEY in options or TRACE_KEY in options:
        raise ValueError("caller cannot supply resolved model metadata")
    model = str(options.pop("model", "") or "").strip()
    profile_id = str(options.pop("model_profile", "") or "").strip() or None
    max_generation_tokens = pop_user_generation_limit(options)
    profile_max_generation_tokens = pop_profile_generation_limit(options)
    if not model:
        raise ValueError("model runtime requires a model")
    if runtime.baseURL:
        options["baseURL"] = runtime.baseURL
    options.pop("tools", None)
    options.pop("tool_choice", None)
    options.pop("response_format", None)
    profile = resolve_model_profile(
        profile_id,
        model,
        str(options.get("baseURL") or ""),
    )
    binding = options.pop("profile_binding", None)
    if profile_id and not profile.matches(model, str(options.get("baseURL") or "")):
        if binding != "compatible":
            raise ValueError("model profile does not match model/endpoint; declare compatible binding explicitly")
    if profile_id and provider == "anthropic" and not profile.native_anthropic_thinking:
        raise ValueError("model profile does not support Anthropic protocol")
    if profile_id and provider == "zai" and not profile_id.startswith("zai:"):
        raise ValueError("model profile does not support Zai protocol")
    descriptor = describe_profile(profile)
    expected_digest = options.pop("model_descriptor_digest", None)
    if expected_digest is not None and expected_digest != descriptor_digest(descriptor):
        raise ValueError("model descriptor changed; refresh model configuration")
    trace = resolve_preferences(options, profile, task_reasoning_preference=task_reasoning_preference)
    reasoning_mode = reasoning_mode_from_options(options)
    snapshot = profile.capability_snapshot(
        context_window_tokens=runtime_context_window_tokens(runtime),
    )
    snapshot = apply_user_declared_generic_capabilities(
        snapshot,
        options,
        profile_max_generation_tokens=profile_max_generation_tokens,
        requested_user_max_generation_tokens=max_generation_tokens,
    )
    if snapshot.profile_id == "generic":
        descriptor["reasoningControl"] = snapshot.protocol.reasoning_control.value
        descriptor["maxGenerationTokens"] = snapshot.max_generation_tokens
        if snapshot.protocol.reasoning_control is not ReasoningControl.UNAVAILABLE:
            descriptor["openaiExtraBodies"] = {
                "default": {}, "enabled": {"thinking": {"type": "enabled"}},
                "disabled": None if snapshot.protocol.reasoning_control is ReasoningControl.ALWAYS_ENABLED else {"thinking": {"type": "disabled"}},
            }
    options.pop("context_window", None)
    unknown = set(options) - {"baseURL", "thinking", "thinking_enabled", "temperature", "reasoning_effort", "top_p", "top_k", "output_config"}
    if unknown:
        raise ValueError("unsupported model request options: " + ", ".join(sorted(unknown)))
    if provider == "zai" and any(k in options for k in ("top_k", "top_p", "output_config")):
        raise ValueError("unsupported Zai request option")
    options[DESCRIPTOR_KEY] = descriptor
    options[TRACE_KEY] = {"schemaVersion": 1, "choices": trace, "descriptorDigest": descriptor_digest(descriptor)}
    preflight_capabilities(
        snapshot,
        replace(requirements, reasoning_mode=reasoning_mode) if requirements is not None else TaskCapabilityRequirements(
            reasoning_mode=reasoning_mode,
            tool_calling=FeatureRequirement.OPTIONAL,
            structured_output_level=(
                "json_object" if json_object_output else "none"
            ),
            streaming_required=True,
            cancellation_required=True,
        ),
    )
    if json_object_output and profile.supports_json_object_output:
        options["response_format"] = {"type": "json_object"}
    return ModelRequest(
        provider=provider,
        model=model,
        capability_snapshot=snapshot,
        max_generation_tokens=max_generation_tokens,
        options=options,
    )


def runtime_context_window_tokens(runtime) -> int:
    value = (
        getattr(runtime, "contextWindow", None)
        or getattr(runtime, "options", {}).get("context_window")
    )
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    key = str(value or "").strip().lower()
    if not key:
        options = getattr(runtime, "options", {})
        profile = resolve_model_profile(options.get("model_profile"), options.get("model", ""), getattr(runtime, "baseURL", None))
        if profile.profile_id == "generic":
            raise ValueError("custom model requires context_window")
        return _CONTEXT_WINDOWS[profile.default_context_window]
    return context_window_tokens(value)


def context_window_tokens(value) -> int:
    if type(value) is int and value > 0:
        return value
    key = str(value or "").strip().lower()
    if key not in _CONTEXT_WINDOWS:
        raise ValueError(f"unsupported context window: {value}")
    return _CONTEXT_WINDOWS[key]


def runtime_from_settings(config):
    """Map persisted product fields to the same DTO used by conversations."""
    config = upgrade_model_config(config)
    options = {"model": config.get("name", "")}
    for field, target in (("presetId", "model_profile"), ("maxGenerationTokens", "max_generation_tokens"),
                          ("profileMaxGenerationTokens", "profile_max_generation_tokens"),
                          ("modelPreferences", "model_preferences"), ("descriptorDigest", "model_descriptor_digest"),
                          ("profileBinding", "profile_binding")):
        if config.get(field) is not None:
            options[target] = config[field]
    if not config.get("presetId"):
        options.update(supports_thinking=config.get("supportsThinking"), thinking_only=config.get("thinkingOnly"))
    enabled = config.get("thinkingEnabled")
    if type(enabled) is bool:
        options["thinking"] = {"type": "enabled" if enabled else "disabled"}
        if enabled and config.get("thinkingBudgetTokens") is not None:
            options["thinking"]["budget_tokens"] = config["thinkingBudgetTokens"]
    if config.get("reasoningEffort") is not None:
        options["reasoning_effort"] = config["reasoningEffort"]
    if config.get("customizeTemperature") is not False and type(enabled) is bool:
        value = config.get("temperatureThinking" if enabled else "temperatureNonThinking")
        if value is not None:
            options["temperature"] = value
    return SimpleNamespace(options=options, apiProvider=config.get("apiProvider") or "openai",
                           baseURL=config.get("baseUrl") or config.get("baseURL") or "",
                           contextWindow=config.get("contextWindow"))


def pop_user_generation_limit(options: dict[str, object]) -> int | None:
    """Move the one user-owned generation ceiling out of Provider options."""

    if "max_tokens" in options:
        raise ValueError(
            "legacy max_tokens is unsupported; use max_generation_tokens"
        )
    value = options.pop("max_generation_tokens", None)
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError("max_generation_tokens must be a positive integer")
    return value


def pop_profile_generation_limit(options: dict[str, object]) -> int | None:
    """Move a custom model's verified capability ceiling out of Provider options."""

    value = options.pop("profile_max_generation_tokens", None)
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError(
            "profile_max_generation_tokens must be a positive integer"
        )
    return value


def apply_user_declared_generic_capabilities(
    snapshot,
    options: dict[str, object],
    *,
    profile_max_generation_tokens: int | None,
    requested_user_max_generation_tokens: int | None,
):
    """Turn explicit custom-model settings into a frozen Run snapshot."""

    supports_thinking = options.pop("supports_thinking", None)
    thinking_only = options.pop("thinking_only", None)
    if snapshot.profile_id != "generic":
        if (
            profile_max_generation_tokens is not None
            or supports_thinking is not None
            or thinking_only is not None
        ):
            raise ValueError(
                "built-in model capabilities cannot be overridden"
            )
        return snapshot
    if profile_max_generation_tokens is None:
        raise ValueError(
            "custom model requires profile_max_generation_tokens"
        )
    if (
        requested_user_max_generation_tokens is not None
        and requested_user_max_generation_tokens > profile_max_generation_tokens
    ):
        raise ValueError(
            "max_generation_tokens exceeds profile_max_generation_tokens"
        )
    if type(supports_thinking) is not bool or type(thinking_only) is not bool:
        raise ValueError(
            "custom model requires explicit supports_thinking and thinking_only"
        )
    if thinking_only and not supports_thinking:
        raise ValueError("thinking-only model must support thinking")
    reasoning_control = (
        ReasoningControl.ALWAYS_ENABLED
        if thinking_only
        else ReasoningControl.SELECTABLE
        if supports_thinking
        else ReasoningControl.UNAVAILABLE
    )
    return replace(
        snapshot,
        max_generation_tokens=profile_max_generation_tokens,
        protocol=replace(
            snapshot.protocol,
            reasoning_control=reasoning_control,
        ),
        source="user_declared",
    )


def run_execution_intent(
    request: ModelRequest,
    reasoning_mode: ReasoningMode,
    *,
    output_contract: str,
    tool_protocol_contract: str,
    recovery_policy_id: str = "purra.default.v1",
    result_capacity_target_tokens: int | None = None,
) -> RunExecutionIntent:
    return RunExecutionIntent(
        requested_reasoning_mode=ReasoningMode(reasoning_mode).value,
        output_contract=output_contract,
        tool_protocol_contract=tool_protocol_contract,
        recovery_policy_id=recovery_policy_id,
        capability_snapshot_digest=request.capability_snapshot.digest(),
        requested_user_max_generation_tokens=request.max_generation_tokens,
        result_capacity_target_tokens=result_capacity_target_tokens,
    )


__all__ = [
    "model_request_from_runtime",
    "apply_user_declared_generic_capabilities",
    "pop_profile_generation_limit",
    "pop_user_generation_limit",
    "reasoning_mode_from_options",
    "run_execution_intent",
    "runtime_context_window_tokens",
    "with_adapter_public_progress",
]
