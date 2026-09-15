"""Strict semantic Planner contract for scalable whole-book analysis."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agents.novel_analysis.source_slicing import NovelAnalysisSliceManifest
from agents.novel_analysis.creator_skill_resource import (
    CREATOR_SKILL_ID,
    CREATOR_SKILL_VERSION,
    creator_skill_digest,
)
from purra.contracts import ExecutionRecipe, ExecutionRecipeStep


SCALABLE_ANALYSIS_PLAN_SCHEMA_VERSION = 1
SCALABLE_ANALYSIS_RECIPE_VERSION = 2
_ALLOWED_DIMENSIONS = frozenset({
    "characters",
    "relationships",
    "setting",
    "worldbuilding",
    "plot",
    "structure",
    "style",
    "techniques",
})


class NovelAnalysisPlannerContractError(ValueError):
    code = "novel_analysis_planner_contract_invalid"


@dataclass(frozen=True, slots=True)
class AnalysisPass:
    id: str
    dimensions: tuple[str, ...]

    def __post_init__(self) -> None:
        pass_id = str(self.id or "").strip()
        dimensions = tuple(str(item or "").strip() for item in self.dimensions)
        if (
            not pass_id
            or not pass_id.replace("-", "").replace("_", "").isalnum()
            or not dimensions
            or len(set(dimensions)) != len(dimensions)
            or any(item not in _ALLOWED_DIMENSIONS for item in dimensions)
        ):
            raise NovelAnalysisPlannerContractError(
                "analysis Planner pass is invalid"
            )
        object.__setattr__(self, "id", pass_id)
        object.__setattr__(self, "dimensions", dimensions)

    def to_mapping(self) -> dict[str, object]:
        return {"id": self.id, "dimensions": list(self.dimensions)}


@dataclass(frozen=True, slots=True)
class ScalableAnalysisPlan:
    passes: tuple[AnalysisPass, ...]
    reduce_fan_in: int
    synthesis_sections: tuple[str, ...]
    quality_checks: tuple[str, ...]

    def __post_init__(self) -> None:
        passes = tuple(self.passes)
        synthesis = _canonical_text_items(self.synthesis_sections)
        checks = _canonical_text_items(self.quality_checks)
        if (
            not passes
            or len({item.id for item in passes}) != len(passes)
            or type(self.reduce_fan_in) is not int
            or not 2 <= self.reduce_fan_in <= 16
            or not synthesis
            or not checks
        ):
            raise NovelAnalysisPlannerContractError(
                "analysis Planner output is incomplete"
            )
        object.__setattr__(self, "passes", passes)
        object.__setattr__(self, "synthesis_sections", synthesis)
        object.__setattr__(self, "quality_checks", checks)

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": SCALABLE_ANALYSIS_PLAN_SCHEMA_VERSION,
            "passes": [item.to_mapping() for item in self.passes],
            "reduceFanIn": self.reduce_fan_in,
            "synthesisSections": list(self.synthesis_sections),
            "qualityChecks": list(self.quality_checks),
        }

    @classmethod
    def from_mapping(cls, value: object) -> "ScalableAnalysisPlan":
        required = {
            "schemaVersion",
            "passes",
            "reduceFanIn",
            "synthesisSections",
            "qualityChecks",
        }
        if not isinstance(value, Mapping) or not required.issubset(value):
            raise NovelAnalysisPlannerContractError(
                "analysis Planner output must use the canonical shape"
            )
        if value.get("schemaVersion") != SCALABLE_ANALYSIS_PLAN_SCHEMA_VERSION:
            raise NovelAnalysisPlannerContractError(
                "analysis Planner schema version is unsupported"
            )
        raw_passes = value.get("passes")
        if not _is_json_sequence(raw_passes):
            raise NovelAnalysisPlannerContractError(
                "analysis Planner passes must be a list"
            )
        passes = []
        for raw in raw_passes:
            if not isinstance(raw, Mapping) or not {"id", "dimensions"}.issubset(raw):
                raise NovelAnalysisPlannerContractError(
                    "analysis Planner pass must use the canonical shape"
                )
            dimensions = raw.get("dimensions")
            if not _is_json_sequence(dimensions):
                raise NovelAnalysisPlannerContractError(
                    "analysis Planner dimensions must be a list"
                )
            passes.append(AnalysisPass(
                id=raw.get("id"),
                dimensions=tuple(dimensions),
            ))
        sections = value.get("synthesisSections")
        checks = value.get("qualityChecks")
        if not _is_json_sequence(sections) or not _is_json_sequence(checks):
            raise NovelAnalysisPlannerContractError(
                "analysis Planner synthesis contract must use lists"
            )
        return cls(
            passes=tuple(passes),
            reduce_fan_in=value.get("reduceFanIn"),
            synthesis_sections=tuple(sections),
            quality_checks=tuple(checks),
        )


@dataclass(frozen=True, slots=True)
class AnalysisPlanningLimits:
    max_passes: int = 4
    max_model_calls: int = 512
    max_parallelism: int = 4

    def __post_init__(self) -> None:
        if (
            type(self.max_passes) is not int
            or type(self.max_model_calls) is not int
            or type(self.max_parallelism) is not int
            or self.max_passes < 1
            or self.max_model_calls < 1
            or self.max_parallelism < 1
        ):
            raise NovelAnalysisPlannerContractError(
                "analysis Host planning limits are invalid"
            )


def compile_scalable_analysis_recipe(
    *,
    manifest: NovelAnalysisSliceManifest,
    plan: ScalableAnalysisPlan,
    limits: AnalysisPlanningLimits = AnalysisPlanningLimits(),
) -> ExecutionRecipe:
    """Compile Planner semantics into a bounded Host-owned durable DAG."""

    if not manifest.slices:
        raise NovelAnalysisPlannerContractError(
            "analysis SliceManifest is empty"
        )
    if len(plan.passes) > limits.max_passes:
        raise NovelAnalysisPlannerContractError(
            "analysis Planner exceeds the allowed pass count"
        )

    specs: list[dict[str, object]] = []
    reduce_roots: list[str] = []
    for analysis_pass in plan.passes:
        level = []
        for source_slice in manifest.slices:
            unit_id = f"map:{analysis_pass.id}:{source_slice.position}"
            level.append(unit_id)
            specs.append({
                "id": unit_id,
                "kind": "map",
                "dependsOn": (),
                "maxAttempts": 2,
                "metadata": {
                    "passId": analysis_pass.id,
                    "dimensions": list(analysis_pass.dimensions),
                    "sliceId": source_slice.id,
                    "slicePosition": source_slice.position,
                    "sourceTokenLimit": manifest.source_token_limit,
                    "sliceTokenCount": source_slice.token_count,
                },
            })
        reduce_level = 0
        while len(level) > 1:
            next_level = []
            for group_index, start in enumerate(range(0, len(level), plan.reduce_fan_in)):
                dependencies = tuple(level[start:start + plan.reduce_fan_in])
                unit_id = f"reduce:{analysis_pass.id}:{reduce_level}:{group_index}"
                next_level.append(unit_id)
                specs.append({
                    "id": unit_id,
                    "kind": "reduce",
                    "dependsOn": dependencies,
                    "maxAttempts": 2,
                    "metadata": {
                        "passId": analysis_pass.id,
                        "dimensions": list(analysis_pass.dimensions),
                        "reduceLevel": reduce_level,
                        "fanIn": len(dependencies),
                    },
                })
            level = next_level
            reduce_level += 1
        reduce_roots.append(level[0])

    synthesize_id = "synthesize:whole-work"
    specs.append({
        "id": synthesize_id,
        "kind": "synthesize",
        "dependsOn": tuple(reduce_roots),
        "maxAttempts": 2,
        "metadata": {
            "sections": list(plan.synthesis_sections),
            "wholeWork": True,
        },
    })
    coverage_id = "coverage:gate"
    specs.append({
        "id": coverage_id,
        "kind": "coverage",
        "dependsOn": (synthesize_id,),
        "maxAttempts": 1,
        "metadata": {
            "expectedSliceIds": [item.id for item in manifest.slices],
            "expectedPassIds": [item.id for item in plan.passes],
            "qualityChecks": list(plan.quality_checks),
            "deterministic": True,
        },
    })
    specs.append({
        "id": "skill:create",
        "kind": "skill",
        "dependsOn": (coverage_id,),
        "maxAttempts": 2,
        "metadata": {
            "wholeWork": True,
            "creatorSkill": CREATOR_SKILL_ID,
            "creatorSkillVersion": CREATOR_SKILL_VERSION,
            "creatorSkillDigest": creator_skill_digest(),
            "outputKind": "file_backed_skill",
        },
    })
    specs.append({
        "id": "review:artifact",
        "kind": "review",
        "dependsOn": ("skill:create",),
        "maxAttempts": 2,
        "metadata": {"wholeWork": True, "publishAfterCoverage": True},
    })

    model_call_count = sum(item["kind"] != "coverage" for item in specs)
    if model_call_count > limits.max_model_calls:
        raise NovelAnalysisPlannerContractError(
            "analysis Planner exceeds the Host model-call budget"
        )
    canonical = {
        "recipeVersion": SCALABLE_ANALYSIS_RECIPE_VERSION,
        "manifest": manifest.to_mapping(),
        "analysisPlan": plan.to_mapping(),
        "steps": specs,
        "maxParallelism": min(limits.max_parallelism, len(manifest.slices)),
    }
    steps = tuple(ExecutionRecipeStep(
        id=str(item["id"]),
        kind=str(item["kind"]),
        depends_on=tuple(item["dependsOn"]),
        executor="novel_analysis.scalable.v2",
        max_attempts=int(item["maxAttempts"]),
        metadata=item["metadata"],
    ) for item in specs)
    return ExecutionRecipe(
        kind="novel_analysis.scalable.v2",
        steps=steps,
        max_parallelism=min(limits.max_parallelism, len(manifest.slices)),
        metadata={
            "recipeVersion": SCALABLE_ANALYSIS_RECIPE_VERSION,
            "analysisPlanSchemaVersion": SCALABLE_ANALYSIS_PLAN_SCHEMA_VERSION,
            "recipeDigest": _digest(canonical),
            "plannerAuthority": "semantic_dag",
            "sliceManifest": manifest.to_mapping(),
            "analysisPlan": plan.to_mapping(),
            "estimatedModelCalls": model_call_count,
        },
    )


def planner_context_mapping(
    *,
    manifest: NovelAnalysisSliceManifest,
    limits: AnalysisPlanningLimits,
) -> dict[str, object]:
    """Return Planner input without any source body."""

    return {
        "schemaVersion": SCALABLE_ANALYSIS_PLAN_SCHEMA_VERSION,
        "sourceScale": {
            "totalCharacters": manifest.total_character_count,
            "estimatedTokens": manifest.total_token_count,
            "sliceCount": len(manifest.slices),
        },
        "allowedDimensions": sorted(_ALLOWED_DIMENSIONS),
        "planTarget": {
            "placement": "plan.taskSpec.target",
            "shape": {
                "schemaVersion": 1,
                "passes": [{"id": "short-id", "dimensions": ["allowed dimension"]}],
                "reduceFanIn": "integer 2..16",
                "synthesisSections": ["report section"],
                "qualityChecks": ["whole-work quality goal"],
            },
            "maxPasses": limits.max_passes,
        },
        "rules": [
            "Choose whole-work analysis dimensions and report sections from the user's goal.",
            "Return one to three semantic todos; never make todos for slices or chapters.",
            "The Host owns slicing, ordering, reduction execution, concurrency, and budgets.",
        ],
    }


def _canonical_text_items(values: Sequence[object]) -> tuple[str, ...]:
    items = tuple(str(item or "").strip() for item in values)
    if (
        not items
        or any(not item or len(item) > 120 for item in items)
        or len(set(items)) != len(items)
    ):
        raise NovelAnalysisPlannerContractError(
            "analysis Planner text items are invalid"
        )
    return items


def _is_json_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


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
    "AnalysisPass",
    "AnalysisPlanningLimits",
    "NovelAnalysisPlannerContractError",
    "SCALABLE_ANALYSIS_PLAN_SCHEMA_VERSION",
    "SCALABLE_ANALYSIS_RECIPE_VERSION",
    "ScalableAnalysisPlan",
    "compile_scalable_analysis_recipe",
    "planner_context_mapping",
]
