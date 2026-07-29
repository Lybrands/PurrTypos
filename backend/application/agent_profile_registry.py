"""Immutable composition registry for product Agent profiles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from agent_core.contracts import AgentRunRequest


@dataclass(frozen=True, slots=True)
class AgentProfileRegistration:
    id: str
    domain_namespace: str
    adapter: Any

    def __post_init__(self) -> None:
        profile_id = str(self.id or "").strip()
        namespace = str(self.domain_namespace or "").strip()
        if not profile_id or not namespace:
            raise ValueError("Agent profile id and domain namespace are required")
        object.__setattr__(self, "id", profile_id)
        object.__setattr__(self, "domain_namespace", namespace)


class AgentProfileRegistry:
    def __init__(self, registrations: Iterable[AgentProfileRegistration]):
        items = tuple(registrations)
        by_id = {item.id: item for item in items}
        by_namespace = {item.domain_namespace: item for item in items}
        if not items:
            raise ValueError("Agent profile registry cannot be empty")
        if len(by_id) != len(items) or len(by_namespace) != len(items):
            raise ValueError("Agent profile ids and namespaces must be unique")
        self._registrations = items
        self._by_id = by_id
        self._by_namespace = by_namespace

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self._registrations)

    def require(self, profile_id: str) -> AgentProfileRegistration:
        normalized = str(profile_id or "").strip()
        registration = self._by_id.get(normalized)
        if registration is None:
            raise ValueError(f"unsupported Agent profile: {normalized or '<empty>'}")
        return registration

    def for_request(self, request: AgentRunRequest) -> AgentProfileRegistration:
        registration = self._by_namespace.get(
            request.domain_context.namespace
        )
        if registration is None:
            raise ValueError(
                "unsupported Agent domain namespace: "
                f"{request.domain_context.namespace}"
            )
        return registration
