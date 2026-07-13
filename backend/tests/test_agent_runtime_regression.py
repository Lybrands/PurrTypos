from __future__ import annotations

import pytest


def test_runtime_regression_suite_covers_promoted_incidents():
    from services.agent_runtime_regression import run_runtime_regression_suite

    suite = run_runtime_regression_suite()

    assert suite["summary"] == {"total": 7, "passed": 7, "failed": 0}
    assert [result["caseId"] for result in suite["results"]] == [
        "healthy-sequential-read",
        "planner-silent-fallback",
        "missing-required-tool-call",
        "unauthorized-tool-rejected",
        "context-overflow-stops-run",
        "tool-handler-error-stops-run",
        "client-disconnect-cancels-run",
    ]


def test_healthy_runtime_contract_requires_exact_tool_order():
    from dataclasses import replace

    from services.agent_runtime_regression import evaluate_runtime_regression_case
    from services.agent_runtime_regression_cases import RUNTIME_REGRESSION_CASES

    healthy = RUNTIME_REGRESSION_CASES[0]
    wrong_order = replace(
        healthy,
        expected_tool_sequence=("getChapterContent", "listWritingChapters"),
    )

    result = evaluate_runtime_regression_case(wrong_order)

    assert result["verdict"] == "fail"
    tool_check = next(check for check in result["checks"] if check["name"] == "toolSequence")
    assert tool_check["status"] == "fail"


def test_failure_incident_passes_only_when_diagnostics_detect_the_failure():
    from dataclasses import replace

    from services.agent_runtime_regression import evaluate_runtime_regression_case
    from services.agent_runtime_regression_cases import RUNTIME_REGRESSION_CASES

    planner_failure = RUNTIME_REGRESSION_CASES[1]
    wrong_expectation = replace(planner_failure, expected_report_verdict="pass")

    result = evaluate_runtime_regression_case(wrong_expectation)

    assert result["verdict"] == "fail"
    verdict_check = next(check for check in result["checks"] if check["name"] == "reportVerdict")
    assert verdict_check["detail"] == {"expected": "pass", "actual": "fail"}


@pytest.mark.asyncio
async def test_runtime_regression_endpoint_returns_suite_report():
    from routers.ai import get_agent_runtime_regressions

    response = await get_agent_runtime_regressions()

    assert response["success"] is True
    assert response["data"]["summary"]["failed"] == 0
