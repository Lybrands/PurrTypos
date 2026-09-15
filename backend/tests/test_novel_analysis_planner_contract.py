from __future__ import annotations

from hashlib import sha256

import pytest

from agents.novel_analysis.planner_contract import (
    AnalysisPass,
    AnalysisPlanningLimits,
    NovelAnalysisPlannerContractError,
    ScalableAnalysisPlan,
    compile_scalable_analysis_recipe,
    planner_context_mapping,
)
from agents.novel_analysis.source_slicing import (
    SliceSourceSection,
    SourceTokenizer,
    compile_source_slice_manifest,
)


def _manifest(*, characters=900, context_window=1_000):
    text = "文" * characters
    section = SliceSourceSection(
        id="section-0",
        ordinal=0,
        title="全文",
        text=text,
        content_digest=sha256(text.encode()).hexdigest(),
        byte_count=len(text.encode()),
        character_count=len(text),
        token_count=len(text),
    )
    return compile_source_slice_manifest(
        source_revision_id="revision-1",
        source_revision_digest="revision-digest",
        context_window_tokens=context_window,
        sections=(section,),
        tokenizer=SourceTokenizer(
            id="test.characters",
            version="1",
            count_kind="exact",
            count=len,
            safety_basis_points=10_000,
        ),
    )


def _plan():
    return ScalableAnalysisPlan(
        passes=(
            AnalysisPass("story", ("characters", "relationships", "plot")),
            AnalysisPass("craft", ("structure", "style", "techniques")),
        ),
        reduce_fan_in=2,
        synthesis_sections=("人物与关系", "故事与结构", "写作技法"),
        quality_checks=("覆盖全部分片", "结论面向整部作品"),
    )


def test_planner_context_contains_only_source_scale_and_semantic_choices():
    context = planner_context_mapping(
        manifest=_manifest(),
        limits=AnalysisPlanningLimits(max_model_calls=32),
    )

    assert context["sourceScale"] == {
        "totalCharacters": 900,
        "estimatedTokens": 900,
        "sliceCount": 3,
    }
    assert not _contains_key(context, "text")
    assert "文" not in repr(context)
    assert "sliceManifest" not in context
    assert "sourceTokenLimit" not in repr(context)
    assert "packingTokenLimit" not in repr(context)
    assert "maxModelCalls" not in repr(context)
    assert "maxParallelism" not in repr(context)
    assert "exactShape" not in repr(context)


def test_planner_output_normalizes_to_canonical_schema():
    raw = _plan().to_mapping()
    assert ScalableAnalysisPlan.from_mapping(raw) == _plan()

    assert ScalableAnalysisPlan.from_mapping({
        **raw,
        "explanation": "规划说明",
    }) == _plan()
    with pytest.raises(NovelAnalysisPlannerContractError, match="pass is invalid"):
        ScalableAnalysisPlan.from_mapping({
            **raw,
            "passes": [{"id": "quote", "dimensions": ["evidence"]}],
        })


def test_compiler_expands_planner_semantics_into_real_bounded_dag():
    manifest = _manifest()
    recipe = compile_scalable_analysis_recipe(
        manifest=manifest,
        plan=_plan(),
        limits=AnalysisPlanningLimits(max_model_calls=32, max_parallelism=2),
    )
    steps = {item.id: item for item in recipe.steps}

    assert recipe.metadata["plannerAuthority"] == "semantic_dag"
    assert recipe.max_parallelism == 2
    assert len([item for item in recipe.steps if item.kind == "map"]) == 6
    assert steps["map:story:0"].metadata["sliceId"] == manifest.slices[0].id
    assert steps["reduce:story:0:0"].depends_on == (
        "map:story:0", "map:story:1"
    )
    assert steps["synthesize:whole-work"].depends_on == (
        "reduce:story:1:0", "reduce:craft:1:0"
    )
    assert steps["coverage:gate"].metadata["expectedSliceIds"] == [
        item.id for item in manifest.slices
    ]
    assert steps["skill:create"].depends_on == ("coverage:gate",)
    assert steps["skill:create"].metadata["creatorSkill"] == "purrtypos-writing-skill-creator"
    assert steps["skill:create"].metadata["creatorSkillVersion"] == 2
    assert steps["skill:create"].metadata["creatorSkillDigest"].startswith("sha256:")
    assert steps["review:artifact"].depends_on == ("skill:create",)


def test_recipe_identity_binds_semantic_plan_and_slice_manifest():
    first = compile_scalable_analysis_recipe(manifest=_manifest(), plan=_plan())
    second = compile_scalable_analysis_recipe(manifest=_manifest(), plan=_plan())
    changed = compile_scalable_analysis_recipe(
        manifest=_manifest(),
        plan=ScalableAnalysisPlan(
            passes=(AnalysisPass("story", ("characters", "plot")),),
            reduce_fan_in=2,
            synthesis_sections=("全书分析",),
            quality_checks=("覆盖全部分片",),
        ),
    )

    assert first.metadata["recipeDigest"] == second.metadata["recipeDigest"]
    assert first.metadata["recipeDigest"] != changed.metadata["recipeDigest"]


def test_host_rejects_excess_passes_and_model_calls_before_execution():
    manifest = _manifest()
    with pytest.raises(NovelAnalysisPlannerContractError, match="pass count"):
        compile_scalable_analysis_recipe(
            manifest=manifest,
            plan=_plan(),
            limits=AnalysisPlanningLimits(max_passes=1),
        )
    with pytest.raises(NovelAnalysisPlannerContractError, match="model-call budget"):
        compile_scalable_analysis_recipe(
            manifest=manifest,
            plan=_plan(),
            limits=AnalysisPlanningLimits(max_model_calls=3),
        )


def _contains_key(value, expected):
    if isinstance(value, dict):
        return expected in value or any(
            _contains_key(item, expected) for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_key(item, expected) for item in value)
    return False
