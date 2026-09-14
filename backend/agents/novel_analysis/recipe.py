"""Versioned host recipe for the replacement Novel Analysis Agent."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from purra.contracts import ExecutionRecipe, ExecutionRecipeStep


NOVEL_ANALYSIS_REPLACEMENT_RECIPE_VERSION = 1
NOVEL_ANALYSIS_REPLACEMENT_SCHEMA_VERSION = 1


class AnalysisUnitKind(StrEnum):
    EXTRACT = "extract"
    NORMALIZE = "normalize"
    VALIDATE_EVIDENCE = "validate_evidence"
    OVERVIEW = "overview"
    DISTILL_TECHNIQUE = "distill_technique"
    COVERAGE = "coverage"
    REVIEW = "review"


@dataclass(frozen=True, slots=True)
class AnalysisSegment:
    id: str
    section_id: str
    section_digest: str
    section_ordinal: int
    start_character: int
    end_character: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.id, str)
            or not self.id.strip()
            or not isinstance(self.section_id, str)
            or not self.section_id.strip()
            or not isinstance(self.section_digest, str)
            or not self.section_digest.strip()
            or type(self.section_ordinal) is not int
            or type(self.start_character) is not int
            or type(self.end_character) is not int
            or self.section_ordinal < 0
            or self.start_character < 0
            or self.end_character <= self.start_character
        ):
            raise ValueError("novel analysis replacement segment is invalid")
        object.__setattr__(self, "id", self.id.strip())
        object.__setattr__(self, "section_id", self.section_id.strip())
        object.__setattr__(self, "section_digest", self.section_digest.strip())

    def to_mapping(self) -> dict[str, object]:
        return {
            "id": self.id,
            "sectionId": self.section_id,
            "sectionDigest": self.section_digest,
            "sectionOrdinal": self.section_ordinal,
            "startCharacter": self.start_character,
            "endCharacter": self.end_character,
        }


def compile_analysis_recipe(
    *,
    source_revision_id: str,
    segments: Sequence[AnalysisSegment],
    plan_step_ids: Sequence[str],
) -> ExecutionRecipe:
    """Compile one immutable DAG; Planner ids affect presentation only."""

    revision_id = str(source_revision_id or "").strip()
    frozen_segments = tuple(segments)
    planner_steps = tuple(dict.fromkeys(
        str(item or "").strip() for item in plan_step_ids
    ))
    if not revision_id or not frozen_segments:
        raise ValueError("replacement analysis requires a revision and segments")
    if any(not item for item in planner_steps):
        raise ValueError("replacement analysis Planner steps must be non-empty")
    if len({item.id for item in frozen_segments}) != len(frozen_segments):
        raise ValueError("replacement analysis segment ids must be unique")

    specs: list[tuple[str, AnalysisUnitKind, tuple[str, ...], dict]] = []
    extract_ids = []
    for index, segment in enumerate(frozen_segments, start=1):
        unit_id = f"extract:{index}:{segment.id}"
        extract_ids.append(unit_id)
        specs.append((
            unit_id,
            AnalysisUnitKind.EXTRACT,
            (),
            {
                "sourceRevisionId": revision_id,
                "segment": segment.to_mapping(),
                "displayTitle": f"提取来源片段 {index}",
            },
        ))

    normalized_root, normalize_specs = _normalization_tree(extract_ids)
    specs.extend(normalize_specs)
    specs.extend((
        (
            "evidence:validate",
            AnalysisUnitKind.VALIDATE_EVIDENCE,
            (normalized_root,),
            {"displayTitle": "校验来源证据"},
        ),
        (
            "overview:build",
            AnalysisUnitKind.OVERVIEW,
            ("evidence:validate",),
            {
                "displayTitle": "生成故事概览",
                "outputRequired": True,
                "artifactField": "storyOverview",
            },
        ),
        (
            "technique:distill",
            AnalysisUnitKind.DISTILL_TECHNIQUE,
            ("evidence:validate",),
            {
                "displayTitle": "提炼写作技法",
                "outputRequired": True,
                "emptyOutputAllowed": True,
            },
        ),
        (
            "coverage:report",
            AnalysisUnitKind.COVERAGE,
            (
                "evidence:validate",
                "overview:build",
                "technique:distill",
            ),
            {"displayTitle": "核验分析覆盖"},
        ),
        (
            "review:artifact",
            AnalysisUnitKind.REVIEW,
            ("coverage:report",),
            {
                "displayTitle": "形成待审核成果",
                "requiresStoryOverview": True,
            },
        ),
    ))

    stage_order = tuple(dict.fromkeys(kind for _, kind, _, _ in specs))
    if len(planner_steps) > len(stage_order):
        raise ValueError("Planner has more steps than host recipe stages")
    plan_by_kind = _presentation_plan_mapping(stage_order, planner_steps)
    steps = tuple(
        ExecutionRecipeStep(
            id=unit_id,
            kind=kind.value,
            depends_on=dependencies,
            executor="novel_analysis.purra-native",
            plan_step_id=plan_by_kind.get(kind),
            max_attempts=(
                2
                if kind in {
                    AnalysisUnitKind.EXTRACT,
                    AnalysisUnitKind.NORMALIZE,
                    AnalysisUnitKind.OVERVIEW,
                    AnalysisUnitKind.DISTILL_TECHNIQUE,
                }
                else 1
            ),
            metadata=metadata,
        )
        for unit_id, kind, dependencies, metadata in specs
    )
    canonical = {
        "recipeVersion": NOVEL_ANALYSIS_REPLACEMENT_RECIPE_VERSION,
        "schemaVersion": NOVEL_ANALYSIS_REPLACEMENT_SCHEMA_VERSION,
        "sourceRevisionId": revision_id,
        "segments": [item.to_mapping() for item in frozen_segments],
        "steps": [
            {
                "id": unit_id,
                "kind": kind.value,
                "dependsOn": list(dependencies),
                "maxAttempts": (
                    2
                    if kind in {
                        AnalysisUnitKind.EXTRACT,
                        AnalysisUnitKind.NORMALIZE,
                        AnalysisUnitKind.OVERVIEW,
                        AnalysisUnitKind.DISTILL_TECHNIQUE,
                    }
                    else 1
                ),
                "metadata": metadata,
            }
            for unit_id, kind, dependencies, metadata in specs
        ],
    }
    return ExecutionRecipe(
        kind="novel_analysis.purra-native",
        steps=steps,
        max_parallelism=min(4, len(frozen_segments)),
        metadata={
            "recipeVersion": NOVEL_ANALYSIS_REPLACEMENT_RECIPE_VERSION,
            "analysisSchemaVersion": NOVEL_ANALYSIS_REPLACEMENT_SCHEMA_VERSION,
            "recipeDigest": _digest(canonical),
            "storyOverviewContract": "required_independent_artifact",
            "plannerAuthority": "presentation_mapping_only",
        },
    )


def _normalization_tree(extract_ids: Sequence[str]):
    current = list(extract_ids)
    specs = []
    level = 1
    while len(current) > 1:
        following = []
        for index in range(0, len(current), 2):
            dependencies = tuple(current[index:index + 2])
            unit_id = f"normalize:{level}:{index // 2 + 1}"
            following.append(unit_id)
            specs.append((
                unit_id,
                AnalysisUnitKind.NORMALIZE,
                dependencies,
                {
                    "displayTitle": f"归一分析结果 第 {level} 层",
                    "fanIn": len(dependencies),
                },
            ))
        current = following
        level += 1
    if not specs:
        unit_id = "normalize:1:1"
        specs.append((
            unit_id,
            AnalysisUnitKind.NORMALIZE,
            (current[0],),
            {"displayTitle": "归一分析结果 第 1 层", "fanIn": 1},
        ))
        current = [unit_id]
    return current[0], specs


def _presentation_plan_mapping(kinds, planner_steps):
    if not planner_steps:
        return {}
    return {
        kind: planner_steps[
            min(index * len(planner_steps) // len(kinds), len(planner_steps) - 1)
        ]
        for index, kind in enumerate(kinds)
    }


def _digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = [
    "AnalysisSegment",
    "AnalysisUnitKind",
    "NOVEL_ANALYSIS_REPLACEMENT_RECIPE_VERSION",
    "NOVEL_ANALYSIS_REPLACEMENT_SCHEMA_VERSION",
    "compile_analysis_recipe",
]
