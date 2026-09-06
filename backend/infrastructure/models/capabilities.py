"""Normalize the caller's default/enabled/disabled reasoning preference.

OpenAI-compatible parameter shapes belong to each ModelProfile. Native
Anthropic budget translation is shared here by its protocol adapter.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from purra.contracts import ReasoningMode
from purra.errors import ContractViolationError
from purra.model_protocol import ModelProtocolCapabilities


def reasoning_mode_from_options(
    opts: Mapping[str, Any] | None,
) -> ReasoningMode:
    """Read the caller's tri-state reasoning preference without rewriting it."""
    options = opts or {}
    thinking = options.get("thinking")
    legacy = options.get("thinking_enabled")
    if thinking is None:
        if legacy is None:
            return ReasoningMode.DEFAULT
        if isinstance(legacy, bool):
            return ReasoningMode.ENABLED if legacy else ReasoningMode.DISABLED
        raise ValueError("thinking_enabled must be a boolean")
    if not isinstance(thinking, Mapping):
        raise ValueError("thinking must be an object")
    value = thinking.get("type")
    if value not in {"enabled", "adaptive", "disabled"}:
        raise ValueError("thinking.type must be enabled, adaptive or disabled")
    mode = ReasoningMode.ENABLED if value == "adaptive" else ReasoningMode(value)
    if legacy is not None and (
        not isinstance(legacy, bool)
        or legacy is not (mode is ReasoningMode.ENABLED)
    ):
        raise ValueError("thinking options conflict")
    return mode


def normalize_thinking_enabled(opts: Mapping[str, Any] | None) -> bool | None:
    """从 options 推断 thinking 开关。"""
    mode = reasoning_mode_from_options(opts)
    if mode is ReasoningMode.DEFAULT:
        return None
    return mode is ReasoningMode.ENABLED


def require_supported_reasoning_mode(
    opts: Mapping[str, Any] | None,
    capabilities: ModelProtocolCapabilities,
) -> ReasoningMode:
    mode = reasoning_mode_from_options(opts)
    if not capabilities.reasoning_mode_is_supported(mode):
        raise ValueError(
            "selected reasoning mode is incompatible with model capabilities"
        )
    return mode


# ── Anthropic Messages API ───────────────────────────────────────────

def build_anthropic_thinking_param(
    enabled: bool | None,
    max_tokens: int | None,
    explicit_thinking: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, int]:
    """Validate and preserve an explicit Anthropic thinking configuration.

    Anthropic counts extended thinking inside ``max_tokens``.  The host must
    therefore never invent a thinking budget or enlarge the generation limit
    to make an invalid pair fit.  ``adaptive`` has no manual token budget;
    manual ``enabled`` thinking requires the caller's exact ``budget_tokens``.
    """
    valid_max_tokens = type(max_tokens) is int and max_tokens > 0
    if enabled and not valid_max_tokens:
        raise ContractViolationError(
            "Anthropic thinking requires a resolved positive generation limit",
            code="anthropic_generation_limit_required",
        )
    max_out = max_tokens if valid_max_tokens else 8192
    if not enabled:
        return None, max_out
    if not isinstance(explicit_thinking, Mapping):
        raise ContractViolationError(
            "Anthropic enabled thinking requires explicit budget_tokens",
            code="anthropic_thinking_budget_required",
        )
    thinking = dict(explicit_thinking)
    thinking_type = thinking.get("type")
    if thinking_type == "adaptive":
        return thinking, max_out
    if thinking_type != "enabled":
        raise ContractViolationError(
            "Anthropic thinking configuration conflicts with enabled reasoning",
            code="anthropic_thinking_configuration_invalid",
        )
    if "budget_tokens" not in thinking:
        raise ContractViolationError(
            "Anthropic enabled thinking requires explicit budget_tokens",
            code="anthropic_thinking_budget_required",
        )
    budget = thinking["budget_tokens"]
    if type(budget) is not int or not 1024 <= budget < max_out:
        raise ContractViolationError(
            "Anthropic thinking budget_tokens must satisfy 1024 <= budget_tokens < max_tokens",
            code="anthropic_thinking_budget_invalid",
        )
    return thinking, max_out
