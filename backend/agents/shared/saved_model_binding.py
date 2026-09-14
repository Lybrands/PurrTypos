"""Verified, secret-free model bindings for unattended replacement recovery."""

from __future__ import annotations

from collections.abc import Mapping
from hmac import compare_digest
from types import SimpleNamespace
from typing import Any

from application.model_runtime import model_request_from_runtime, runtime_from_settings
from pydantic import SecretStr
from services.model_settings_service import get_setting_value


SAVED_MODEL_BINDING_SCHEMA_VERSION = 1


def _model_identity(runtime) -> dict[str, object]:
    request = model_request_from_runtime(
        runtime,
        task_reasoning_preference="economical",
    )
    return {
        "provider": request.provider,
        "model": request.model,
        "endpoint": str(getattr(runtime, "baseURL", "") or "").strip(),
        "contextWindow": getattr(runtime, "contextWindow", None),
        "capabilityDigest": request.capability_snapshot.digest(),
    }


async def capture_saved_model_binding(db, runtime) -> dict[str, object] | None:
    """Persist identity only when the request exactly matches a saved config."""

    config_id = str(getattr(runtime, "modelConfigId", "") or "").strip()
    requested_key = runtime.apiKey.get_secret_value().strip()
    if not requested_key:
        return None
    try:
        requested = _model_identity(runtime)
    except (TypeError, ValueError):
        return None
    configured = await get_setting_value(db, "ai_model_configs")
    if not isinstance(configured, list):
        return None
    matches = []
    for candidate in configured:
        if not isinstance(candidate, Mapping):
            continue
        candidate_id = str(candidate.get("id") or "").strip()
        if config_id and candidate_id != config_id:
            continue
        configured_key = str(candidate.get("apiKey") or "").strip()
        if (
            not candidate_id
            or not configured_key
            or not str(candidate.get("name") or "").strip()
            or not compare_digest(requested_key, configured_key)
        ):
            continue
        try:
            if _model_identity(runtime_from_settings(candidate)) != requested:
                continue
        except (TypeError, ValueError):
            continue
        matches.append(dict(candidate))
    # Older Screenplay wire requests do not carry modelConfigId. Identity-only
    # lookup is safe only when it resolves one exact saved configuration.
    if len(matches) != 1:
        return None
    config = matches[0]
    config_id = str(config["id"])
    return {
        "schemaVersion": SAVED_MODEL_BINDING_SCHEMA_VERSION,
        "modelConfigId": config_id,
        **requested,
    }


async def resolve_saved_model_runtime(db, binding: Mapping[str, Any]):
    """Resolve one exact saved config; task data never contains the secret."""

    if int(binding.get("schemaVersion") or 0) != SAVED_MODEL_BINDING_SCHEMA_VERSION:
        return None
    config_id = str(binding.get("modelConfigId") or "").strip()
    if not config_id:
        return None
    config = await _saved_model_config_by_id(db, config_id)
    if config is None:
        return None
    runtime = runtime_from_settings(config)
    expected = {
        key: binding.get(key)
        for key in (
            "provider",
            "model",
            "endpoint",
            "contextWindow",
            "capabilityDigest",
        )
    }
    try:
        if _model_identity(runtime) != expected:
            return None
    except (TypeError, ValueError):
        return None
    return SimpleNamespace(
        apiKey=SecretStr(str(config["apiKey"])),
        modelConfigId=config_id,
        baseURL=getattr(runtime, "baseURL", None),
        apiProvider=getattr(runtime, "apiProvider", "openai"),
        options=dict(getattr(runtime, "options", {})),
        contextWindow=getattr(runtime, "contextWindow", None),
        locale="zh-CN",
    )


async def _saved_model_config_by_id(db, config_id: str) -> dict[str, object] | None:
    configured = await get_setting_value(db, "ai_model_configs")
    if not isinstance(configured, list):
        return None
    for candidate in configured:
        if not isinstance(candidate, Mapping):
            continue
        if str(candidate.get("id") or "").strip() != config_id:
            continue
        if not str(candidate.get("apiKey") or "").strip():
            return None
        if not str(candidate.get("name") or "").strip():
            return None
        return dict(candidate)
    return None


__all__ = [
    "SAVED_MODEL_BINDING_SCHEMA_VERSION",
    "capture_saved_model_binding",
    "resolve_saved_model_runtime",
]
