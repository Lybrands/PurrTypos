"""Pure screenplay review-adjudication and finalization projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


FINAL_FINDING_STATUSES = frozenset({
    "resolved",
    "dismissed",
    "riskAccepted",
})
FINDING_STATUSES = frozenset({
    "pending",
    "planned",
    *FINAL_FINDING_STATUSES,
})


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _decisions_by_issue(
    values: Sequence[Mapping[str, object]],
) -> dict[str, dict[str, Any]]:
    decisions: dict[str, dict[str, Any]] = {}
    for raw in values:
        item = _mapping(raw)
        issue_id = str(item.get("issueId") or "").strip()
        status = str(item.get("status") or "pending").strip()
        if not issue_id or status not in FINDING_STATUSES:
            continue
        decisions[issue_id] = {
            "status": status,
            "note": str(item.get("note") or ""),
            "actor": item.get("actor"),
            "decidedAt": item.get("decidedAt"),
        }
    return decisions


def _hard_check(value: Mapping[str, object]) -> dict[str, str]:
    return {
        "code": str(value.get("code") or "review_validation_failed"),
        "message": str(value.get("message") or "当前剧本未通过定稿校验"),
    }


def derive_review_state(
    *,
    draft_revision_id: str | None,
    draft_content: Mapping[str, object] | None,
    review_revision_id: str | None,
    review_content: Mapping[str, object] | None,
    decisions: Sequence[Mapping[str, object]],
    hard_checks: Sequence[Mapping[str, object]],
    completion_source: str | None,
) -> dict[str, object]:
    """Build the authoritative review phase without mutating Agent output."""

    normalized_draft_id = str(draft_revision_id or "").strip() or None
    normalized_review_id = str(review_revision_id or "").strip() or None
    draft = _mapping(draft_content)
    review = _mapping(review_content)
    checks = [_hard_check(value) for value in hard_checks]
    completion = (
        str(completion_source).strip()
        if str(completion_source or "").strip() in {"user", "legacyAgentVerdict"}
        else None
    )
    try:
        input_contract_version = int(review.get("inputContractVersion") or 0)
    except (TypeError, ValueError):
        input_contract_version = 0
    contract_verified = bool(
        not normalized_review_id
        or input_contract_version >= 2
        or completion == "legacyAgentVerdict"
    )
    execution_contaminated = bool(
        normalized_review_id
        and any(
            key in review
            for key in (
                "error",
                "errorCode",
                "executionError",
                "failedEpisodes",
                "failure",
                "failureCode",
            )
        )
    )
    input_verified = contract_verified and not execution_contaminated
    recommendation = (
        str(review.get("verdict") or "").strip() or None
        if input_verified else None
    )
    if execution_contaminated:
        checks.append({
            "code": "review_execution_contaminated",
            "message": "当前审阅报告混入了执行故障，需要重新审阅",
        })
    elif normalized_review_id and not contract_verified:
        checks.append({
            "code": "review_input_unverified",
            "message": "当前审阅报告没有可验证的正文输入，需要重新审阅",
        })

    if normalized_draft_id and draft.get("isComplete") is not True:
        checks.append({
            "code": "draft_incomplete",
            "message": "当前剧本正文尚未完整生成",
        })
    reviewed_draft_id = str(review.get("reviewedDraftId") or "").strip()
    if (
        normalized_review_id
        and normalized_draft_id
        and reviewed_draft_id != normalized_draft_id
    ):
        checks.append({
            "code": "review_stale",
            "message": "当前审阅报告对应的不是当前剧本版本",
        })

    decision_by_issue = _decisions_by_issue(decisions)
    findings: list[dict[str, object]] = []
    for raw_issue in review.get("issues", []) if input_verified else []:
        if not isinstance(raw_issue, Mapping):
            continue
        issue = dict(raw_issue)
        issue_id = str(issue.get("id") or "").strip()
        if not issue_id:
            continue
        decision = decision_by_issue.get(issue_id, {})
        findings.append({
            "id": issue_id,
            "severity": str(issue.get("severity") or "minor"),
            "description": str(issue.get("description") or ""),
            "sceneIds": [
                str(scene_id)
                for scene_id in issue.get("sceneIds", [])
            ] if isinstance(issue.get("sceneIds"), list) else [],
            "status": str(decision.get("status") or "pending"),
            "note": str(decision.get("note") or ""),
            "actor": decision.get("actor"),
            "decidedAt": decision.get("decidedAt"),
        })

    statuses = [str(finding["status"]) for finding in findings]
    counts = {
        "total": len(findings),
        "pending": statuses.count("pending"),
        "planned": statuses.count("planned"),
        "resolved": statuses.count("resolved"),
        "dismissed": statuses.count("dismissed"),
        "riskAccepted": statuses.count("riskAccepted"),
    }
    next_action: dict[str, str] | None = None
    if not normalized_review_id:
        phase = "awaitingReview"
        next_action = {
            "type": "generateDeliverable",
            "targetRole": "review",
        }
    elif completion is not None:
        phase = "completed"
    elif not input_verified:
        phase = "awaitingReview"
        next_action = {
            "type": "generateDeliverable",
            "targetRole": "review",
        }
    elif counts["pending"] > 0:
        phase = "adjudicating"
    elif counts["planned"] > 0:
        phase = "readyToRevise"
        next_action = {
            "type": "generateDeliverable",
            "targetRole": "screenplayDraft",
        }
    else:
        phase = "readyToFinalize"
        next_action = {"type": "finalizeProject"}

    can_finalize = bool(
        normalized_draft_id
        and normalized_review_id
        and not checks
        and counts["pending"] == 0
        and counts["planned"] == 0
        and completion is None
    )
    if phase == "readyToFinalize" and not can_finalize:
        next_action = None

    return {
        "phase": phase,
        "draftRevisionId": normalized_draft_id,
        "reviewRevisionId": normalized_review_id,
        "recommendation": recommendation,
        "findings": findings,
        "counts": counts,
        "hardChecks": checks,
        "canFinalize": can_finalize,
        "completionSource": completion,
        "nextAction": next_action,
    }


__all__ = [
    "FINAL_FINDING_STATUSES",
    "FINDING_STATUSES",
    "derive_review_state",
]
