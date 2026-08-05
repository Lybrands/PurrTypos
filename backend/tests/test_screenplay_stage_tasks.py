from domains.screenplay.stage_tasks import (
    build_screenplay_stage_planning_facts,
)


def _facts(stage: str, documents=(), **updates):
    values = {
        "stage": stage,
        "source_kind": "original",
        "screenplay_format": "电影",
        "source_book_bound": False,
        "source_scope_restricted": False,
        "documents": documents,
    }
    values.update(updates)
    return build_screenplay_stage_planning_facts(**values)


def test_source_orientation_exposes_outcome_without_tool_sequence():
    facts = _facts(
        "orientation",
        source_kind="book",
        source_book_bound=True,
        source_scope_restricted=True,
    )

    assert facts["planningMode"] == "dynamic-within-stage"
    assert facts["deliverable"] == "A reviewable source-range analysis."
    assert facts["sourceAvailable"] is True
    serialized = str(facts)
    assert "getSourceCoveragePlan" not in serialized
    assert "proposeSourceAnalysis" not in serialized
    assert "Do not create the adaptation creative brief" in serialized


def test_explicit_stage_task_requires_its_terminal_capability():
    orientation = _facts(
        "orientation",
        source_kind="book",
        source_book_bound=True,
        require_deliverable=True,
    )
    brief = _facts(
        "brief",
        source_kind="book",
        source_book_bound=True,
        require_deliverable=True,
    )
    structure = _facts(
        "structure",
        screenplay_format="竖屏短剧",
        require_deliverable=True,
    )
    scenes = _facts("scenes", require_deliverable=True)
    draft = _facts("draft", require_deliverable=True)

    assert orientation["stageDeliverableRequired"] is True
    assert orientation["completionCapabilities"] == [
        "finalizeSourceAnalysisProposal"
    ]
    assert orientation["taskAdmissionVocabulary"] == {
        "domainActions": ["generate_stage_deliverable"],
        "requiredForTools": {
            "finalizeSourceAnalysisProposal": "generate_stage_deliverable",
        },
        "scopes": ["current_stage"],
    }
    assert brief["completionCapabilities"] == [
        "finalizeCreativeBriefProposal"
    ]
    assert structure["completionCapabilities"] == [
        "finalizeScreenplayStructureProposal"
    ]
    assert scenes["completionCapabilities"] == ["finalizeSceneListProposal"]
    assert draft["completionCapabilities"] == ["proposeSceneDraft"]
    assert draft["modelOnlyPlanningFallbackAllowed"] is False


def test_draft_task_identifies_only_the_next_incomplete_scene():
    documents = [
        {
            "id": "scenes-v1",
            "kind": "scene_list",
            "status": "accepted",
            "version": 1,
            "content_json": {
                "scenes": [
                    {"id": "scene-1", "heading": "门外"},
                    {"id": "scene-2", "heading": "雨夜"},
                ],
            },
        },
        {
            "id": "draft-v1",
            "kind": "scene_draft",
            "status": "accepted",
            "version": 1,
            "content_json": {"completedSceneIds": ["scene-1"]},
        },
    ]

    facts = _facts("draft", documents=documents)

    assert facts["currentScene"] == {
        "id": "scene-2",
        "heading": "雨夜",
    }
    assert "exactly one newly completed scene" in facts["deliverable"]

    batch_facts = _facts(
        "draft",
        documents=documents,
        draft_scene_count=2,
    )
    assert batch_facts["requestedSceneCount"] == 1
    assert batch_facts["currentScenes"] == [{
        "id": "scene-2",
        "heading": "雨夜",
    }]


def test_episode_scope_includes_legacy_scene_appended_after_later_episode():
    documents = [
        {
            "id": "scenes-v1",
            "kind": "scene_list",
            "status": "accepted",
            "version": 1,
            "content_json": {
                "scenes": [
                    {"id": "s01", "order": 1, "episodeNumber": 1},
                    {"id": "s02", "order": 2, "episodeNumber": 2},
                    {"id": "s03", "order": 3, "episodeNumber": 3},
                    {"id": "s04-a", "order": 4, "episodeNumber": 4},
                    {"id": "s05", "order": 5, "episodeNumber": 5},
                    {"id": "s04-b", "order": 6, "episodeNumber": 4},
                ],
            },
        },
        {
            "id": "draft-v1",
            "kind": "scene_draft",
            "status": "accepted",
            "version": 1,
            "content_json": {"completedSceneIds": ["s01"]},
        },
    ]

    facts = _facts(
        "draft",
        documents=documents,
        screenplay_format="竖屏短剧",
        draft_scope="next_3_episodes",
    )

    assert [scene["id"] for scene in facts["currentScenes"]] == [
        "s02", "s03", "s04-a", "s04-b",
    ]


def test_multi_scene_draft_requires_a_planner_deliverable_plan():
    documents = [{
        "id": "scenes-v1",
        "kind": "scene_list",
        "status": "accepted",
        "version": 1,
        "content_json": {
            "scenes": [
                {"id": "scene-1"},
                {"id": "scene-2"},
                {"id": "scene-3"},
            ],
        },
    }]

    facts = _facts(
        "draft",
        documents=documents,
        draft_scene_count=2,
    )

    assert facts["requestedSceneCount"] == 2
    assert facts["stageDeliverableRequired"] is True
    assert facts["completionCapabilities"] == ["proposeSceneDraft"]
    assert facts["modelOnlyPlanningFallbackAllowed"] is False
    assert facts["durableExecutionPlan"]["requiredItemIds"] == [
        "scene-1",
        "scene-2",
    ]
    assert facts["durableExecutionPlan"]["generationUnitKind"] == (
        "scene_generation"
    )
    assert "batch size" in facts["durableExecutionPlan"]["rules"][0]


def test_draft_scope_is_resolved_from_current_accepted_episode_boundary():
    documents = [{
        "id": "scenes-v1",
        "kind": "scene_list",
        "status": "accepted",
        "version": 1,
        "content_json": {
            "scenes": [
                {"id": "s1", "heading": "一", "episodeNumber": 1},
                {"id": "s2", "heading": "二", "episodeNumber": 1},
                {"id": "s3", "heading": "三", "episodeNumber": 2},
                {"id": "s4", "heading": "四", "episodeNumber": 2},
                {"id": "s5", "heading": "五", "episodeNumber": 3},
                {"id": "s6", "heading": "六", "episodeNumber": 4},
            ],
        },
    }, {
        "id": "draft-v1",
        "kind": "scene_draft",
        "status": "accepted",
        "version": 1,
        "content_json": {"completedSceneIds": ["s1"]},
    }]

    next_episode = _facts(
        "draft",
        documents=documents,
        draft_scene_count=99,
        draft_scope="next_episode",
    )
    next_three = _facts(
        "draft",
        documents=documents,
        draft_scene_count=1,
        draft_scope="next_3_episodes",
    )

    assert [scene["id"] for scene in next_episode["currentScenes"]] == ["s2"]
    assert [scene["id"] for scene in next_three["currentScenes"]] == [
        "s2", "s3", "s4", "s5",
    ]
    assert next_three["requestedDraftScope"] == "next_3_episodes"
    assert any(
        "do not restate scene totals" in rule
        for rule in next_three["planningRules"]
    )


def test_review_task_switches_from_revision_to_re_review():
    review = {
        "id": "review-v1",
        "kind": "review",
        "status": "accepted",
        "version": 1,
        "content_json": {
            "reviewedDraftId": "draft-v1",
            "issues": [{"id": "issue-1"}],
        },
    }
    revision_facts = _facts(
        "review",
        require_deliverable=True,
        documents=[
            {
                "id": "draft-v1",
                "kind": "scene_draft",
                "status": "accepted",
                "version": 1,
                "content_json": {},
            },
            review,
        ],
    )
    re_review_facts = _facts(
        "review",
        require_deliverable=True,
        documents=[
            review,
            {
                "id": "draft-v2",
                "kind": "scene_draft",
                "status": "accepted",
                "version": 2,
                "content_json": {"reviewId": "review-v1"},
            },
        ],
    )

    assert revision_facts["reviewIssueCount"] == 1
    assert revision_facts["completionCapabilities"] == [
        "finalizeScreenplayRevisionProposal"
    ]
    assert "Revise the complete screenplay" in revision_facts["objective"]
    assert re_review_facts["previousReviewIssueCount"] == 1
    assert re_review_facts["completionCapabilities"] == [
        "finalizeScreenplayReviewProposal"
    ]
    assert "Re-review" in re_review_facts["objective"]
