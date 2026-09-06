"""Typed recovery facts for screenplay execution boundaries.

PurrA decides how a durable failure is settled. This module owns the
screenplay-specific translation from stable domain error codes into the
provider-neutral facts consumed by that decision.
"""

from __future__ import annotations

from purra.errors import ModelGatewayError
from purra.recovery import (
    FailureCategory,
    FailureScope,
    FailureSignal,
    RecoveryEffectState,
)


_TRANSIENT_PROVIDER_CODES = frozenset({
    "upstream_stream_interrupted",
    "model_gateway_error",
    "provider_unavailable",
    "provider_rate_limited",
})

_MODEL_OUTPUT_CODES = frozenset({
    "model_output_truncated",
    "tool_call_truncated",
    "invalid_tool_arguments_json",
    "invalid_tool_arguments_schema",
    "invalid_tool_results",
    "max_model_rounds",
    "missing_required_tool_call",
    "unstructured_tool_protocol",
    "empty_model_response",
    "model_candidate_invalid",
    "candidate_validation_failed",
})

_PROTOCOL_CODES = frozenset({
    "provider_bad_request",
    "provider_reasoning_context_invalid",
    "unsupported_model_feature",
    "unsupported_model_finish_reason",
})

_PERMANENT_EXTERNAL_CODES = frozenset({
    "provider_authentication_failed",
    "provider_insufficient_balance",
    "model_output_filtered",
})

_TOOL_EXECUTION_CODES = frozenset({
    "tool_execution_failed",
    "candidate_commit_failed",
    "candidate_commit_missing",
})


def classify_screenplay_run_failure(error: object) -> FailureSignal:
    """Translate one failed screenplay attempt without choosing settlement."""

    code = _failure_code(error)
    declared_retryable = bool(getattr(error, "retryable", False))
    if code in _MODEL_OUTPUT_CODES:
        return FailureSignal(
            category=FailureCategory.MODEL_OUTPUT_INVALID,
            code=code,
            retryable=code not in {
                "model_output_truncated",
                "tool_call_truncated",
            },
        )
    if code == "tool_input_invalid":
        return FailureSignal(
            category=FailureCategory.TOOL_INPUT_INVALID,
            code=code,
            retryable=True,
        )
    if code in _TRANSIENT_PROVIDER_CODES:
        return FailureSignal(
            category=FailureCategory.TRANSIENT_PROVIDER,
            code=code,
            retryable=True,
        )
    if code in _PROTOCOL_CODES:
        return FailureSignal(
            category=FailureCategory.PROTOCOL_INCOMPATIBLE,
            code=code,
            retryable=False,
            scope=FailureScope.SYSTEMIC,
        )
    if code in _PERMANENT_EXTERNAL_CODES:
        return FailureSignal(
            category=FailureCategory.PERMANENT_EXTERNAL,
            code=code,
            retryable=False,
            scope=FailureScope.SYSTEMIC,
        )
    if code in _TOOL_EXECUTION_CODES:
        return FailureSignal(
            category=FailureCategory.TOOL_EXECUTION,
            code=code,
            retryable=declared_retryable,
            effect_state=RecoveryEffectState.UNKNOWN,
        )
    if isinstance(error, ModelGatewayError) and declared_retryable:
        return FailureSignal(
            category=FailureCategory.TRANSIENT_PROVIDER,
            code=code,
            retryable=True,
        )
    return FailureSignal(
        category=FailureCategory.BUSINESS_INVARIANT,
        code=code,
        retryable=False,
    )


def screenplay_failure_message(code: str) -> str:
    messages = {
        "model_output_truncated": (
            "模型本轮输出额度耗尽，未形成完整候选稿；不完整结果未被保存。"
            "请重试；若重复出现，请更换模型或减少本次生成的内容量。"
        ),
        "model_output_filtered": "模型输出被服务商安全策略中止，请调整要求后重试。",
        "upstream_stream_interrupted": "模型流式响应在完成前中断，请检查网络后重试。",
        "unsupported_model_finish_reason": "模型以不受支持的状态结束，请更换模型后重试。",
    }
    return messages.get(code, "剧本任务执行失败，请查看诊断信息后重试。")


def _failure_code(error: object) -> str:
    code = str(getattr(error, "code", "") or "").strip()
    if not code:
        code = str(error or "").strip()
    return (code or "screenplay_task_failed")[:240]


__all__ = [
    "classify_screenplay_run_failure",
    "screenplay_failure_message",
]
