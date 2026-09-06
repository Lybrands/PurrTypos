"""Generic host profile contract and immutable registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Iterable, Protocol, runtime_checkable

from purra.api import AgentModelTaskRunner
from purra.context_strategies import ContextStrategy
from purra.contracts import AgentRunRequest, RuntimeLimits
from purra.long_tasks import LongTaskRepository
from purra.ports import (
    ContextProvider,
    ExecutionStateFactory,
    PlanningPolicy,
    ResponseJudgePolicy,
    ToolCatalog,
)
from purra.recovery import RecoveryPolicy
from purra.task_admission import LongTaskDispatcher, TaskAdmissionEvaluator
from application.agent_tool_presentation import (
    validate_agent_tool_catalog_presentation,
)


ContextProviderFactory = Callable[[AgentModelTaskRunner], ContextProvider]


@runtime_checkable
class AgentProfileAdapter(Protocol):
    """Product-neutral capabilities consumed by the composition root."""

    planning_policy: PlanningPolicy | None
    context_strategy: ContextStrategy
    execution_state_factory: ExecutionStateFactory
    tool_catalog: ToolCatalog
    context_provider: ContextProvider | None
    runtime_limits: RuntimeLimits
    recovery_policy: RecoveryPolicy


@runtime_checkable
class AgentProfile(Protocol):
    """One host-owned Agent style and all of its optional hooks."""

    id: str
    domain_namespace: str
    adapter: AgentProfileAdapter

    async def prepare_request(
        self,
        request: AgentRunRequest,
    ) -> AgentRunRequest | None: ...

    def context_provider_factory(self) -> ContextProviderFactory | None: ...

    def response_judge_policies(
        self,
        request: AgentRunRequest,
    ) -> tuple[ResponseJudgePolicy, ...]: ...

    def task_admission(self) -> TaskAdmissionEvaluator | None: ...

    def create_long_task_dispatcher(
        self,
        *,
        long_task_repository: LongTaskRepository | None = None,
        executor: Any = None,
    ) -> LongTaskDispatcher | None: ...

    def clear_active_executions(self) -> None: ...


@dataclass(frozen=True, slots=True)
class StaticAgentProfile:
    id: str
    domain_namespace: str
    adapter: AgentProfileAdapter

    def __post_init__(self) -> None:
        profile_id = str(self.id or "").strip()
        namespace = str(self.domain_namespace or "").strip()
        if not profile_id or not namespace:
            raise ValueError("Agent profile id and domain namespace are required")
        object.__setattr__(self, "id", profile_id)
        object.__setattr__(self, "domain_namespace", namespace)

    async def prepare_request(
        self,
        request: AgentRunRequest,
    ) -> AgentRunRequest | None:
        del request
        return None

    def context_provider_factory(self) -> ContextProviderFactory | None:
        return None

    def response_judge_policies(
        self,
        request: AgentRunRequest,
    ) -> tuple[ResponseJudgePolicy, ...]:
        del request
        return ()

    def task_admission(self) -> TaskAdmissionEvaluator | None:
        return None

    def create_long_task_dispatcher(
        self,
        *,
        long_task_repository: LongTaskRepository | None = None,
        executor: Any = None,
    ) -> LongTaskDispatcher | None:
        del long_task_repository, executor
        return None

    def clear_active_executions(self) -> None:
        return None


class AgentProfileRegistry:
    def __init__(self, profiles: Iterable[AgentProfile]):
        items = tuple(profiles)
        if not items:
            raise ValueError("Agent profile registry cannot be empty")
        by_id: dict[str, AgentProfile] = {}
        by_namespace: dict[str, AgentProfile] = {}
        for profile in items:
            profile_id = str(profile.id or "").strip()
            namespace = str(profile.domain_namespace or "").strip()
            if not profile_id or not namespace:
                raise ValueError(
                    "Agent profile id and domain namespace are required"
                )
            if profile_id in by_id or namespace in by_namespace:
                raise ValueError(
                    "Agent profile ids and namespaces must be unique"
                )
            validate_agent_tool_catalog_presentation(
                profile_id,
                profile.adapter.tool_catalog,
            )
            by_id[profile_id] = profile
            by_namespace[namespace] = profile
        self._profiles = items
        self._by_id = by_id
        self._by_namespace = by_namespace

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(profile.id for profile in self._profiles)

    def require(self, profile_id: str) -> AgentProfile:
        normalized = str(profile_id or "").strip()
        profile = self._by_id.get(normalized)
        if profile is None:
            raise ValueError(
                f"unsupported Agent profile: {normalized or '<empty>'}"
            )
        return profile

    def for_request(self, request: AgentRunRequest) -> AgentProfile:
        return self.for_domain_namespace(request.domain_context.namespace)

    def for_domain_namespace(self, domain_namespace: str) -> AgentProfile:
        normalized = str(domain_namespace or "").strip()
        profile = self._by_namespace.get(normalized)
        if profile is None:
            raise ValueError(
                "unsupported Agent domain namespace: "
                f"{normalized or '<empty>'}"
            )
        return profile


__all__ = [
    "AgentProfile",
    "AgentProfileAdapter",
    "AgentProfileRegistry",
    "ContextProviderFactory",
    "StaticAgentProfile",
]
