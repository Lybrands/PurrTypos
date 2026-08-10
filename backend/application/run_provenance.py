"""Build non-secret, comparable provenance for persisted Agent Runs."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any

from purra.contracts import RunProvenance
from application.agent_run_input import AgentRunInput
from application.request_mapping import context_window_tokens
from utils.url import normalize_base_url


REQUEST_PROFILE_SCHEMA_VERSION = "agent-run-request-profile/v1"
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
) -> RunProvenance:
    """Hash the complete inbound profile without retaining credentials or URLs.

    Runtime implementation details are intentionally not part of the request
    profile, so equivalent model requests remain comparable over time.
    """

    raw = body.model_dump()
    options = raw.get("options")
    if not isinstance(options, Mapping):
        options = {}
    model_name = str(options.get("model") or "").strip()
    if not model_name:
        raise ValueError("run provenance requires a model name")
    model_provider = str(body.apiProvider or "").strip().lower() or "openai"
    context_window = context_window_tokens(
        body.contextWindow or options.get("context_window")
    )
    endpoint_digest = digest_model_endpoint(body.baseURL)

    # Keep every non-secret request field, including messages, scope ids,
    # model options and caller tools. Endpoint identity is represented only by
    # its digest, and all credential-like values are replaced with a constant.
    profile = _redact_secrets(raw)
    if not isinstance(profile, dict):  # pragma: no cover - model_dump is a dict
        raise TypeError("chat request profile must be an object")
    profile["baseURL"] = {"endpointDigest": endpoint_digest}
    profile["apiProvider"] = model_provider
    profile["contextWindow"] = context_window
    normalized_options = profile.get("options")
    if isinstance(normalized_options, dict):
        normalized_options["model"] = model_name
    profile = {
        "schemaVersion": REQUEST_PROFILE_SCHEMA_VERSION,
        "request": profile,
    }
    request_profile_digest = _canonical_digest(profile)
    return RunProvenance(
        model_provider=model_provider,
        model_name=model_name,
        context_window=context_window,
        endpoint_digest=endpoint_digest,
        request_profile_digest=request_profile_digest,
    )


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
