"""Run-scoped, provider-neutral recovery policy and attempt accounting."""

from purra.recovery.contracts import (
    FailureCategory,
    FailureDecision,
    FailureDisposition,
    FailureSignal,
    FailureScope,
    RecoveryAction,
    RecoveryCause,
    RecoveryDecision,
    RecoveryEffectState,
    RecoveryReason,
    RecoveryRequest,
)
from purra.recovery.disposition import decide_failure
from purra.recovery.ledger import RecoveryLedger
from purra.recovery.policy import RecoveryPolicy

__all__ = [
    "FailureCategory",
    "FailureDecision",
    "FailureDisposition",
    "FailureSignal",
    "FailureScope",
    "RecoveryAction",
    "RecoveryCause",
    "RecoveryDecision",
    "RecoveryEffectState",
    "RecoveryLedger",
    "RecoveryPolicy",
    "RecoveryReason",
    "RecoveryRequest",
    "decide_failure",
]
