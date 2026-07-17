"""Lifespan-scoped observations about model-provider compatibility."""

from __future__ import annotations

from threading import Lock


class ProviderCapabilityCache:
    """Remember only explicitly observed negative provider capabilities.

    Instances are owned by an application composition.  Dropping the
    composition deliberately drops the observations so a restarted backend
    probes providers again after an upgrade or configuration change.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._required_tool_choice_unsupported: set[str] = set()

    @staticmethod
    def key(
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

    def required_tool_choice_is_unsupported(self, key: str) -> bool:
        with self._lock:
            return str(key) in self._required_tool_choice_unsupported

    def mark_required_tool_choice_unsupported(self, key: str) -> None:
        with self._lock:
            self._required_tool_choice_unsupported.add(str(key))

    def clear(self) -> None:
        with self._lock:
            self._required_tool_choice_unsupported.clear()


__all__ = ["ProviderCapabilityCache"]
