from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from dependencies import get_db
from schemas.settings import SetSettingsRequest
from application.model_preferences import upgrade_model_config
from application.provider_capacity_policy import (
    SETTING_KEY as PROVIDER_CAPACITY_POLICY_SETTING_KEY,
    normalize_provider_capacity_policies,
)
from application.run_provenance import digest_model_endpoint
from infrastructure.models.profiles.descriptors import model_descriptors

router = APIRouter(tags=["settings"])


def _serialize_value(value) -> str:
    """Serialize a settings value to string for storage.
    Strings are stored as-is; everything else is JSON-encoded.
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


@router.get("/settings")
async def get_settings():
    db = get_db()
    rows = await db.fetch_all("SELECT key, value FROM settings")
    data: dict = {}
    for r in rows:
        raw = r["value"] or ""
        # Attempt to JSON-decode non-string values stored by new code
        try:
            parsed = json.loads(raw)
            data[r["key"]] = parsed
        except (json.JSONDecodeError, TypeError):
            data[r["key"]] = raw
    if isinstance(data.get("ai_model_configs"), list):
        data["ai_model_configs"] = [upgrade_model_config(c) for c in data["ai_model_configs"]]
    data["model_descriptors"] = model_descriptors()
    return {"success": True, "data": data}


@router.put("/settings")
async def set_settings(body: SetSettingsRequest):
    db = get_db()
    for key, value in body.data.items():
        if key == "model_descriptors":
            continue
        if key == "ai_model_configs" and isinstance(value, list):
            value = [upgrade_model_config(c) for c in value]
        if key == PROVIDER_CAPACITY_POLICY_SETTING_KEY:
            try:
                value = normalize_provider_capacity_policies(value)
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
            await _save_provider_capacity_policies(db, value)
            continue
        serialized = _serialize_value(value)
        existing = await db.fetch_one(
            "SELECT key FROM settings WHERE key = ?", [key]
        )
        if existing:
            await db.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                [serialized, key],
            )
        else:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                [key, serialized],
            )
    return {"success": True}


async def _save_provider_capacity_policies(db, policies: list[dict]) -> None:
    """Persist policy and its content-free change events as one transaction."""

    async with db.transaction():
        existing = await db.fetch_one(
            "SELECT value FROM settings WHERE key = ?",
            [PROVIDER_CAPACITY_POLICY_SETTING_KEY],
        )
        try:
            previous_raw = json.loads(str((existing or {}).get("value") or "[]"))
            previous = normalize_provider_capacity_policies(previous_raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            # Corrupt local history cannot yield a trustworthy diff.  Replace it
            # with the explicitly validated policy without inventing an event.
            previous = []
        serialized = _serialize_value(policies)
        if existing:
            await db.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                [serialized, PROVIDER_CAPACITY_POLICY_SETTING_KEY],
            )
        else:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                [PROVIDER_CAPACITY_POLICY_SETTING_KEY, serialized],
            )
        previous_scopes = _policy_scopes(previous)
        next_scopes = _policy_scopes(policies)
        for scope in sorted(set(previous_scopes) | set(next_scopes)):
            before = previous_scopes.get(scope)
            after = next_scopes.get(scope)
            if before == after:
                continue
            event_type = (
                "capacity_policy_set"
                if before is None
                else "capacity_policy_reset"
                if after is None
                else "capacity_policy_changed"
            )
            await db.execute(
                "INSERT INTO ai_provider_capacity_policy_events "
                "(provider, endpoint_digest, event_type, previous_capacity, next_capacity) "
                "VALUES (?, ?, ?, ?, ?)",
                [scope[0], scope[1], event_type, before, after],
            )


def _policy_scopes(policies: list[dict]) -> dict[tuple[str, str], int]:
    return {
        (
            str(policy["provider"]),
            digest_model_endpoint(str(policy["endpoint"])),
        ): int(policy["maxConcurrentCalls"])
        for policy in policies
    }
