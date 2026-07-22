"""Shared access to persisted model selections for background AI services."""

from __future__ import annotations

import json
from typing import Any, Sequence


async def get_setting_value(db: Any, key: str) -> Any:
    row = await db.fetch_one("SELECT value FROM settings WHERE key = ?", [key])
    if not row:
        return None
    raw = row["value"]
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw


async def is_setting_enabled(db: Any, key: str, *, default: bool = False) -> bool:
    return coerce_bool(await get_setting_value(db, key), default)


async def resolve_model_config(
    db: Any,
    *,
    selection_key: str,
    preferred_model_id: str | None = None,
    fallback_selection_keys: Sequence[str] = (),
) -> dict[str, Any] | None:
    raw_configs = await get_setting_value(db, "ai_model_configs")
    configs = (
        [item for item in raw_configs if isinstance(item, dict)]
        if isinstance(raw_configs, list)
        else []
    )
    selected_id = str(preferred_model_id or "").strip()
    if not selected_id:
        selected_id = str(await get_setting_value(db, selection_key) or "").strip()
    if not selected_id:
        for key in fallback_selection_keys:
            selected_id = str(await get_setting_value(db, key) or "").strip()
            if selected_id:
                break
    selected = (
        next(
            (item for item in configs if str(item.get("id") or "") == selected_id),
            None,
        )
        if selected_id
        else None
    )
    if selected is None:
        selected = next(
            (
                item
                for item in configs
                if str(item.get("apiKey") or "").strip()
                and str(item.get("name") or "").strip()
            ),
            None,
        )
    if selected is None:
        return None
    if not str(selected.get("apiKey") or "").strip():
        return None
    if not str(selected.get("name") or "").strip():
        return None
    return dict(selected)


def coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return default


__all__ = [
    "coerce_bool",
    "get_setting_value",
    "is_setting_enabled",
    "resolve_model_config",
]
