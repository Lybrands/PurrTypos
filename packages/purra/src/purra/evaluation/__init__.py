"""Business-agnostic Agent run evaluation primitives.

The Core package owns only content-free diagnostics, performance accounting,
and regression scoring. Domain cases and application operations remain outside
the business-agnostic runtime.
"""

from purra.evaluation.diagnostics import (
    TRACE_EVENT_TYPE,
    CanonicalRunObservation,
    build_canonical_run_observation,
    evaluate_agent_run,
)
from purra.evaluation.performance import (
    PERFORMANCE_BUDGETS,
    evaluate_agent_run_performance,
)
from purra.evaluation.recovery import evaluate_agent_run_recovery
from purra.evaluation.failure_classification import (
    classify_agent_run_failures,
)
from purra.evaluation.regression import (
    AgentRuntimeRegressionCase,
    evaluate_runtime_regression_case,
    run_runtime_regression_suite,
)
from purra.evaluation.security import (
    SecurityRedTeamCase,
    get_core_security_redteam_cases,
    run_security_redteam_cases,
)
from purra.evaluation.stability import evaluate_agent_run_stability
from purra.evaluation.stability_gate import (
    DEFAULT_STABILITY_REGRESSION_GATE_POLICY,
    StabilityRegressionGatePolicy,
    evaluate_stability_regression_gate,
)
from purra.evaluation.stability_trend import (
    DEFAULT_STABILITY_TREND_POLICY,
    StabilityTrendPolicy,
    evaluate_agent_run_stability_trend,
)

__all__ = [
    "AgentRuntimeRegressionCase",
    "CanonicalRunObservation",
    "TRACE_EVENT_TYPE",
    "PERFORMANCE_BUDGETS",
    "DEFAULT_STABILITY_REGRESSION_GATE_POLICY",
    "DEFAULT_STABILITY_TREND_POLICY",
    "SecurityRedTeamCase",
    "StabilityRegressionGatePolicy",
    "StabilityTrendPolicy",
    "build_canonical_run_observation",
    "classify_agent_run_failures",
    "evaluate_agent_run",
    "evaluate_agent_run_performance",
    "evaluate_agent_run_recovery",
    "evaluate_agent_run_stability",
    "evaluate_agent_run_stability_trend",
    "evaluate_stability_regression_gate",
    "evaluate_runtime_regression_case",
    "get_core_security_redteam_cases",
    "run_runtime_regression_suite",
    "run_security_redteam_cases",
]
