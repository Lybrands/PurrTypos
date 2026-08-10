from __future__ import annotations

from purra.contracts import AgentRunResult, RunStatus
from purra.events import AgentEvent, CoreEventType
from application.sse_mapping import core_update_to_sse_chunk


def test_long_task_dispatch_receipt_is_metadata_not_model_output():
    chunk = core_update_to_sse_chunk(
        AgentEvent(
            type=CoreEventType.LONG_TASK_DISPATCHED,
            run_id="run-long",
            payload={
                "taskId": "task-1",
                "kind": "screenplay_draft_generation",
                "message": "已创建长篇正文任务。",
            },
        ),
        model="model",
    )

    assert chunk == {
        "longTaskDispatched": {
            "runId": "run-long",
            "taskId": "task-1",
            "kind": "screenplay_draft_generation",
            "message": "已创建长篇正文任务。",
        },
    }


def test_deterministic_terminal_response_remains_visible_without_model_delta():
    chunk = core_update_to_sse_chunk(
        AgentEvent(
            type=CoreEventType.RUN_COMPLETED,
            run_id="run-rejected",
            payload={
                "status": "done",
                "final_response": "当前阶段已经变化，请刷新后重试。",
            },
        ),
        model="model",
    )

    assert chunk == {
        "agentRunCompleted": {
            "runId": "run-rejected",
            "status": "done",
            "final_response": "当前阶段已经变化，请刷新后重试。",
            "finalResponse": "当前阶段已经变化，请刷新后重试。",
        },
    }


def test_composed_response_constraint_failure_has_actionable_user_message():
    chunk = core_update_to_sse_chunk(
        AgentRunResult(
            run_id="run-constraint",
            status=RunStatus.FAILED,
            error="response_constraint_violation",
        ),
        model="model",
    )

    assert chunk == {
        "error": (
            "模型两次生成的回答都未满足当前写作要求，未展示不合规内容。"
            "请缩小任务范围、明确输出格式后重试。"
        ),
    }


def test_semantic_judge_failures_never_fall_back_to_a_generic_error():
    assert core_update_to_sse_chunk(
        AgentRunResult(
            run_id="run-judge",
            status=RunStatus.FAILED,
            error="response_judge_error",
        ),
        model="model",
    ) == {
        "error": "语义校验暂时无法完成，候选回答未展示；请稍后重试。",
    }
    assert core_update_to_sse_chunk(
        AgentRunResult(
            run_id="run-judge-contract",
            status=RunStatus.FAILED,
            error="response_judge_contract_violation",
        ),
        model="model",
    ) == {
        "error": "语义校验返回了无效结果，候选回答未展示；请稍后重试。",
    }


def test_reasoning_only_failure_explains_that_no_visible_answer_was_returned():
    assert core_update_to_sse_chunk(
        AgentRunResult(
            run_id="run-empty-response",
            status=RunStatus.FAILED,
            error="empty_model_response",
        ),
        model="model",
    ) == {
        "error": "模型多次只返回内部推理，没有生成可展示的答复。请重试或更换模型。",
    }


def test_deferred_action_failure_explains_that_incomplete_text_was_withheld():
    assert core_update_to_sse_chunk(
        AgentRunResult(
            run_id="run-incomplete-response",
            status=RunStatus.FAILED,
            error="incomplete_model_response",
        ),
        model="model",
    ) == {
        "error": (
            "模型多次只说明准备执行的步骤，没有真正完成当前回答。"
            "未展示不完整内容，请重试或更换模型。"
        ),
    }


def test_incomplete_model_terminations_have_specific_safe_messages():
    cases = {
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
    }

    for error_code, expected_message in cases.items():
        assert core_update_to_sse_chunk(
            AgentRunResult(
                run_id=f"run-{error_code}",
                status=RunStatus.FAILED,
                error=error_code,
            ),
            model="model",
        ) == {"error": expected_message}
