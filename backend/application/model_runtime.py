"""Map product runtime configuration into provider-neutral PurrA contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from purra.contracts import ModelRequest, ReasoningMode, RunExecutionIntent
from purra.model_protocol import (
    FeatureRequirement,
    TaskCapabilityRequirements,
    preflight_capabilities,
)
from infrastructure.models.profiles.registry import resolve_model_profile


_CONTEXT_WINDOWS = {
    "32k": 32_000,
    "64k": 64_000,
    "128k": 128_000,
    "200k": 200_000,
    "256k": 256_000,
    "300k": 300_000,
    "1m": 1_000_000,
}


def model_request_from_runtime(
    runtime,
    *,
    json_object_output: bool = False,
    requirements: TaskCapabilityRequirements | None = None,
) -> ModelRequest:
    options = dict(runtime.options)
    model = str(options.pop("model", "") or "").strip()
    profile_id = str(options.pop("model_profile", "") or "").strip() or None
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
    reasoning_mode = reasoning_mode_from_options(options)
    snapshot = profile.capability_snapshot(
        context_window_tokens=runtime_context_window_tokens(runtime),
    )
    preflight_capabilities(
        snapshot,
        requirements or TaskCapabilityRequirements(
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
        provider=str(runtime.apiProvider or "openai").strip().lower(),
        model=model,
        capability_snapshot=snapshot,
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
        return 200_000
    if key not in _CONTEXT_WINDOWS:
        raise ValueError(f"unsupported context window: {value}")
    return _CONTEXT_WINDOWS[key]


def reasoning_mode_from_options(options: Mapping[str, Any]) -> ReasoningMode:
    thinking = options.get("thinking")
    return (
        ReasoningMode.DEFAULT
        if isinstance(thinking, Mapping) and thinking.get("type") == "enabled"
        else ReasoningMode.DISABLED
    )


def run_execution_intent(
    request: ModelRequest,
    reasoning_mode: ReasoningMode,
    *,
    output_contract: str,
    tool_protocol_contract: str,
    recovery_policy_id: str = "purra.default.v1",
) -> RunExecutionIntent:
    return RunExecutionIntent(
        requested_reasoning_mode=(
            "disabled"
            if ReasoningMode(reasoning_mode) is ReasoningMode.DISABLED
            else "enabled"
        ),
        output_contract=output_contract,
        tool_protocol_contract=tool_protocol_contract,
        recovery_policy_id=recovery_policy_id,
        capability_snapshot_digest=request.capability_snapshot.digest(),
    )


__all__ = [
    "model_request_from_runtime",
    "reasoning_mode_from_options",
    "run_execution_intent",
    "runtime_context_window_tokens",
]
