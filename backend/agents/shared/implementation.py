"""Immutable implementation identity for version-routed Agent Runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any


class AgentKind(StrEnum):
    WRITING = "writing"
    NOVEL_ANALYSIS = "novel_analysis"
    SCREENPLAY = "screenplay"


LEGACY_IMPLEMENTATION_ID = "legacy-frozen-2026-09-12"
REPLACEMENT_IMPLEMENTATION_ID = "purra-native"
REPLACEMENT_IMPLEMENTATION_VERSION = 1
REPLACEMENT_TOOL_CONTRACT_VERSION = 1
REPLACEMENT_ARTIFACT_SCHEMA_VERSION = 1
AGENT_IMPLEMENTATION_ATTRIBUTE = "agentImplementation"

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]*$")
_REQUIRED_KEYS = frozenset({
    "agentKind",
    "implementationId",
    "implementationVersion",
    "toolContractVersion",
    "artifactSchemaVersion",
})
_OPTIONAL_KEYS = frozenset({"recipeVersion"})


@dataclass(frozen=True, slots=True)
class AgentImplementationIdentity:
    """The execution contract selected once when a Root Run is created."""

    agent_kind: AgentKind
    implementation_id: str
    implementation_version: int
    tool_contract_version: int
    artifact_schema_version: int
    recipe_version: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "agent_kind", AgentKind(self.agent_kind))
        normalized = str(self.implementation_id or "").strip()
        if not _IDENTIFIER.fullmatch(normalized):
            raise ValueError("Agent implementation id is invalid")
        object.__setattr__(self, "implementation_id", normalized)
        for field_name in (
            "implementation_version",
            "tool_contract_version",
            "artifact_schema_version",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        if self.recipe_version is not None:
            if type(self.recipe_version) is not int or self.recipe_version < 1:
                raise ValueError("recipe_version must be a positive integer")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "agentKind": self.agent_kind.value,
            "implementationId": self.implementation_id,
            "implementationVersion": self.implementation_version,
            "toolContractVersion": self.tool_contract_version,
            "artifactSchemaVersion": self.artifact_schema_version,
            **(
                {"recipeVersion": self.recipe_version}
                if self.recipe_version is not None
                else {}
            ),
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "AgentImplementationIdentity":
        keys = frozenset(value)
        if not _REQUIRED_KEYS.issubset(keys) or not keys.issubset(
            _REQUIRED_KEYS | _OPTIONAL_KEYS
        ):
            raise ValueError("Agent implementation identity shape is invalid")
        return cls(
            agent_kind=AgentKind(str(value["agentKind"])),
            implementation_id=str(value["implementationId"]),
            implementation_version=value["implementationVersion"],
            tool_contract_version=value["toolContractVersion"],
            artifact_schema_version=value["artifactSchemaVersion"],
            recipe_version=value.get("recipeVersion"),
        )


def legacy_implementation(agent_kind: AgentKind) -> AgentImplementationIdentity:
    """Classify an unversioned historical Run without routing it to new code."""

    return AgentImplementationIdentity(
        agent_kind=agent_kind,
        implementation_id=LEGACY_IMPLEMENTATION_ID,
        implementation_version=1,
        tool_contract_version=1,
        artifact_schema_version=1,
    )


def replacement_implementation(
    agent_kind: AgentKind,
    *,
    recipe_version: int | None = None,
) -> AgentImplementationIdentity:
    return AgentImplementationIdentity(
        agent_kind=agent_kind,
        implementation_id=REPLACEMENT_IMPLEMENTATION_ID,
        implementation_version=REPLACEMENT_IMPLEMENTATION_VERSION,
        tool_contract_version=REPLACEMENT_TOOL_CONTRACT_VERSION,
        artifact_schema_version=REPLACEMENT_ARTIFACT_SCHEMA_VERSION,
        recipe_version=recipe_version,
    )


def resolve_implementation(
    value: Mapping[str, Any] | None,
    *,
    expected_agent_kind: AgentKind,
) -> AgentImplementationIdentity:
    """Resolve persisted identity; missing identity is always dated legacy."""

    identity = (
        legacy_implementation(expected_agent_kind)
        if value is None
        else AgentImplementationIdentity.from_mapping(value)
    )
    if identity.agent_kind is not AgentKind(expected_agent_kind):
        raise ValueError("Agent implementation identity belongs to another Agent")
    return identity


__all__ = [
    "AGENT_IMPLEMENTATION_ATTRIBUTE",
    "AgentImplementationIdentity",
    "AgentKind",
    "LEGACY_IMPLEMENTATION_ID",
    "REPLACEMENT_IMPLEMENTATION_ID",
    "legacy_implementation",
    "replacement_implementation",
    "resolve_implementation",
]
