"""Compatibility facade for dependency-inversion ports owned by Agent Core.

Concrete port definitions live in focused modules.  Existing callers may keep
using ``agent_core.ports`` while migrations adopt the narrower import paths.
"""

from agent_core.ports.context import (
    ContextCompressionHook,
    ContextDemandProvider,
    ContextProvider,
    ConversationCompactor,
    StagedContextProvider,
    TaskContextDemandProvider,
)
from agent_core.ports.model import CancellationSignal, ModelGateway
from agent_core.ports.persistence import (
    CheckpointStore,
    DelegationRepository,
    ExecutionLeaseStore,
)
from agent_core.ports.planning import (
    DynamicTaskPlanner,
    ExecutionStateFactory,
    PlanningPolicy,
    ResponseJudge,
    ResponseValidator,
    TaskPlanner,
    TaskPlanningConstraintProvider,
)
from agent_core.ports.projection import DomainEventProjector
from agent_core.ports.run_lifecycle import (
    CONTROLLER_OWNED_RUN_EVENT_TYPES,
    TERMINAL_RUN_EVENT_TYPES,
    RunBeginResult,
    RunCommit,
    RunRepository,
    validate_run_commit_lifecycle,
)
from agent_core.ports.tools import (
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

__all__ = [name for name in globals() if not name.startswith("_")]
