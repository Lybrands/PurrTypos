"""
Settings CRUD – key/value store, port from database.js.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from database.connection import DatabaseConnection

BOOL_SETTINGS_KEYS = ["sync_outline_chapter"]
STRING_SETTINGS_KEYS = ["ai_model_configs"]
SETTINGS_KEYS = [*BOOL_SETTINGS_KEYS, *STRING_SETTINGS_KEYS]


def _parse_ai_model_configs(raw: str | None) -> list[Any]:
    if raw is None or raw == "":
        return []
    try:
        arr = json.loads(raw)
        return arr if isinstance(arr, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


async def get_settings(db: DatabaseConnection) -> dict[str, Any]:
    kv: dict[str, str | None] = {}
    for key in SETTINGS_KEYS:
        row = await db.fetch_one(
            "SELECT value FROM settings WHERE key = ?", [key]
        )
        kv[key] = row["value"] if row else None
    return {
        "sync_outline_chapter": kv["sync_outline_chapter"] == "1",
        "ai_model_configs": _parse_ai_model_configs(kv["ai_model_configs"]),
    }


async def set_settings(db: DatabaseConnection, data: dict[str, Any]) -> None:
    for key in BOOL_SETTINGS_KEYS:
        if key in data:
            val = data[key]
            if isinstance(val, bool):
                store = "1" if val else "0"
            else:
                store = "1" if val == "1" else "0"
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                [key, store],
            )
    for key in STRING_SETTINGS_KEYS:
        if key in data:
            val = data[key]
            if key == "ai_model_configs" and isinstance(val, list):
                store = json.dumps(val)
            else:
                store = str(val)
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                [key, store],
            )
