"""Business-agnostic Agent run evaluation primitives.

The Core package owns only content-free diagnostics, performance accounting,
and regression scoring. Domain cases and application operations remain outside
the business-agnostic runtime.
"""

from agent_core.evaluation.diagnostics import (
    TRACE_EVENT_TYPE,
    CanonicalRunObservation,
    build_canonical_run_observation,
    evaluate_agent_run,
)
from agent_core.evaluation.performance import (
    PERFORMANCE_BUDGETS,
    evaluate_agent_run_performance,
)
from agent_core.evaluation.regression import (
    AgentRuntimeRegressionCase,
    evaluate_runtime_regression_case,
    run_runtime_regression_suite,
)
from agent_core.evaluation.security import (
    SecurityRedTeamCase,
    get_core_security_redteam_cases,
    run_security_redteam_cases,
)

__all__ = [
    "AgentRuntimeRegressionCase",
    "CanonicalRunObservation",
    "TRACE_EVENT_TYPE",
    "PERFORMANCE_BUDGETS",
    "SecurityRedTeamCase",
    "build_canonical_run_observation",
    "evaluate_agent_run",
    "evaluate_agent_run_performance",
    "evaluate_runtime_regression_case",
    "get_core_security_redteam_cases",
    "run_runtime_regression_suite",
    "run_security_redteam_cases",
]
