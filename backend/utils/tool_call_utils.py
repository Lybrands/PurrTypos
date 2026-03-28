"""
Tool call normalisation — port of electron/toolCallUtils.js.
"""

from __future__ import annotations

from typing import Any


def normalize_tool_calls_list(calls: list[dict[str, Any]] | None) -> list[dict]:
    """Filter invalid entries and unify field shapes."""
    if not calls:
        return []
    result: list[dict] = []
    for tc in calls:
        if not tc:
            continue
        tc_id = (tc.get("id") or "").strip()
        fn = tc.get("function") or {}
        name = (fn.get("name") or "").strip()
        if not tc_id or not name:
            continue
        result.append({
            "id": tc_id,
            "type": tc.get("type", "function"),
            "function": {
                "name": name,
                "arguments": fn.get("arguments", "") if isinstance(fn.get("arguments"), str) else "",
            },
        })
    return result
