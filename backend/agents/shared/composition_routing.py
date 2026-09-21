"""Version-aware profile selection inside one shared Agent composition."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from agents.shared.implementation import AgentKind
from agents.shared.implementation_registry import AgentLifecycleAction
from application.agent_profile_registry import AgentProfile
from application.agent_tool_presentation import (
    validate_agent_tool_catalog_presentation,
)
from purra.contracts import AgentRunRequest


RUNTIME_PROFILE_METADATA_KEY = "hostAgentRuntimeProfile"
IMPLEMENTATION_OWNER_RUN_METADATA_KEY = "hostAgentImplementationOwnerRunId"

_AGENT_KIND_BY_NAMESPACE = {
    "purrtypos.writing": AgentKind.WRITING,
    "purrtypos.novel_analysis": AgentKind.NOVEL_ANALYSIS,
    "purrtypos.screenplay": AgentKind.SCREENPLAY,
}


class VersionedAgentProfileRegistry:
    """Allow versioned profiles to share a domain namespace safely.

    The runtime profile is selected by host-owned request metadata. Requests
    without that marker resolve only through an explicit namespace default, so
    a caller can never win a rollout decision by supplying another profile id.
    """

    def __init__(
        self,
        profiles: Iterable[AgentProfile],
        *,
        default_profile_ids: Mapping[str, str],
    ) -> None:
        items = tuple(profiles)
        if not items:
            raise ValueError("Agent profile registry cannot be empty")
        by_id: dict[str, AgentProfile] = {}
        by_namespace: dict[str, list[AgentProfile]] = {}
        for profile in items:
            profile_id = str(profile.id or "").strip()
            namespace = str(profile.domain_namespace or "").strip()
            if not profile_id or not namespace:
                raise ValueError("Agent profile id and domain namespace are required")
            if profile_id in by_id:
                raise ValueError("Agent profile ids must be unique")
            validate_agent_tool_catalog_presentation(
                profile_id,
                profile.adapter.tool_catalog,
            )
            by_id[profile_id] = profile
            by_namespace.setdefault(namespace, []).append(profile)

        defaults: dict[str, AgentProfile] = {}
        for namespace, profile_id in default_profile_ids.items():
            normalized_namespace = str(namespace or "").strip()
            normalized_profile_id = str(profile_id or "").strip()
            profile = by_id.get(normalized_profile_id)
            if profile is None or profile.domain_namespace != normalized_namespace:
                raise ValueError("Agent namespace default profile is invalid")
            defaults[normalized_namespace] = profile
        for namespace, candidates in by_namespace.items():
            if len(candidates) > 1 and namespace not in defaults:
                raise ValueError(
                    "Versioned Agent namespace requires an explicit default profile"
                )
            if len(candidates) == 1:
                defaults.setdefault(namespace, candidates[0])

        self._profiles = items
        self._by_id = by_id
        self._by_namespace = by_namespace
        self._defaults = defaults

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(profile.id for profile in self._profiles)

    def require(self, profile_id: str) -> AgentProfile:
        normalized = str(profile_id or "").strip()
        profile = self._by_id.get(normalized)
        if profile is None:
            raise ValueError(f"unsupported Agent profile: {normalized or '<empty>'}")
        return profile

    def for_request(self, request: AgentRunRequest) -> AgentProfile:
        namespace = str(request.domain_context.namespace or "").strip()
        selected = str(request.metadata.get(RUNTIME_PROFILE_METADATA_KEY) or "").strip()
        if not selected:
            return self.for_domain_namespace(namespace)
        profile = self.require(selected)
        if profile.domain_namespace != namespace:
            raise ValueError("Agent runtime profile conflicts with request domain")
        return profile

    def for_domain_namespace(self, domain_namespace: str) -> AgentProfile:
        normalized = str(domain_namespace or "").strip()
        profile = self._defaults.get(normalized)
        if profile is None:
            raise ValueError(
                "unsupported Agent domain namespace: "
                f"{normalized or '<empty>'}"
            )
        return profile


class VersionedAgentRequestRouter:
    """Translate implementation routes into host-owned profile selection."""

    def __init__(self, implementation_router) -> None:
        self._implementation_router = implementation_router

    def route_for_create(self, request: AgentRunRequest) -> AgentRunRequest:
        agent_kind = _agent_kind_for_request(request)
        route = self._implementation_router.for_create(agent_kind)
        return _bind_runtime_profile(request, route.runtime_profile_id)

    async def route_for_request(
        self,
        request: AgentRunRequest,
        *,
        run_id: str | None = None,
    ) -> AgentRunRequest:
        if run_id is not None:
            return await self.route_for_run(request, run_id)
        owner_run_id = str(
            request.metadata.get(IMPLEMENTATION_OWNER_RUN_METADATA_KEY) or ""
        ).strip()
        if not owner_run_id:
            return self.route_for_create(request)
        agent_kind = _agent_kind_for_request(request)
        route = await self._implementation_router.for_run(
            owner_run_id,
            action=AgentLifecycleAction.REPLAY,
            expected_agent_kind=agent_kind,
        )
        return _bind_runtime_profile(request, route.runtime_profile_id)

    async def route_for_run(
        self,
        request: AgentRunRequest,
        run_id: str,
    ) -> AgentRunRequest:
        agent_kind = _agent_kind_for_request(request)
        route = await self._implementation_router.for_run(
            run_id,
            action=AgentLifecycleAction.RESUME,
            expected_agent_kind=agent_kind,
        )
        return _bind_runtime_profile(request, route.runtime_profile_id)


def _agent_kind_for_request(request: AgentRunRequest) -> AgentKind:
    namespace = str(request.domain_context.namespace or "").strip()
    agent_kind = _AGENT_KIND_BY_NAMESPACE.get(namespace)
    if agent_kind is None:
        raise ValueError(
            f"unsupported Agent domain namespace: {namespace or '<empty>'}"
        )
    return agent_kind


def _bind_runtime_profile(
    request: AgentRunRequest,
    runtime_profile_id: str,
) -> AgentRunRequest:
    metadata = dict(request.metadata)
    supplied = str(metadata.get(RUNTIME_PROFILE_METADATA_KEY) or "").strip()
    if supplied and supplied != runtime_profile_id:
        raise ValueError("Agent runtime profile is host-owned")
    metadata[RUNTIME_PROFILE_METADATA_KEY] = runtime_profile_id
    return replace(request, metadata=metadata)


__all__ = [
    "IMPLEMENTATION_OWNER_RUN_METADATA_KEY",
    "RUNTIME_PROFILE_METADATA_KEY",
    "VersionedAgentProfileRegistry",
    "VersionedAgentRequestRouter",
]
