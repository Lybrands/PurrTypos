from __future__ import annotations

from agent_core.contracts import AgentRunResult, RunStatus
from application.sse_mapping import core_update_to_sse_chunk


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
