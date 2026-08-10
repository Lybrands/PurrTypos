from __future__ import annotations

import pytest


def test_runtime_regression_suite_covers_promoted_incidents():
    from application.operations.deterministic_checks import (
        run_runtime_regression_suite,
    )

    suite = run_runtime_regression_suite()

    assert suite["summary"] == {"total": 11, "passed": 11, "failed": 0}
    assert [result["caseId"] for result in suite["results"]] == [
        "healthy-sequential-read",
        "planner-silent-fallback",
        "missing-required-tool-call",
        "unauthorized-tool-rejected",
        "context-overflow-stops-run",
        "tool-handler-error-stops-run",
        "client-disconnect-cancels-run",
        "malformed-tool-json-is-classified",
        "started-tool-without-result-is-classified",
        "compaction-failure-is-classified",
        "model-interruption-is-classified",
    ]


def test_healthy_runtime_contract_requires_exact_tool_order():
    from dataclasses import replace

    from purra.evaluation import evaluate_runtime_regression_case
    from domains.writing.evaluation import WRITING_RUNTIME_REGRESSION_CASES

    healthy = WRITING_RUNTIME_REGRESSION_CASES[0]
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

    from purra.evaluation import evaluate_runtime_regression_case
    from domains.writing.evaluation import WRITING_RUNTIME_REGRESSION_CASES

    planner_failure = WRITING_RUNTIME_REGRESSION_CASES[1]
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


def test_stability_quality_gate_passes_only_when_all_incidents_are_recognized():
    from application.operations.deterministic_checks import (
        run_agent_stability_quality_gate,
    )

    gate = run_agent_stability_quality_gate()

    assert gate["verdict"] == "pass"
    assert gate["summary"] == {
        "promotedIncidents": 11,
        "failedIncidents": 0,
    }


@pytest.mark.asyncio
async def test_stability_quality_gate_endpoint_returns_gate_report():
    from routers.ai import get_agent_stability_quality_gate

    response = await get_agent_stability_quality_gate()

    assert response["success"] is True
    assert response["data"]["verdict"] == "pass"
