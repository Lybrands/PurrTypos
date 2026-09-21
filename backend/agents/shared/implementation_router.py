"""Create-time policy and persisted Run routing for product Agent lifecycles."""

from __future__ import annotations

import json
from collections.abc import Mapping

from agents.shared.implementation import (
    AGENT_IMPLEMENTATION_ATTRIBUTE,
    AgentKind,
    legacy_implementation,
)
from agents.shared.implementation_registry import (
    AgentImplementationRegistry,
    AgentImplementationRoute,
    AgentLifecycleAction,
    AgentRolloutPolicy,
)
from agents.shared.implementation_store import SqliteAgentImplementationStore


_NAMESPACE_PREFIXES = {
    AgentKind.WRITING: ("writing.", "purrtypos.writing"),
    AgentKind.NOVEL_ANALYSIS: (
        "novel_source_analysis",
        "purrtypos.novel_analysis",
    ),
    AgentKind.SCREENPLAY: ("screenplay.", "purrtypos.screenplay"),
}


class AgentImplementationRouteConflictError(ValueError):
    """Persisted or historical evidence points at conflicting Agent kinds."""


class SqliteAgentImplementationRouter:
    def __init__(
        self,
        db,
        registry: AgentImplementationRegistry,
        *,
        rollout_policy: AgentRolloutPolicy = AgentRolloutPolicy(),
    ) -> None:
        self._db = db
        self._store = SqliteAgentImplementationStore(db)
        self._registry = registry
        self._rollout_policy = rollout_policy

    def for_create(
        self,
        agent_kind: AgentKind,
        *,
        recipe_version: int | None = None,
    ) -> AgentImplementationRoute:
        identity = self._rollout_policy.identity_for_create(
            AgentKind(agent_kind),
            recipe_version=recipe_version,
        )
        return AgentImplementationRoute(
            action=AgentLifecycleAction.CREATE,
            identity=identity,
            profile=self._registry.require(identity),
            identity_source="create_policy",
        )

    async def for_run(
        self,
        run_id: str,
        *,
        action: AgentLifecycleAction,
        expected_agent_kind: AgentKind | None = None,
    ) -> AgentImplementationRoute:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("Agent Run id is required")
        normalized_action = AgentLifecycleAction(action)
        if normalized_action is AgentLifecycleAction.CREATE:
            raise ValueError("Persisted Run routing cannot use create action")

        persisted = await self._store.load(normalized_run_id)
        source = "persisted"
        if persisted is None:
            agent_kind = await self._infer_legacy_agent_kind(normalized_run_id)
            persisted = legacy_implementation(agent_kind)
            source = "legacy_inferred"
        else:
            await self._validate_persisted_agent_kind(
                normalized_run_id,
                persisted.agent_kind,
            )
        if expected_agent_kind is not None:
            expected = AgentKind(expected_agent_kind)
            if persisted.agent_kind is not expected:
                raise AgentImplementationRouteConflictError(
                    "Agent Run belongs to another Agent kind"
                )
        return AgentImplementationRoute(
            action=normalized_action,
            identity=persisted,
            profile=self._registry.require(persisted),
            identity_source=source,
        )

    async def should_route(self, run_id: str) -> bool:
        """Distinguish product Agent Runs from unrelated host/runtime fixtures."""

        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("Agent Run id is required")
        row = await self._db.fetch_one(
            "SELECT agent_kind, implementation_id, implementation_version, "
            "tool_contract_version, recipe_version, artifact_schema_version, "
            "root_run_id, binding_namespace, binding_attributes_json, mode "
            "FROM ai_agent_runs WHERE id = ?",
            [normalized_run_id],
        )
        if row is None:
            raise LookupError("Agent Run does not exist")
        if any(
            row.get(column) is not None
            for column in (
                "agent_kind",
                "implementation_id",
                "implementation_version",
                "tool_contract_version",
                "recipe_version",
                "artifact_schema_version",
            )
        ):
            return True
        attributes = _load_attributes(row.get("binding_attributes_json"))
        if (
            "agentProfile" in attributes
            or AGENT_IMPLEMENTATION_ATTRIBUTE in attributes
        ):
            return True
        if _agent_kind_candidates(row):
            return True
        root_run_id = str(row.get("root_run_id") or "").strip()
        if root_run_id and root_run_id != normalized_run_id:
            return await self.should_route(root_run_id)
        return False

    async def _infer_legacy_agent_kind(self, run_id: str) -> AgentKind:
        row = await self._load_run_evidence(run_id)
        if row is None:
            raise LookupError("Agent Run does not exist")
        candidates = _agent_kind_candidates(row)
        if len(candidates) > 1:
            raise AgentImplementationRouteConflictError(
                "Historical Agent Run has conflicting Agent kind evidence"
            )
        if len(candidates) == 1:
            return next(iter(candidates))
        root_run_id = str(row.get("root_run_id") or "").strip()
        if root_run_id and root_run_id != run_id:
            root_route = await self.for_run(
                root_run_id,
                action=AgentLifecycleAction.REPLAY,
            )
            return root_route.identity.agent_kind
        raise AgentImplementationRouteConflictError(
            "Historical Agent Run kind cannot be inferred"
        )

    async def _validate_persisted_agent_kind(
        self,
        run_id: str,
        persisted_agent_kind: AgentKind,
    ) -> None:
        row = await self._load_run_evidence(run_id)
        if row is None:  # pragma: no cover - store already established existence
            raise LookupError("Agent Run does not exist")
        candidates = _agent_kind_candidates(row)
        if len(candidates) > 1 or (
            candidates and persisted_agent_kind not in candidates
        ):
            raise AgentImplementationRouteConflictError(
                "Persisted Agent implementation conflicts with Run evidence"
            )

    async def _load_run_evidence(self, run_id: str):
        return await self._db.fetch_one(
            "SELECT root_run_id, binding_namespace, binding_attributes_json, mode "
            "FROM ai_agent_runs WHERE id = ?",
            [run_id],
        )


def _agent_kind_candidates(row: Mapping[str, object]) -> set[AgentKind]:
    candidates: set[AgentKind] = set()
    attributes = _load_attributes(row.get("binding_attributes_json"))
    profile_id = str(attributes.get("agentProfile") or "").strip()
    if profile_id:
        profile_kind = _agent_kind_for_profile(profile_id)
        if profile_kind is None:
            raise AgentImplementationRouteConflictError(
                "Historical Agent Run has an unknown Agent profile"
            )
        candidates.add(profile_kind)
    namespace = str(row.get("binding_namespace") or "").strip()
    for agent_kind, prefixes in _NAMESPACE_PREFIXES.items():
        if any(_namespace_matches(namespace, prefix) for prefix in prefixes):
            candidates.add(agent_kind)
    mode = str(row.get("mode") or "").strip()
    if mode == "writing" or mode.startswith("writing_"):
        candidates.add(AgentKind.WRITING)
    elif mode == "novel_analysis" or mode.startswith("novel_analysis_"):
        candidates.add(AgentKind.NOVEL_ANALYSIS)
    elif mode == "screenplay" or mode.startswith("screenplay_"):
        candidates.add(AgentKind.SCREENPLAY)
    return candidates


def _load_attributes(raw: object) -> Mapping[str, object]:
    if raw in (None, ""):
        return {}
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError) as error:
        raise AgentImplementationRouteConflictError(
            "Historical Agent Run binding attributes are invalid"
        ) from error
    if not isinstance(value, Mapping):
        raise AgentImplementationRouteConflictError(
            "Historical Agent Run binding attributes are invalid"
        )
    return value


def _namespace_matches(namespace: str, prefix: str) -> bool:
    if prefix.endswith("."):
        return namespace.startswith(prefix)
    return namespace == prefix or namespace.startswith(prefix + ".")


def _agent_kind_for_profile(profile_id: str) -> AgentKind | None:
    normalized = str(profile_id or "").strip()
    for agent_kind in AgentKind:
        if normalized == agent_kind.value or normalized.startswith(
            agent_kind.value + "."
        ):
            return agent_kind
    return None


__all__ = [
    "AgentImplementationRouteConflictError",
    "SqliteAgentImplementationRouter",
]
