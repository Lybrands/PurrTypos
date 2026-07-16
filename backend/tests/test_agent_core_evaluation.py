from __future__ import annotations

import pytest

from agent_core.evaluation import (
    TRACE_EVENT_TYPE,
    AgentRuntimeRegressionCase,
    build_canonical_run_observation,
    evaluate_agent_run,
    evaluate_agent_run_performance,
    get_core_security_redteam_cases,
    run_runtime_regression_suite,
    run_security_redteam_cases,
)


def _trace(
    stage: str,
    outcome: str,
    *,
    duration_ms: int = 0,
    details: dict | None = None,
) -> dict:
    payload = {
        "stage": stage,
        "outcome": outcome,
        "durationMs": duration_ms,
    }
    if details is not None:
        payload["details"] = details
    return {"eventType": TRACE_EVENT_TYPE, "payload": payload}


def _core_event(event_type: str, **payload) -> dict:
    return {"eventType": event_type, "payload": payload}


def _check(report: dict, name: str) -> dict:
    return next(check for check in report["checks"] if check["name"] == name)


@pytest.mark.parametrize(
    ("run_status", "event_type"),
    [
        ("done", "run.completed"),
        ("blocked", "run.blocked"),
        ("failed", "run.failed"),
        ("canceled", "run.canceled"),
    ],
)
def test_core_planning_and_durable_terminal_events_satisfy_trace_coverage(
    run_status: str,
    event_type: str,
):
    report = evaluate_agent_run(
        {"id": "run-core", "status": run_status},
        [
            _trace("planning", "planned", duration_ms=100),
            _trace("context_budget", "within_budget"),
            _core_event(event_type, status=run_status),
        ],
    )

    assert _check(report, "traceCoverage") == {
        "name": "traceCoverage",
        "status": "pass",
        "detail": ["context_budget", "planner", "terminal"],
    }
    assert _check(report, "plannerHealth")["status"] == "pass"


@pytest.mark.parametrize("outcome", ["direct_response", "skipped"])
def test_core_non_planned_success_outcomes_are_healthy(outcome: str):
    report = evaluate_agent_run(
        {"id": "run-core", "status": "done"},
        [
            _trace("planning", outcome),
            _trace("context_budget", "within_budget"),
            _core_event("run.completed", status="done"),
        ],
    )

    assert _check(report, "plannerHealth") == {
        "name": "plannerHealth",
        "status": "pass",
        "detail": outcome,
    }


def test_core_planning_failures_remain_fail_closed():
    report = evaluate_agent_run(
        {"id": "run-core", "status": "failed"},
        [
            _trace("planning", "contract_violation"),
            _core_event("run.failed", status="failed"),
        ],
    )

    assert _check(report, "plannerHealth")["status"] == "fail"
    assert report["verdict"] == "fail"


def test_core_performance_prefers_context_event_and_counts_planning_latency():
    report = evaluate_agent_run_performance([
        _trace("planning", "planned", duration_ms=2_500),
        _trace("context_budget", "within_budget", details={
            "estimatedInputTokens": 1,
            "toolSchemaTokens": 2,
            "projectedTotalTokens": 3,
            "windowTokens": 4,
        }),
        _core_event("context.budgeted", **{
            "estimatedInputTokens": 2_000,
            "toolSchemaTokens": 500,
            "projectedTotalTokens": 20_000,
            "windowTokens": 100_000,
        }),
        _trace("model_round", "stop", duration_ms=8_000),
    ])

    assert report["metrics"] == {
        "plannerMs": 2_500,
        "modelTotalMs": 8_000,
        "generationModelTotalMs": 8_000,
        "responseJudgeTotalMs": 0,
        "toolTotalMs": 0,
        "humanApprovalWaitMs": 0,
        "activeTotalMs": 10_500,
        "recordedTotalMs": 10_500,
        "modelRounds": 1,
        "generationModelRounds": 1,
        "responseJudgeRounds": 0,
        "toolRounds": 0,
        "compatibilityFallbacks": 0,
        "estimatedInputTokens": 2_000,
        "toolSchemaTokens": 500,
        "projectedTotalTokens": 20_000,
        "windowTokens": 100_000,
        "headroomTokens": 80_000,
        "projectedTokensIncludeResponseJudge": False,
    }


def test_core_observation_accepts_one_shot_event_iterables():
    events = (
        event
        for event in [
            _trace("planning", "planned"),
            _core_event("run.completed", status="done"),
        ]
    )

    observation = build_canonical_run_observation(events)

    assert observation.by_stage["planner"][0]["outcome"] == "planned"
    assert observation.by_stage["terminal"][0]["outcome"] == "done"


def test_core_diagnostics_counts_interrupted_generation_attempts():
    report = evaluate_agent_run(
        {"id": "run-core", "status": "failed"},
        [
            _trace("planning", "planned"),
            _trace("model_round", "stop"),
            _trace("stream", "interrupted", duration_ms=300_000),
            _core_event("run.failed", status="failed"),
        ],
    )

    assert report["metrics"]["modelRounds"] == 2


def test_core_diagnostics_counts_marked_canceled_attempt_once():
    report = evaluate_agent_run(
        {"id": "run-core", "status": "canceled"},
        [
            _trace("planning", "planned"),
            _trace(
                "stream",
                "cancel_requested",
                details={"providerAttemptTerminal": False},
            ),
            _trace(
                "stream",
                "canceled",
                duration_ms=30,
                details={"providerAttemptTerminal": True},
            ),
            _core_event("run.canceled", status="canceled"),
        ],
    )

    assert report["metrics"]["modelRounds"] == 1


def test_core_regression_framework_uses_caller_owned_content_free_cases():
    case = AgentRuntimeRegressionCase(
        case_id="ordered-capability-run",
        title="Capabilities complete in planned order",
        run={"id": "run-core", "status": "done"},
        events=(
            _trace("planning", "planned"),
            _trace("context_budget", "within_budget"),
            _trace(
                "tool_round",
                "completed",
                details={"requestedTools": ["read_record"]},
            ),
            _trace(
                "tool_round",
                "completed",
                details={"requestedTools": ["propose_change"]},
            ),
            _core_event("run.completed", status="done"),
        ),
        expected_report_verdict="pass",
        expected_planner_outcome="planned",
        expected_terminal_status="done",
        expected_tool_sequence=("read_record", "propose_change"),
        expected_check_statuses={
            "toolGovernance": "pass",
            "toolReliability": "pass",
        },
    )

    suite = run_runtime_regression_suite([case])

    assert suite["summary"] == {"total": 1, "passed": 1, "failed": 0}


def test_core_regression_framework_has_no_implicit_domain_catalogue():
    assert run_runtime_regression_suite(()) == {
        "summary": {"total": 0, "passed": 0, "failed": 0},
        "results": [],
    }


def test_core_security_evaluation_contains_only_content_neutral_cases():
    cases = get_core_security_redteam_cases()

    assert [case_id for case_id, _, _ in cases] == [
        "RT1-malformed-tool-arguments",
        "RT2-duplicate-tool-call-ids",
        "RT3-tool-batch-resource-limit",
        "RT5-sensitive-error-redaction",
    ]
    assert run_security_redteam_cases(cases)["summary"] == {
        "total": 4,
        "passed": 4,
        "failed": 0,
    }
