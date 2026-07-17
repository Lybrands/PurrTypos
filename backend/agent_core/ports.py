"""Dependency-inversion ports owned by Agent Core."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    ApprovalStatus,
    ContextBudget,
    ContextBundle,
    ExecutionState,
    ModelCompletion,
    ModelInvocation,
    ModelStream,
    PlanningCapabilities,
    PlanningConstraints,
    PlanningResult,
    ResponseValidationResult,
    RunCreateParams,
    RunId,
    RunStatus,
    TaskStep,
    TaskStepUpdate,
    TerminalRunStatus,
    ToolBatchOutcome,
    ToolBatchRequest,
    ToolBatchResult,
    ToolHandlerResult,
    ToolPolicy,
    ToolSchema,
    TraceRecord,
)
from agent_core.events import AgentEvent, CoreEventType


@runtime_checkable
class CancellationSignal(Protocol):
    def is_set(self) -> bool: ...

    async def wait(self) -> bool: ...


@runtime_checkable
class ModelGateway(Protocol):
    async def stream(
        self,
        messages: Sequence[AgentMessage],
        invocation: ModelInvocation,
        signal: CancellationSignal | None = None,
    ) -> ModelStream: ...

    async def complete(
        self,
        messages: Sequence[AgentMessage],
        invocation: ModelInvocation,
        signal: CancellationSignal | None = None,
    ) -> ModelCompletion: ...


@runtime_checkable
class ContextProvider(Protocol):
    async def build_context(
        self,
        request: AgentRunRequest,
        budget: ContextBudget,
        signal: CancellationSignal | None = None,
    ) -> ContextBundle: ...


@runtime_checkable
class PlanningPolicy(Protocol):
    def should_plan(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> bool: ...

    def planning_constraints(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
    ) -> PlanningConstraints: ...


@runtime_checkable
class TaskPlanner(Protocol):
    async def create_plan(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
        signal: CancellationSignal | None = None,
    ) -> PlanningResult: ...


@runtime_checkable
class ExecutionStateFactory(Protocol):
    def create(self, request: AgentRunRequest) -> ExecutionState: ...


@runtime_checkable
class ResponseValidator(Protocol):
    """Validate a buffered final response without embedding domain semantics."""

    def validate(
        self,
        *,
        content: str,
        messages: Sequence[AgentMessage],
    ) -> ResponseValidationResult: ...


@runtime_checkable
class ResponseJudge(Protocol):
    """Asynchronously judge buffered output without owning domain semantics."""

    async def judge(
        self,
        *,
        content: str,
        messages: Sequence[AgentMessage],
        signal: CancellationSignal | None = None,
    ) -> ResponseValidationResult: ...


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
    planning_dependencies: tuple[str, ...] = ()

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


@runtime_checkable
class ToolCatalog(Protocol):
    """Expose the full registry for startup validation and a request scope."""

    def registrations(self) -> Sequence[ToolRegistration]: ...

    def enabled_names(self, request: AgentRunRequest) -> frozenset[str]: ...


@runtime_checkable
class ToolExecutionGateway(Protocol):
    """Temporary stage-3 boundary over a complete, policy-enforcing executor."""

    async def execute_batch(
        self,
        request: ToolBatchRequest,
        event_sink: "EventSink",
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


@dataclass(frozen=True, slots=True)
class RunBeginResult:
    """Atomic result of creating a run and its first outbox event."""

    run_id: RunId
    event: AgentEvent

    def __post_init__(self) -> None:
        run_id = str(self.run_id or "").strip()
        if not run_id:
            raise ValueError("run begin result requires a run id")
        if self.event.run_id != run_id:
            raise ValueError("run begin event must be bound to the created run")
        object.__setattr__(self, "run_id", run_id)


@dataclass(frozen=True, slots=True)
class RunCommit:
    """One atomic Run state/outbox write requested by the Core controller.

    ``replace_steps=None`` means that the current plan is unchanged, while an
    empty tuple explicitly replaces it with no steps.  Events are returned by
    the repository unchanged after persistence so the exact same objects can
    be delivered to an EventSink.
    """

    replace_steps: tuple[TaskStep, ...] | None = None
    step_updates: tuple[TaskStepUpdate, ...] = ()
    terminal_status: TerminalRunStatus | None = None
    final_response: str | None = None
    error: str | None = None
    events: tuple[AgentEvent, ...] = ()

    def __post_init__(self) -> None:
        replacement = (
            None if self.replace_steps is None else tuple(self.replace_steps)
        )
        updates = tuple(self.step_updates)
        events = tuple(self.events)
        update_ids = [update.step_id for update in updates]
        if len(update_ids) != len(set(update_ids)):
            raise ValueError("run commit step updates must be unique")

        terminal = self.terminal_status
        if terminal is not None:
            normalized = RunStatus(terminal)
            if normalized is RunStatus.RUNNING:
                raise ValueError("run commit cannot reopen a run")
            terminal = normalized  # type: ignore[assignment]
        if terminal is RunStatus.FAILED and not str(self.error or "").strip():
            raise ValueError("failed run commit requires a non-empty error")
        if terminal is not RunStatus.FAILED and self.error is not None:
            raise ValueError("run commit error is only valid for failed runs")
        if terminal is not RunStatus.DONE and self.final_response is not None:
            raise ValueError(
                "run commit final_response is only valid for completed runs"
            )

        object.__setattr__(self, "replace_steps", replacement)
        object.__setattr__(self, "step_updates", updates)
        object.__setattr__(self, "terminal_status", terminal)
        object.__setattr__(self, "events", events)


TERMINAL_RUN_EVENT_TYPES = frozenset({
    CoreEventType.RUN_COMPLETED,
    CoreEventType.RUN_BLOCKED,
    CoreEventType.RUN_FAILED,
    CoreEventType.RUN_CANCELED,
})

# These event names describe state owned exclusively by AgentRunController.
# Runtime, tool, approval and domain adapters may emit other Core/domain events,
# but must never synthesize Run lifecycle or to-do state through append_event.
CONTROLLER_OWNED_RUN_EVENT_TYPES = frozenset({
    CoreEventType.RUN_STARTED,
    CoreEventType.RUN_TODOS_UPDATED,
    CoreEventType.RUN_TODO_UPDATED,
    *TERMINAL_RUN_EVENT_TYPES,
})


def validate_run_commit_lifecycle(commit: RunCommit) -> None:
    """Validate the one-to-one Run terminal state/outbox invariant."""

    if any(
        event.type == CoreEventType.RUN_STARTED
        for event in commit.events
    ):
        raise ValueError("run.started is only valid for repository.begin")

    terminal_events = tuple(
        event
        for event in commit.events
        if event.type in TERMINAL_RUN_EVENT_TYPES
    )
    if commit.terminal_status is None:
        if terminal_events:
            raise ValueError(
                "non-terminal run commit cannot include terminal events"
            )
        return

    expected = {
        RunStatus.DONE: CoreEventType.RUN_COMPLETED,
        RunStatus.BLOCKED: CoreEventType.RUN_BLOCKED,
        RunStatus.FAILED: CoreEventType.RUN_FAILED,
        RunStatus.CANCELED: CoreEventType.RUN_CANCELED,
    }[RunStatus(commit.terminal_status)]
    if len(terminal_events) != 1:
        raise ValueError(
            "terminal run commit requires exactly one "
            f"{expected.value} event"
        )
    actual = terminal_events[0].type
    if actual != expected:
        raise ValueError(
            f"terminal status {commit.terminal_status.value} requires "
            f"{expected.value}, got {actual}"
        )


@runtime_checkable
class RunRepository(Protocol):
    """Atomic, cancellation-linearizable Run persistence.

    ``begin`` and ``commit`` persist state and their supplied outbox events in
    one transaction. Their cancellation result is authoritative:

    * raising ``asyncio.CancelledError`` means the transaction definitely did
      not durably commit;
    * once the transaction is durable, the method must return its receipt even
      if cancellation arrived while the storage acknowledgement was in flight.

    Implementations are single writers for a Run and must reject attempts to
    replace an existing terminal status or append a second terminal outbox
    event. A terminal commit must contain exactly one matching terminal event;
    a non-terminal commit must contain none. These guarantees let the
    controller synchronize its in-memory snapshot before it propagates caller
    cancellation.
    """

    async def begin(
        self,
        params: RunCreateParams,
        started_event: AgentEvent,
    ) -> RunBeginResult: ...

    async def commit(
        self,
        run_id: RunId,
        commit: RunCommit,
    ) -> tuple[AgentEvent, ...]: ...

    # Lower-level methods support repository transactions and post-run
    # attachments. Core lifecycle orchestration uses begin/commit so state and
    # outbox events remain atomic.
    async def create(self, params: RunCreateParams) -> RunId: ...

    async def bind_conversation(self, run_id: RunId, conversation_id: int) -> None: ...

    async def replace_steps(self, run_id: RunId, steps: Sequence[TaskStep]) -> None: ...

    async def update_step(self, run_id: RunId, update: TaskStepUpdate) -> None: ...

    async def transition(
        self,
        run_id: RunId,
        status: TerminalRunStatus,
        *,
        final_response: str | None = None,
        error: str | None = None,
    ) -> None: ...

    async def append_event(self, run_id: RunId, event: AgentEvent) -> None:
        """Append a non-controller event.

        Implementations must reject every event type in
        ``CONTROLLER_OWNED_RUN_EVENT_TYPES``. Run lifecycle and to-do events
        enter the outbox only through ``begin``/``commit``.
        """
        ...

    async def append_trace(self, run_id: RunId, trace: TraceRecord) -> None: ...


@runtime_checkable
class EventSink(Protocol):
    async def emit(self, event: AgentEvent) -> None: ...


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

    def resolve(
        self,
        run_id: RunId,
        approval_id: str,
        decision: ApprovalDecision,
    ) -> ApprovalStatus | None: ...

    def cancel_pending(self, run_id: RunId) -> int: ...
