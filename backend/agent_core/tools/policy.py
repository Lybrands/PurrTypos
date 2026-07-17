"""Business-agnostic outcome rules for tool policy and approvals."""

from __future__ import annotations

from collections.abc import Iterable

from agent_core.contracts import ApprovalStatus, ToolBatchOutcome


_OUTCOME_PRECEDENCE = {
    ToolBatchOutcome.COMPLETED: 0,
    ToolBatchOutcome.DECLINED: 1,
    ToolBatchOutcome.REJECTED: 2,
    ToolBatchOutcome.FAILED: 3,
    ToolBatchOutcome.CANCELED: 4,
}


def aggregate_outcomes(outcomes: Iterable[ToolBatchOutcome]) -> ToolBatchOutcome:
    values = tuple(ToolBatchOutcome(item) for item in outcomes)
    if not values:
        return ToolBatchOutcome.COMPLETED
    return max(values, key=_OUTCOME_PRECEDENCE.__getitem__)


def approval_outcome(status: ApprovalStatus) -> ToolBatchOutcome:
    value = ApprovalStatus(status)
    if value is ApprovalStatus.APPROVED:
        return ToolBatchOutcome.COMPLETED
    if value is ApprovalStatus.REJECTED:
        return ToolBatchOutcome.DECLINED
    if value is ApprovalStatus.CANCELED:
        return ToolBatchOutcome.CANCELED
    return ToolBatchOutcome.FAILED


def approval_error_code(status: ApprovalStatus) -> str | None:
    value = ApprovalStatus(status)
    if value is ApprovalStatus.APPROVED:
        return None
    if value is ApprovalStatus.REJECTED:
        return "approval_rejected"
    if value is ApprovalStatus.CANCELED:
        return "approval_canceled"
    if value is ApprovalStatus.TIMED_OUT:
        return "approval_timed_out"
    return "approval_unavailable"
