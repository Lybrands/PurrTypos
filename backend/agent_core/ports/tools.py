"""Tool execution, observation, approval, and idempotency ports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    ApprovalStatus,
    ExecutionState,
    RunId,
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolBatchResult,
    ToolCall,
    ToolContextContract,
    ToolDataContract,
    ToolHandlerResult,
    ToolPolicy,
    ToolSchema,
    TraceRecord,
)
from agent_core.events import AgentEvent
from agent_core.json_values import freeze_json_mapping
from agent_core.ports.model import CancellationSignal


@runtime_checkable
class ToolHandler(Protocol):
    async def __call__(
        self,
        state: ExecutionState,
        arguments: Mapping[str, Any],
        signal: CancellationSignal | None = None,
    ) -> ToolHandlerResult: ...


@runtime_checkable
class ScopeValidator(Protocol):
    async def __call__(
        self,
        state: ExecutionState,
        arguments: Mapping[str, Any],
        signal: CancellationSignal | None = None,
    ) -> str | None: ...


@runtime_checkable
class CacheProbe(Protocol):
    def will_hit(
        self,
        state: ExecutionState,
        arguments: Mapping[str, Any],
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class ToolRegistration:
    schema: ToolSchema
    handler: ToolHandler
    policy: ToolPolicy
    scope_validator: ScopeValidator | None = None
    cache_probe: CacheProbe | None = None
    cancellation_linearizable: bool = False
    # Long-running host workflows may own durable state transitions outside
    # Core's single tool-receipt transaction.
    host_managed_durability: bool = False
    planning_dependencies: tuple[str, ...] = ()
    context_contract: ToolContextContract = ToolContextContract()
    data_contract: ToolDataContract = ToolDataContract()
    max_argument_chars: int | None = None
    planning_capability: ToolSchema | None = None
    host_planned_arguments: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "planning_dependencies",
            tuple(dict.fromkeys(
                str(name).strip()
                for name in self.planning_dependencies
                if str(name).strip()
            )),
        )
        object.__setattr__(
            self,
            "host_managed_durability",
            bool(self.host_managed_durability),
        )
        if not isinstance(self.context_contract, ToolContextContract):
            raise TypeError("tool context_contract must be ToolContextContract")
        if not isinstance(self.data_contract, ToolDataContract):
            raise TypeError("tool data_contract must be ToolDataContract")
        if self.max_argument_chars is not None:
            limit = int(self.max_argument_chars)
            if limit <= 0:
                raise ValueError("tool max_argument_chars must be positive")
            object.__setattr__(self, "max_argument_chars", limit)
        if (
            self.planning_capability is not None
            and not isinstance(self.planning_capability, ToolSchema)
        ):
            raise TypeError("tool planning_capability must be a ToolSchema")
        if self.host_planned_arguments is not None:
            if not isinstance(self.host_planned_arguments, Mapping):
                raise TypeError("tool host_planned_arguments must be a mapping")
            if self.data_contract.model_owned_paths:
                raise ValueError(
                    "host-planned tools cannot require model-owned arguments"
                )
            object.__setattr__(
                self,
                "host_planned_arguments",
                freeze_json_mapping(self.host_planned_arguments),
            )

    @property
    def prerequisite_tools(self) -> tuple[str, ...]:
        """Return the host-owned prerequisites with legacy compatibility."""

        if self.context_contract.prerequisite_tools:
            return self.context_contract.prerequisite_tools
        return self.planning_dependencies


@runtime_checkable
class ToolCatalog(Protocol):
    """Expose the full registry for startup validation and a request scope."""

    def registrations(self) -> Sequence[ToolRegistration]: ...

    def enabled_names(self, request: AgentRunRequest) -> frozenset[str]: ...


@runtime_checkable
class EventSink(Protocol):
    async def emit(self, event: AgentEvent) -> None: ...


@runtime_checkable
class ToolExecutionGateway(Protocol):
    """Boundary over a complete, policy-enforcing executor."""

    async def execute_batch(
        self,
        request: ToolBatchRequest,
        event_sink: EventSink,
        signal: CancellationSignal | None = None,
    ) -> ToolBatchResult: ...


@runtime_checkable
class RuntimeObserver(Protocol):
    """Compatibility observer for the existing Run todo state machine."""

    def current_allowed_tool_names(self) -> frozenset[str]: ...

    def future_allowed_tool_names(self) -> frozenset[str]: ...

    async def record_trace(self, trace: TraceRecord) -> None: ...

    async def on_model_delta(self) -> None: ...

    async def on_tool_calls_started(self, tool_names: tuple[str, ...]) -> None: ...

    async def on_tool_round_completed(
        self,
        outcome: ToolBatchOutcome = ToolBatchOutcome.COMPLETED,
    ) -> None: ...


@runtime_checkable
class RuntimePlanningHook(Protocol):
    """Revise runtime authority after an exceptional planning event."""

    async def replan_after_tool(
        self,
        messages: Sequence[AgentMessage],
        *,
        round_number: int,
        remaining_model_rounds: int,
        outcome: ToolBatchOutcome,
        signal: CancellationSignal | None = None,
    ) -> AgentMessage | None: ...


@runtime_checkable
class ApprovalGateway(Protocol):
    """Own approval events and consume each run-bound decision once."""

    async def request(
        self,
        run_id: RunId,
        approval: ApprovalRequest,
        event_sink: EventSink,
        signal: CancellationSignal | None = None,
    ) -> ApprovalResult: ...

    async def resolve(
        self,
        run_id: RunId,
        approval_id: str,
        decision: ApprovalDecision,
    ) -> ApprovalStatus | None: ...

    async def cancel_pending(self, run_id: RunId) -> int: ...


@runtime_checkable
class ToolIdempotencyGateway(Protocol):
    """Execute one side-effecting tool call at most once for a Run."""

    async def execute_once(
        self,
        run_id: RunId,
        tool_call: ToolCall,
        operation: Callable[[], Awaitable[ToolHandlerResult]],
    ) -> ToolHandlerResult: ...


__all__ = [name for name in globals() if not name.startswith("_")]
