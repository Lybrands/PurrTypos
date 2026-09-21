"""Atomically persist replacement implementation identity when a Run begins."""

from __future__ import annotations

from collections.abc import Mapping

from agents.shared.implementation import (
    AGENT_IMPLEMENTATION_ATTRIBUTE,
    AgentImplementationIdentity,
    AgentKind,
    legacy_implementation,
)
from agents.shared.implementation_store import SqliteAgentImplementationStore
from purra.contracts import RunCreateParams
from purra.json_values import thaw_json_mapping


class AgentImplementationBeginProjector:
    """Join Run begin and persist the exact implementation when it is known."""

    def __init__(self, db) -> None:
        self._store = SqliteAgentImplementationStore(db)

    async def project(self, run_id: str, params: RunCreateParams) -> None:
        binding = params.binding
        if binding is None:
            return None
        attributes = thaw_json_mapping(binding.attributes)
        raw = attributes.get(AGENT_IMPLEMENTATION_ATTRIBUTE)
        if raw is None:
            profile_id = str(attributes.get("agentProfile") or "").strip()
            agent_kind = _agent_kind_for_profile(profile_id)
            if agent_kind is None:
                return None
            if profile_id != agent_kind.value:
                raise ValueError(
                    "Replacement Agent profile requires implementation identity"
                )
            _validate_profile_domain(attributes, agent_kind)
            await self._store.bind(
                run_id,
                legacy_implementation(agent_kind),
            )
            return None
        if not isinstance(raw, Mapping):
            raise ValueError("Agent implementation identity must be a mapping")
        identity = AgentImplementationIdentity.from_mapping(raw)
        profile_id = str(attributes.get("agentProfile") or "").strip()
        profile_agent_kind = _agent_kind_for_profile(profile_id)
        if profile_id and profile_agent_kind is None:
            raise ValueError("Agent implementation identity has unknown profile")
        if profile_agent_kind is not None and profile_agent_kind is not identity.agent_kind:
            raise ValueError("Agent implementation identity conflicts with profile")
        _validate_profile_domain(attributes, identity.agent_kind)
        await self._store.bind(
            run_id,
            identity,
        )
        return None


_DOMAIN_NAMESPACES = {
    AgentKind.WRITING: "purrtypos.writing",
    AgentKind.NOVEL_ANALYSIS: "purrtypos.novel_analysis",
    AgentKind.SCREENPLAY: "purrtypos.screenplay",
}


def _validate_profile_domain(
    attributes: Mapping[str, object],
    agent_kind: AgentKind,
) -> None:
    namespace = str(attributes.get("domainNamespace") or "").strip()
    if namespace and namespace != _DOMAIN_NAMESPACES[agent_kind]:
        raise ValueError("Agent implementation identity conflicts with domain")


def _agent_kind_for_profile(profile_id: str) -> AgentKind | None:
    normalized = str(profile_id or "").strip()
    for agent_kind in AgentKind:
        if normalized == agent_kind.value or normalized.startswith(
            agent_kind.value + "."
        ):
            return agent_kind
    return None


__all__ = ["AgentImplementationBeginProjector"]
