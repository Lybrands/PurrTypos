from __future__ import annotations

import pytest

from agent_core.evaluation import evaluate_agent_run_performance
from infrastructure.persistence.run_store import TRACE_EVENT_TYPE


def _trace(stage: str, outcome: str, *, duration_ms: int = 0, details: dict | None = None):
    payload = {"stage": stage, "outcome": outcome, "durationMs": duration_ms}
    if details is not None:
        payload["details"] = details
    return {"eventType": TRACE_EVENT_TYPE, "payload": payload}


def _check_statuses(report: dict) -> dict[str, str]:
    return {check["name"]: check["status"] for check in report["checks"]}


def _core_event(event_type: str, **payload) -> dict:
    return {"eventType": event_type, "payload": payload}


def test_performance_report_sums_stage_latencies_and_token_proxies():
    report = evaluate_agent_run_performance([
        _trace("planner", "model_plan", duration_ms=2_000),
        _trace("context_budget", "within_budget", details={
            "estimatedInputTokens": 2_000,
            "toolSchemaTokens": 500,
            "projectedTotalTokens": 20_000,
            "windowTokens": 100_000,
        }),
        _trace("model_round", "tool_calls", duration_ms=8_000),
        _trace("tool_round", "completed", duration_ms=1_000),
        _trace("model_round", "stop", duration_ms=12_000),
    ])

    assert report["verdict"] == "pass"
    assert report["metrics"]["recordedTotalMs"] == 23_000
    assert report["metrics"]["activeTotalMs"] == 23_000
    assert report["metrics"]["modelRounds"] == 2
    assert report["metrics"]["headroomTokens"] == 80_000


def test_performance_report_warns_without_failing_operational_run():
    report = evaluate_agent_run_performance([
        _trace("planner", "model_plan", duration_ms=40_000),
        _trace("tool_choice", "provider_fallback_auto"),
        _trace("model_round", "stop", duration_ms=90_000),
    ])

    assert report["verdict"] == "warn"
    warned = {check["name"] for check in report["checks"] if check["status"] == "warn"}
    assert {"plannerLatency", "modelLatency", "providerCompatibility"}.issubset(warned)


def test_performance_report_counts_interrupted_stream_attempt_latency():
    report = evaluate_agent_run_performance([
        _trace("model_round", "tool_calls", duration_ms=10),
        _trace(
            "stream",
            "interrupted",
            duration_ms=300_000,
            details={"round": 2},
        ),
        _trace("model_round", "stop", duration_ms=20),
    ])

    metrics = report["metrics"]
    assert metrics["generationModelRounds"] == 3
    assert metrics["modelRounds"] == 3
    assert metrics["generationModelTotalMs"] == 300_030
    assert metrics["modelTotalMs"] == 300_030
    assert metrics["activeTotalMs"] == 300_030
    assert metrics["recordedTotalMs"] == 300_030
    assert _check_statuses(report)["modelLatency"] == "warn"


def test_performance_report_counts_marked_retry_attempt_once():
    report = evaluate_agent_run_performance([
        _trace(
            "stream",
            "interrupted_retry",
            duration_ms=30,
            details={"providerAttemptTerminal": True},
        ),
        _trace(
            "stream",
            "retry_scheduled",
            duration_ms=999,
            details={"providerAttemptTerminal": False},
        ),
        _trace("model_round", "stop", duration_ms=20),
    ])

    assert report["metrics"]["generationModelRounds"] == 2
    assert report["metrics"]["generationModelTotalMs"] == 50


def test_performance_report_counts_marked_canceled_attempt_once():
    report = evaluate_agent_run_performance([
        _trace(
            "stream",
            "cancel_requested",
            duration_ms=999,
            details={"providerAttemptTerminal": False},
        ),
        _trace(
            "stream",
            "canceled",
            duration_ms=30,
            details={"providerAttemptTerminal": True},
        ),
    ])

    assert report["metrics"]["generationModelRounds"] == 1
    assert report["metrics"]["modelRounds"] == 1
    assert report["metrics"]["generationModelTotalMs"] == 30
    assert report["metrics"]["modelTotalMs"] == 30


def test_performance_report_separates_declined_human_approval_wait():
    report = evaluate_agent_run_performance([
        _trace("planner", "model_plan", duration_ms=2_000),
        _trace("model_round", "tool_calls", duration_ms=4_000),
        _trace("tool_round", "completed", duration_ms=5),
        _trace("model_round", "tool_calls", duration_ms=3_000),
        _trace("tool_round", "declined", duration_ms=65_000),
        _trace("model_round", "stop", duration_ms=2_000),
    ])

    assert report["verdict"] == "pass"
    assert report["metrics"]["toolRounds"] == 2
    assert report["metrics"]["toolTotalMs"] == 5
    assert report["metrics"]["humanApprovalWaitMs"] == 65_000
    assert report["metrics"]["activeTotalMs"] == 11_005
    assert report["metrics"]["recordedTotalMs"] == 76_005


@pytest.mark.parametrize(
    ("approval_status", "tool_outcome"),
    [
        ("declined", "declined"),
        ("rejected", "declined"),
        ("timed_out", "failed"),
        ("canceled", "canceled"),
        ("error", "failed"),
    ],
)
def test_performance_report_excludes_confirmed_non_executing_approval_statuses(
    approval_status: str,
    tool_outcome: str,
):
    report = evaluate_agent_run_performance([
        _trace(
            "tool_round",
            tool_outcome,
            duration_ms=65_000,
            details={"approvalStatus": approval_status},
        ),
    ])

    assert report["verdict"] == "pass"
    assert report["metrics"]["toolTotalMs"] == 0
    assert report["metrics"]["humanApprovalWaitMs"] == 65_000
    assert report["metrics"]["activeTotalMs"] == 0
    assert report["metrics"]["recordedTotalMs"] == 65_000


def test_performance_report_uses_typed_approval_resolution_when_trace_has_no_status():
    report = evaluate_agent_run_performance([
        _trace("tool_round", "completed", duration_ms=2),
        _core_event(
            "approval.resolved",
            approvalId="approval-1",
            toolName="deleteCharacter",
            status="timed_out",
        ),
        _trace("tool_round", "failed", duration_ms=65_000),
    ])

    assert report["metrics"]["toolTotalMs"] == 2
    assert report["metrics"]["humanApprovalWaitMs"] == 65_000
    assert report["metrics"]["activeTotalMs"] == 2
    assert report["metrics"]["recordedTotalMs"] == 65_002


@pytest.mark.parametrize("tool_outcome", ["failed", "canceled"])
def test_performance_report_keeps_unattributed_failure_as_active_tool_latency(
    tool_outcome: str,
):
    report = evaluate_agent_run_performance([
        _trace("tool_round", tool_outcome, duration_ms=65_000),
    ])

    assert report["verdict"] == "warn"
    assert report["metrics"]["toolTotalMs"] == 65_000
    assert report["metrics"]["humanApprovalWaitMs"] == 0
    assert report["metrics"]["activeTotalMs"] == 65_000
    assert report["metrics"]["recordedTotalMs"] == 65_000
    assert _check_statuses(report)["toolLatency"] == "warn"


def test_performance_report_does_not_hide_handler_latency_after_approval():
    report = evaluate_agent_run_performance([
        _core_event(
            "approval.resolved",
            approvalId="approval-1",
            toolName="editChapterContent",
            status="approved",
        ),
        _trace(
            "tool_round",
            "failed",
            duration_ms=65_000,
            details={"approvalStatus": "approved"},
        ),
    ])

    assert report["verdict"] == "warn"
    assert report["metrics"]["toolTotalMs"] == 65_000
    assert report["metrics"]["humanApprovalWaitMs"] == 0
    assert _check_statuses(report)["toolLatency"] == "warn"


def test_performance_report_keeps_inconsistent_approval_evidence_active():
    report = evaluate_agent_run_performance([
        _trace(
            "tool_round",
            "completed",
            duration_ms=65_000,
            details={"approvalStatus": "rejected"},
        ),
    ])

    assert report["verdict"] == "warn"
    assert report["metrics"]["toolTotalMs"] == 65_000
    assert report["metrics"]["humanApprovalWaitMs"] == 0
    assert _check_statuses(report)["toolLatency"] == "warn"


def test_performance_report_subtracts_only_exact_approval_wait_from_slow_handler():
    report = evaluate_agent_run_performance([
        _trace(
            "tool_round",
            "completed",
            duration_ms=21_001,
            details={
                "approvalStatus": "approved",
                "approvalWaitMs": 5_000,
            },
        ),
    ])

    assert report["verdict"] == "warn"
    assert report["metrics"]["toolTotalMs"] == 16_001
    assert report["metrics"]["humanApprovalWaitMs"] == 5_000
    assert report["metrics"]["activeTotalMs"] == 16_001
    assert report["metrics"]["recordedTotalMs"] == 21_001
    assert _check_statuses(report)["toolLatency"] == "warn"


def test_performance_report_counts_semantic_judge_calls_as_model_work():
    report = evaluate_agent_run_performance([
        _trace("planner", "model_plan", duration_ms=2_000),
        _trace("model_round", "tool_calls", duration_ms=4_000),
        _trace("model_round", "stop", duration_ms=6_000),
        _trace("model_output", "response_judge_rejected", duration_ms=1_500),
        _trace("model_round", "stop", duration_ms=5_000),
        _trace("model_output", "response_judge_passed", duration_ms=1_000),
    ])

    metrics = report["metrics"]
    assert metrics["generationModelTotalMs"] == 15_000
    assert metrics["responseJudgeTotalMs"] == 2_500
    assert metrics["modelTotalMs"] == 17_500
    assert metrics["generationModelRounds"] == 3
    assert metrics["responseJudgeRounds"] == 2
    assert metrics["modelRounds"] == 5
    assert metrics["recordedTotalMs"] == 19_500
    assert metrics["activeTotalMs"] == 19_500
    assert metrics["projectedTokensIncludeResponseJudge"] is False
    assert report["verdict"] == "pass"


def test_performance_report_accepts_generation_and_judge_round_boundaries():
    report = evaluate_agent_run_performance([
        *[_trace("model_round", "stop") for _ in range(4)],
        *[_trace("model_output", "response_judge_passed") for _ in range(2)],
    ])

    assert report["metrics"]["generationModelRounds"] == 4
    assert report["metrics"]["responseJudgeRounds"] == 2
    assert report["metrics"]["modelRounds"] == 6
    assert report["verdict"] == "pass"
    statuses = _check_statuses(report)
    assert statuses["generationModelRounds"] == "pass"
    assert statuses["responseJudgeRounds"] == "pass"
    assert statuses["modelRounds"] == "pass"


def test_performance_report_warns_when_generation_exceeds_its_phase_budget():
    report = evaluate_agent_run_performance([
        *[_trace("model_round", "stop") for _ in range(5)],
        _trace("model_output", "response_judge_passed"),
    ])

    assert report["metrics"]["generationModelRounds"] == 5
    assert report["metrics"]["responseJudgeRounds"] == 1
    assert report["metrics"]["modelRounds"] == 6
    assert report["verdict"] == "warn"
    statuses = _check_statuses(report)
    assert statuses["generationModelRounds"] == "warn"
    assert statuses["responseJudgeRounds"] == "pass"
    assert statuses["modelRounds"] == "pass"


def test_performance_report_warns_for_judge_and_total_round_overruns():
    report = evaluate_agent_run_performance([
        *[_trace("model_round", "stop") for _ in range(4)],
        *[_trace("model_output", "response_judge_passed") for _ in range(3)],
    ])

    assert report["metrics"]["generationModelRounds"] == 4
    assert report["metrics"]["responseJudgeRounds"] == 3
    assert report["metrics"]["modelRounds"] == 7
    assert report["verdict"] == "warn"
    statuses = _check_statuses(report)
    assert statuses["generationModelRounds"] == "pass"
    assert statuses["responseJudgeRounds"] == "warn"
    assert statuses["modelRounds"] == "warn"
