from __future__ import annotations

import pytest

from agents.novel_analysis.recipe import (
    AnalysisSegment,
    AnalysisUnitKind,
    compile_analysis_recipe,
)


def _segments(count: int = 4):
    return tuple(
        AnalysisSegment(
            id=f"segment-{index}",
            section_id=f"section-{index // 2}",
            section_digest=f"digest-{index // 2}",
            section_ordinal=index // 2,
            start_character=(index % 2) * 100,
            end_character=(index % 2 + 1) * 100,
        )
        for index in range(count)
    )


def test_recipe_has_explicit_required_overview_and_review_dependency() -> None:
    recipe = compile_analysis_recipe(
        source_revision_id="revision-1",
        segments=_segments(),
        plan_step_ids=("understand", "review"),
    )
    by_kind = {}
    for step in recipe.steps:
        by_kind.setdefault(step.kind, []).append(step)

    assert set(by_kind) == {kind.value for kind in AnalysisUnitKind}
    overview = by_kind[AnalysisUnitKind.OVERVIEW.value][0]
    coverage = by_kind[AnalysisUnitKind.COVERAGE.value][0]
    review = by_kind[AnalysisUnitKind.REVIEW.value][0]
    assert overview.metadata["artifactField"] == "storyOverview"
    assert overview.metadata["outputRequired"] is True
    assert overview.id in coverage.depends_on
    assert review.depends_on == (coverage.id,)
    assert review.metadata["requiresStoryOverview"] is True
    assert recipe.metadata["storyOverviewContract"] == (
        "required_independent_artifact"
    )


def test_planner_mapping_never_changes_host_dag_or_digest() -> None:
    first = compile_analysis_recipe(
        source_revision_id="revision-1",
        segments=_segments(),
        plan_step_ids=("one",),
    )
    second = compile_analysis_recipe(
        source_revision_id="revision-1",
        segments=_segments(),
        plan_step_ids=("one", "two", "three"),
    )

    assert [
        (step.id, step.kind, step.depends_on, dict(step.metadata))
        for step in first.steps
    ] == [
        (step.id, step.kind, step.depends_on, dict(step.metadata))
        for step in second.steps
    ]
    assert first.metadata["recipeDigest"] == second.metadata["recipeDigest"]
    assert {step.plan_step_id for step in first.steps} == {"one"}
    assert {step.plan_step_id for step in second.steps} == {
        "one", "two", "three"
    }


def test_normalization_is_bounded_binary_fan_in() -> None:
    recipe = compile_analysis_recipe(
        source_revision_id="revision-1",
        segments=_segments(7),
        plan_step_ids=(),
    )
    normalization = [
        step for step in recipe.steps
        if step.kind == AnalysisUnitKind.NORMALIZE.value
    ]

    assert normalization
    assert all(1 <= len(step.depends_on) <= 2 for step in normalization)
    assert recipe.max_parallelism == 4


def test_recipe_digest_binds_source_revision_and_segments() -> None:
    base = compile_analysis_recipe(
        source_revision_id="revision-1",
        segments=_segments(1),
        plan_step_ids=(),
    )
    changed_revision = compile_analysis_recipe(
        source_revision_id="revision-2",
        segments=_segments(1),
        plan_step_ids=(),
    )
    changed_range = compile_analysis_recipe(
        source_revision_id="revision-1",
        segments=(AnalysisSegment(
            id="segment-0",
            section_id="section-0",
            section_digest="digest-0",
            section_ordinal=0,
            start_character=0,
            end_character=101,
        ),),
        plan_step_ids=(),
    )
    changed_digest = compile_analysis_recipe(
        source_revision_id="revision-1",
        segments=(AnalysisSegment(
            id="segment-0",
            section_id="section-0",
            section_digest="different-digest",
            section_ordinal=0,
            start_character=0,
            end_character=100,
        ),),
        plan_step_ids=(),
    )

    assert base.metadata["recipeDigest"] != changed_revision.metadata[
        "recipeDigest"
    ]
    assert base.metadata["recipeDigest"] != changed_range.metadata[
        "recipeDigest"
    ]
    assert base.metadata["recipeDigest"] != changed_digest.metadata[
        "recipeDigest"
    ]


def test_recipe_rejects_duplicate_segments_and_excess_planner_steps() -> None:
    duplicate = (_segments(1)[0], _segments(1)[0])
    with pytest.raises(ValueError, match="segment ids must be unique"):
        compile_analysis_recipe(
            source_revision_id="revision-1",
            segments=duplicate,
            plan_step_ids=(),
        )
    with pytest.raises(ValueError, match="more steps"):
        compile_analysis_recipe(
            source_revision_id="revision-1",
            segments=_segments(1),
            plan_step_ids=tuple(f"plan-{index}" for index in range(8)),
        )
