"""Business-owned Agent role definitions shared by product adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from purra.contracts import ToolExecutionMode


@dataclass(frozen=True, slots=True)
class AgentRoleDefinition:
    """One product role layered on top of Core's generic delegation model."""

    id: str
    title: str
    delegation_description: str
    instruction: str
    allowed_tool_modes: frozenset[ToolExecutionMode] = frozenset({
        ToolExecutionMode.READ,
    })

    def __post_init__(self) -> None:
        for field_name in (
            "id",
            "title",
            "delegation_description",
            "instruction",
        ):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"Agent role {field_name} is required")
            object.__setattr__(self, field_name, value)
        modes = frozenset(ToolExecutionMode(mode) for mode in self.allowed_tool_modes)
        if not modes:
            raise ValueError("Agent role must allow at least one tool mode")
        object.__setattr__(self, "allowed_tool_modes", modes)


class AgentRoleRegistry:
    """Immutable product role registry injected into application services."""

    def __init__(self, definitions: Iterable[AgentRoleDefinition]) -> None:
        items = tuple(definitions)
        by_id = {item.id: item for item in items}
        if not items:
            raise ValueError("Agent role registry cannot be empty")
        if len(by_id) != len(items):
            raise ValueError("Agent role ids must be unique")
        self._definitions = items
        self._by_id = by_id

    @property
    def definitions(self) -> tuple[AgentRoleDefinition, ...]:
        return self._definitions

    @property
    def role_ids(self) -> tuple[str, ...]:
        return tuple(item.id for item in self._definitions)

    def get(self, role_id: str) -> AgentRoleDefinition | None:
        return self._by_id.get(str(role_id or "").strip())

    def require(self, role_id: str) -> AgentRoleDefinition:
        normalized = str(role_id or "").strip()
        definition = self.get(normalized)
        if definition is None:
            raise ValueError(f"unsupported Agent role: {normalized or '<empty>'}")
        return definition
