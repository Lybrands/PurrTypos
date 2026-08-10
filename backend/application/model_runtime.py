"""Map product runtime configuration into provider-neutral PurrA contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from purra.contracts import ModelRequest, ReasoningMode
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
    if json_object_output and profile.supports_json_object_output:
        options["response_format"] = {"type": "json_object"}
    return ModelRequest(
        provider=str(runtime.apiProvider or "openai").strip().lower(),
        model=model,
        profile_id=profile_id,
        output_capabilities=profile.output_capabilities(),
        options=options,
    )


def reasoning_mode_from_options(options: Mapping[str, Any]) -> ReasoningMode:
    thinking = options.get("thinking")
    return (
        ReasoningMode.DEFAULT
        if isinstance(thinking, Mapping) and thinking.get("type") == "enabled"
        else ReasoningMode.DISABLED
    )


__all__ = ["model_request_from_runtime", "reasoning_mode_from_options"]
