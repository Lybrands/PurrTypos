"""Deterministic operational evaluation for persisted Agent Run traces.

This deliberately evaluates host behavior (budget safety, trace coverage and
tool-governance), not whether a creative answer is good. Content quality needs
a separately curated benchmark and human or model judging.
"""

from __future__ import annotations

from typing import Any

from services.agent_run_store import TRACE_EVENT_TYPE


def evaluate_agent_run(
    run: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    traces = [
        dict(event.get("payload") or {})
        for event in events
        if event.get("eventType") == TRACE_EVENT_TYPE
    ]
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for trace in traces:
        stage = str(trace.get("stage") or "unknown")
        by_stage.setdefault(stage, []).append(trace)

    status = str(run.get("status") or "unknown")
    planner_traces = by_stage.get("planner", [])
    latest_planner = planner_traces[-1] if planner_traces else None
    planner_outcome = str((latest_planner or {}).get("outcome") or "unknown")
    context_traces = by_stage.get("context_budget", [])
    latest_context = context_traces[-1] if context_traces else None
    context_outcome = str((latest_context or {}).get("outcome") or "unknown")
    tool_traces = by_stage.get("tool_round", [])
    unauthorized_tools = sum(
        1 for trace in tool_traces if trace.get("outcome") == "rejected"
    )
    missing_required_calls = sum(
        1 for trace in tool_traces if trace.get("outcome") == "missing_required_call"
    )
    failed_tool_rounds = sum(
        1 for trace in tool_traces if trace.get("outcome") == "failed"
    )
    model_rounds = len(by_stage.get("model_round", []))

    checks = [
        {
            "name": "terminalState",
            "status": "pass" if status in {"done", "blocked", "failed", "canceled"} else "fail",
            "detail": status,
        },
        {
            "name": "taskOutcome",
            "status": (
                "pass" if status == "done"
                else "warn" if status in {"blocked", "canceled"}
                else "fail" if status == "failed"
                else "warn"
            ),
            "detail": status,
        },
        {
            "name": "traceCoverage",
            "status": "pass" if {"planner", "terminal"}.issubset(by_stage) else "warn",
            "detail": sorted(by_stage),
        },
        {
            "name": "plannerHealth",
            "status": (
                "pass"
                if planner_outcome in {"model_plan", "skipped"}
                else "fail"
                if planner_outcome in {"exception", "invalid_plan", "fallback_after_error"}
                else "warn"
            ),
            "detail": planner_outcome,
        },
        {
            "name": "contextSafety",
            "status": (
                "pass" if context_outcome == "within_budget"
                else "fail" if context_outcome.startswith("overflow")
                else "warn"
            ),
            "detail": context_outcome,
        },
        {
            "name": "toolGovernance",
            "status": (
                "pass"
                if unauthorized_tools == 0 and missing_required_calls == 0
                else "fail"
            ),
            "detail": {
                "rejectedRounds": unauthorized_tools,
                "missingRequiredCalls": missing_required_calls,
            },
        },
        {
            "name": "toolReliability",
            "status": "pass" if failed_tool_rounds == 0 else "fail",
            "detail": {"failedRounds": failed_tool_rounds},
        },
    ]
    if any(check["status"] == "fail" for check in checks):
        verdict = "fail"
    elif any(check["status"] == "warn" for check in checks):
        verdict = "warn"
    else:
        verdict = "pass"

    return {
        "runId": run.get("id"),
        "runStatus": status,
        "release": {
            "version": run.get("release_version") or "development",
            "cohort": run.get("rollout_cohort") or "local",
        },
        "verdict": verdict,
        "checks": checks,
        "metrics": {
            "traceCount": len(traces),
            "modelRounds": model_rounds,
            "toolRounds": len(tool_traces),
            "rejectedToolRounds": unauthorized_tools,
            "missingRequiredToolCalls": missing_required_calls,
            "failedToolRounds": failed_tool_rounds,
        },
        "traces": traces,
    }
