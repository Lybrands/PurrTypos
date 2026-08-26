"""Authoritative contracts for evidence-backed source analysis."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from purra.contracts import DomainContext, ExecutionRecipe, ExecutionRecipeStep
from purra.json_values import thaw_json_mapping


NOVEL_ANALYSIS_DOMAIN_NAMESPACE = "purrtypos.novel_analysis"
NOVEL_ANALYSIS_SCHEMA_VERSION = 1
NOVEL_ANALYSIS_ARTIFACT_KIND = "novel_source_analysis_candidate"
NOVEL_ANALYSIS_REVIEW_ARTIFACT_KIND = "novel_source_analysis_review"
NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX = "novel-analysis-artifact://"


@dataclass(frozen=True, slots=True)
class NovelAnalysisDomainContext:
    source_revision_id: str
    command_id: str
    section_ids: tuple[str, ...] = ()
    schema_version: int = NOVEL_ANALYSIS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        revision_id = str(self.source_revision_id or "").strip()
        command_id = str(self.command_id or "").strip()
        section_ids = tuple(
            str(value or "").strip() for value in self.section_ids
        )
        if not revision_id or not command_id:
            raise ValueError("novel analysis revision and command are required")
        if any(not value for value in section_ids):
            raise ValueError("novel analysis section ids must be non-empty")
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("novel analysis section ids must be unique")
        if int(self.schema_version) != NOVEL_ANALYSIS_SCHEMA_VERSION:
            raise ValueError("unsupported novel analysis schema version")
        object.__setattr__(self, "source_revision_id", revision_id)
        object.__setattr__(self, "command_id", command_id)
        object.__setattr__(self, "section_ids", section_ids)
        object.__setattr__(self, "schema_version", int(self.schema_version))

    def to_core_context(self) -> DomainContext:
        return DomainContext(
            namespace=NOVEL_ANALYSIS_DOMAIN_NAMESPACE,
            payload={
                "sourceRevisionId": self.source_revision_id,
                "commandId": self.command_id,
                "sectionIds": list(self.section_ids),
                "analysisSchemaVersion": self.schema_version,
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
        return cls(
            source_revision_id=str(payload.get("sourceRevisionId") or ""),
            command_id=str(payload.get("commandId") or ""),
            section_ids=tuple(section_ids),
            schema_version=int(
                payload.get("analysisSchemaVersion")
                or NOVEL_ANALYSIS_SCHEMA_VERSION
            ),
        )


def compile_novel_analysis_recipe(
    *,
    section_ids: Sequence[str],
    plan_step_ids: Sequence[str],
) -> ExecutionRecipe:
    """Compile the fixed host recipe; model-authored plans cannot change scope."""

    sections = tuple(str(value or "").strip() for value in section_ids)
    planned = tuple(str(value or "").strip() for value in plan_step_ids)
    if not sections or any(not value for value in sections):
        raise ValueError("novel analysis requires bound source sections")
    if not planned or any(not value for value in planned):
        raise ValueError("novel analysis requires admitted plan steps")

    specs: list[tuple[str, str, tuple[str, ...], Mapping[str, Any]]] = []
    extract_ids: list[str] = []
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
    specs.extend((
        (
            "normalize:entities",
            "normalize_entities",
            tuple(extract_ids),
            {"displayTitle": "归一人物、实体与时间线"},
        ),
        (
            "aggregate:story",
            "aggregate_story",
            ("normalize:entities",),
            {"displayTitle": "聚合剧情线与人物认知"},
        ),
        (
            "validate:evidence",
            "validate_evidence",
            ("aggregate:story",),
            {"displayTitle": "校验来源证据"},
        ),
        (
            "report:coverage",
            "coverage_report",
            ("validate:evidence",),
            {"displayTitle": "生成覆盖率与冲突报告"},
        ),
        (
            "artifact:review",
            "build_review_artifact",
            ("report:coverage",),
            {
                "displayTitle": "形成待审核分析",
                "finalResponse": "来源分析已完成，等待用户审核后发布。",
            },
        ),
    ))
    steps = tuple(
        ExecutionRecipeStep(
            id=unit_id,
            kind=kind,
            depends_on=dependencies,
            executor="novel_analysis",
            plan_step_id=planned[index % len(planned)],
            max_attempts=2 if kind in {
                "extract_section",
                "normalize_entities",
                "aggregate_story",
            } else 1,
            metadata=dict(metadata),
        )
        for index, (unit_id, kind, dependencies, metadata) in enumerate(specs)
    )
    mapped = {step.plan_step_id for step in steps}
    if mapped != set(planned):
        # A planner may emit more todos than the minimum recipe has units. Fail
        # closed instead of silently claiming work that the recipe cannot map.
        raise ValueError("novel analysis plan has too many steps")
    canonical = {
        "schemaVersion": NOVEL_ANALYSIS_SCHEMA_VERSION,
        "sections": list(sections),
        "steps": [step.id for step in steps],
    }
    digest = "sha256:" + hashlib.sha256(json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return ExecutionRecipe(
        kind="novel_source_analysis",
        steps=steps,
        max_parallelism=min(4, len(sections)),
        metadata={
            "recipeVersion": 1,
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


__all__ = [
    "NOVEL_ANALYSIS_ARTIFACT_KIND",
    "NOVEL_ANALYSIS_ARTIFACT_REF_PREFIX",
    "NOVEL_ANALYSIS_DOMAIN_NAMESPACE",
    "NOVEL_ANALYSIS_REVIEW_ARTIFACT_KIND",
    "NOVEL_ANALYSIS_SCHEMA_VERSION",
    "NovelAnalysisDomainContext",
    "canonical_digest",
    "compile_novel_analysis_recipe",
]
