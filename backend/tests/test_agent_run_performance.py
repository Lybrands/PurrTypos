from __future__ import annotations

from services.agent_run_performance import evaluate_agent_run_performance
from services.agent_run_store import TRACE_EVENT_TYPE


def _trace(stage: str, outcome: str, *, duration_ms: int = 0, details: dict | None = None):
    payload = {"stage": stage, "outcome": outcome, "durationMs": duration_ms}
    if details is not None:
        payload["details"] = details
    return {"eventType": TRACE_EVENT_TYPE, "payload": payload}


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
