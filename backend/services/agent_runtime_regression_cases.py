"""Deterministic runtime incidents promoted into permanent regression cases."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.agent_run_store import TRACE_EVENT_TYPE


@dataclass(frozen=True)
class AgentRuntimeRegressionCase:
    case_id: str
    title: str
    run: dict[str, Any]
    events: tuple[dict[str, Any], ...]
    expected_report_verdict: str
    expected_planner_outcome: str
    expected_terminal_status: str
    expected_tool_sequence: tuple[str, ...] = ()
    expected_check_statuses: dict[str, str] = field(default_factory=dict)


def _trace(stage: str, outcome: str, **details: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"stage": stage, "outcome": outcome}
    if details:
        payload["details"] = details
    return {"eventType": TRACE_EVENT_TYPE, "payload": payload}


RUNTIME_REGRESSION_CASES: tuple[AgentRuntimeRegressionCase, ...] = (
    AgentRuntimeRegressionCase(
        case_id="healthy-sequential-read",
        title="读取链必须按计划完成并以 done 结束",
        run={"id": "fixture_healthy_read", "status": "done"},
        events=(
            _trace("planner", "model_plan"),
            _trace("context_budget", "within_budget"),
            _trace("tool_choice", "provider_fallback_auto"),
            _trace("tool_round", "completed", requestedTools=["listWritingChapters"]),
            _trace("tool_round", "completed", requestedTools=["getChapterContent"]),
            _trace("terminal", "done"),
        ),
        expected_report_verdict="pass",
        expected_planner_outcome="model_plan",
        expected_terminal_status="done",
        expected_tool_sequence=("listWritingChapters", "getChapterContent"),
        expected_check_statuses={"plannerHealth": "pass", "toolGovernance": "pass"},
    ),
    AgentRuntimeRegressionCase(
        case_id="planner-silent-fallback",
        title="Planner 异常不得伪装成成功运行",
        run={"id": "fixture_planner_fallback", "status": "done"},
        events=(
            _trace("planner", "fallback_after_error"),
            _trace("context_budget", "within_budget"),
            _trace("terminal", "done"),
        ),
        expected_report_verdict="fail",
        expected_planner_outcome="fallback_after_error",
        expected_terminal_status="done",
        expected_check_statuses={"plannerHealth": "fail"},
    ),
    AgentRuntimeRegressionCase(
        case_id="missing-required-tool-call",
        title="必需工具未调用必须明确失败",
        run={"id": "fixture_missing_tool", "status": "failed"},
        events=(
            _trace("planner", "model_plan"),
            _trace("context_budget", "within_budget"),
            _trace("tool_round", "missing_required_call"),
            _trace("terminal", "failed"),
        ),
        expected_report_verdict="fail",
        expected_planner_outcome="model_plan",
        expected_terminal_status="failed",
        expected_check_statuses={"toolGovernance": "fail"},
    ),
    AgentRuntimeRegressionCase(
        case_id="unauthorized-tool-rejected",
        title="越权工具调用必须被治理层识别",
        run={"id": "fixture_rejected_tool", "status": "failed"},
        events=(
            _trace("planner", "model_plan"),
            _trace("context_budget", "within_budget"),
            _trace("tool_round", "rejected", requestedTools=["deleteCharacter"]),
            _trace("terminal", "failed"),
        ),
        expected_report_verdict="fail",
        expected_planner_outcome="model_plan",
        expected_terminal_status="failed",
        expected_check_statuses={"toolGovernance": "fail"},
    ),
    AgentRuntimeRegressionCase(
        case_id="context-overflow-stops-run",
        title="上下文溢出必须失败而不是继续调用模型",
        run={"id": "fixture_context_overflow", "status": "failed"},
        events=(
            _trace("planner", "model_plan"),
            _trace("context_budget", "overflow"),
            _trace("terminal", "failed"),
        ),
        expected_report_verdict="fail",
        expected_planner_outcome="model_plan",
        expected_terminal_status="failed",
        expected_check_statuses={"contextSafety": "fail"},
    ),
    AgentRuntimeRegressionCase(
        case_id="tool-handler-error-stops-run",
        title="工具处理器错误不得被记录为完成",
        run={"id": "fixture_tool_handler_error", "status": "failed"},
        events=(
            _trace("planner", "model_plan"),
            _trace("context_budget", "within_budget"),
            _trace("tool_round", "failed", requestedTools=["getChapterContent"]),
            _trace("terminal", "failed"),
        ),
        expected_report_verdict="fail",
        expected_planner_outcome="model_plan",
        expected_terminal_status="failed",
        expected_check_statuses={"toolReliability": "fail"},
    ),
    AgentRuntimeRegressionCase(
        case_id="client-disconnect-cancels-run",
        title="客户端断开后运行不得停留在 running",
        run={"id": "fixture_client_disconnect", "status": "canceled"},
        events=(
            _trace("planner", "model_plan"),
            _trace("context_budget", "within_budget"),
            _trace("terminal", "canceled", reason="client_disconnected"),
        ),
        expected_report_verdict="warn",
        expected_planner_outcome="model_plan",
        expected_terminal_status="canceled",
        expected_check_statuses={"terminalState": "pass", "toolReliability": "pass"},
    ),
)
