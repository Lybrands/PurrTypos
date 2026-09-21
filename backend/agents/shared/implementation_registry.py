"""Exact, fail-closed registry for versioned product Agent implementations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Iterable

from agents.shared.implementation import (
    AgentImplementationIdentity,
    AgentKind,
    legacy_implementation,
    replacement_implementation,
)


class AgentLifecycleAction(StrEnum):
    CREATE = "create"
    RESUME = "resume"
    CANCEL = "cancel"
    REPLAY = "replay"


class AgentImplementationNotInstalledError(LookupError):
    """The persisted or selected implementation has no installed profile."""


@dataclass(frozen=True, slots=True)
class AgentImplementationProfile:
    """Bind one exact implementation contract to one runtime profile."""

    identity: AgentImplementationIdentity
    runtime_profile_id: str

    def __post_init__(self) -> None:
        normalized = str(self.runtime_profile_id or "").strip()
        if not normalized:
            raise ValueError("Agent runtime profile id is required")
        if self.identity.recipe_version is not None:
            raise ValueError(
                "Agent implementation profile cannot pin one recipe version"
            )
        object.__setattr__(self, "runtime_profile_id", normalized)


@dataclass(frozen=True, slots=True)
class AgentImplementationRoute:
    action: AgentLifecycleAction
    identity: AgentImplementationIdentity
    profile: AgentImplementationProfile
    identity_source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", AgentLifecycleAction(self.action))
        if self.identity_source not in {"create_policy", "persisted", "legacy_inferred"}:
            raise ValueError("Agent implementation identity source is invalid")
        if replace(self.identity, recipe_version=None) != self.profile.identity:
            raise ValueError("Agent implementation route profile conflicts")

    @property
    def runtime_profile_id(self) -> str:
        return self.profile.runtime_profile_id


class AgentImplementationRegistry:
    """Resolve only exact identities; versions never silently fall forward."""

    def __init__(self, profiles: Iterable[AgentImplementationProfile]) -> None:
        items = tuple(profiles)
        if not items:
            raise ValueError("Agent implementation registry cannot be empty")
        by_identity: dict[AgentImplementationIdentity, AgentImplementationProfile] = {}
        for profile in items:
            if profile.identity in by_identity:
                raise ValueError("Agent implementation profile is duplicated")
            by_identity[profile.identity] = profile
        self._profiles = items
        self._by_identity = by_identity

    @property
    def profiles(self) -> tuple[AgentImplementationProfile, ...]:
        return self._profiles

    def require(
        self,
        identity: AgentImplementationIdentity,
    ) -> AgentImplementationProfile:
        profile = self._by_identity.get(replace(identity, recipe_version=None))
        if profile is None:
            raise AgentImplementationNotInstalledError(
                "Agent implementation is not installed: "
                f"{identity.agent_kind.value}/"
                f"{identity.implementation_id}/"
                f"v{identity.implementation_version}"
            )
        return profile


@dataclass(frozen=True, slots=True)
class AgentRolloutPolicy:
    """Process configuration used only when a brand-new Run is created."""

    replacement_agent_kinds: frozenset[AgentKind] = frozenset()
    create_identity_overrides: tuple[AgentImplementationIdentity, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "replacement_agent_kinds",
            frozenset(AgentKind(value) for value in self.replacement_agent_kinds),
        )
        overrides = tuple(self.create_identity_overrides)
        if any(item.recipe_version is not None for item in overrides):
            raise ValueError("create identity override cannot pin a recipe version")
        if len({item.agent_kind for item in overrides}) != len(overrides):
            raise ValueError("create identity override is duplicated")
        object.__setattr__(self, "create_identity_overrides", overrides)

    def identity_for_create(
        self,
        agent_kind: AgentKind,
        *,
        recipe_version: int | None = None,
    ) -> AgentImplementationIdentity:
        normalized = AgentKind(agent_kind)
        override = next((
            item for item in self.create_identity_overrides
            if item.agent_kind is normalized
        ), None)
        if override is not None:
            return replace(override, recipe_version=recipe_version)
        if normalized in self.replacement_agent_kinds:
            return replacement_implementation(
                normalized,
                recipe_version=recipe_version,
            )
        if recipe_version is not None:
            return replace(
                legacy_implementation(normalized),
                recipe_version=recipe_version,
            )
        return legacy_implementation(normalized)


def legacy_implementation_profiles() -> tuple[AgentImplementationProfile, ...]:
    return tuple(
        AgentImplementationProfile(
            identity=legacy_implementation(agent_kind),
            runtime_profile_id=agent_kind.value,
        )
        for agent_kind in AgentKind
    )


__all__ = [
    "AgentImplementationNotInstalledError",
    "AgentImplementationProfile",
    "AgentImplementationRegistry",
    "AgentImplementationRoute",
    "AgentLifecycleAction",
    "AgentRolloutPolicy",
    "legacy_implementation_profiles",
]
