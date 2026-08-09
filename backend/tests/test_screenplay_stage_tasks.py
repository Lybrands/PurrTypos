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


def test_explicit_stage_task_exposes_outcome_without_core_business_protocols():
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

    for facts in (orientation, brief, structure, scenes, draft):
        assert facts["requestedOutcome"] == "complete_current_stage_deliverable"
        assert "stageDeliverableRequired" not in facts
        assert "taskAdmissionVocabulary" not in facts
        assert "durableExecutionPlan" not in facts
        assert "modelOnlyPlanningFallbackAllowed" not in facts
    assert orientation["requestedCompletionCapability"] == (
        "analyzeSourceMaterial"
    )
    assert brief["requestedCompletionCapability"] == "generateCreativeBrief"
    assert structure["requestedCompletionCapability"] == (
        "generateScreenplayStructure"
    )
    assert scenes["requestedCompletionCapability"] == "generateSceneList"
    assert draft["requestedCompletionCapability"] == (
        "continueScreenplayDraft"
    )


def test_draft_task_identifies_only_the_next_incomplete_scene():
    scenes = [
        {"id": "scene-1", "heading": "门外"},
        {"id": "scene-2", "heading": "雨夜"},
    ]

    facts = _facts(
        "draft",
        draft_scenes=scenes,
        completed_scene_ids=("scene-1",),
    )

    assert facts["currentScene"] == {
        "id": "scene-2",
        "heading": "雨夜",
    }
    assert "exactly one newly completed scene" in facts["deliverable"]

    batch_facts = _facts(
        "draft",
        draft_scenes=scenes,
        completed_scene_ids=("scene-1",),
        draft_scene_count=2,
    )
    assert batch_facts["requestedSceneCount"] == 1
    assert batch_facts["currentScenes"] == [{
        "id": "scene-2",
        "heading": "雨夜",
    }]


def test_multi_scene_draft_exposes_semantics_but_not_an_execution_graph():
    facts = _facts(
        "draft",
        draft_scenes=tuple(
            {"id": f"scene-{index}"} for index in range(1, 4)
        ),
        draft_scene_count=2,
    )

    assert facts["requestedSceneCount"] == 2
    assert facts["draftIntentSchema"]["capability"] == (
        "continueScreenplayDraft"
    )
    assert "all_remaining" in facts["draftIntentSchema"]["scopes"]
    assert "durableExecutionPlan" not in facts
    assert "executionUnits" not in str(facts)


def test_delegated_draft_context_uses_host_bound_scene_ids():
    facts = _facts(
        "draft",
        draft_scenes=(
            {"id": "s08-01", "order": 1, "episodeNumber": 8},
            {"id": "s09-01", "order": 2, "episodeNumber": 9},
            {"id": "s10-01", "order": 3, "episodeNumber": 10},
        ),
        bound_draft_scene_ids=("s09-01",),
    )

    assert [scene["id"] for scene in facts["currentScenes"]] == [
        "s09-01",
    ]
    assert facts["draftPosition"]["selectedEpisodeNumbers"] == [9]
    assert facts["hostBoundSceneIds"] == ["s09-01"]


def test_draft_scope_is_resolved_from_current_accepted_episode_boundary():
    scenes = [
        {"id": "s1", "heading": "一", "episodeNumber": 1},
        {"id": "s2", "heading": "二", "episodeNumber": 1},
        {"id": "s3", "heading": "三", "episodeNumber": 2},
        {"id": "s4", "heading": "四", "episodeNumber": 2},
        {"id": "s5", "heading": "五", "episodeNumber": 3},
        {"id": "s6", "heading": "六", "episodeNumber": 4},
    ]

    next_episode = _facts(
        "draft",
        draft_scenes=scenes,
        completed_scene_ids=("s1",),
        draft_scene_count=99,
        draft_scope="next_episode",
    )
    next_three = _facts(
        "draft",
        draft_scenes=scenes,
        completed_scene_ids=("s1",),
        draft_scene_count=1,
        draft_scope="next_3_episodes",
    )
    next_four = _facts(
        "draft",
        draft_scenes=scenes,
        completed_scene_ids=("s1",),
        draft_scene_count=1,
        draft_scope="next_4_episodes",
    )

    assert [scene["id"] for scene in next_episode["currentScenes"]] == ["s2"]
    assert [scene["id"] for scene in next_three["currentScenes"]] == [
        "s2", "s3", "s4", "s5",
    ]
    assert next_three["requestedDraftScope"] == "next_3_episodes"
    assert [scene["id"] for scene in next_four["currentScenes"]] == [
        "s2", "s3", "s4", "s5", "s6",
    ]
    assert next_four["requestedDraftScope"] == "next_4_episodes"
    assert any(
        "do not restate scene totals" in rule
        for rule in next_three["planningRules"]
    )
    assert any(
        "execute in parallel" in rule
        and "AI-visible plan" in rule
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
            "issueCount": 1,
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
    assert revision_facts["requestedOutcome"] == (
        "complete_current_stage_deliverable"
    )
    assert "Revise the complete screenplay" in revision_facts["objective"]
    assert re_review_facts["previousReviewIssueCount"] == 1
    assert re_review_facts["requestedOutcome"] == (
        "complete_current_stage_deliverable"
    )
    assert "Re-review" in re_review_facts["objective"]
