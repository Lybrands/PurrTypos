"""Stable mapping from typed Core updates to the existing desktop SSE API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from purra.contracts import AgentRunResult, RunStatus
from purra.events import AgentEvent, CoreEventType
from purra.json_values import thaw_json_mapping


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
    if event.type in {
        "conversation.compaction.started",
        "conversation.compaction.completed",
    }:
        return {"contextCompaction": payload}
    if event.type == CoreEventType.RUN_TODOS_UPDATED:
        return {"agentRunTodosUpdated": {
            "runId": run_id,
            **_plan_payload(payload),
        }}
    if event.type == CoreEventType.RUN_TODO_UPDATED:
        step = payload.get("step")
        if (
            isinstance(step, Mapping)
            and bool(step.get("protocol_private"))
        ):
            return None
        return {"agentRunTodoUpdated": {
            "runId": run_id,
            "stepId": payload.get("step_id"),
            "step": _step_payload(step) if isinstance(step, Mapping) else step,
            "status": payload.get("status"),
        }}
    if event.type == CoreEventType.MODEL_CALL_RECORDED:
        return {"modelInvocation": payload}
    terminal_names = {
        CoreEventType.RUN_COMPLETED: "agentRunCompleted",
        CoreEventType.RUN_BLOCKED: "agentRunBlocked",
        CoreEventType.RUN_FAILED: "agentRunFailed",
        CoreEventType.RUN_CANCELED: "agentRunCanceled",
    }
    if event.type in terminal_names:
        terminal_payload = {"runId": run_id, **payload}
        final_response = str(payload.get("final_response") or "")
        if final_response:
            terminal_payload["finalResponse"] = final_response
        return {terminal_names[event.type]: terminal_payload}

    if event.type == CoreEventType.ASSISTANT_FINAL_DELTA:
        return {"delta": str(payload.get("delta") or "")}
    if event.type == CoreEventType.ASSISTANT_COMMENTARY_DELTA:
        return {"commentaryDelta": str(payload.get("delta") or "")}
    if event.type == CoreEventType.MODEL_CONTENT_DELTA:
        return {"modelContentDelta": str(payload.get("delta") or "")}
    if event.type == CoreEventType.MODEL_REASONING_DELTA:
        return {"reasoningDelta": str(payload.get("delta") or "")}
    if event.type == CoreEventType.TOOL_CALLS_STARTED:
        return {
            "toolCalls": [
                {
                    "id": call.get("id"),
                    "type": "function",
                    "displayNames": dict(call.get("display_names") or {}),
                    "function": {
                        "name": call.get("name"),
                        "arguments": call.get("arguments_json", ""),
                    },
                }
                for call in payload.get("calls", [])
                if isinstance(call, Mapping)
            ],
            "toolCallsInProgress": bool(payload.get("in_progress")),
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
        optional_fields = {
            "toolCallId": payload.get("toolCallId") or payload.get("tool_call_id"),
            "toolName": payload.get("toolName") or payload.get("tool_name"),
            "toolOutcome": payload.get("outcome"),
            "toolErrorCode": payload.get("errorCode") or payload.get("error_code"),
            "toolExceptionType": (
                payload.get("exceptionType") or payload.get("exception_type")
            ),
        }
        chunk.update({key: value for key, value in optional_fields.items() if value})
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
    if event.type == CoreEventType.DELEGATION_EVENT:
        child_event = payload.get("event")
        if not isinstance(child_event, Mapping):
            return None
        child_type = str(child_event.get("type") or "").strip()
        child_run_id = str(
            child_event.get("runId")
            or payload.get("childRunId")
            or ""
        ).strip()
        child_payload = child_event.get("payload")
        if not child_type or not isinstance(child_payload, Mapping):
            return None
        child_chunk = core_event_to_sse_chunk(
            AgentEvent(
                type=child_type,
                run_id=child_run_id or None,
                payload=child_payload,
            ),
        )
        if child_chunk is None:
            return None
        envelope = {
            "runId": run_id,
            "parentRunId": payload.get("parentRunId") or run_id,
            "rootRunId": payload.get("rootRunId") or run_id,
            "delegationId": payload.get("delegationId"),
            "childRunId": child_run_id or None,
            "agentRole": payload.get("agentRole"),
            "agentTitle": payload.get("agentTitle"),
            "objective": payload.get("objective"),
            "chunk": child_chunk,
        }
        if payload.get("unitId") is not None:
            envelope["unitId"] = payload.get("unitId")
        if payload.get("attempt") is not None:
            envelope["attempt"] = payload.get("attempt")
        return {"agentSubRunEvent": envelope}
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
        if isinstance(payload.get("outputBudget"), Mapping):
            context_budget["outputBudget"] = dict(payload["outputBudget"])
        return {"contextBudget": context_budget}
    if event.type == CoreEventType.CONTEXT_USAGE_RECORDED:
        context_usage = {
            "actualInputTokens": payload.get("actualInputTokens"),
            "actualOutputTokens": payload.get("actualOutputTokens"),
            "actualTotalTokens": payload.get("actualTotalTokens"),
            "cachedInputTokens": payload.get("cachedInputTokens"),
            "reasoningOutputTokens": payload.get(
                "reasoningOutputTokens"
            ),
            "actualUsageRound": payload.get("actualUsageRound"),
            "inputTokenEstimateAtUsage": payload.get(
                "inputTokenEstimateAtUsage"
            ),
            "usageSource": payload.get("usageSource"),
        }
        if payload.get("requestedOutputTokens") is not None:
            context_usage["requestedOutputTokens"] = payload.get(
                "requestedOutputTokens"
            )
        if payload.get("finishReason") is not None:
            context_usage["finishReason"] = payload.get("finishReason")
        if isinstance(payload.get("outputBudget"), Mapping):
            context_usage["outputBudget"] = dict(payload["outputBudget"])
        return {
            "contextBudget": context_usage,
        }
    if event.type == CoreEventType.TASK_ADMISSION_DECIDED:
        return {"taskAdmission": {"runId": run_id, **payload}}
    if event.type == CoreEventType.LONG_TASK_DISPATCHED:
        # Dispatch metadata drives the durable task attachment.  Its host
        # receipt is not model output and must never be rendered as if the AI
        # had said it in the conversation.
        return {"longTaskDispatched": {"runId": run_id, **payload}}
    if event.type == CoreEventType.LONG_TASK_PROGRESS:
        return {"longTaskProgress": {"runId": run_id, **payload}}

    domain_names = {
        "writing.proposed_chapter_diff": "proposedChapterDiff",
        "writing.proposed_setting_diff": "proposedSettingDiff",
        "writing.setting_updated": "settingUpdated",
        "writing.chapter_created": "chapterCreated",
    }
    if event.type in domain_names:
        return {domain_names[event.type]: dict(payload)}
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
            and not bool(step.get("protocol_private"))
        ],
    }


def _step_payload(step: Mapping[str, Any]) -> dict[str, Any]:
    planning_capability = str(
        step.get("planning_capability") or ""
    ).strip()
    payload = {
        "id": step.get("id"),
        "title": step.get("title"),
        "type": step.get("type"),
        "executor": step.get("executor"),
        "status": step.get("status"),
        "riskLevel": step.get("risk_level"),
        "suggestedTools": (
            [planning_capability]
            if planning_capability
            else list(step.get("suggested_tools") or ())
        ),
        "planningCapability": planning_capability or None,
        "protocolPrivate": bool(step.get("protocol_private")),
        "agentRole": step.get("agent_role"),
        "assignment": dict(step.get("assignment") or {}),
        "dependsOn": list(step.get("depends_on") or ()),
        "description": step.get("description"),
        "resultSummary": step.get("result_summary"),
        "error": step.get("error"),
    }
    if not payload["agentRole"]:
        payload.pop("agentRole")
    if not payload["assignment"]:
        payload.pop("assignment")
    if not payload["dependsOn"]:
        payload.pop("dependsOn")
    if not payload["planningCapability"]:
        payload.pop("planningCapability")
    if not payload["protocolPrivate"]:
        payload.pop("protocolPrivate")
    return payload


def _runtime_error_message(error_code: str | None) -> str:
    return {
        "planning_invalid": "Agent 计划格式无效，已安全停止。",
        "planning_contract_violation": "Agent 计划超出当前工具授权，已安全停止。",
        "planning_failed": "Agent 计划生成失败，已停止执行。",
        "task_routing_failed": "批量任务路由失败，尚未开始生成，请重试。",
        "context_overflow_initial": "当前问题与必要上下文超过模型窗口，请减少上下文。",
        "context_setup_failed": "写作上下文准备失败，Agent 已安全停止。",
        "context_overflow_after_tool": "工具结果超过剩余上下文窗口，Agent 已停止。",
        "missing_required_tool_call": "当前计划步骤必须调用工具，但模型未返回结构化调用。",
        "tool_call_truncated": (
            "模型在生成工具参数时达到输出上限；残缺调用已被丢弃，工具未执行。"
            "请缩小单次生成内容或提高模型输出上限后重试。"
        ),
        "model_output_truncated": (
            "模型回答达到输出上限且未完整结束；系统未把不完整内容视为成功结果。"
            "请缩小任务范围或提高模型输出上限后重试。"
        ),
        "model_output_filtered": (
            "模型服务因内容安全策略中止了本轮输出；不完整内容和工具调用均未提交。"
            "请调整请求内容后重试。"
        ),
        "unsupported_model_finish_reason": (
            "模型服务使用了系统无法确认完整性的结束状态；本轮输出未提交。"
            "请在调试面板核对供应商结束原因和模型兼容性。"
        ),
        "malformed_tool_call_batch": (
            "模型返回的工具调用协议不完整或存在冲突；整批调用均未执行。"
            "请重试；若持续出现，请在调试面板查看调用数量和参数长度。"
        ),
        "empty_model_response": "模型多次只返回内部推理，没有生成可展示的答复。请重试或更换模型。",
        "incomplete_model_response": (
            "模型多次只说明准备执行的步骤，没有真正完成当前回答。"
            "未展示不完整内容，请重试或更换模型。"
        ),
        "max_model_rounds": "Agent 已达到最大工具轮次，已停止继续执行。",
        "tool_not_authorized": "模型请求了当前计划未授权的工具，Agent 已停止。",
        "tool_call_after_approval_rejection": "您已拒绝审批；操作未执行，相关数据仍保留。Agent 已阻止再次调用工具。",
        "unstructured_tool_call_after_rejection": "您已拒绝审批；操作未执行，相关数据仍保留。模型未能生成安全说明，Agent 已停止。",
        "approval_unavailable": "工具批准请求超时或当前不可用，未执行操作。",
        "tool_execution_failed": "工具执行失败，Agent 已停止。",
        "tool_input_invalid": (
            "工具参数未通过该工具的结构或业务字段校验，Agent 未执行该操作。"
        ),
        "tool_internal_error": "工具内部执行异常，相关操作未完成。",
        "tool_scope_violation": (
            "当前工具不符合项目的数据范围约束，Agent 已停止。"
            "调试面板会显示失败工具和具体范围原因。"
        ),
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
        "provider_reasoning_context_invalid": (
            "模型思考模式的工具调用上下文不完整，请重试本次任务。"
        ),
        "provider_unavailable": "模型服务暂时不可用，请稍后重试。",
    }.get(str(error_code or ""), "Agent 运行过程中发生异常，已安全停止；请稍后重试。")
