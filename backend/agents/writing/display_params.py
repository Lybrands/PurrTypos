"""Model-owned arguments projected into operation display for the UI.

PurrA tool events deliberately omit arguments; the conversation timeline
therefore renders only a static tool name. Writing tools opt into showing
the user-visible specifics (which chapter, which search query) by projecting
a whitelisted subset of model-owned arguments into the operation's
``labelParams.toolArguments``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Mapping

# Pagination windows, transport caps, and bulk payloads never help a user
# read a tool row; dropping them keeps labelParams small enough for the
# canonical output stream.
_DISPLAY_NOISE_KEYS = frozenset({
    "page",
    "pageSize",
    "limit",
    "offset",
    "maxTextLength",
    "tokenBudget",
    "content",
    "automaticRefs",
})

_MAX_STRING_CHARS = 120
_MAX_LIST_ITEMS = 8
_MAX_TOTAL_CHARS = 600


def display_arguments(
    arguments: Mapping[str, Any],
    model_owned_keys: Mapping[str, object] | tuple[str, ...],
) -> dict[str, Any]:
    """Return a compact, JSON-safe subset of arguments for display."""

    keys = tuple(model_owned_keys)
    projected: dict[str, Any] = {}
    budget = _MAX_TOTAL_CHARS
    for key in keys:
        if key in _DISPLAY_NOISE_KEYS or key not in arguments:
            continue
        value, cost = _compact(arguments[key])
        if value is None or cost > budget:
            continue
        projected[key] = value
        budget -= cost
    return projected


def _compact(value: Any) -> tuple[Any, int]:
    """Return (display value, approximate serialized cost)."""

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, 0
        if len(text) > _MAX_STRING_CHARS:
            return text[:_MAX_STRING_CHARS] + "…", _MAX_STRING_CHARS + 1
        return text, len(text)
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value, 8
    if value is None:
        return None, 0
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items: list[Any] = []
        cost = 2
        for item in value[:_MAX_LIST_ITEMS]:
            compact_item, item_cost = _compact(item)
            if compact_item is None:
                continue
            items.append(compact_item)
            cost += item_cost
        return (items, cost) if items else (None, 0)
    if isinstance(value, Mapping):
        entries: list[tuple[str, Any]] = []
        cost = 2
        for key, item in value.items():
            compact_item, item_cost = _compact(item)
            if compact_item is None:
                continue
            entries.append((str(key), compact_item))
            cost += item_cost + len(str(key))
        if not entries:
            return None, 0
        return dict(entries), cost
    return None, 0
