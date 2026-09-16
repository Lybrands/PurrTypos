"""Compact local error-report persistence for AI stream failures."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Mapping
from uuid import uuid4

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


ERROR_REPORT_STATUSES = frozenset({"captured", "submitted", "resolved"})
_FAILED_TOOL_OUTCOMES = frozenset({"failed", "rejected", "canceled"})
_FAILED_TRACE_OUTCOMES = frozenset({
    "contract_violation",
    "exception",
    "failed",
    "interrupted",
    "invalid",
    "overflow",
    "rejected",
    "response_judge_error",
    "response_judge_contract_violation",
    "stream_exception",
})


def new_error_report_id() -> str:
    return f"err_{uuid4().hex[:16]}"


async def capture_error_report(
    db: "DatabaseConnection",
    *,
    stream_id: str,
    error_message: str,
    agent_run_id: str | None = None,
    session_id: int | None = None,
    conversation_id: int | None = None,
    book_id: str | None = None,
    chapter_id: str | None = None,
    source: str = "ai_chat_stream",
    error_code: str | None = None,
    model_name: str | None = None,
    diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Idempotently create or enrich one report for a renderer stream."""

    report_id = new_error_report_id()
    resolved_diagnostics = dict(diagnostics or {})
    if agent_run_id:
        resolved_diagnostics.update(
            await _agent_run_failure_diagnostics(db, agent_run_id)
        )
    await db.execute(
        "INSERT INTO ai_error_reports "
        "(id, stream_id, agent_run_id, session_id, conversation_id, book_id, "
        "chapter_id, source, error_code, error_message, model_name, "
        "diagnostic_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(stream_id) DO UPDATE SET "
        "agent_run_id = COALESCE(excluded.agent_run_id, agent_run_id), "
        "session_id = COALESCE(excluded.session_id, session_id), "
        "conversation_id = COALESCE(excluded.conversation_id, conversation_id), "
        "book_id = COALESCE(excluded.book_id, book_id), "
        "chapter_id = COALESCE(excluded.chapter_id, chapter_id), "
        "error_code = COALESCE(excluded.error_code, error_code), "
        "error_message = excluded.error_message, "
        "model_name = COALESCE(excluded.model_name, model_name), "
        "diagnostic_json = excluded.diagnostic_json, "
        "update_time = CURRENT_TIMESTAMP",
        [
            report_id,
            stream_id,
            agent_run_id,
            session_id,
            conversation_id,
            book_id,
            chapter_id,
            source,
            error_code,
            error_message,
            model_name,
            json.dumps(resolved_diagnostics, ensure_ascii=False),
        ],
    )
    report = await get_error_report_by_stream(db, stream_id)
    if report is None:  # pragma: no cover - SQLite write/read invariant
        raise RuntimeError("error report was not persisted")
    return report


async def get_error_report(
    db: "DatabaseConnection",
    report_id: str,
) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM ai_error_reports WHERE id = ?",
        [report_id],
    )
    return await _enrich_report(db, _report_row(row)) if row else None


async def get_error_report_by_stream(
    db: "DatabaseConnection",
    stream_id: str,
) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM ai_error_reports WHERE stream_id = ?",
        [stream_id],
    )
    return await _enrich_report(db, _report_row(row)) if row else None


async def list_error_reports(
    db: "DatabaseConnection",
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    normalized_limit = max(1, min(200, int(limit)))
    if status:
        rows = await db.fetch_all(
            "SELECT * FROM ai_error_reports WHERE status = ? "
            "ORDER BY create_time DESC, id DESC LIMIT ?",
            [status, normalized_limit],
        )
    else:
        rows = await db.fetch_all(
            "SELECT * FROM ai_error_reports "
            "ORDER BY create_time DESC, id DESC LIMIT ?",
            [normalized_limit],
        )
    return [
        await _enrich_report(db, _report_row(row))
        for row in rows
    ]


async def submit_error_report(
    db: "DatabaseConnection",
    report_id: str,
    *,
    user_note: str | None = None,
) -> dict[str, Any] | None:
    await db.execute(
        "UPDATE ai_error_reports SET status = 'submitted', user_note = ?, "
        "submitted_at = COALESCE(submitted_at, CURRENT_TIMESTAMP), "
        "update_time = CURRENT_TIMESTAMP WHERE id = ?",
        [user_note, report_id],
    )
    return await get_error_report(db, report_id)


def _report_row(row: Mapping[str, Any]) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    try:
        value = json.loads(str(row.get("diagnostic_json") or "{}"))
        if isinstance(value, dict):
            diagnostics = value
    except (TypeError, json.JSONDecodeError):
        pass
    report = {
        "id": row.get("id"),
        "streamId": row.get("stream_id"),
        "agentRunId": row.get("agent_run_id"),
        "sessionId": row.get("session_id"),
        "conversationId": row.get("conversation_id"),
        "bookId": row.get("book_id"),
        "chapterId": row.get("chapter_id"),
        "source": row.get("source"),
        "status": row.get("status"),
        "errorCode": row.get("error_code"),
        "errorMessage": row.get("error_message"),
        "model": row.get("model_name"),
        "diagnostics": diagnostics,
        "userNote": row.get("user_note"),
        "submittedAt": row.get("submitted_at"),
        "resolvedAt": row.get("resolved_at"),
        "createTime": row.get("create_time"),
        "updateTime": row.get("update_time"),
    }
    return {key: value for key, value in report.items() if value is not None}


async def _enrich_report(
    db: "DatabaseConnection",
    report: dict[str, Any],
) -> dict[str, Any]:
    """Attach current content-neutral Run diagnostics to old reports too."""

    run_id = str(report.get("agentRunId") or "").strip()
    if not run_id:
        return report
    persisted = report.get("diagnostics")
    diagnostics = dict(persisted) if isinstance(persisted, Mapping) else {}
    diagnostics.update(await _agent_run_failure_diagnostics(db, run_id))
    diagnostics.update(await _agent_run_task_diagnostics(db, run_id))
    return {**report, "diagnostics": diagnostics}


async def _agent_run_task_diagnostics(
    db: "DatabaseConnection",
    run_id: str,
) -> dict[str, Any]:
    """Attach a content-free task state snapshot to a Run error report."""

    task = await db.fetch_one(
        "SELECT t.id, t.kind, t.status, t.total_units, t.completed_units, "
        "t.failed_units, t.state_reason_code, t.state_reason_scope "
        "FROM ai_agent_long_tasks AS t WHERE t.created_by_run_id = ? "
        "OR EXISTS (SELECT 1 FROM ai_agent_long_task_runs AS relation "
        "WHERE relation.task_id = t.id AND relation.run_id = ?) "
        "ORDER BY t.create_time DESC LIMIT 1",
        [run_id, run_id],
    )
    if task is None:
        return {}
    kind = str(task.get("kind") or "").strip()
    latest = await db.fetch_one(
        "SELECT event_type, reason_code, reason_scope, create_time "
        "FROM ai_agent_long_task_events WHERE task_id = ? "
        "ORDER BY id DESC LIMIT 1",
        [task["id"]],
    )
    event_count = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM ai_agent_long_task_events WHERE task_id = ?",
        [task["id"]],
    )
    task_type = (
        "持久化长任务 · 剧本正文分批创作"
        if kind == "screenplay_draft_generation"
        else f"持久化长任务 · {kind or '通用任务'}"
    )
    diagnostics: dict[str, Any] = {
        "taskType": task_type,
        "taskWorkflowStatus": task.get("status"),
        "taskUnitProgress": {
            "completed": int(task.get("completed_units") or 0),
            "total": int(task.get("total_units") or 0),
            "failed": int(task.get("failed_units") or 0),
        },
        "taskEventCount": int((event_count or {}).get("count") or 0),
    }
    if task.get("state_reason_code"):
        diagnostics["taskReasonCode"] = task["state_reason_code"]
    if task.get("state_reason_scope"):
        diagnostics["taskReasonScope"] = task["state_reason_scope"]
    if latest is not None:
        diagnostics["taskLatestEvent"] = {
            "type": latest.get("event_type"),
            "reasonCode": latest.get("reason_code"),
            "reasonScope": latest.get("reason_scope"),
            "at": latest.get("create_time"),
        }
    return diagnostics


async def _agent_run_failure_diagnostics(
    db: "DatabaseConnection",
    run_id: str,
) -> dict[str, Any]:
    """Derive a precise diagnosis from trusted, content-neutral Run events."""

    rows = await db.fetch_all(
        "SELECT event_type, payload_json FROM ai_agent_run_events "
        "WHERE run_id = ? AND event_type IN "
        "('agentRunTrace', 'tool.call_completed', 'run.failed') "
        "ORDER BY id ASC",
        [run_id],
    )
    diagnosis: dict[str, Any] = {}
    trace_diagnosis: dict[str, Any] = {}
    terminal_error_code = ""
    for row in rows:
        payload = _event_payload(row.get("payload_json"))
        event_type = str(row.get("event_type") or "")
        if event_type == "tool.call_completed":
            outcome = str(payload.get("outcome") or "").strip()
            error_code = str(
                payload.get("errorCode")
                or payload.get("error_code")
                or ""
            ).strip()
            if error_code or outcome in _FAILED_TOOL_OUTCOMES:
                diagnosis = {
                    "failureStage": "tool_execution",
                    "failureOutcome": outcome or "failed",
                    "failureTool": str(
                        payload.get("toolName")
                        or payload.get("tool_name")
                        or ""
                    ).strip(),
                    "failureToolCallId": str(
                        payload.get("toolCallId")
                        or payload.get("tool_call_id")
                        or ""
                    ).strip(),
                    "failureErrorCode": error_code or "tool_execution_failed",
                }
                exception_type = str(
                    payload.get("exceptionType")
                    or payload.get("exception_type")
                    or ""
                ).strip()
                if exception_type:
                    diagnosis["exceptionType"] = exception_type
        elif event_type == "agentRunTrace":
            stage = str(payload.get("stage") or "").strip()
            outcome = str(payload.get("outcome") or "").strip()
            details = payload.get("details")
            detail_map = details if isinstance(details, Mapping) else {}
            if (
                outcome in _FAILED_TRACE_OUTCOMES
                or detail_map.get("errorType")
                or detail_map.get("primaryErrorCode")
                or detail_map.get("recoveryErrorCode")
            ):
                trace_diagnosis = {
                    "failureStage": stage or "agent_runtime",
                    "failureOutcome": outcome or "failed",
                }
                for source_key, target_key in (
                    ("errorType", "exceptionType"),
                    ("primaryErrorCode", "primaryErrorCode"),
                    ("recoveryErrorCode", "recoveryErrorCode"),
                    ("reasonCode", "validationReasonCode"),
                ):
                    value = str(detail_map.get(source_key) or "").strip()
                    if value:
                        trace_diagnosis[target_key] = value
        elif event_type == "run.failed":
            error_code = str(payload.get("error") or "").strip()
            if error_code:
                terminal_error_code = error_code

    if not diagnosis:
        diagnosis.update(trace_diagnosis)
    else:
        for key in (
            "exceptionType",
            "primaryErrorCode",
            "recoveryErrorCode",
            "validationReasonCode",
        ):
            if trace_diagnosis.get(key):
                diagnosis[key] = trace_diagnosis[key]
    prior_error_code = str(diagnosis.get("failureErrorCode") or "").strip()
    if terminal_error_code and terminal_error_code != prior_error_code:
        if prior_error_code:
            for source, target in (
                ("failureStage", "lastToolFailureStage"),
                ("failureOutcome", "lastToolFailureOutcome"),
                ("failureTool", "lastToolFailureTool"),
                ("failureToolCallId", "lastToolFailureToolCallId"),
                ("failureErrorCode", "lastToolFailureErrorCode"),
            ):
                if source in diagnosis:
                    diagnosis[target] = diagnosis.pop(source)
        diagnosis.update({
            "failureStage": "agent_runtime",
            "failureOutcome": "failed",
            "failureErrorCode": terminal_error_code,
        })
    elif terminal_error_code:
        diagnosis["failureErrorCode"] = terminal_error_code
    error_code = str(diagnosis.get("failureErrorCode") or "").strip()
    if error_code:
        diagnosis["failureReason"] = _failure_reason(
            error_code,
            str(diagnosis.get("failureTool") or "").strip(),
        )
    return {
        key: value
        for key, value in diagnosis.items()
        if value not in (None, "")
    }


def _event_payload(value: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _failure_reason(error_code: str, tool_name: str) -> str:
    if error_code == "tool_scope_violation":
        if tool_name in {"getSourceCharacters", "getSourceWorldSettings"}:
            return (
                "当前剧本项目只改编限定章节或卷；人物卡和世界设定属于"
                "整书级资料，无法证明全部位于改编范围内，因此该工具被"
                "项目作用域安全规则拒绝。"
            )
        return "工具请求超出了当前书籍、项目或改编范围，已被作用域安全规则拒绝。"
    return {
        "tool_not_authorized": "模型调用了当前计划没有授权的工具。",
        "tool_input_invalid": (
            "工具参数未通过该工具的结构或业务字段校验；这不是任务授权或"
            "项目作用域拒绝。"
        ),
        "tool_internal_error": "工具处理函数内部发生异常；数据操作未完成。",
        "tool_execution_failed": "工具执行路径发生异常；数据操作未完成。",
        "missing_required_tool_call": "当前步骤必须调用工具，但模型没有返回结构化工具调用。",
        "planning_contract_violation": "规划结果引用了当前运行不可用或未授权的能力。",
        "context_setup_failed": "后端在组装可信项目上下文时发生异常。",
        "provider_circuit_open": "模型服务正在冷却恢复，尚未发起新的调用。",
        "provider_capacity_limited": "模型服务当前处理容量已满，任务将稍后恢复。",
        "upstream_stream_interrupted": "模型服务的流式连接在返回完整结果前中断。",
    }.get(error_code, f"Agent 以错误代码 {error_code} 终止。")
