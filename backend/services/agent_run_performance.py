"""Performance-budget report derived from content-free Agent Run traces."""

from __future__ import annotations

from typing import Any

from services.agent_run_store import TRACE_EVENT_TYPE


PERFORMANCE_BUDGETS = {
    "plannerMs": 15_000,
    "modelTotalMs": 60_000,
    "toolTotalMs": 15_000,
    "modelRounds": 4,
    "toolSchemaTokens": 4_000,
}


def evaluate_agent_run_performance(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    traces = [
        dict(event.get("payload") or {})
        for event in events
        if event.get("eventType") == TRACE_EVENT_TYPE
    ]
    planner_ms = sum(_duration(trace) for trace in traces if trace.get("stage") == "planner")
    model_ms = sum(_duration(trace) for trace in traces if trace.get("stage") == "model_round")
    tool_ms = sum(_duration(trace) for trace in traces if trace.get("stage") == "tool_round")
    model_rounds = sum(1 for trace in traces if trace.get("stage") == "model_round")
    tool_rounds = sum(1 for trace in traces if trace.get("stage") == "tool_round")
    compatibility_fallbacks = sum(
        1
        for trace in traces
        if trace.get("stage") == "tool_choice"
        and trace.get("outcome") == "provider_fallback_auto"
    )
    context_trace = next(
        (trace for trace in reversed(traces) if trace.get("stage") == "context_budget"),
        {},
    )
    context = context_trace.get("details") if isinstance(context_trace.get("details"), dict) else {}
    schema_tokens = _integer(context.get("toolSchemaTokens"))
    estimated_input = _integer(context.get("estimatedInputTokens"))
    projected_total = _integer(context.get("projectedTotalTokens"))
    window_tokens = _integer(context.get("windowTokens"))
    headroom = max(0, window_tokens - projected_total) if window_tokens else 0

    checks = [
        _budget_check("plannerLatency", planner_ms, PERFORMANCE_BUDGETS["plannerMs"], "ms"),
        _budget_check("modelLatency", model_ms, PERFORMANCE_BUDGETS["modelTotalMs"], "ms"),
        _budget_check("toolLatency", tool_ms, PERFORMANCE_BUDGETS["toolTotalMs"], "ms"),
        _budget_check("modelRounds", model_rounds, PERFORMANCE_BUDGETS["modelRounds"], "rounds"),
        _budget_check("toolSchemaSize", schema_tokens, PERFORMANCE_BUDGETS["toolSchemaTokens"], "tokens"),
        {
            "name": "providerCompatibility",
            "status": "pass" if compatibility_fallbacks == 0 else "warn",
            "detail": {"fallbackRequests": compatibility_fallbacks},
        },
    ]
    verdict = "warn" if any(check["status"] == "warn" for check in checks) else "pass"
    return {
        "verdict": verdict,
        "budgets": dict(PERFORMANCE_BUDGETS),
        "metrics": {
            "plannerMs": planner_ms,
            "modelTotalMs": model_ms,
            "toolTotalMs": tool_ms,
            "recordedTotalMs": planner_ms + model_ms + tool_ms,
            "modelRounds": model_rounds,
            "toolRounds": tool_rounds,
            "compatibilityFallbacks": compatibility_fallbacks,
            "estimatedInputTokens": estimated_input,
            "toolSchemaTokens": schema_tokens,
            "projectedTotalTokens": projected_total,
            "windowTokens": window_tokens,
            "headroomTokens": headroom,
        },
        "checks": checks,
    }


def _duration(trace: dict[str, Any]) -> int:
    return max(0, _integer(trace.get("durationMs")))


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _budget_check(name: str, actual: int, budget: int, unit: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "pass" if actual <= budget else "warn",
        "detail": {"actual": actual, "budget": budget, "unit": unit},
    }
