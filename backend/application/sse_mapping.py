"""Stable mapping from typed Core updates to the existing desktop SSE API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_core.contracts import AgentRunResult, RunStatus
from agent_core.events import AgentEvent, CoreEventType
from agent_core.json_values import thaw_json_mapping


def core_update_to_sse_chunk(
    update: AgentEvent | AgentRunResult,
    *,
    model: str,
) -> dict[str, Any] | None:
    if isinstance(update, AgentRunResult):
        if update.status is RunStatus.DONE:
            return {"done": True, "model": update.model or model}
        if update.status is RunStatus.CANCELED:
            return None
        if update.status is RunStatus.BLOCKED:
            return {"error": "Agent 未完成全部计划步骤，已安全停止。"}
        return {"error": _runtime_error_message(update.error)}
    return core_event_to_sse_chunk(update)


def core_event_to_sse_chunk(event: AgentEvent) -> dict[str, Any] | None:
    payload = thaw_json_mapping(event.payload)
    run_id = str(event.run_id or "")

    if event.type == CoreEventType.RUN_STARTED:
        return {"agentRunStarted": {"runId": run_id, **payload}}
    if event.type == CoreEventType.RUN_TODOS_UPDATED:
        return {"agentRunTodosUpdated": {
            "runId": run_id,
            **_plan_payload(payload),
        }}
    if event.type == CoreEventType.RUN_TODO_UPDATED:
        step = payload.get("step")
        return {"agentRunTodoUpdated": {
            "runId": run_id,
            "stepId": payload.get("step_id"),
            "step": _step_payload(step) if isinstance(step, Mapping) else step,
            "status": payload.get("status"),
        }}
    terminal_names = {
        CoreEventType.RUN_COMPLETED: "agentRunCompleted",
        CoreEventType.RUN_BLOCKED: "agentRunBlocked",
        CoreEventType.RUN_FAILED: "agentRunFailed",
        CoreEventType.RUN_CANCELED: "agentRunCanceled",
    }
    if event.type in terminal_names:
        return {terminal_names[event.type]: {"runId": run_id, **payload}}

    if event.type == CoreEventType.MODEL_DELTA:
        return {"delta": str(payload.get("delta") or "")}
    if event.type == CoreEventType.MODEL_THINKING_DELTA:
        return {"thinkingDelta": str(payload.get("delta") or "")}
    if event.type == CoreEventType.TOOL_CALLS_STARTED:
        return {
            "toolCalls": [
                {
                    "id": call.get("id"),
                    "type": "function",
                    "function": {
                        "name": call.get("name"),
                        "arguments": call.get("arguments_json", ""),
                    },
                }
                for call in payload.get("calls", [])
                if isinstance(call, Mapping)
            ],
            "toolCallsInProgress": bool(payload.get("in_progress")),
            "partialContent": str(payload.get("partial_content") or ""),
            "partialThinking": str(payload.get("partial_thinking") or ""),
            "model": payload.get("model"),
        }
    if event.type == CoreEventType.TOOL_RESULTS:
        return {"toolResults": [
            {
                "tool_call_id": item.get("tool_call_id"),
                "name": item.get("tool_name"),
                "content": item.get("content", ""),
            }
            for item in payload.get("results", [])
            if isinstance(item, Mapping)
        ]}
    if event.type == CoreEventType.TOOL_CALL_COMPLETED:
        chunk: dict[str, Any] = {
            "toolIndexCompleted": int(payload.get("index") or 0),
        }
        if payload.get("fromCache") or payload.get("from_cache"):
            chunk["toolFromCache"] = True
        return chunk
    if event.type == CoreEventType.TOOL_ROUND_COMPLETED:
        return None
    if event.type == CoreEventType.APPROVAL_REQUESTED:
        return {"toolApprovalRequired": {"runId": run_id, **payload}}
    if event.type == CoreEventType.APPROVAL_RESOLVED:
        return {"toolApprovalResolved": {"runId": run_id, **payload}}
    if event.type == CoreEventType.DELEGATION_CREATED:
        return {"agentDelegationCreated": {"runId": run_id, **payload}}
    if event.type in {
        CoreEventType.DELEGATION_CLAIMED,
        CoreEventType.DELEGATION_COMPLETED,
        CoreEventType.DELEGATION_FAILED,
        CoreEventType.DELEGATION_CANCELED,
    }:
        return {"agentDelegationUpdated": {"runId": run_id, **payload}}
    if event.type == CoreEventType.CONTEXT_BUDGETED:
        diagnostics = payload.get("diagnostics")
        context_budget = {
            "windowTokens": payload.get("windowTokens", 0),
            "estimatedInputTokens": payload.get("estimatedInputTokens", 0),
            "toolSchemaTokens": payload.get("toolSchemaTokens", 0),
            "outputReserveTokens": payload.get("outputReserveTokens", 0),
            "safetyReserveTokens": payload.get("safetyReserveTokens", 0),
            "runtimeReserveTokens": payload.get("runtimeReserveTokens", 0),
            "droppedMessages": payload.get("droppedMessages", 0),
            "projectedTotalTokens": payload.get("projectedTotalTokens", 0),
            "overflowTokens": payload.get("overflowTokens", 0),
        }
        if isinstance(diagnostics, Mapping):
            context_budget.update(dict(diagnostics))
        return {"contextBudget": context_budget}

    domain_names = {
        "writing.proposed_chapter_diff": "proposedChapterDiff",
        "writing.proposed_setting_diff": "proposedSettingDiff",
        "writing.setting_updated": "settingUpdated",
        "writing.chapter_created": "chapterCreated",
    }
    if event.type in domain_names:
        return {domain_names[event.type]: payload}
    if event.type == "writing.progress":
        return payload
    return None


def _plan_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(payload),
        "steps": [
            _step_payload(step)
            for step in payload.get("steps", [])
            if isinstance(step, Mapping)
        ],
    }


def _step_payload(step: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": step.get("id"),
        "title": step.get("title"),
        "type": step.get("type"),
        "executor": step.get("executor"),
        "status": step.get("status"),
        "riskLevel": step.get("risk_level"),
        "suggestedTools": list(step.get("suggested_tools") or ()),
        "description": step.get("description"),
        "resultSummary": step.get("result_summary"),
        "error": step.get("error"),
    }


def _runtime_error_message(error_code: str | None) -> str:
    return {
        "planning_invalid": "Agent 计划格式无效，已安全停止。",
        "planning_contract_violation": "Agent 计划超出当前工具授权，已安全停止。",
        "planning_failed": "Agent 计划生成失败，已停止执行。",
        "context_overflow_initial": "当前问题与必要上下文超过模型窗口，请减少上下文。",
        "context_setup_failed": "写作上下文准备失败，Agent 已安全停止。",
        "context_overflow_after_tool": "工具结果超过剩余上下文窗口，Agent 已停止。",
        "missing_required_tool_call": "当前计划步骤必须调用工具，但模型未返回结构化调用。",
        "max_model_rounds": "Agent 已达到最大工具轮次，已停止继续执行。",
        "tool_not_authorized": "模型请求了当前计划未授权的工具，Agent 已停止。",
        "tool_call_after_approval_rejection": "您已拒绝审批；操作未执行，相关数据仍保留。Agent 已阻止再次调用工具。",
        "unstructured_tool_call_after_rejection": "您已拒绝审批；操作未执行，相关数据仍保留。模型未能生成安全说明，Agent 已停止。",
        "approval_unavailable": "工具批准请求超时或当前不可用，未执行操作。",
        "tool_execution_failed": "工具执行失败，Agent 已停止。",
        "response_constraint_violation": (
            "模型两次生成的回答都未满足当前写作要求，未展示不合规内容。"
            "请缩小任务范围、明确输出格式后重试。"
        ),
        "response_judge_error": (
            "语义校验暂时无法完成，候选回答未展示；请稍后重试。"
        ),
        "response_judge_contract_violation": (
            "语义校验返回了无效结果，候选回答未展示；请稍后重试。"
        ),
        "upstream_stream_interrupted": "模型服务流式响应中断，请检查网络或稍后重试。",
        "provider_insufficient_balance": "模型服务账户余额或额度不足，请充值或更换模型。",
        "provider_authentication_failed": "模型服务鉴权失败，请检查 API Key 和接口地址。",
        "provider_rate_limited": "模型服务请求过于频繁或已达到限额，请稍后重试。",
        "provider_bad_request": "模型服务拒绝了请求，请检查模型名称及其参数兼容性。",
        "provider_unavailable": "模型服务暂时不可用，请稍后重试。",
    }.get(str(error_code or ""), "Agent 运行过程中发生异常，已安全停止；请稍后重试。")
