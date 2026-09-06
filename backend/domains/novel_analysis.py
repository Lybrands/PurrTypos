"""Authoritative contracts for evidence-backed source analysis."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from purra.contracts import DomainContext, ExecutionRecipe, ExecutionRecipeStep
from purra.json_values import freeze_json_mapping, thaw_json_mapping


NOVEL_ANALYSIS_DOMAIN_NAMESPACE = "purrtypos.novel_analysis"
NOVEL_ANALYSIS_SCHEMA_VERSION = 2
NOVEL_ANALYSIS_ARTIFACT_KIND = "novel_source_analysis_candidate"
NOVEL_ANALYSIS_REVIEW_ARTIFACT_KIND = "novel_source_analysis_review"
NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX = "novel-analysis-artifact://"
@dataclass(frozen=True, slots=True)
class NovelAnalysisSegment:
    id: str
    section_id: str
    section_ordinal: int
    start_character: int
    end_character: int

    def __post_init__(self) -> None:
        segment_id = str(self.id or "").strip()
        section_id = str(self.section_id or "").strip()
        start = int(self.start_character)
        end = int(self.end_character)
        if not segment_id or not section_id or start < 0 or end <= start:
            raise ValueError("novel analysis segment is invalid")
        object.__setattr__(self, "id", segment_id)
        object.__setattr__(self, "section_id", section_id)
        object.__setattr__(self, "section_ordinal", int(self.section_ordinal))
        object.__setattr__(self, "start_character", start)
        object.__setattr__(self, "end_character", end)

    def to_mapping(self) -> dict[str, object]:
        return {
            "id": self.id,
            "sectionId": self.section_id,
            "sectionOrdinal": self.section_ordinal,
            "startCharacter": self.start_character,
            "endCharacter": self.end_character,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]):
        return cls(
            id=str(value.get("id") or ""),
            section_id=str(value.get("sectionId") or ""),
            section_ordinal=int(value.get("sectionOrdinal") or 0),
            start_character=int(value.get("startCharacter") or 0),
            end_character=int(value.get("endCharacter") or 0),
        )


@dataclass(frozen=True, slots=True)
class NovelAnalysisDomainContext:
    source_revision_id: str
    command_id: str
    section_ids: tuple[str, ...] = ()
    segments: tuple[NovelAnalysisSegment, ...] = ()
    input_token_budget: int = 0
    interaction_kind: str = "analysis"
    analysis_artifact_ref: str | None = None
    schema_version: int = NOVEL_ANALYSIS_SCHEMA_VERSION
    unit_input: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        revision_id = str(self.source_revision_id or "").strip()
        command_id = str(self.command_id or "").strip()
        section_ids = tuple(
            str(value or "").strip() for value in self.section_ids
        )
        interaction_kind = str(self.interaction_kind or "analysis").strip()
        artifact_ref = str(self.analysis_artifact_ref or "").strip() or None
        if not revision_id or not command_id:
            raise ValueError("novel analysis revision and command are required")
        if any(not value for value in section_ids):
            raise ValueError("novel analysis section ids must be non-empty")
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("novel analysis section ids must be unique")
        segments = tuple(self.segments)
        if segments:
            if len({item.id for item in segments}) != len(segments):
                raise ValueError("novel analysis segment ids must be unique")
            if any(item.section_id not in section_ids for item in segments):
                raise ValueError("novel analysis segment is outside section scope")
            by_section: dict[str, list[NovelAnalysisSegment]] = {}
            for item in segments:
                by_section.setdefault(item.section_id, []).append(item)
            if set(by_section) != set(section_ids):
                raise ValueError("novel analysis segments must cover every section")
            for values in by_section.values():
                ordered = sorted(values, key=lambda item: item.start_character)
                if ordered[0].start_character != 0 or any(
                    current.end_character != following.start_character
                    for current, following in zip(ordered, ordered[1:])
                ):
                    raise ValueError("novel analysis segments must be contiguous")
        if int(self.schema_version) != NOVEL_ANALYSIS_SCHEMA_VERSION:
            raise ValueError("unsupported novel analysis schema version")
        if interaction_kind not in {"analysis", "follow_up", "unit"}:
            raise ValueError("unsupported novel analysis interaction kind")
        if interaction_kind == "unit" and not isinstance(self.unit_input, Mapping):
            raise ValueError("novel analysis unit requires bound input")
        if interaction_kind != "unit" and self.unit_input is not None:
            raise ValueError("only analysis units may carry bound input")
        if interaction_kind == "follow_up" and artifact_ref is None:
            raise ValueError("novel analysis follow-up requires an Artifact")
        object.__setattr__(self, "source_revision_id", revision_id)
        object.__setattr__(self, "command_id", command_id)
        object.__setattr__(self, "section_ids", section_ids)
        object.__setattr__(self, "segments", segments)
        object.__setattr__(self, "input_token_budget", max(0, int(self.input_token_budget)))
        object.__setattr__(self, "interaction_kind", interaction_kind)
        object.__setattr__(self, "analysis_artifact_ref", artifact_ref)
        object.__setattr__(self, "schema_version", int(self.schema_version))
        if self.unit_input is not None:
            object.__setattr__(self, "unit_input", freeze_json_mapping(self.unit_input))

    def to_core_context(self) -> DomainContext:
        return DomainContext(
            namespace=NOVEL_ANALYSIS_DOMAIN_NAMESPACE,
            payload={
                "sourceRevisionId": self.source_revision_id,
                "commandId": self.command_id,
                "sectionIds": list(self.section_ids),
                "segments": [item.to_mapping() for item in self.segments],
                "inputTokenBudget": self.input_token_budget,
                "interactionKind": self.interaction_kind,
                "analysisArtifactRef": self.analysis_artifact_ref,
                "analysisSchemaVersion": self.schema_version,
                **({"unitInput": thaw_json_mapping(self.unit_input)}
                   if self.unit_input is not None else {}),
            },
        )

    @classmethod
    def from_core_context(cls, context: DomainContext):
        if context.namespace != NOVEL_ANALYSIS_DOMAIN_NAMESPACE:
            raise ValueError("unsupported novel analysis namespace")
        payload = thaw_json_mapping(context.payload)
        section_ids = payload.get("sectionIds") or ()
        if not isinstance(section_ids, (list, tuple)):
            raise ValueError("novel analysis sectionIds must be an array")
        raw_segments = payload.get("segments") or ()
        if not isinstance(raw_segments, (list, tuple)) or any(
            not isinstance(item, Mapping) for item in raw_segments
        ):
            raise ValueError("novel analysis segments must be an array")
        return cls(
            source_revision_id=str(payload.get("sourceRevisionId") or ""),
            command_id=str(payload.get("commandId") or ""),
            section_ids=tuple(section_ids),
            segments=tuple(
                NovelAnalysisSegment.from_mapping(item) for item in raw_segments
            ),
            input_token_budget=int(payload.get("inputTokenBudget") or 0),
            interaction_kind=str(payload.get("interactionKind") or "analysis"),
            analysis_artifact_ref=(
                str(payload.get("analysisArtifactRef") or "").strip() or None
            ),
            unit_input=payload.get("unitInput"),
            schema_version=int(
                payload.get("analysisSchemaVersion")
                or NOVEL_ANALYSIS_SCHEMA_VERSION
            ),
        )


def compile_novel_analysis_recipe(
    *,
    section_ids: Sequence[str],
    segments: Sequence[NovelAnalysisSegment] = (),
    plan_step_ids: Sequence[str],
) -> ExecutionRecipe:
    """Compile the fixed host recipe; model-authored plans cannot change scope."""

    sections = tuple(str(value or "").strip() for value in section_ids)
    planned = tuple(str(value or "").strip() for value in plan_step_ids)
    if not sections or any(not value for value in sections):
        raise ValueError("novel analysis requires bound source sections")
    if not planned or any(not value for value in planned):
        raise ValueError("novel analysis requires admitted plan steps")

    frozen_segments = tuple(segments)
    compact_source = (
        len(frozen_segments) == 1
        and len(sections) == 1
        and frozen_segments[0].end_character - frozen_segments[0].start_character <= 4_000
    )
    if any(item.section_id not in sections for item in frozen_segments):
        raise ValueError("novel analysis recipe segment is outside section scope")
    specs: list[tuple[str, str, tuple[str, ...], Mapping[str, Any]]] = []
    extract_ids: list[str] = []
    if frozen_segments:
        for index, segment in enumerate(frozen_segments):
            unit_id = f"extract:{index + 1}:{segment.id}"
            extract_ids.append(unit_id)
            specs.append((
                unit_id,
                "extract_section",
                (),
                {
                    "sourceRevisionId": "__BOUND__",
                    "segmentId": segment.id,
                    "sectionId": segment.section_id,
                    "sectionOrdinal": segment.section_ordinal,
                    "startCharacter": segment.start_character,
                    "endCharacter": segment.end_character,
                    "analysisSchemaVersion": NOVEL_ANALYSIS_SCHEMA_VERSION,
                    "displayTitle": f"分析来源片段 {index + 1}",
                    **({"includeStoryOverview": True} if compact_source else {}),
                },
            ))
    else:
        for index, section_id in enumerate(sections):
            unit_id = f"extract:{index + 1}:{section_id}"
            extract_ids.append(unit_id)
            specs.append((
                unit_id,
                "extract_section",
                (),
                {
                    "sourceRevisionId": "__BOUND__",
                    "sectionId": section_id,
                    "analysisSchemaVersion": NOVEL_ANALYSIS_SCHEMA_VERSION,
                    "displayTitle": f"分析来源章节 {index + 1}",
                },
            ))
    if compact_source:
        normalized_root_id = extract_ids[0]
    elif frozen_segments:
        current = list(extract_ids)
        level = 1
        while len(current) > 1:
            following: list[str] = []
            for index in range(0, len(current), 2):
                group = tuple(current[index:index + 2])
                if len(group) == 1:
                    following.append(group[0])
                    continue
                unit_id = f"normalize:merge:{level}:{index // 2 + 1}"
                following.append(unit_id)
                specs.append((
                    unit_id,
                    "normalize_entities",
                    group,
                    {
                        "displayTitle": (
                            f"分批归一来源片段 {index + 1}-{index + len(group)}"
                            if level == 1
                            else f"分层归一第 {level} 层"
                        ),
                        "aggregationRole": "leaf" if level == 1 else "merge",
                        "aggregationLevel": level,
                    },
                ))
            current = following
            level += 1
        if current[0] in extract_ids:
            normalized_root_id = "normalize:root"
            specs.append((
                normalized_root_id,
                "normalize_entities",
                (current[0],),
                {
                    "displayTitle": "归一来源片段 1",
                    "aggregationRole": "leaf",
                    "aggregationLevel": 1,
                },
            ))
        else:
            normalized_root_id = current[0]
    else:
        normalized_root_id = "normalize:entities"
        specs.append((
            normalized_root_id,
            "normalize_entities",
            tuple(extract_ids),
            {"displayTitle": "归一人物、实体与时间线"},
        ))
    if not compact_source:
        specs.append((
            "aggregate:story",
            "aggregate_story",
            (normalized_root_id,),
            {"displayTitle": "聚合剧情线与人物认知"},
        ))
    specs.extend((
        (
            "validate:evidence",
            "validate_evidence",
            tuple(dict.fromkeys((*extract_ids, normalized_root_id if compact_source else "aggregate:story"))),
            {"displayTitle": "校验来源证据"},
        ),
        ("skill:draft", "distill_skill", ("validate:evidence",), {"displayTitle": "蒸馏可执行写作方法"}),
        ("skill:trial", "trial_skill", ("skill:draft",), {"displayTitle": "在新场景中试写"}),
        ("skill:revise", "revise_skill", ("validate:evidence", "skill:draft", "skill:trial"), {"displayTitle": "复核证据并修订方法"}),
        ("skill:retrial", "trial_skill", ("skill:revise",), {"displayTitle": "检验修订后的方法"}),
        ("skill:assess", "assess_skill", ("validate:evidence", "skill:revise", "skill:retrial"), {"displayTitle": "评估迁移效果与适用边界"}),
        (
            "report:coverage",
            "coverage_report",
            ("validate:evidence", "skill:trial", "skill:revise", "skill:retrial", "skill:assess"),
            {"displayTitle": "汇总分析与方法检验结果"},
        ),
        (
            "artifact:review",
            "build_review_artifact",
            ("report:coverage",),
            {"displayTitle": "形成待审核分析"},
        ),
    ))
    if len(planned) > len(specs):
        raise ValueError("novel analysis execution recipe has fewer units than the model-authored plan")
    planner_step_ids = tuple(
        planned[min(index * len(planned) // len(specs), len(planned) - 1)]
        for index in range(len(specs))
    )
    steps = tuple(
        ExecutionRecipeStep(
            id=unit_id,
            kind=kind,
            depends_on=dependencies,
            executor="novel_analysis",
            plan_step_id=planner_step_id,
            max_attempts=2 if kind in {
                "extract_section",
                "normalize_entities",
                "aggregate_story",
                "distill_skill", "trial_skill", "revise_skill", "assess_skill",
            } else 1,
            metadata=dict(metadata),
        )
        for (unit_id, kind, dependencies, metadata), planner_step_id in zip(
            specs,
            planner_step_ids,
        )
    )
    mapped = {step.plan_step_id for step in steps}
    if mapped != set(planned):
        raise ValueError("novel analysis execution recipe did not cover the plan")
    canonical = {
        "schemaVersion": NOVEL_ANALYSIS_SCHEMA_VERSION,
        "sections": list(sections),
        **(
            {"segments": [item.to_mapping() for item in frozen_segments]}
            if frozen_segments else {}
        ),
        "steps": [{
            "id": step.id,
            "kind": step.kind,
            "dependsOn": list(step.depends_on),
            "metadata": dict(step.metadata),
        } for step in steps],
    }
    digest = "sha256:" + hashlib.sha256(json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return ExecutionRecipe(
        kind="novel_source_analysis",
        steps=steps,
        max_parallelism=min(4, len(frozen_segments or sections)),
        metadata={
            "recipeVersion": 5,
            "analysisSchemaVersion": NOVEL_ANALYSIS_SCHEMA_VERSION,
            "recipeDigest": digest,
        },
    )


def canonical_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def novel_analysis_model_call_count(recipe: ExecutionRecipe) -> int:
    return sum(
        step.kind in {"extract_section", "normalize_entities", "aggregate_story", "distill_skill", "trial_skill", "revise_skill", "assess_skill"}
        for step in recipe.steps
    )


__all__ = [
    "NOVEL_ANALYSIS_ARTIFACT_KIND",
    "NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX",
    "NOVEL_ANALYSIS_DOMAIN_NAMESPACE",
    "NOVEL_ANALYSIS_REVIEW_ARTIFACT_KIND",
    "NOVEL_ANALYSIS_SCHEMA_VERSION",
    "NovelAnalysisSegment",
    "NovelAnalysisDomainContext",
    "canonical_digest",
    "compile_novel_analysis_recipe",
    "novel_analysis_model_call_count",
]
