"""Validate screenplay review findings and revision evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from domains.screenplay.scene_execution import (
    normalize_scene_execution,
    validate_scene_execution_history,
)


REVIEW_EXECUTION_FIELDS = frozenset({
    "objectiveResult",
    "conflictResult",
    "turnResult",
    "continuityState",
    "unresolvedNotes",
})
REVIEW_SEVERITIES = frozenset({"critical", "major", "minor"})
REVIEW_CATEGORIES = frozenset({
    "continuity",
    "character",
    "structure",
    "pacing",
    "dialogue",
    "format",
})
REVISION_RESOLUTION_STATUSES = frozenset({
    "resolved",
    "partially_resolved",
})
REVIEW_VERIFICATION_STATUSES = frozenset({
    "verified",
    "still_open",
    "regressed",
})


def normalize_review_issues(
    *,
    issues: object,
    completed_scene_ids: object,
    scene_executions: object,
) -> list[dict[str, Any]]:
    if not _is_list(issues):
        raise ValueError("审阅报告必须包含结构化 issues")
    completed_ids = _string_list(completed_scene_ids)
    execution_ids = {
        str(item.get("sceneId") or "").strip()
        for item in scene_executions
        if isinstance(item, Mapping)
        and str(item.get("sceneId") or "").strip()
    } if _is_list(scene_executions) else set()
    valid_scene_ids = set(completed_ids) & execution_ids
    normalized: list[dict[str, Any]] = []
    seen_issue_ids: set[str] = set()
    for issue in issues:
        if not isinstance(issue, Mapping):
            raise ValueError("每个审阅问题都必须是结构化对象")
        issue_id = str(issue.get("id") or "").strip()
        if not issue_id:
            raise ValueError("每个审阅问题都必须有稳定 id")
        if issue_id in seen_issue_ids:
            raise ValueError("审阅问题 id 不能重复")
        seen_issue_ids.add(issue_id)
        severity = str(issue.get("severity") or "").strip()
        if severity not in REVIEW_SEVERITIES:
            raise ValueError("审阅问题必须声明有效严重程度")
        category = str(issue.get("category") or "").strip()
        if category not in REVIEW_CATEGORIES:
            raise ValueError("审阅问题必须声明有效类别")
        scene_ids = _string_list(issue.get("sceneIds"))
        if not scene_ids:
            raise ValueError("每个审阅问题必须定位至少一个场景")
        if not set(scene_ids).issubset(valid_scene_ids):
            raise ValueError("审阅问题只能引用已有执行记录的已完成场景")
        execution_fields = _string_list(issue.get("executionFields"))
        if not execution_fields:
            raise ValueError("每个审阅问题必须关联至少一个场景执行字段")
        if not set(execution_fields).issubset(REVIEW_EXECUTION_FIELDS):
            raise ValueError("审阅问题包含无效的场景执行字段")
        problem = str(issue.get("problem") or "").strip()
        recommendation = str(issue.get("recommendation") or "").strip()
        acceptance_criteria = str(
            issue.get("acceptanceCriteria") or ""
        ).strip()
        if not problem or not recommendation or not acceptance_criteria:
            raise ValueError("审阅问题必须包含问题、修改建议和验收标准")
        normalized.append({
            "id": issue_id,
            "severity": severity,
            "category": category,
            "sceneIds": scene_ids,
            "executionFields": execution_fields,
            "problem": problem,
            "recommendation": recommendation,
            "acceptanceCriteria": acceptance_criteria,
        })
    return normalized


def build_revision_trace(
    *,
    scene_list_content: Mapping[str, Any],
    completed_scene_ids: object,
    previous_executions: object,
    review_issues: object,
    issue_resolutions: object,
    execution_updates: object,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    baseline = validate_scene_execution_history(
        scene_list_content=scene_list_content,
        completed_scene_ids=completed_scene_ids,
        scene_executions=previous_executions,
    )
    if not _is_list(review_issues) or not review_issues:
        raise ValueError("当前审阅报告没有可供修订的问题")
    issue_map = {
        str(issue.get("id") or "").strip(): issue
        for issue in review_issues
        if isinstance(issue, Mapping)
        and str(issue.get("id") or "").strip()
    }
    if len(issue_map) != len(review_issues):
        raise ValueError("当前审阅报告包含无效或重复的问题")
    if not _is_list(issue_resolutions) or not issue_resolutions:
        raise ValueError("修订稿必须逐项提交 issueResolutions")
    normalized_resolutions: list[dict[str, Any]] = []
    seen_resolution_ids: set[str] = set()
    required_scene_ids: set[str] = set()
    for resolution in issue_resolutions:
        if not isinstance(resolution, Mapping):
            raise ValueError("每项问题解决记录都必须是结构化对象")
        issue_id = str(resolution.get("issueId") or "").strip()
        issue = issue_map.get(issue_id)
        if issue is None:
            raise ValueError("issueResolutions 只能引用当前已接受审阅的问题")
        if issue_id in seen_resolution_ids:
            raise ValueError("同一审阅问题不能重复提交解决记录")
        seen_resolution_ids.add(issue_id)
        status = str(resolution.get("status") or "").strip()
        if status not in REVISION_RESOLUTION_STATUSES:
            raise ValueError("问题解决状态必须是 resolved 或 partially_resolved")
        evidence = str(resolution.get("resolutionEvidence") or "").strip()
        if not evidence:
            raise ValueError("每项问题解决记录必须提供正文修改证据")
        scene_ids = _string_list(issue.get("sceneIds"))
        execution_fields = _string_list(issue.get("executionFields"))
        required_scene_ids.update(scene_ids)
        normalized_resolutions.append({
            "issueId": issue_id,
            "status": status,
            "sceneIds": scene_ids,
            "executionFields": execution_fields,
            "resolutionEvidence": evidence,
        })
    if seen_resolution_ids != set(issue_map):
        raise ValueError("修订稿必须回应当前审阅报告中的全部问题")

    if not _is_list(execution_updates) or not execution_updates:
        raise ValueError("修订稿必须重评受影响场景的 executionUpdates")
    raw_scenes = scene_list_content.get("scenes")
    if not _is_list(raw_scenes):
        raise ValueError("当前场景表缺少结构化场景")
    scenes = {
        str(scene.get("id") or "").strip(): scene
        for scene in raw_scenes
        if isinstance(scene, Mapping)
        and str(scene.get("id") or "").strip()
    }
    completed_ids = _string_list(completed_scene_ids)
    update_map: dict[str, dict[str, Any]] = {}
    for update in execution_updates:
        if not isinstance(update, Mapping):
            raise ValueError("每项场景重评都必须是结构化对象")
        scene_id = str(update.get("sceneId") or "").strip()
        if scene_id in update_map:
            raise ValueError("同一场景不能重复提交执行重评")
        if scene_id not in completed_ids or scene_id not in scenes:
            raise ValueError("场景执行重评只能引用当前完整剧本中的场景")
        update_map[scene_id] = normalize_scene_execution(
            scene=scenes[scene_id],
            execution=update,
        )
    if not required_scene_ids.issubset(update_map):
        raise ValueError("修订稿必须重评每个审阅问题关联的全部场景")

    merged = [
        update_map.get(str(execution.get("sceneId") or "").strip(), execution)
        for execution in baseline
    ]
    normalized_executions = validate_scene_execution_history(
        scene_list_content=scene_list_content,
        completed_scene_ids=completed_ids,
        scene_executions=merged,
    )
    reassessed_scene_ids = [
        scene_id for scene_id in completed_ids if scene_id in update_map
    ]
    return normalized_resolutions, normalized_executions, reassessed_scene_ids


def normalize_review_verifications(
    *,
    previous_review_issues: object,
    issue_resolutions: object,
    verification_results: object,
) -> list[dict[str, Any]]:
    if not _is_list(previous_review_issues) or not previous_review_issues:
        raise ValueError("复审引用的上一轮审阅没有结构化问题")
    previous_issue_map = {
        str(issue.get("id") or "").strip(): issue
        for issue in previous_review_issues
        if isinstance(issue, Mapping)
        and str(issue.get("id") or "").strip()
    }
    if len(previous_issue_map) != len(previous_review_issues):
        raise ValueError("复审引用的上一轮审阅问题无效或重复")
    if not _is_list(issue_resolutions) or not issue_resolutions:
        raise ValueError("当前修订稿缺少逐项问题解决记录")
    resolution_map = {
        str(item.get("issueId") or "").strip(): item
        for item in issue_resolutions
        if isinstance(item, Mapping)
        and str(item.get("issueId") or "").strip()
    }
    if set(resolution_map) != set(previous_issue_map):
        raise ValueError("当前修订稿的问题解决记录与上一轮审阅不一致")
    if not _is_list(verification_results) or not verification_results:
        raise ValueError("复审必须逐项提交 verificationResults")
    raw_verification_map: dict[str, Mapping[str, Any]] = {}
    for result in verification_results:
        if not isinstance(result, Mapping):
            raise ValueError("每项复审核验都必须是结构化对象")
        issue_id = str(result.get("issueId") or "").strip()
        if issue_id not in previous_issue_map:
            raise ValueError("verificationResults 只能引用上一轮审阅问题")
        if issue_id in raw_verification_map:
            raise ValueError("同一问题不能重复提交复审核验")
        raw_verification_map[issue_id] = result
    if set(raw_verification_map) != set(previous_issue_map):
        raise ValueError("复审必须核验上一轮审阅中的全部问题")

    normalized: list[dict[str, Any]] = []
    for issue in previous_review_issues:
        issue_id = str(issue.get("id") or "").strip()
        resolution = resolution_map[issue_id]
        result = raw_verification_map[issue_id]
        status = str(result.get("status") or "").strip()
        if status not in REVIEW_VERIFICATION_STATUSES:
            raise ValueError(
                "复审核验状态必须是 verified、still_open 或 regressed"
            )
        evidence = str(result.get("verificationEvidence") or "").strip()
        if not evidence:
            raise ValueError("每项复审核验必须提供正文核验证据")
        normalized.append({
            "issueId": issue_id,
            "status": status,
            "sceneIds": _string_list(issue.get("sceneIds")),
            "executionFields": _string_list(issue.get("executionFields")),
            "acceptanceCriteria": str(
                issue.get("acceptanceCriteria") or ""
            ).strip(),
            "priorResolutionStatus": str(
                resolution.get("status") or ""
            ).strip(),
            "resolutionEvidence": str(
                resolution.get("resolutionEvidence") or ""
            ).strip(),
            "verificationEvidence": evidence,
        })
    return normalized


def _string_list(value: object) -> list[str]:
    if not _is_list(value):
        return []
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item).strip()
    ))


def _is_list(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
