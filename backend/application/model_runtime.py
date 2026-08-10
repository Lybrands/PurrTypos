"""Map product runtime configuration into provider-neutral PurrA contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from purra.contracts import ModelRequest, ReasoningMode, RunExecutionIntent
from purra.errors import UnsupportedModelFeatureError
from infrastructure.models.profiles.registry import resolve_model_profile


def model_request_from_runtime(
    runtime,
    *,
    json_object_output: bool = False,
) -> ModelRequest:
    options = dict(runtime.options)
    model = str(options.pop("model", "") or "").strip()
    profile_id = str(options.pop("model_profile", "") or "").strip() or None
    if not model:
        raise ValueError("model runtime requires a model")
    if runtime.baseURL:
        options["baseURL"] = runtime.baseURL
    options.pop("max_tokens", None)
    options.pop("tools", None)
    options.pop("tool_choice", None)
    options.pop("response_format", None)
    profile = resolve_model_profile(
        profile_id,
        model,
        str(options.get("baseURL") or ""),
    )
    reasoning_mode = reasoning_mode_from_options(options)
    protocol_capabilities = profile.protocol_capabilities()
    if not protocol_capabilities.reasoning_mode_is_supported(reasoning_mode):
        raise UnsupportedModelFeatureError(
            "selected reasoning mode is incompatible with the model profile"
        )
    if json_object_output and profile.supports_json_object_output:
        options["response_format"] = {"type": "json_object"}
    return ModelRequest(
        provider=str(runtime.apiProvider or "openai").strip().lower(),
        model=model,
        profile_id=profile_id,
        output_capabilities=profile.output_capabilities(),
        protocol_capabilities=protocol_capabilities,
        options=options,
    )


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
        capability_snapshot_digest=request.protocol_capabilities.digest(),
    )


__all__ = [
    "model_request_from_runtime",
    "reasoning_mode_from_options",
    "run_execution_intent",
]
