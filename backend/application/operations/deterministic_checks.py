"""Application composition for deterministic Core and Writing evaluations."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agent_core.evaluation import (
    AgentRuntimeRegressionCase,
    evaluate_runtime_regression_case,
    get_core_security_redteam_cases,
    run_runtime_regression_suite as run_core_runtime_regression_suite,
    run_security_redteam_cases,
)
from domains.writing.evaluation import (
    WRITING_RUNTIME_REGRESSION_CASES,
    get_writing_security_redteam_cases,
)


def run_runtime_regression_suite(
    cases: Iterable[
        AgentRuntimeRegressionCase
    ] = WRITING_RUNTIME_REGRESSION_CASES,
) -> dict[str, Any]:
    """Run the Writing catalogue through the content-neutral Core harness."""

    return run_core_runtime_regression_suite(cases)


def run_agent_security_redteam_suite() -> dict[str, Any]:
    """Combine Core and Writing-owned security checks in stable case order."""

    cases = (
        *get_core_security_redteam_cases(),
        *get_writing_security_redteam_cases(),
    )
    return run_security_redteam_cases(
        sorted(cases, key=lambda item: item[0])
    )


__all__ = [
    "AgentRuntimeRegressionCase",
    "evaluate_runtime_regression_case",
    "run_agent_security_redteam_suite",
    "run_runtime_regression_suite",
]
