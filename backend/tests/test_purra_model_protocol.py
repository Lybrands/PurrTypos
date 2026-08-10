from __future__ import annotations

import pytest

from purra.contracts import ModelFinishReason
from purra.model_protocol import classify_model_termination


@pytest.mark.parametrize(
    ("call_count", "expected_error"),
    [
        (0, "model_output_truncated"),
        (1, "tool_call_truncated"),
        (4, "tool_call_truncated"),
    ],
)
def test_length_is_always_incomplete_and_never_authorizes_tools(
    call_count,
    expected_error,
):
    result = classify_model_termination(
        ModelFinishReason.LENGTH,
        tool_call_count=call_count,
    )

    assert result.incomplete is True
    assert result.authorizes_tool_calls is False
    assert result.error_code == expected_error


@pytest.mark.parametrize(
    "finish_reason",
    [ModelFinishReason.TOOL_CALLS, ModelFinishReason.STOP],
)
def test_complete_compatible_finish_reasons_can_authorize_tools(finish_reason):
    result = classify_model_termination(
        finish_reason,
        tool_call_count=1,
    )

    assert result.incomplete is False
    assert result.authorizes_tool_calls is True
    assert result.error_code is None


def test_unknown_finish_reason_does_not_authorize_tools():
    result = classify_model_termination(
        ModelFinishReason.OTHER,
        tool_call_count=1,
    )

    assert result.incomplete is True
    assert result.authorizes_tool_calls is False
    assert result.retryable is False
    assert result.error_code == "unsupported_model_finish_reason"


def test_filtered_finish_is_incomplete_and_not_automatically_retryable():
    result = classify_model_termination(
        ModelFinishReason.FILTERED,
        tool_call_count=1,
    )

    assert result.incomplete is True
    assert result.authorizes_tool_calls is False
    assert result.retryable is False
    assert result.error_code == "model_output_filtered"
