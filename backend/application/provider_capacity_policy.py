"""Validate the user-owned endpoint capacity policy.

Capacity belongs to a Provider endpoint rather than a model: several model
names can share one upstream quota and connection pool.  The policy stores the
visible endpoint for settings UI, while matching uses its stable digest.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from application.run_provenance import digest_model_endpoint
from utils.url import normalize_base_url


SETTING_KEY = "ai_provider_capacity_policies"
DEFAULT_MAX_CONCURRENT_CALLS = 2
MIN_MAX_CONCURRENT_CALLS = 1
MAX_MAX_CONCURRENT_CALLS = 16
_SUPPORTED_PROVIDERS = frozenset({"openai", "anthropic", "zai"})


def normalize_provider_capacity_policies(value: object) -> list[dict[str, Any]]:
    """Return a strict, de-duplicated persisted policy list.

    This intentionally rejects ambiguous duplicates after endpoint
    normalization.  Silently choosing one would make an operator believe a
    capacity is in force when a second spelling of the same URL overrides it.
    """

    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Provider capacity policies must be a list")
    normalized: list[dict[str, Any]] = []
    seen_scopes: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"Provider capacity policy #{index + 1} must be an object")
        provider = str(item.get("provider") or "").strip().lower()
        endpoint = normalize_base_url(str(item.get("endpoint") or "")) or ""
        limit = item.get("maxConcurrentCalls")
        if provider not in _SUPPORTED_PROVIDERS:
            raise ValueError(f"Provider capacity policy #{index + 1} has an unsupported provider")
        if not endpoint:
            raise ValueError(f"Provider capacity policy #{index + 1} requires an endpoint")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError(f"Provider capacity policy #{index + 1} requires an integer limit")
        if not MIN_MAX_CONCURRENT_CALLS <= limit <= MAX_MAX_CONCURRENT_CALLS:
            raise ValueError(
                f"Provider capacity policy #{index + 1} limit must be between "
                f"{MIN_MAX_CONCURRENT_CALLS} and {MAX_MAX_CONCURRENT_CALLS}",
            )
        endpoint_digest = digest_model_endpoint(endpoint)
        scope = (provider, endpoint_digest)
        if scope in seen_scopes:
            raise ValueError("Provider capacity policies cannot repeat an endpoint")
        seen_scopes.add(scope)
        normalized.append({
            "provider": provider,
            "endpoint": endpoint,
            "maxConcurrentCalls": limit,
        })
    return normalized


def configured_capacity_limit(
    value: object,
    *,
    provider: str,
    endpoint_digest: str,
) -> int:
    """Resolve one endpoint cap, falling back safely for corrupted local data."""

    try:
        policies = normalize_provider_capacity_policies(value)
    except ValueError:
        return DEFAULT_MAX_CONCURRENT_CALLS
    for policy in policies:
        if (
            policy["provider"] == provider
            and digest_model_endpoint(str(policy["endpoint"])) == endpoint_digest
        ):
            return int(policy["maxConcurrentCalls"])
    return DEFAULT_MAX_CONCURRENT_CALLS


__all__ = [
    "DEFAULT_MAX_CONCURRENT_CALLS",
    "MAX_MAX_CONCURRENT_CALLS",
    "MIN_MAX_CONCURRENT_CALLS",
    "SETTING_KEY",
    "configured_capacity_limit",
    "normalize_provider_capacity_policies",
]
