"""Immutable composition registry for product Agent profiles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Iterable, Protocol, runtime_checkable

from purra.api import AgentModelTaskRunner
from purra.contracts import AgentRunRequest
from purra.long_tasks import LongTaskRepository
from purra.ports import ContextProvider, ResponseJudgePolicy
from purra.task_admission import LongTaskDispatcher, TaskAdmissionEvaluator
from purra.work_items.ports import WorkItemRepository


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


ContextProviderFactory = Callable[[AgentModelTaskRunner], ContextProvider]


@runtime_checkable
class AgentProfileExtension(Protocol):
    def profile_registration(self) -> AgentProfileRegistration: ...

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
        work_item_repository: WorkItemRepository | None = None,
        long_task_repository: LongTaskRepository | None = None,
        executor: Any = None,
    ) -> LongTaskDispatcher | None: ...

    def clear_active_executions(self) -> None: ...


@dataclass(frozen=True, slots=True)
class StaticAgentProfileExtension:
    registration: AgentProfileRegistration

    def profile_registration(self) -> AgentProfileRegistration:
        return self.registration

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
        work_item_repository: WorkItemRepository | None = None,
        long_task_repository: LongTaskRepository | None = None,
        executor: Any = None,
    ) -> LongTaskDispatcher | None:
        del work_item_repository, long_task_repository, executor
        return None

    def clear_active_executions(self) -> None:
        return None


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
        return self.for_domain_namespace(request.domain_context.namespace)

    def for_domain_namespace(
        self,
        domain_namespace: str,
    ) -> AgentProfileRegistration:
        normalized = str(domain_namespace or "").strip()
        registration = self._by_namespace.get(normalized)
        if registration is None:
            raise ValueError(
                "unsupported Agent domain namespace: "
                f"{normalized or '<empty>'}"
            )
        return registration
