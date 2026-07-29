import pytest

from domains.screenplay.review_trace import (
    build_revision_trace,
    normalize_review_issues,
    normalize_review_verifications,
)


def _scene_list() -> dict:
    return {
        "scenes": [{
            "id": "scene-1",
            "structureUnitIds": ["beat-1"],
        }, {
            "id": "scene-2",
            "structureUnitIds": ["beat-2"],
        }],
    }


def _execution(scene_id: str, suffix: str = "旧版") -> dict:
    beat_id = "beat-1" if scene_id == "scene-1" else "beat-2"
    return {
        "sceneId": scene_id,
        "structureUnitIds": [beat_id],
        "objectiveResult": f"{suffix}目标结果",
        "conflictResult": f"{suffix}冲突结果",
        "turnResult": f"{suffix}转折结果",
        "continuityState": f"{suffix}连续性状态",
        "unresolvedNotes": [],
    }


def _issue() -> dict:
    return {
        "id": "issue-1",
        "severity": "major",
        "category": "structure",
        "sceneIds": ["scene-1"],
        "executionFields": ["turnResult", "continuityState"],
        "problem": "转折没有改变后续行动。",
        "recommendation": "让新信息迫使角色改变目标。",
        "acceptanceCriteria": "场尾产生可在下一场延续的新行动目标。",
    }


def test_review_issue_requires_execution_field_and_acceptance_criteria():
    issue = _issue()
    issue.pop("executionFields")

    with pytest.raises(ValueError, match="执行字段"):
        normalize_review_issues(
            issues=[issue],
            completed_scene_ids=["scene-1"],
            scene_executions=[_execution("scene-1")],
        )


def test_review_issue_must_target_scene_with_execution_record():
    issue = {**_issue(), "sceneIds": ["scene-2"]}

    with pytest.raises(ValueError, match="已有执行记录"):
        normalize_review_issues(
            issues=[issue],
            completed_scene_ids=["scene-1", "scene-2"],
            scene_executions=[_execution("scene-1")],
        )


def test_revision_reassesses_affected_scene_and_preserves_other_scene():
    resolutions, executions, reassessed = build_revision_trace(
        scene_list_content=_scene_list(),
        completed_scene_ids=["scene-1", "scene-2"],
        previous_executions=[
            _execution("scene-1"),
            _execution("scene-2"),
        ],
        review_issues=[_issue()],
        issue_resolutions=[{
            "issueId": "issue-1",
            "status": "resolved",
            "resolutionEvidence": "场尾新增追踪来电者的行动。",
        }],
        execution_updates=[_execution("scene-1", "修订后")],
    )

    assert resolutions[0]["sceneIds"] == ["scene-1"]
    assert executions[0]["turnResult"] == "修订后转折结果"
    assert executions[1] == _execution("scene-2")
    assert reassessed == ["scene-1"]


def test_revision_must_reassess_every_issue_scene():
    issue = {**_issue(), "sceneIds": ["scene-1", "scene-2"]}

    with pytest.raises(ValueError, match="全部场景"):
        build_revision_trace(
            scene_list_content=_scene_list(),
            completed_scene_ids=["scene-1", "scene-2"],
            previous_executions=[
                _execution("scene-1"),
                _execution("scene-2"),
            ],
            review_issues=[issue],
            issue_resolutions=[{
                "issueId": "issue-1",
                "status": "partially_resolved",
                "resolutionEvidence": "只修订了第一场。",
            }],
            execution_updates=[_execution("scene-1", "修订后")],
        )


def test_rereview_normalizes_verification_against_prior_acceptance_criteria():
    results = normalize_review_verifications(
        previous_review_issues=[_issue()],
        issue_resolutions=[{
            "issueId": "issue-1",
            "status": "resolved",
            "resolutionEvidence": "新增明确行动目标。",
        }],
        verification_results=[{
            "issueId": "issue-1",
            "status": "verified",
            "verificationEvidence": "下一场开头延续了追踪行动。",
        }],
    )

    assert results == [{
        "issueId": "issue-1",
        "status": "verified",
        "sceneIds": ["scene-1"],
        "executionFields": ["turnResult", "continuityState"],
        "acceptanceCriteria": "场尾产生可在下一场延续的新行动目标。",
        "priorResolutionStatus": "resolved",
        "resolutionEvidence": "新增明确行动目标。",
        "verificationEvidence": "下一场开头延续了追踪行动。",
    }]


def test_rereview_must_verify_every_prior_issue():
    with pytest.raises(ValueError, match="全部问题"):
        normalize_review_verifications(
            previous_review_issues=[
                _issue(),
                {**_issue(), "id": "issue-2"},
            ],
            issue_resolutions=[{
                "issueId": "issue-1",
                "status": "resolved",
                "resolutionEvidence": "修订一。",
            }, {
                "issueId": "issue-2",
                "status": "resolved",
                "resolutionEvidence": "修订二。",
            }],
            verification_results=[{
                "issueId": "issue-1",
                "status": "verified",
                "verificationEvidence": "核验一。",
            }],
        )
