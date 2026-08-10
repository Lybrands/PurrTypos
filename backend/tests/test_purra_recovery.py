from __future__ import annotations

import pytest

from purra.recovery import (
    RecoveryAction,
    RecoveryCause,
    RecoveryEffectState,
    RecoveryLedger,
    RecoveryPolicy,
    RecoveryRequest,
)


def _request(**overrides) -> RecoveryRequest:
    values = {
        "cause": RecoveryCause.PROVIDER_STREAM_INTERRUPTED,
        "action": RecoveryAction.RETRY_MODEL,
        "remaining_model_rounds": 2,
    }
    values.update(overrides)
    return RecoveryRequest(**values)


def test_recovery_ledger_consumes_a_bounded_attempt_and_traces_no_content():
    ledger = RecoveryLedger()

    allowed = ledger.decide(_request())
    denied = ledger.decide(_request())

    assert allowed.allowed is True
    assert allowed.attempt == 1
    assert denied.allowed is False
    assert denied.reason_code == "attempt_budget_exhausted"
    assert denied.attempt == 1
    assert allowed.to_trace_details() == {
        "cause": "provider_stream_interrupted",
        "action": "retry_model",
        "scope": "run",
        "allowed": True,
        "reasonCode": "allowed",
        "attempt": 1,
        "maxAttempts": 1,
        "remainingModelRounds": 2,
        "minimumRemainingRounds": 1,
        "effectState": "not_started",
        "mayRepeatSideEffect": False,
    }


def test_recovery_policy_denies_cancellation_visible_output_and_round_exhaustion():
    assert RecoveryLedger().decide(_request(
        cancellation_requested=True,
    )).reason_code == "request_canceled"
    assert RecoveryLedger().decide(_request(
        visible_output_emitted=True,
    )).reason_code == "visible_output_already_emitted"
    assert RecoveryLedger().decide(_request(
        remaining_model_rounds=1,
        minimum_remaining_rounds=2,
    )).reason_code == "round_budget_exhausted"


def test_recovery_policy_never_replays_committed_or_unknown_side_effects():
    committed = RecoveryLedger().decide(_request(
        cause=RecoveryCause.TOOL_INPUT_INVALID,
        may_repeat_side_effect=True,
        effect_state=RecoveryEffectState.COMMITTED,
    ))
    unknown = RecoveryLedger().decide(_request(
        cause=RecoveryCause.TOOL_INPUT_INVALID,
        may_repeat_side_effect=True,
        effect_state=RecoveryEffectState.UNKNOWN,
    ))

    assert committed.reason_code == "side_effect_committed"
    assert unknown.reason_code == "side_effect_state_unknown"


def test_recovery_policy_is_profile_configurable_and_missing_rules_fail_closed():
    disabled = RecoveryPolicy().with_overrides({
        RecoveryCause.PROVIDER_STREAM_INTERRUPTED: 0,
    })
    decision = RecoveryLedger(disabled).decide(_request())

    assert decision.allowed is False
    assert decision.reason_code == "policy_disabled"


def test_recovery_policy_stores_one_immutable_attempt_mapping():
    policy = RecoveryPolicy()
    limits = policy.attempt_limits

    assert policy.attempt_limits is limits
    with pytest.raises(TypeError):
        limits[RecoveryCause.PROVIDER_STREAM_INTERRUPTED] = 0  # type: ignore[index]

    overridden = policy.with_overrides({
        RecoveryCause.PROVIDER_STREAM_INTERRUPTED: 0,
    })
    assert policy.max_attempts(RecoveryCause.PROVIDER_STREAM_INTERRUPTED) == 1
    assert overridden.max_attempts(RecoveryCause.PROVIDER_STREAM_INTERRUPTED) == 0

    with pytest.raises(ValueError, match="non-negative"):
        policy.with_overrides({RecoveryCause.PROVIDER_STREAM_INTERRUPTED: -1})
