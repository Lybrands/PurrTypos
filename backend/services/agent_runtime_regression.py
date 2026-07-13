"""Run and score deterministic Agent runtime regression contracts."""

from __future__ import annotations

from typing import Any, Iterable

from services.agent_run_evaluation import evaluate_agent_run
from services.agent_runtime_regression_cases import (
    RUNTIME_REGRESSION_CASES,
    AgentRuntimeRegressionCase,
)


def evaluate_runtime_regression_case(
    case: AgentRuntimeRegressionCase,
) -> dict[str, Any]:
    report = evaluate_agent_run(case.run, list(case.events))
    traces = report["traces"]
    planner_outcome = next(
        (
            str(trace.get("outcome") or "unknown")
            for trace in reversed(traces)
            if trace.get("stage") == "planner"
        ),
        "unknown",
    )
    actual_tool_sequence = tuple(
        str(tool)
        for trace in traces
        if trace.get("stage") == "tool_round" and trace.get("outcome") == "completed"
        for tool in ((trace.get("details") or {}).get("requestedTools") or [])
    )
    report_checks = {
        str(check.get("name")): str(check.get("status"))
        for check in report.get("checks") or []
    }

    checks: list[dict[str, Any]] = [
        {
            "name": "reportVerdict",
            "status": "pass" if report["verdict"] == case.expected_report_verdict else "fail",
            "detail": {"expected": case.expected_report_verdict, "actual": report["verdict"]},
        },
        {
            "name": "plannerOutcome",
            "status": "pass" if planner_outcome == case.expected_planner_outcome else "fail",
            "detail": {"expected": case.expected_planner_outcome, "actual": planner_outcome},
        },
        {
            "name": "terminalStatus",
            "status": "pass" if report["runStatus"] == case.expected_terminal_status else "fail",
            "detail": {"expected": case.expected_terminal_status, "actual": report["runStatus"]},
        },
        {
            "name": "toolSequence",
            "status": "pass" if actual_tool_sequence == case.expected_tool_sequence else "fail",
            "detail": {
                "expected": list(case.expected_tool_sequence),
                "actual": list(actual_tool_sequence),
            },
        },
    ]
    for name, expected in case.expected_check_statuses.items():
        actual = report_checks.get(name, "missing")
        checks.append({
            "name": f"diagnostic:{name}",
            "status": "pass" if actual == expected else "fail",
            "detail": {"expected": expected, "actual": actual},
        })

    return {
        "caseId": case.case_id,
        "title": case.title,
        "verdict": "pass" if all(check["status"] == "pass" for check in checks) else "fail",
        "checks": checks,
        "operationalReport": report,
    }


def run_runtime_regression_suite(
    cases: Iterable[AgentRuntimeRegressionCase] = RUNTIME_REGRESSION_CASES,
) -> dict[str, Any]:
    results = [evaluate_runtime_regression_case(case) for case in cases]
    passed = sum(1 for result in results if result["verdict"] == "pass")
    return {
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
        },
        "results": results,
    }
