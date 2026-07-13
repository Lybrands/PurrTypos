"""Process-local provider capability observations.

Only negative, explicitly observed compatibility results are cached.  A process
restart clears the cache, so provider upgrades are detected without migration.
"""

from __future__ import annotations

from threading import Lock


_lock = Lock()
_required_tool_choice_unsupported: set[str] = set()


def provider_capability_key(
    *,
    api_provider: str,
    base_url: str | None,
    model: str,
    thinking_enabled: bool,
) -> str:
    return "|".join((
        str(api_provider or "openai").strip().lower(),
        str(base_url or "").strip().lower().rstrip("/"),
        str(model or "").strip().lower(),
        "thinking" if thinking_enabled else "non-thinking",
    ))


def required_tool_choice_is_unsupported(key: str) -> bool:
    with _lock:
        return str(key) in _required_tool_choice_unsupported


def mark_required_tool_choice_unsupported(key: str) -> None:
    with _lock:
        _required_tool_choice_unsupported.add(str(key))


def clear_provider_capability_cache() -> None:
    """Test/dev helper; production naturally clears observations on restart."""
    with _lock:
        _required_tool_choice_unsupported.clear()
