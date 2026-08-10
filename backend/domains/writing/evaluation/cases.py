"""Writing-owned deterministic runtime regression evidence."""

from __future__ import annotations

from typing import Any

from purra.evaluation import (
    TRACE_EVENT_TYPE,
    AgentRuntimeRegressionCase,
)


def _trace(stage: str, outcome: str, **details: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"stage": stage, "outcome": outcome}
    if details:
        payload["details"] = details
    return {"eventType": TRACE_EVENT_TYPE, "payload": payload}


def _event(event_type: str, **payload: Any) -> dict[str, Any]:
    return {"eventType": event_type, "payload": payload}


WRITING_RUNTIME_REGRESSION_CASES: tuple[AgentRuntimeRegressionCase, ...] = (
    AgentRuntimeRegressionCase(
        case_id="healthy-sequential-read",
        title="读取链必须按计划完成并以 done 结束",
        run={"id": "fixture_healthy_read", "status": "done"},
        events=(
            _trace("planner", "model_plan"),
            _trace("context_budget", "within_budget"),
            _trace("tool_choice", "provider_fallback_auto"),
            _trace(
                "tool_round",
                "completed",
                requestedTools=["listWritingChapters"],
            ),
            _trace(
                "tool_round",
                "completed",
                requestedTools=["getChapterContent"],
            ),
            _trace("terminal", "done"),
        ),
        expected_report_verdict="pass",
        expected_planner_outcome="model_plan",
        expected_terminal_status="done",
        expected_tool_sequence=("listWritingChapters", "getChapterContent"),
        expected_check_statuses={
            "plannerHealth": "pass",
            "toolGovernance": "pass",
        },
        expected_failure_codes=(),
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
        expected_failure_codes=("planner.contract_failure",),
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
        expected_failure_codes=("tool.missing_required_call",),
    ),
    AgentRuntimeRegressionCase(
        case_id="unauthorized-tool-rejected",
        title="越权工具调用必须被治理层识别",
        run={"id": "fixture_rejected_tool", "status": "failed"},
        events=(
            _trace("planner", "model_plan"),
            _trace("context_budget", "within_budget"),
            _trace(
                "tool_round",
                "rejected",
                requestedTools=["deleteCharacter"],
            ),
            _trace("terminal", "failed"),
        ),
        expected_report_verdict="fail",
        expected_planner_outcome="model_plan",
        expected_terminal_status="failed",
        expected_check_statuses={"toolGovernance": "fail"},
        expected_failure_codes=("tool.authorization_rejected",),
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
        expected_failure_codes=("context.overflow",),
    ),
    AgentRuntimeRegressionCase(
        case_id="tool-handler-error-stops-run",
        title="工具处理器错误不得被记录为完成",
        run={"id": "fixture_tool_handler_error", "status": "failed"},
        events=(
            _trace("planner", "model_plan"),
            _trace("context_budget", "within_budget"),
            _trace(
                "tool_round",
                "failed",
                requestedTools=["getChapterContent"],
            ),
            _trace("terminal", "failed"),
        ),
        expected_report_verdict="fail",
        expected_planner_outcome="model_plan",
        expected_terminal_status="failed",
        expected_check_statuses={"toolReliability": "fail"},
        expected_failure_codes=("tool.execution_failed",),
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
        expected_check_statuses={
            "terminalState": "pass",
            "toolReliability": "pass",
        },
        expected_failure_codes=(),
    ),
    AgentRuntimeRegressionCase(
        case_id="malformed-tool-json-is-classified",
        title="工具 JSON 解析失败必须保留协议根因",
        run={"id": "fixture_tool_json", "status": "failed"},
        events=(
            _trace("planner", "model_plan"),
            _trace("context_budget", "within_budget"),
            _event(
                "tool.calls_started",
                calls=[{"id": "call-json", "name": "persistArtifactBatch"}],
            ),
            _event(
                "tool.results",
                results=[{
                    "tool_call_id": "call-json",
                    "tool_name": "persistArtifactBatch",
                    "error": "invalid_tool_arguments_json",
                }],
            ),
            _trace("terminal", "failed"),
        ),
        expected_report_verdict="fail",
        expected_planner_outcome="model_plan",
        expected_terminal_status="failed",
        expected_failure_codes=("tool.protocol_invalid_arguments",),
    ),
    AgentRuntimeRegressionCase(
        case_id="started-tool-without-result-is-classified",
        title="已发起但没有结果的工具必须归类为生命周期未收口",
        run={"id": "fixture_incomplete_tool", "status": "failed"},
        events=(
            _trace("planner", "model_plan"),
            _trace("context_budget", "within_budget"),
            _event(
                "tool.calls_started",
                calls=[{"id": "call-lost", "name": "persistArtifactBatch"}],
            ),
            _trace("terminal", "failed"),
        ),
        expected_report_verdict="fail",
        expected_planner_outcome="model_plan",
        expected_terminal_status="failed",
        expected_failure_codes=("tool.incomplete_terminalization",),
    ),
    AgentRuntimeRegressionCase(
        case_id="compaction-failure-is-classified",
        title="上下文压缩失败必须保留压缩阶段根因",
        run={"id": "fixture_compaction_failure", "status": "failed"},
        events=(
            _trace("planner", "model_plan"),
            _trace(
                "conversation_compaction",
                "generation_failed",
                compactedTurnCount=0,
            ),
            _trace("context_budget", "within_budget"),
            _trace("terminal", "failed"),
        ),
        expected_report_verdict="fail",
        expected_planner_outcome="model_plan",
        expected_terminal_status="failed",
        expected_failure_codes=("context.compaction_failed",),
    ),
    AgentRuntimeRegressionCase(
        case_id="model-interruption-is-classified",
        title="模型流中断必须归类为模型传输故障",
        run={"id": "fixture_model_interrupted", "status": "failed"},
        events=(
            _trace("planner", "model_plan"),
            _trace("context_budget", "within_budget"),
            _trace(
                "stream",
                "interrupted",
                providerAttemptTerminal=True,
            ),
            _trace("terminal", "failed"),
        ),
        expected_report_verdict="fail",
        expected_planner_outcome="model_plan",
        expected_terminal_status="failed",
        expected_failure_codes=("model.interrupted",),
    ),
)

__all__ = ["WRITING_RUNTIME_REGRESSION_CASES"]
