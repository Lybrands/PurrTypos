"""Compatibility facade for dependency-inversion ports owned by PurrA.

Concrete port definitions live in focused modules.  Existing callers may keep
using ``purra.ports`` while migrations adopt the narrower import paths.
"""

from purra.ports.context import (
    ContextCompressionHook,
    ContextDemandProvider,
    ContextProvider,
    ConversationCompactor,
    StagedContextProvider,
    TaskContextDemandProvider,
)
from purra.ports.model import CancellationSignal, ModelGateway
from purra.ports.persistence import (
    CheckpointStore,
    DelegationRepository,
    ExecutionLeaseStore,
)
from purra.ports.planning import (
    DynamicTaskPlanner,
    ExecutionStateFactory,
    PlanningPolicy,
    ResponseJudge,
    ResponseValidator,
    TaskPlanner,
    TaskPlanningConstraintProvider,
)
from purra.ports.projection import DomainEventProjector
from purra.ports.run_lifecycle import (
    CONTROLLER_OWNED_RUN_EVENT_TYPES,
    TERMINAL_RUN_EVENT_TYPES,
    RunBeginResult,
    RunCommit,
    RunRepository,
    validate_run_commit_lifecycle,
)
from purra.ports.tools import (
    ApprovalGateway,
    CacheProbe,
    EventSink,
    RuntimeObserver,
    RuntimePlanningHook,
    ScopeValidator,
    ToolCatalog,
    ToolExecutionGateway,
    ToolHandler,
    ToolIdempotencyGateway,
    ToolRegistration,
)
from purra.output.ports import (
    AgentOutputPolicy,
    AgentOutputPublisher,
    AgentOutputRepository,
)

__all__ = [name for name in globals() if not name.startswith("_")]
