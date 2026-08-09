"""Context retrieval and compaction ports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from agent_core.context_orchestration.contracts import (
    ContextCompressionRequest,
    ConversationCompactionResult,
)
from agent_core.context_orchestration.ledger import ContextCompactionBudget
from agent_core.contracts import (
    AgentRunRequest,
    ContextBudget,
    ContextBudgetClaim,
    ContextBundle,
    PostPlanningContextOptimizationResult,
    TaskContextRequest,
)
from agent_core.ports.model import CancellationSignal


@runtime_checkable
class ContextProvider(Protocol):
    async def build_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle: ...


@runtime_checkable
class ContextDemandProvider(Protocol):
    """Optionally describe request-specific demand before Core allocates it."""

    async def describe_context_demands(
        self,
        request: AgentRunRequest,
        signal: CancellationSignal | None = None,
    ) -> tuple[ContextBudgetClaim, ...]: ...


@runtime_checkable
class StagedContextProvider(Protocol):
    """Optional provider separating lightweight planning from formal recall."""

    async def build_planning_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle: ...

    async def build_task_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        task: TaskContextRequest,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle: ...


@runtime_checkable
class TaskContextDemandProvider(Protocol):
    """Optionally declare post-planning demand from a compiled TaskSpec."""

    async def describe_task_context_demands(
        self,
        request: AgentRunRequest,
        task: TaskContextRequest,
        signal: CancellationSignal | None = None,
    ) -> tuple[ContextBudgetClaim, ...]: ...


@runtime_checkable
class ContextCompressionHook(Protocol):
    """Application-owned implementation of semantic context reduction."""

    async def compress(
        self,
        compression: ContextCompressionRequest,
        signal: CancellationSignal | None = None,
    ) -> ConversationCompactionResult: ...


@runtime_checkable
class ConversationCompactor(Protocol):
    """Core coordinator used by the run lifecycle."""

    async def prepare(
        self,
        request: AgentRunRequest,
        signal: CancellationSignal | None = None,
        *,
        on_compaction_started: (
            Callable[[Mapping[str, Any]], Awaitable[None]] | None
        ) = None,
        budget: ContextCompactionBudget | None = None,
        anticipated_context_tokens: int = 0,
        resolved_context_tokens: int | None = None,
        output_reserve_tokens: int | None = None,
        provider_input_tokens: int | None = None,
        planned_step_count: int | None = None,
        planned_tool_count: int | None = None,
        selected_tool_count: int | None = None,
    ) -> ConversationCompactionResult: ...


@runtime_checkable
class PostPlanningContextOptimizer(Protocol):
    """Re-evaluate conversation pressure after the actual plan is compiled."""

    async def optimize(
        self,
        request: AgentRunRequest,
        *,
        provider_input_tokens: int,
        resolved_context_tokens: int,
        output_reserve_tokens: int,
        planned_step_count: int,
        planned_tool_count: int,
        selected_tool_names: Sequence[str],
        signal: CancellationSignal | None = None,
        on_compaction_started: (
            Callable[[Mapping[str, Any]], Awaitable[None]] | None
        ) = None,
    ) -> PostPlanningContextOptimizationResult: ...


__all__ = [name for name in globals() if not name.startswith("_")]
