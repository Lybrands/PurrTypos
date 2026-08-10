from domains.screenplay.project_aggregate import derive_stage, next_actions
from domains.screenplay.review_adjudication import derive_review_state


def test_incomplete_draft_keeps_the_project_in_draft():
    heads = {
        "creativeBrief": {},
        "structure": {},
        "sceneList": {},
        "screenplayDraft": {"isComplete": False},
    }

    stage = derive_stage(
        source_kind="original",
        head_roles=heads,
        head_contents=heads,
    )

    assert stage == "draft"
    assert next_actions(stage, head_contents=heads) == [{
        "type": "generateDeliverable",
        "targetRole": "screenplayDraft",
    }]


def test_agent_review_recommendation_does_not_select_revision_before_user_triage():
    heads = {
        "creativeBrief": {},
        "structure": {},
        "sceneList": {},
        "screenplayDraft": {"isComplete": True},
        "review": {"verdict": "revise"},
    }

    stage = derive_stage(
        source_kind="original",
        head_roles=heads,
        head_contents=heads,
    )

    assert stage == "review"
    assert next_actions(stage, head_contents=heads) == []


def test_ready_review_waits_for_explicit_user_finalization():
    heads = {
        "creativeBrief": {},
        "structure": {},
        "sceneList": {},
        "screenplayDraft": {"isComplete": True},
        "review": {"verdict": "ready"},
    }

    assert derive_stage(
        source_kind="original",
        head_roles=heads,
        head_contents=heads,
    ) == "review"
    assert derive_stage(
        source_kind="original",
        head_roles=heads,
        head_contents=heads,
        has_current_finalization=True,
    ) == "completed"


def test_review_findings_without_decisions_require_adjudication():
    state = derive_review_state(
        draft_revision_id="draft-v3",
        draft_content={"isComplete": True},
        review_revision_id="review-v2",
        review_content={
            "reviewedDraftId": "draft-v3",
            "inputContractVersion": 2,
            "verdict": "revise",
            "issues": [
                {
                    "id": "pace-1",
                    "severity": "major",
                    "description": "中段节奏偏慢",
                    "sceneIds": ["scene-4"],
                },
                {
                    "id": "dialogue-1",
                    "severity": "minor",
                    "description": "对白表达重复",
                    "sceneIds": ["scene-8"],
                },
            ],
        },
        decisions=[],
        hard_checks=[],
        completion_source=None,
    )

    assert state["phase"] == "adjudicating"
    assert state["counts"] == {
        "total": 2,
        "pending": 2,
        "planned": 0,
        "resolved": 0,
        "dismissed": 0,
        "riskAccepted": 0,
    }
    assert [finding["status"] for finding in state["findings"]] == [
        "pending",
        "pending",
    ]
    assert state["canFinalize"] is False


def test_review_execution_failure_blocks_finalization_without_creating_finding():
    state = derive_review_state(
        draft_revision_id="draft-v3",
        draft_content={"isComplete": True},
        review_revision_id="review-v2",
        review_content={
            "reviewedDraftId": "draft-v3",
            "inputContractVersion": 2,
            "verdict": "revise",
            "issues": [],
            "failedEpisodes": [{
                "episodeNumber": 2,
                "code": "model_output_truncated",
                "message": "第 2 集审阅失败",
                "retryable": True,
            }],
        },
        decisions=[],
        hard_checks=[],
        completion_source=None,
    )

    assert state["findings"] == []
    assert state["failedEpisodes"] == [{
        "episodeNumber": 2,
        "code": "model_output_truncated",
        "message": "第 2 集审阅失败",
        "retryable": True,
        "runId": None,
    }]
    assert state["canFinalize"] is False
    assert state["hardChecks"] == [{
        "code": "review_episode_failed",
        "message": "第 2 集审阅失败，需要重新审阅",
    }]
    assert state["nextAction"] == {
        "type": "generateDeliverable",
        "targetRole": "review",
    }


def test_unverified_review_input_is_not_exposed_as_content_finding():
    state = derive_review_state(
        draft_revision_id="draft-v3",
        draft_content={"isComplete": True},
        review_revision_id="legacy-unverified-review",
        review_content={
            "reviewedDraftId": "draft-v3",
            "verdict": "major_rework",
            "issues": [{
                "id": "missing-tool",
                "severity": "critical",
                "description": "当前环境未提供正文读取工具",
                "sceneIds": ["scene-1"],
            }],
        },
        decisions=[],
        hard_checks=[],
        completion_source=None,
    )

    assert state["findings"] == []
    assert state["canFinalize"] is False
    assert state["hardChecks"] == [{
        "code": "review_input_unverified",
        "message": "当前审阅报告没有可验证的正文输入，需要重新审阅",
    }]
    assert state["nextAction"] == {
        "type": "generateDeliverable",
        "targetRole": "review",
    }


def test_planned_findings_select_revision_after_all_findings_are_triaged():
    state = derive_review_state(
        draft_revision_id="draft-v3",
        draft_content={"isComplete": True},
        review_revision_id="review-v2",
        review_content={
            "reviewedDraftId": "draft-v3",
            "inputContractVersion": 2,
            "verdict": "major_rework",
            "issues": [
                {"id": "arc-1", "severity": "critical", "description": "人物弧光中断", "sceneIds": []},
                {"id": "pace-1", "severity": "major", "description": "节奏偏慢", "sceneIds": []},
            ],
        },
        decisions=[
            {"issueId": "arc-1", "status": "planned", "note": "进入下一版修订"},
            {"issueId": "pace-1", "status": "riskAccepted", "note": "保留当前节奏"},
        ],
        hard_checks=[],
        completion_source=None,
    )

    assert state["phase"] == "readyToRevise"
    assert state["counts"]["pending"] == 0
    assert state["counts"]["planned"] == 1
    assert state["canFinalize"] is False
    assert state["nextAction"] == {
        "type": "generateDeliverable",
        "targetRole": "screenplayDraft",
    }


def test_final_decisions_enable_but_do_not_perform_user_finalization():
    decisions = [
        {"issueId": f"issue-{index}", "status": "riskAccepted", "note": ""}
        for index in range(1, 6)
    ] + [{"issueId": "issue-6", "status": "dismissed", "note": "误报"}]
    state = derive_review_state(
        draft_revision_id="draft-v3",
        draft_content={"isComplete": True},
        review_revision_id="review-v2",
        review_content={
            "reviewedDraftId": "draft-v3",
            "inputContractVersion": 2,
            "verdict": "major_rework",
            "issues": [
                {
                    "id": f"issue-{index}",
                    "severity": "major",
                    "description": f"问题 {index}",
                    "sceneIds": [],
                }
                for index in range(1, 7)
            ],
        },
        decisions=decisions,
        hard_checks=[],
        completion_source=None,
    )

    assert state["phase"] == "readyToFinalize"
    assert state["counts"] == {
        "total": 6,
        "pending": 0,
        "planned": 0,
        "resolved": 0,
        "dismissed": 1,
        "riskAccepted": 5,
    }
    assert state["canFinalize"] is True
    assert state["nextAction"] == {"type": "finalizeProject"}

    completed = derive_review_state(
        draft_revision_id="draft-v3",
        draft_content={"isComplete": True},
        review_revision_id="review-v2",
        review_content={
            "reviewedDraftId": "draft-v3",
            "inputContractVersion": 2,
            "verdict": "major_rework",
            "issues": [
                {
                    "id": f"issue-{index}",
                    "severity": "major",
                    "description": f"问题 {index}",
                    "sceneIds": [],
                }
                for index in range(1, 7)
            ],
        },
        decisions=decisions,
        hard_checks=[],
        completion_source="user",
    )
    assert completed["phase"] == "completed"
    assert completed["nextAction"] is None
