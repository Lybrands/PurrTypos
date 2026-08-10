from __future__ import annotations

import pytest

from purra.contracts import ModelFinishReason, ReasoningMode
from purra.errors import UnsupportedModelFeatureError
from purra.model_protocol import (
    FeatureRequirement,
    FeatureSupport,
    ModelCapabilitySnapshot,
    ModelProtocolCapabilities,
    ReasoningControl,
    TaskCapabilityRequirements,
    ThinkingTokenAccounting,
    classify_model_termination,
    preflight_capabilities,
)


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
    assert result.termination == "length"
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
    assert result.termination == "completed"
    assert result.error_code is None


def test_unknown_finish_reason_does_not_authorize_tools():
    result = classify_model_termination(
        ModelFinishReason.OTHER,
        tool_call_count=1,
    )

    assert result.incomplete is True
    assert result.authorizes_tool_calls is False
    assert result.termination == "protocol_invalid"
    assert result.error_code == "unsupported_model_finish_reason"


def test_filtered_finish_is_incomplete_and_not_automatically_retryable():
    result = classify_model_termination(
        ModelFinishReason.FILTERED,
        tool_call_count=1,
    )

    assert result.incomplete is True
    assert result.authorizes_tool_calls is False
    assert result.termination == "provider_rejected"
    assert result.error_code == "model_output_filtered"


def _snapshot(
    *,
    reasoning_control=ReasoningControl.SELECTABLE,
    tool_calling=FeatureSupport.SUPPORTED,
    json_schema_level="json_object",
    streaming=FeatureSupport.SUPPORTED,
    cancellation=FeatureSupport.SUPPORTED,
    actionable=True,
):
    return ModelCapabilitySnapshot(
        schema_version=1,
        profile_id="test:profile",
        provider_protocol="test_protocol",
        context_window_tokens=200_000,
        max_output_tokens=100_000,
        thinking_token_accounting=ThinkingTokenAccounting.INCLUDED,
        protocol=ModelProtocolCapabilities(
            reasoning_control=reasoning_control,
            tool_calling=tool_calling,
            json_schema_level=json_schema_level,
            streaming=streaming,
            cancellation=cancellation,
        ),
        actionable=actionable,
    )


def _requirements(
    *,
    reasoning_mode=ReasoningMode.DEFAULT,
    tool_calling=FeatureRequirement.REQUIRED,
    structured_output_level="json_object",
    streaming_required=True,
    cancellation_required=True,
):
    return TaskCapabilityRequirements(
        reasoning_mode=reasoning_mode,
        tool_calling=tool_calling,
        structured_output_level=structured_output_level,
        streaming_required=streaming_required,
        cancellation_required=cancellation_required,
    )


@pytest.mark.parametrize(
    ("reasoning_control", "mode"),
    [
        (ReasoningControl.SELECTABLE, ReasoningMode.DEFAULT),
        (ReasoningControl.SELECTABLE, ReasoningMode.DISABLED),
        (ReasoningControl.ALWAYS_ENABLED, ReasoningMode.DEFAULT),
        (ReasoningControl.UNAVAILABLE, ReasoningMode.DISABLED),
    ],
)
def test_capability_preflight_accepts_supported_reasoning_modes(
    reasoning_control,
    mode,
):
    preflight_capabilities(
        _snapshot(reasoning_control=reasoning_control),
        _requirements(reasoning_mode=mode),
    )


@pytest.mark.parametrize(
    ("snapshot", "requirements"),
    [
        (
            _snapshot(reasoning_control=ReasoningControl.ALWAYS_ENABLED),
            _requirements(reasoning_mode=ReasoningMode.DISABLED),
        ),
        (
            _snapshot(reasoning_control=ReasoningControl.UNAVAILABLE),
            _requirements(reasoning_mode=ReasoningMode.DEFAULT),
        ),
        (
            _snapshot(tool_calling=FeatureSupport.UNKNOWN),
            _requirements(tool_calling=FeatureRequirement.REQUIRED),
        ),
        (
            _snapshot(json_schema_level="unknown"),
            _requirements(structured_output_level="json_object"),
        ),
        (
            _snapshot(streaming=FeatureSupport.UNAVAILABLE),
            _requirements(streaming_required=True),
        ),
        (
            _snapshot(cancellation=FeatureSupport.UNAVAILABLE),
            _requirements(cancellation_required=True),
        ),
        (
            _snapshot(actionable=False),
            _requirements(),
        ),
    ],
)
def test_capability_preflight_rejects_incompatible_tasks(snapshot, requirements):
    with pytest.raises(UnsupportedModelFeatureError) as captured:
        preflight_capabilities(snapshot, requirements)

    assert captured.value.code == "model_capability_incompatible"
    assert captured.value.retryable is False


def test_optional_tool_requirement_accepts_unknown_support():
    preflight_capabilities(
        _snapshot(tool_calling=FeatureSupport.UNKNOWN),
        _requirements(tool_calling=FeatureRequirement.OPTIONAL),
    )
