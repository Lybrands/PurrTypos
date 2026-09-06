"""Build non-secret, comparable provenance for persisted Agent Runs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any

from purra.contracts import ModelRequest, RunProvenance
from application.model_runtime import (
    reasoning_mode_from_options,
    run_execution_intent,
)
from application.agent_run_input import AgentRunInput
from utils.url import normalize_base_url


REQUEST_PROFILE_SCHEMA_VERSION = "agent-run-request-profile/v2"
ENDPOINT_DIGEST_SCHEMA_VERSION = "agent-run-endpoint/v1"
_SECRET_FIELD_NAMES = frozenset({
    "apikey",
    "authorization",
    "proxyauthorization",
    "password",
    "secret",
    "accesstoken",
    "refreshtoken",
})


def build_chat_run_provenance(
    body: AgentRunInput,
    model_request: ModelRequest,
    *,
    result_capacity_target_tokens: int | None = None,
) -> RunProvenance:
    """Hash the complete inbound profile without retaining credentials or URLs.

    The normalized model contract is supplied by the mapper that will execute
    the Run. Provider SDK implementation details remain outside the profile.
    """

    if not isinstance(model_request, ModelRequest):
        raise TypeError("run provenance requires the normalized ModelRequest")
    if (
        result_capacity_target_tokens is not None
        and (
            type(result_capacity_target_tokens) is not int
            or result_capacity_target_tokens <= 0
        )
    ):
        raise ValueError("result capacity target must be a positive integer")

    raw = body.model_dump()
    model_name = model_request.model
    model_provider = model_request.provider
    context_window = model_request.capability_snapshot.context_window_tokens
    endpoint_digest = digest_model_endpoint(
        str(model_request.options.get("baseURL") or body.baseURL or "")
    )

    # Keep every non-secret request field, including messages, scope ids,
    # model options and caller tools. Endpoint identity is represented only by
    # its digest, and all credential-like values are replaced with a constant.
    profile = _redact_secrets(raw)
    if not isinstance(profile, dict):  # pragma: no cover - model_dump is a dict
        raise TypeError("chat request profile must be an object")
    profile["baseURL"] = {"endpointDigest": endpoint_digest}
    profile["apiProvider"] = model_provider
    profile["contextWindow"] = context_window
    profile["options"] = _normalized_model_request_profile(
        model_request,
        endpoint_digest=endpoint_digest,
        result_capacity_target_tokens=result_capacity_target_tokens,
    )
    profile = {
        "schemaVersion": REQUEST_PROFILE_SCHEMA_VERSION,
        "request": profile,
    }
    request_profile_digest = _canonical_digest(profile)
    snapshot = model_request.capability_snapshot
    requested_mode = reasoning_mode_from_options(model_request.options)
    return RunProvenance(
        model_provider=model_provider,
        model_name=model_name,
        context_window=context_window,
        endpoint_digest=endpoint_digest,
        request_profile_digest=request_profile_digest,
        capability_snapshot=snapshot.to_mapping(include_digest=True),
        execution_intent=run_execution_intent(
            model_request,
            requested_mode,
            output_contract="assistant_text",
            tool_protocol_contract=(
                "host_tools" if body.enableAgentTools else "no_tools"
            ),
            result_capacity_target_tokens=result_capacity_target_tokens,
        ),
    )


def _normalized_model_request_profile(
    model_request: ModelRequest,
    *,
    endpoint_digest: str,
    result_capacity_target_tokens: int | None,
) -> dict[str, Any]:
    provider_options = _redact_secrets(dict(model_request.options))
    if not isinstance(provider_options, dict):  # pragma: no cover - dict input
        raise TypeError("normalized model request options must be an object")
    for key in tuple(provider_options):
        if _normalized_field_name(key) == "baseurl":
            provider_options[key] = {"endpointDigest": endpoint_digest}
    return {
        "provider": model_request.provider,
        "model": model_request.model,
        "capabilitySnapshotDigest": (
            model_request.capability_snapshot.digest()
        ),
        "profileMaxGenerationTokens": (
            model_request.capability_snapshot.max_generation_tokens
        ),
        "requestedUserMaxGenerationTokens": (
            model_request.max_generation_tokens
        ),
        "resultCapacityTargetTokens": result_capacity_target_tokens,
        "providerOptions": provider_options,
    }


def digest_model_endpoint(base_url: str | None) -> str:
    """Return a stable digest without persisting the provider URL itself."""

    normalized = normalize_base_url(base_url) or "<provider-default>"
    return sha256(
        f"{ENDPOINT_DIGEST_SCHEMA_VERSION}\0{normalized}".encode("utf-8")
    ).hexdigest()


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _redact_secrets(value: Any, *, field_name: str | None = None) -> Any:
    if (
        field_name is not None
        and _normalized_field_name(field_name) in _SECRET_FIELD_NAMES
    ):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {
            str(key): _redact_secrets(item, field_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [_redact_secrets(item) for item in value]
    return value


def _normalized_field_name(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum())


__all__ = [
    "ENDPOINT_DIGEST_SCHEMA_VERSION",
    "REQUEST_PROFILE_SCHEMA_VERSION",
    "build_chat_run_provenance",
    "digest_model_endpoint",
]
