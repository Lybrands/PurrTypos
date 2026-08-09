from __future__ import annotations

import pytest

from domains.screenplay.workflow_compiler import (
    ScreenplayDraftUnitKind,
    build_screenplay_draft_workflow,
)


def test_host_workflow_checkpoints_each_scene_and_reviews_the_whole_range():
    scenes = [
        {"id": "s01-a", "heading": "第一集第一场", "episodeNumber": 1},
        {"id": "s01-b", "heading": "第一集第二场", "episodeNumber": 1},
        {"id": "s02-a", "heading": "第二集第一场", "episodeNumber": 2},
    ]

    workflow = build_screenplay_draft_workflow(scenes)
    units = {unit.id: unit for unit in workflow.units}

    assert [unit.kind for unit in workflow.units] == [
        ScreenplayDraftUnitKind.SCENE_GENERATION,
        ScreenplayDraftUnitKind.SCENE_GENERATION,
        ScreenplayDraftUnitKind.SCENE_GENERATION,
        ScreenplayDraftUnitKind.CONTINUITY_REVIEW,
        ScreenplayDraftUnitKind.SCENE_REVISION,
        ScreenplayDraftUnitKind.SCENE_REVISION,
        ScreenplayDraftUnitKind.SCENE_REVISION,
        ScreenplayDraftUnitKind.FINALIZE,
    ]
    assert units["write_s01-a"].depends_on == ()
    assert units["write_s01-b"].depends_on == ("write_s01-a",)
    assert units["write_s02-a"].depends_on == ()
    assert units["review_draft_continuity"].depends_on == (
        "write_s01-a",
        "write_s01-b",
        "write_s02-a",
    )
    assert units["review_draft_continuity"].scene_ids == (
        "s01-a",
        "s01-b",
        "s02-a",
    )
    assert units["revise_s01-a"].depends_on == (
        "write_s01-a",
        "review_draft_continuity",
    )
    assert units["propose_draft"].depends_on == (
        "revise_s01-a",
        "revise_s01-b",
        "revise_s02-a",
    )


def test_host_workflow_reports_baseline_and_worst_case_model_calls():
    workflow = build_screenplay_draft_workflow([
        {"id": "s01", "episodeNumber": 1},
        {"id": "s02", "episodeNumber": 2},
        {"id": "s03", "episodeNumber": 3},
    ])

    # Three Writers plus one compact global Reviewer are mandatory. The three
    # Rewriters run only for scenes named by the review report.
    assert workflow.minimum_model_call_count == 4
    assert workflow.model_call_count == 7
    assert workflow.max_parallelism == 3


def test_scenes_without_episode_metadata_form_one_ordered_lane():
    workflow = build_screenplay_draft_workflow([
        {"id": "s01"},
        {"id": "s02"},
        {"id": "s03"},
    ])
    writers = [
        unit for unit in workflow.units
        if unit.kind is ScreenplayDraftUnitKind.SCENE_GENERATION
    ]

    assert [unit.depends_on for unit in writers] == [
        (),
        ("write_s01",),
        ("write_s02",),
    ]


def test_host_workflow_rejects_missing_or_duplicate_scene_ids():
    with pytest.raises(ValueError, match="scene id is required"):
        build_screenplay_draft_workflow([{}])
    with pytest.raises(ValueError, match="must be unique"):
        build_screenplay_draft_workflow([{"id": "s01"}, {"id": "s01"}])
