"""Shared transformations for character and world-setting tools."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


SETTING_ARG_COLUMNS = {
    "name": "name",
    "tags": "tags",
    "profileMd": "profile_md",
}


def fields_from_args(args: Mapping[str, Any]) -> dict[str, str]:
    """Return only explicitly supplied, writable setting fields."""

    return {
        column: str(args[argument])
        for argument, column in SETTING_ARG_COLUMNS.items()
        if args.get(argument) is not None
    }


def setting_snapshot(row: Mapping[str, Any]) -> dict[str, str]:
    """Convert a database row into the public setting-diff representation."""

    return {
        "name": str(row.get("name") or ""),
        "tags": str(row.get("tags") or ""),
        "profileMd": str(row.get("profile_md") or ""),
    }


def merge_setting_proposal(
    current: Mapping[str, Any],
    updates: Mapping[str, Any],
) -> dict[str, str]:
    """Apply normalized database-column updates to a public snapshot."""

    proposed = setting_snapshot(current)
    for public_name, column in SETTING_ARG_COLUMNS.items():
        if column in updates:
            proposed[public_name] = str(updates[column])
    return proposed


def filter_setting_rows(
    rows: list[dict],
    *,
    ids: Any = None,
    names: Any = None,
) -> list[dict]:
    """Filter rows by numeric IDs or case-insensitive partial names."""

    if isinstance(ids, list) and ids:
        normalized_ids = set()
        for value in ids:
            try:
                normalized_ids.add(int(value))
            except (TypeError, ValueError):
                continue
        return [row for row in rows if row.get("id") in normalized_ids]

    if isinstance(names, list) and names:
        needles = [
            str(name).strip().lower()
            for name in names
            if str(name).strip()
        ]
        if needles:
            return [
                row
                for row in rows
                if any(
                    needle in str(row.get("name") or "").lower()
                    for needle in needles
                )
            ]

    return rows


def send_setting_updated(
    send_chunk: Callable[[dict], None] | None,
    kind: str,
    **extra: Any,
) -> None:
    """Emit the common cache/UI invalidation event after a persisted change."""

    if send_chunk:
        send_chunk({"settingUpdated": {"kind": kind, **extra}})


__all__ = [
    "SETTING_ARG_COLUMNS",
    "fields_from_args",
    "filter_setting_rows",
    "merge_setting_proposal",
    "send_setting_updated",
    "setting_snapshot",
]
