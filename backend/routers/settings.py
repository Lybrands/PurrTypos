from __future__ import annotations

import json

from fastapi import APIRouter

from dependencies import get_db
from schemas.settings import SetSettingsRequest
from application.model_preferences import upgrade_model_config
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
