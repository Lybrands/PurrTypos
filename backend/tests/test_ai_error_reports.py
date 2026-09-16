from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from database.connection import DatabaseConnection
from dependencies import clear_db, set_db
from schemas.ai import (
    CaptureAiErrorReportRequest,
    SubmitAiErrorReportRequest,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def report_db(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    try:
        yield db
    finally:
        clear_db(db)
        await db.close()


async def test_error_report_capture_is_idempotent_and_content_free(
    report_db: DatabaseConnection,
):
    from infrastructure.persistence.run_store import create_run
    from routers.ai import capture_ai_error_report

    run_id = await create_run(
        report_db,
        session_id=42,
        prompt="这段提示词不得复制进错误报告",
        mode="agent",
    )
    body = CaptureAiErrorReportRequest(
        streamId="chat-report-1",
        agentRunId=run_id,
        sessionId=42,
        bookId="book-1",
        chapterId="chapter-1",
        source="workspace_chat",
        errorCode="missing_required_tool_call",
        errorMessage="模型未返回结构化调用",
        model="model-a",
        diagnostics={
            "provider": "openai",
            "taskType": "剧本阶段交付 · 场景正文",
            "messageCount": 7,
            "toolsEnabled": True,
            "prompt": "不得保存",
            "apiKey": "secret",
            "chapterText": "不得保存",
        },
    )

    first = await capture_ai_error_report(body)
    second = await capture_ai_error_report(body.model_copy(update={
        "errorMessage": "模型仍未返回结构化调用",
    }))

    assert first["success"] is True
    assert second["data"]["id"] == first["data"]["id"]
    assert second["data"]["streamId"] == "chat-report-1"
    assert second["data"]["agentRunId"] == run_id
    assert second["data"]["diagnostics"] == {
        "provider": "openai",
        "taskType": "剧本阶段交付 · 场景正文",
        "messageCount": 7,
        "toolsEnabled": True,
    }
    row = await report_db.fetch_one(
        "SELECT diagnostic_json, error_message FROM ai_error_reports "
        "WHERE stream_id = ?",
        ["chat-report-1"],
    )
    assert row is not None
    assert "secret" not in row["diagnostic_json"]
    assert "不得保存" not in row["diagnostic_json"]
    assert row["error_message"] == "模型仍未返回结构化调用"


async def test_historical_report_recovers_durable_task_type(
    report_db: DatabaseConnection,
):
    from infrastructure.persistence.error_report_store import (
        capture_error_report,
        get_error_report,
    )
    from infrastructure.persistence.run_store import create_run

    run_id = await create_run(
        report_db,
        session_id=None,
        prompt="不会进入诊断信息",
        mode="agent",
    )
    await report_db.execute(
        "INSERT INTO ai_agent_long_tasks "
        "(id, namespace, kind, owner_id, created_by_run_id, "
        "total_units, max_parallelism) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            "task-history",
            "purrtypos.screenplay",
            "screenplay_draft_generation",
            "project-1",
            run_id,
            2,
            1,
        ],
    )
    captured = await capture_error_report(
        report_db,
        stream_id="screenplay-history",
        agent_run_id=run_id,
        error_message="历史错误",
    )

    report = await get_error_report(report_db, captured["id"])

    assert report is not None
    assert report["diagnostics"]["taskType"] == (
        "持久化长任务 · 剧本正文分批创作"
    )


async def test_error_report_can_be_queued_for_review_and_listed(
    report_db: DatabaseConnection,
):
    from routers.ai import (
        capture_ai_error_report,
        get_ai_error_report,
        list_ai_error_reports,
        submit_ai_error_report,
    )

    captured = await capture_ai_error_report(CaptureAiErrorReportRequest(
        streamId="inline-edit-report-1",
        source="inline_edit",
        errorMessage="上游连接中断",
        diagnostics={"messageCount": 2},
    ))
    report_id = captured["data"]["id"]

    submitted = await submit_ai_error_report(
        report_id,
        SubmitAiErrorReportRequest(userNote="连续操作两次后复现"),
    )
    assert submitted["success"] is True
    assert submitted["data"]["status"] == "submitted"
    assert submitted["data"]["userNote"] == "连续操作两次后复现"
    assert submitted["data"]["submittedAt"]

    detail = await get_ai_error_report(report_id)
    listed = await list_ai_error_reports(status="submitted", limit=10)
    assert detail["data"]["id"] == report_id
    assert [item["id"] for item in listed["data"]] == [report_id]


async def test_error_report_schema_stores_references_not_conversation_content(
    report_db: DatabaseConnection,
):
    columns = {
        row["name"]
        for row in await report_db.fetch_all("PRAGMA table_info(ai_error_reports)")
    }
    assert {
        "stream_id",
        "agent_run_id",
        "session_id",
        "conversation_id",
        "error_code",
        "diagnostic_json",
    }.issubset(columns)
    assert {"prompt", "response", "chapter_text", "api_key"}.isdisjoint(columns)


async def test_error_report_derives_specific_failure_from_agent_run_events(
    report_db: DatabaseConnection,
):
    from infrastructure.persistence.run_store import append_event, create_run
    from routers.ai import capture_ai_error_report, get_ai_error_report

    run_id = await create_run(
        report_db,
        session_id=None,
        prompt="不得进入诊断的原始提示词",
        mode="agent",
    )
    await append_event(
        report_db,
        run_id,
        "tool.call_completed",
        {
            "index": 0,
            "toolCallId": "call-scope",
            "toolName": "getSourceCharacters",
            "outcome": "rejected",
            "errorCode": "tool_scope_violation",
        },
    )
    await append_event(
        report_db,
        run_id,
        "run.failed",
        {"status": "failed", "error": "tool_scope_violation"},
    )

    captured = await capture_ai_error_report(CaptureAiErrorReportRequest(
        streamId="screenplay-specific-diagnosis",
        agentRunId=run_id,
        source="screenplay_agent",
        errorCode="tool_scope_violation",
        errorMessage="Agent 运行过程中发生异常，已安全停止；请稍后重试。",
    ))
    diagnosis = captured["data"]["diagnostics"]

    assert diagnosis == {
        "failureStage": "tool_execution",
        "failureOutcome": "rejected",
        "failureTool": "getSourceCharacters",
        "failureToolCallId": "call-scope",
        "failureErrorCode": "tool_scope_violation",
        "failureReason": (
            "当前剧本项目只改编限定章节或卷；人物卡和世界设定属于"
            "整书级资料，无法证明全部位于改编范围内，因此该工具被"
            "项目作用域安全规则拒绝。"
        ),
    }

    # Historical reports whose stored JSON predates detailed diagnostics are
    # enriched from the durable Run event log when read.
    await report_db.execute(
        "UPDATE ai_error_reports SET diagnostic_json = '{}' WHERE id = ?",
        [captured["data"]["id"]],
    )
    detail = await get_ai_error_report(captured["data"]["id"])
    assert detail["data"]["diagnostics"] == diagnosis
    assert "原始提示词" not in str(detail["data"]["diagnostics"])


async def test_error_report_keeps_terminal_failure_distinct_from_prior_tool_error(
    report_db: DatabaseConnection,
):
    from infrastructure.persistence.run_store import append_event, create_run
    from infrastructure.persistence.error_report_store import capture_error_report

    run_id = await create_run(
        report_db,
        session_id=None,
        prompt="content-free",
        mode="agent",
    )
    await append_event(report_db, run_id, "tool.call_completed", {
        "toolCallId": "call-old",
        "toolName": "readWritingChapters",
        "outcome": "failed",
        "errorCode": "tool_input_invalid",
    })
    await append_event(report_db, run_id, "run.failed", {
        "status": "failed",
        "error": "max_model_rounds",
    })

    report = await capture_error_report(
        report_db,
        stream_id="terminal-distinct",
        agent_run_id=run_id,
        error_message="failed",
    )
    diagnosis = report["diagnostics"]

    assert diagnosis["failureErrorCode"] == "max_model_rounds"
    assert diagnosis["failureStage"] == "agent_runtime"
    assert diagnosis["lastToolFailureErrorCode"] == "tool_input_invalid"
    assert diagnosis["lastToolFailureTool"] == "readWritingChapters"
