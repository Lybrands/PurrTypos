"""Recoverable screenplay tools backed by Agent Core artifact batches."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from agent_core.artifacts import (
    ArtifactAppendCommand,
    ArtifactBatch,
    ArtifactCreateCommand,
    ArtifactFinalizeCommand,
    ArtifactLifecycle,
    ArtifactMutationLease,
    ArtifactRecord,
    ArtifactScope,
    ArtifactStatus,
    ArtifactValidationResult,
    ArtifactWriteClaimCommand,
)
from agent_core.artifacts.errors import ArtifactValidationError
from agent_core.contracts import DomainEffect, ExecutionState
from agent_core.json_values import thaw_json_mapping
from domains.screenplay.adaptation_brief import (
    ADAPTATION_ACTIONS,
    normalize_creative_brief,
)
from domains.screenplay.proposal_rendering import render_screenplay_proposal
from domains.screenplay.review_trace import (
    REVIEW_CATEGORIES,
    REVIEW_EXECUTION_FIELDS,
    REVIEW_SEVERITIES,
    REVIEW_VERIFICATION_STATUSES,
    normalize_review_issues,
    normalize_review_verifications,
)
from domains.screenplay.review_trace import build_revision_trace
from domains.screenplay.scene_trace import normalize_scene_trace
from domains.screenplay.source_scope import scoped_chapters
from domains.screenplay.structure_trace import normalize_structure_trace
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from infrastructure.persistence.sqlite_artifact_claim_repository import (
    SqliteArtifactClaimRepository,
)
from infrastructure.persistence.sqlite_work_item_artifact_lifecycle import (
    SqliteWorkItemArtifactLifecycle,
)


_NAMESPACE = "purrtypos.screenplay"
_SOURCE_ANALYSIS_ARTIFACT = "source_analysis_entries"
_CREATIVE_BRIEF_ARTIFACT = "creative_brief_entries"
_STRUCTURE_ARTIFACT = "screenplay_structure_units"
_SCENE_LIST_ARTIFACT = "scene_list_batches"
_REVIEW_ARTIFACT = "screenplay_review_entries"
_REVISION_ARTIFACT = "screenplay_revision_changes"
_SERIES_FORMATS = frozenset({"连续剧", "竖屏短剧"})
_STRUCTURE_BATCH_LIMIT = 20
_CREATIVE_BRIEF_BATCH_LIMIT = 8
_CREATIVE_BRIEF_DECISION_LIMIT = 100
_REVIEW_BATCH_LIMIT = 8
_REVIEW_ISSUE_LIMIT = 100
_BEAT_UNIT_LIMIT = 80
_EPISODE_UNIT_LIMIT = 1_000
_SOURCE_ANALYSIS_ITEM_LIMITS = {
    "character": 100,
    "plot_event": 300,
    "central_conflict": 50,
    "adaptation_asset": 80,
    "continuity_risk": 80,
    "open_question": 50,
    "evidence": 300,
}
_SOURCE_ANALYSIS_REQUIRED_TYPES = frozenset({"plot_event", "evidence"})
_VALID_SOURCE_TYPES = frozenset({
    "book",
    "chapter",
    "outline",
    "character",
    "setting",
    "background",
})
_FOUNTAIN_HEADING = re.compile(
    r"(?mi)^(?:INT\.?|EXT\.?|INT\.?/EXT\.?|I/E\.?|内景|外景)[^\n]*$"
)


class ScreenplayArtifactInputError(ValueError):
    """Safe, model-correctable artifact input error."""


class ScreenplayArtifactValidator:
    """Inject screenplay correctness without moving domain rules into Core."""

    def __init__(self, db) -> None:
        self._db = db

    async def validate_batch(
        self,
        artifact: ArtifactRecord,
        command: ArtifactAppendCommand,
    ) -> ArtifactValidationResult:
        if artifact.kind == _SOURCE_ANALYSIS_ARTIFACT:
            metadata = thaw_json_mapping(artifact.metadata)
            counts = _source_analysis_counts(metadata)
            allowed_sources = {
                str(value)
                for value in metadata.get("allowedSourceKeys", [])
                if str(value).strip()
            }
            for item in command.items:
                item_type = str(item.get("itemType") or "").strip()
                index = _positive_int(item.get("index"))
                if (
                    item_type not in counts
                    or index is None
                    or index > counts[item_type]
                ):
                    return ArtifactValidationResult(
                        False,
                        "screenplay_source_analysis_item_invalid",
                        {"itemType": item_type, "index": index},
                    )
                if item_type == "evidence":
                    source_key = (
                        f"{str(item.get('sourceType') or '')}:"
                        f"{str(item.get('sourceId') or '')}"
                    )
                    if source_key not in allowed_sources:
                        return ArtifactValidationResult(
                            False,
                            "screenplay_source_evidence_unread",
                            {"sourceKey": source_key},
                        )
            return ArtifactValidationResult(True)
        if artifact.kind == _CREATIVE_BRIEF_ARTIFACT:
            metadata = thaw_json_mapping(artifact.metadata)
            expected_decision_count = _creative_brief_decision_count(metadata)
            allowed_evidence = set(_creative_brief_evidence_keys(metadata))
            for item in command.items:
                item_type = str(item.get("itemType") or "").strip()
                if item_type == "brief_content":
                    valid = True
                    item_id = "brief"
                elif item_type == "adaptation_decision":
                    index = _positive_int(item.get("index"))
                    valid = (
                        index is not None
                        and index <= expected_decision_count
                    )
                    item_id = str(index or "")
                    for anchor in item.get("sourceAnchors", []):
                        if not isinstance(anchor, Mapping):
                            valid = False
                            break
                        source_key = (
                            f"{str(anchor.get('sourceType') or '')}:"
                            f"{str(anchor.get('sourceId') or '')}"
                        )
                        if source_key not in allowed_evidence:
                            valid = False
                            break
                else:
                    valid = False
                    item_id = ""
                if not valid:
                    return ArtifactValidationResult(
                        False,
                        "screenplay_creative_brief_item_invalid",
                        {"itemType": item_type, "itemId": item_id},
                    )
            return ArtifactValidationResult(True)
        if artifact.kind == _STRUCTURE_ARTIFACT:
            metadata = thaw_json_mapping(artifact.metadata)
            expected_unit_count = _required_count(
                metadata.get("expectedUnitCount"),
                maximum=_EPISODE_UNIT_LIMIT,
                label="expectedUnitCount",
            )
            expected_decision_ids = set(
                _structure_decision_ids(metadata)
            )
            for item in command.items:
                item_type = str(item.get("itemType") or "").strip()
                if item_type == "structure_unit":
                    index = _positive_int(item.get("index"))
                    valid = (
                        index is not None
                        and index <= expected_unit_count
                        and bool(str(item.get("id") or "").strip())
                    )
                    item_id = str(index or "")
                elif item_type == "decision_coverage":
                    item_id = str(item.get("decisionId") or "").strip()
                    valid = item_id in expected_decision_ids
                else:
                    item_id = ""
                    valid = False
                if not valid:
                    return ArtifactValidationResult(
                        False,
                        "screenplay_structure_item_invalid",
                        {"itemType": item_type, "itemId": item_id},
                    )
            return ArtifactValidationResult(True)
        if artifact.kind == _SCENE_LIST_ARTIFACT:
            for item in command.items:
                if not str(item.get("id") or "").strip():
                    return ArtifactValidationResult(
                        False,
                        "screenplay_scene_id_missing",
                    )
                if _positive_int(item.get("order")) is None:
                    return ArtifactValidationResult(
                        False,
                        "screenplay_scene_order_invalid",
                        {"sceneId": str(item.get("id") or "")},
                    )
            return ArtifactValidationResult(True)
        if artifact.kind == _REVIEW_ARTIFACT:
            metadata = thaw_json_mapping(artifact.metadata)
            expected_issue_count = _review_issue_count(metadata)
            verification_issue_ids = _review_verification_issue_ids(metadata)
            allowed_scene_ids = set(_review_scene_ids(metadata))
            for item in command.items:
                item_type = str(item.get("itemType") or "").strip()
                if item_type == "review_summary":
                    valid = True
                    item_id = "summary"
                elif item_type == "review_issue":
                    index = _positive_int(item.get("index"))
                    scene_ids = {
                        str(value)
                        for value in item.get("sceneIds", [])
                        if str(value).strip()
                    }
                    valid = (
                        index is not None
                        and index <= expected_issue_count
                        and bool(scene_ids)
                        and scene_ids.issubset(allowed_scene_ids)
                    )
                    item_id = str(index or "")
                elif item_type == "verification_result":
                    index = _positive_int(item.get("index"))
                    valid = (
                        index is not None
                        and index <= len(verification_issue_ids)
                    )
                    item_id = str(index or "")
                else:
                    valid = False
                    item_id = ""
                if not valid:
                    return ArtifactValidationResult(
                        False,
                        "screenplay_review_item_invalid",
                        {"itemType": item_type, "itemId": item_id},
                    )
            return ArtifactValidationResult(True)
        if artifact.kind == _REVISION_ARTIFACT:
            metadata = thaw_json_mapping(artifact.metadata)
            allowed_scenes = {
                str(value)
                for value in metadata.get("expectedSceneIds", [])
                if str(value).strip()
            }
            allowed_issues = {
                str(value)
                for value in metadata.get("reviewIssueIds", [])
                if str(value).strip()
            }
            for item in command.items:
                item_type = str(item.get("itemType") or "").strip()
                if item_type == "scene_revision":
                    scene_id = str(item.get("sceneId") or "").strip()
                    scene_text = str(item.get("sceneText") or "").strip()
                    valid = scene_id in allowed_scenes and bool(scene_text)
                    item_id = scene_id
                elif item_type == "issue_resolution":
                    item_id = str(item.get("issueId") or "").strip()
                    valid = item_id in allowed_issues
                else:
                    item_id = ""
                    valid = False
                if not valid:
                    return ArtifactValidationResult(
                        False,
                        "screenplay_revision_item_invalid",
                        {"itemType": item_type, "itemId": item_id},
                    )
            return ArtifactValidationResult(True)
        return ArtifactValidationResult(
            False,
            "screenplay_artifact_kind_unsupported",
        )

    async def validate_finalization(
        self,
        artifact: ArtifactRecord,
        batches: Sequence[ArtifactBatch],
        command: ArtifactFinalizeCommand,
    ) -> ArtifactValidationResult:
        del command
        if artifact.kind == _SOURCE_ANALYSIS_ARTIFACT:
            metadata = thaw_json_mapping(artifact.metadata)
            expected = set(_source_analysis_expected_coverage(metadata))
            observed = {
                key
                for batch in batches
                for key in batch.coverage_keys
            }
            if observed != expected:
                return ArtifactValidationResult(
                    False,
                    "screenplay_source_analysis_coverage_incomplete",
                    {
                        "missingCoverageKeys": sorted(expected - observed),
                        "unexpectedCoverageKeys": sorted(observed - expected),
                    },
                )
            return ArtifactValidationResult(True)
        if artifact.kind == _CREATIVE_BRIEF_ARTIFACT:
            metadata = thaw_json_mapping(artifact.metadata)
            expected = set(_creative_brief_expected_coverage(metadata))
            observed = {
                key
                for batch in batches
                for key in batch.coverage_keys
            }
            if observed != expected:
                return ArtifactValidationResult(
                    False,
                    "screenplay_creative_brief_coverage_incomplete",
                    {
                        "missingCoverageKeys": sorted(expected - observed),
                        "unexpectedCoverageKeys": sorted(observed - expected),
                    },
                )
            source_analysis_id = str(
                metadata.get("sourceAnalysisId") or ""
            ).strip()
            source_analysis_content: Mapping[str, Any] | None = None
            if source_analysis_id:
                source_analysis = await self._db.fetch_one(
                    "SELECT content_json FROM screenplay_documents "
                    "WHERE id = ? AND project_id = ? "
                    "AND kind = 'source_analysis' AND status = 'accepted'",
                    [source_analysis_id, artifact.owner_id],
                )
                if source_analysis is None:
                    return ArtifactValidationResult(
                        False,
                        "screenplay_source_analysis_changed",
                    )
                source_analysis_content = _json_object(
                    source_analysis.get("content_json")
                )
            try:
                brief = _assemble_creative_brief(
                    metadata,
                    _batch_items(batches),
                )
                normalize_creative_brief(
                    brief,
                    project_format=str(metadata.get("projectFormat") or ""),
                    source_analysis=source_analysis_content,
                )
            except (ScreenplayArtifactInputError, ValueError) as error:
                return ArtifactValidationResult(
                    False,
                    "screenplay_creative_brief_invalid",
                    {"reason": str(error)},
                )
            return ArtifactValidationResult(True)
        if artifact.kind == _STRUCTURE_ARTIFACT:
            metadata = thaw_json_mapping(artifact.metadata)
            expected = set(_structure_expected_coverage(metadata))
            observed = {
                key
                for batch in batches
                for key in batch.coverage_keys
            }
            if observed != expected:
                return ArtifactValidationResult(
                    False,
                    "screenplay_structure_coverage_incomplete",
                    {
                        "missingCoverageKeys": sorted(expected - observed),
                        "unexpectedCoverageKeys": sorted(observed - expected),
                    },
                )
            brief_id = str(metadata.get("creativeBriefId") or "").strip()
            brief = await self._db.fetch_one(
                "SELECT content_json FROM screenplay_documents "
                "WHERE id = ? AND project_id = ? AND kind = 'creative_brief' "
                "AND status = 'accepted'",
                [brief_id, artifact.owner_id],
            )
            if brief is None:
                return ArtifactValidationResult(
                    False,
                    "screenplay_creative_brief_changed",
                )
            try:
                units, decision_coverage = _assemble_structure_items(
                    metadata,
                    _batch_items(batches),
                )
                normalize_structure_trace(
                    kind=str(metadata.get("structureKind") or ""),
                    units=units,
                    decision_coverage=decision_coverage,
                    creative_brief=_json_object(brief.get("content_json")),
                    require_adaptation_decisions=bool(
                        metadata.get("requiresAdaptationDecisions")
                    ),
                )
            except (ScreenplayArtifactInputError, ValueError) as error:
                return ArtifactValidationResult(
                    False,
                    "screenplay_structure_invalid",
                    {"reason": str(error)},
                )
            return ArtifactValidationResult(True)
        if artifact.kind == _SCENE_LIST_ARTIFACT:
            metadata = thaw_json_mapping(artifact.metadata)
            structure_id = str(metadata.get("structureId") or "").strip()
            structure = await self._db.fetch_one(
                "SELECT kind, content_json FROM screenplay_documents "
                "WHERE id = ? AND project_id = ? AND status = 'accepted' "
                "AND kind IN ('beat_sheet', 'episode_outline')",
                [structure_id, artifact.owner_id],
            )
            if structure is None:
                return ArtifactValidationResult(
                    False,
                    "screenplay_structure_changed",
                )
            try:
                normalize_scene_trace(
                    scenes=_batch_items(batches),
                    structure_kind=str(structure["kind"]),
                    structure_content=_json_object(
                        structure.get("content_json")
                    ),
                )
            except ValueError as error:
                return ArtifactValidationResult(
                    False,
                    "screenplay_scene_list_invalid",
                    {"reason": str(error)},
                )
            return ArtifactValidationResult(True)
        if artifact.kind == _REVIEW_ARTIFACT:
            metadata = thaw_json_mapping(artifact.metadata)
            expected = set(_review_expected_coverage(metadata))
            observed = {
                key
                for batch in batches
                for key in batch.coverage_keys
            }
            if observed != expected:
                return ArtifactValidationResult(
                    False,
                    "screenplay_review_coverage_incomplete",
                    {
                        "missingCoverageKeys": sorted(expected - observed),
                        "unexpectedCoverageKeys": sorted(observed - expected),
                    },
                )
            try:
                await _validate_and_assemble_review(
                    self._db,
                    artifact.owner_id,
                    metadata,
                    _batch_items(batches),
                )
            except (ScreenplayArtifactInputError, ValueError) as error:
                return ArtifactValidationResult(
                    False,
                    "screenplay_review_invalid",
                    {"reason": str(error)},
                )
            return ArtifactValidationResult(True)
        if artifact.kind == _REVISION_ARTIFACT:
            metadata = thaw_json_mapping(artifact.metadata)
            expected = {
                *(f"scene:{value}" for value in metadata.get("expectedSceneIds", [])),
                *(f"issue:{value}" for value in metadata.get("reviewIssueIds", [])),
            }
            observed = {
                key
                for batch in batches
                for key in batch.coverage_keys
            }
            if observed != expected:
                return ArtifactValidationResult(
                    False,
                    "screenplay_revision_coverage_incomplete",
                    {
                        "missingCoverageKeys": sorted(expected - observed),
                        "unexpectedCoverageKeys": sorted(observed - expected),
                    },
                )
            try:
                await _preflight_revision_finalization(
                    self._db,
                    artifact,
                    batches,
                )
            except (ScreenplayArtifactInputError, ValueError) as error:
                return ArtifactValidationResult(
                    False,
                    "screenplay_revision_invalid",
                    {"reason": str(error)},
                )
            return ArtifactValidationResult(True)
        return ArtifactValidationResult(
            False,
            "screenplay_artifact_kind_unsupported",
        )


class ScreenplayArtifactToolService:
    """Host-owned lifecycle for large scene-list and revision proposals."""

    def __init__(self, db) -> None:
        self._db = db
        self._repository = SqliteArtifactRepository(db)
        self._claims = SqliteArtifactClaimRepository(db)
        self._work_item_artifacts = SqliteWorkItemArtifactLifecycle(db)
        self._lifecycle = ArtifactLifecycle(
            self._repository,
            validator=ScreenplayArtifactValidator(db),
        )

    async def begin_source_analysis(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any]]:
        run_id = _required_run_id(state)
        continued = await self._find_for_run(
            project,
            run_id,
            _SOURCE_ANALYSIS_ARTIFACT,
        )
        if continued is not None and continued.scope is ArtifactScope.WORK_ITEM:
            batches = tuple(
                await self._repository.list_batches(continued.id)
            )
            return _source_analysis_progress(
                continued,
                batches=batches,
            ), []
        counts = _required_source_analysis_counts(
            arguments.get("expectedItemCounts")
        )
        coverage, allowed_source_keys = await self._source_analysis_coverage(
            project,
            run_id,
        )
        if not allowed_source_keys:
            raise ScreenplayArtifactInputError(
                "Source analysis requires source material read receipts from "
                "the current Agent run."
            )
        limitations = _optional_text_list(
            arguments.get("coverageLimitations"),
            label="coverageLimitations",
            maximum_items=20,
            maximum_text=2_000,
        )
        coverage["limitations"] = list(dict.fromkeys((
            *coverage.get("limitations", []),
            *limitations,
        )))
        accepted = await self._db.fetch_one(
            "SELECT id FROM screenplay_documents WHERE project_id = ? "
            "AND kind = 'source_analysis' AND status = 'accepted' "
            "ORDER BY version DESC LIMIT 1",
            [project["id"]],
        )
        metadata = {
            "title": _title(arguments, "Agent 原作范围分析"),
            "rangeSummary": _required_text(
                arguments.get("rangeSummary"),
                "rangeSummary",
                maximum=4_000,
            ),
            "narrativeSummary": _required_text(
                arguments.get("narrativeSummary"),
                "narrativeSummary",
                maximum=20_000,
            ),
            "expectedItemCounts": counts,
            "coverage": coverage,
            "allowedSourceKeys": allowed_source_keys,
            "previousAnalysisId": (
                str(accepted["id"]) if accepted is not None else ""
            ),
        }
        existing = await self._find_for_run(
            project,
            run_id,
            _SOURCE_ANALYSIS_ARTIFACT,
        )
        if existing is not None:
            if thaw_json_mapping(existing.metadata) != metadata:
                raise ScreenplayArtifactInputError(
                    "The current run already owns a source-analysis artifact "
                    "with different metadata."
                )
            batches = tuple(await self._repository.list_batches(existing.id))
            return _source_analysis_progress(existing, batches=batches), []
        artifact = await self._begin_artifact(ArtifactCreateCommand(
            namespace=_NAMESPACE,
            kind=_SOURCE_ANALYSIS_ARTIFACT,
            owner_id=str(project["id"]),
            run_id=run_id,
            expected_item_count=sum(counts.values()),
            metadata=metadata,
        ))
        return _source_analysis_progress(artifact), []

    async def append_source_analysis(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any]]:
        artifact = await self._required_artifact(
            project,
            state,
            _SOURCE_ANALYSIS_ARTIFACT,
        )
        metadata = thaw_json_mapping(artifact.metadata)
        counts = _source_analysis_counts(metadata)
        allowed_sources = {
            str(value)
            for value in metadata.get("allowedSourceKeys", [])
            if str(value).strip()
        }
        items = _required_mapping_batch(
            arguments.get("items"),
            label="items",
            maximum=20,
        )
        existing_batches = tuple(
            await self._repository.list_batches(artifact.id)
        )
        occupied = _source_analysis_occupied_indices(existing_batches)
        assigned = {
            item_type: set(values)
            for item_type, values in occupied.items()
        }
        normalized_items: list[dict[str, Any]] = []
        discarded: dict[str, int] = {}
        for item in items:
            item_type = str(item.get("itemType") or "").strip()
            if item_type not in counts:
                raise ScreenplayArtifactInputError(
                    "source-analysis itemType is invalid."
                )
            index = next((
                candidate
                for candidate in range(1, counts[item_type] + 1)
                if candidate not in assigned[item_type]
            ), None)
            if index is None:
                discarded[item_type] = discarded.get(item_type, 0) + 1
                continue
            normalized_items.append(_normalize_source_analysis_item(
                item,
                index=index,
                counts=counts,
                allowed_sources=allowed_sources,
            ))
            assigned[item_type].add(index)
        normalized = tuple(normalized_items)
        if not normalized:
            progress = _source_analysis_progress(
                artifact,
                batches=existing_batches,
                discarded=discarded,
            )
            raise ScreenplayArtifactInputError(
                "The submitted source-analysis item types have no remaining "
                "capacity. Follow the host progress cursor: "
                + json.dumps({
                    "remainingItemCounts": progress["remainingItemCounts"],
                    "nextItemIndices": progress["nextItemIndices"],
                }, ensure_ascii=False, sort_keys=True)
            )
        coverage = tuple(
            f"{item['itemType']}:{item['index']}" for item in normalized
        )
        current = await self._append(
            artifact,
            run_id=_required_run_id(state),
            items=normalized,
            coverage=coverage,
            batch_prefix="source-analysis",
        )
        current_batches = tuple(
            await self._repository.list_batches(current.id)
        )
        return _source_analysis_progress(
            current,
            batches=current_batches,
            discarded=discarded,
        ), []

    async def finalize_source_analysis(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any], tuple[DomainEffect, ...]]:
        del arguments
        artifact = await self._required_artifact(
            project,
            state,
            _SOURCE_ANALYSIS_ARTIFACT,
            allow_finalized=True,
        )
        metadata = thaw_json_mapping(artifact.metadata)
        expected_coverage = _source_analysis_expected_coverage(metadata)
        if artifact.status is ArtifactStatus.OPEN:
            try:
                artifact = await self._lifecycle.finalize(
                    ArtifactFinalizeCommand(
                        artifact_id=artifact.id,
                        expected_revision=artifact.revision,
                        expected_item_count=artifact.expected_item_count,
                        expected_coverage_keys=expected_coverage,
                        resource_ref=_resource_ref(artifact),
                        write_lease=await self._mutation_lease(
                            artifact,
                            _required_run_id(state),
                        ),
                        complete_work_item=(
                            artifact.scope is ArtifactScope.WORK_ITEM
                        ),
                    )
                )
            except ArtifactValidationError as error:
                raise ScreenplayArtifactInputError(
                    str(error.details.get("reason") or error)
                ) from error
        grouped = _group_source_analysis_items(
            _batch_items(await self._repository.list_batches(artifact.id))
        )
        analysis = {
            "rangeSummary": str(metadata.get("rangeSummary") or ""),
            "narrativeSummary": str(metadata.get("narrativeSummary") or ""),
            "coverage": thaw_json_mapping(metadata.get("coverage")),
            "characters": [
                {
                    "name": item["name"],
                    "role": item["role"],
                    "goal": item.get("goal", ""),
                    "conflict": item.get("conflict", ""),
                }
                for item in grouped["character"]
            ],
            "plotEvents": [
                {
                    "order": item["index"],
                    "event": item["event"],
                    "consequence": item["consequence"],
                }
                for item in grouped["plot_event"]
            ],
            "centralConflicts": [
                item["text"] for item in grouped["central_conflict"]
            ],
            "adaptationAssets": [
                item["text"] for item in grouped["adaptation_asset"]
            ],
            "continuityRisks": [
                item["text"] for item in grouped["continuity_risk"]
            ],
            "openQuestions": [
                item["text"] for item in grouped["open_question"]
            ],
            "evidence": [
                {
                    "sourceType": item["sourceType"],
                    "sourceId": item["sourceId"],
                    "claim": item["claim"],
                }
                for item in grouped["evidence"]
            ],
        }
        content_json = {
            "schemaVersion": 1,
            "generatedBy": "screenplay-agent",
            "documentKind": "source_analysis",
            "sourceAnalysisArtifactId": artifact.id,
            "artifactRef": artifact.resource_ref,
            "analysis": analysis,
        }
        previous_analysis_id = str(
            metadata.get("previousAnalysisId") or ""
        ).strip()
        return _proposal_result(
            kind="source_analysis",
            title=str(metadata.get("title") or "Agent 原作范围分析"),
            content_json=content_json,
            content_text=None,
            derived_from_ids=(
                [previous_analysis_id] if previous_analysis_id else []
            ),
        )

    async def _source_analysis_coverage(
        self,
        project: Mapping[str, Any],
        run_id: str,
    ) -> tuple[dict[str, Any], list[str]]:
        chapters = await scoped_chapters(self._db, project)
        chapter_ids = [str(chapter["id"]) for chapter in chapters]
        rows = await self._db.fetch_all(
            "SELECT source_type, source_id, coverage_mode FROM "
            "screenplay_source_refs WHERE project_id = ? AND agent_run_id = ? "
            "ORDER BY id ASC",
            [project["id"], run_id],
        )
        allowed_source_keys = list(dict.fromkeys(
            f"{str(row.get('source_type') or '')}:"
            f"{str(row.get('source_id') or '')}"
            for row in rows
            if str(row.get("source_type") or "").strip()
            and str(row.get("source_id") or "").strip()
        ))
        chapter_modes: dict[str, str] = {}
        for row in rows:
            if str(row.get("source_type") or "") != "chapter":
                continue
            source_id = str(row.get("source_id") or "")
            mode = str(row.get("coverage_mode") or "referenced")
            current = chapter_modes.get(source_id)
            if mode == "full" or current is None:
                chapter_modes[source_id] = mode
            elif mode == "sampled" and current != "full":
                chapter_modes[source_id] = mode
            elif mode == "passage" and current not in {"full", "sampled"}:
                chapter_modes[source_id] = mode
        read_ids = [
            chapter_id for chapter_id in chapter_ids
            if chapter_modes.get(chapter_id) == "full"
        ]
        sampled_ids = [
            chapter_id for chapter_id in chapter_ids
            if chapter_modes.get(chapter_id) in {"sampled", "passage"}
        ]
        missing_count = len(chapter_ids) - len(read_ids) - len(sampled_ids)
        limitations: list[str] = []
        if sampled_ids:
            limitations.append(
                f"{len(sampled_ids)} 个章节仅获得抽样或片段文本，细节判断可能受限。"
            )
        if missing_count:
            limitations.append(
                f"本次运行仍有 {missing_count} 个锁定范围章节未形成正文读取凭证。"
            )
        return ({
            "selectedChapterCount": len(chapter_ids),
            "readChapterIds": read_ids,
            "sampledChapterIds": sampled_ids,
            "limitations": limitations,
        }, allowed_source_keys)

    async def begin_creative_brief(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any]]:
        run_id = _required_run_id(state)
        continued = await self._find_for_run(
            project,
            run_id,
            _CREATIVE_BRIEF_ARTIFACT,
        )
        if continued is not None and continued.scope is ArtifactScope.WORK_ITEM:
            return _creative_brief_progress(continued), []
        is_book_adaptation = bool(project.get("source_book_id"))
        expected_decision_count = _required_non_negative_count(
            arguments.get("expectedDecisionCount"),
            maximum=_CREATIVE_BRIEF_DECISION_LIMIT,
            label="expectedDecisionCount",
        )
        if is_book_adaptation and expected_decision_count < 1:
            raise ScreenplayArtifactInputError(
                "Book adaptations require at least one adaptation decision."
            )
        if not is_book_adaptation and expected_decision_count != 0:
            raise ScreenplayArtifactInputError(
                "Original projects must set expectedDecisionCount to 0."
            )
        source_analysis = (
            await self._accepted_source_analysis(project)
            if is_book_adaptation
            else None
        )
        source_analysis_content = (
            _json_object(source_analysis.get("content_json"))
            if source_analysis is not None
            else {}
        )
        if source_analysis is not None:
            parent_document_ids = [str(source_analysis["id"])]
        else:
            latest_brief = await self._db.fetch_one(
                "SELECT id FROM screenplay_documents WHERE project_id = ? "
                "AND kind = 'creative_brief' "
                "ORDER BY version DESC LIMIT 1",
                [project["id"]],
            )
            parent_document_ids = (
                [str(latest_brief["id"])]
                if latest_brief is not None
                else []
            )
        metadata = {
            "title": _title(arguments, "Agent 创作简报提案"),
            "projectFormat": str(project.get("format") or ""),
            "isBookAdaptation": is_book_adaptation,
            "expectedDecisionCount": expected_decision_count,
            "sourceAnalysisId": (
                str(source_analysis["id"])
                if source_analysis is not None
                else ""
            ),
            "allowedEvidenceKeys": _source_analysis_evidence_keys(
                source_analysis_content
            ),
            "sourceLimitations": _source_analysis_limitations(
                source_analysis_content
            ),
            "parentDocumentIds": parent_document_ids,
        }
        existing = await self._find_for_run(
            project,
            run_id,
            _CREATIVE_BRIEF_ARTIFACT,
        )
        if existing is not None:
            if thaw_json_mapping(existing.metadata) != metadata:
                raise ScreenplayArtifactInputError(
                    "The current run already owns a creative-brief artifact "
                    "with different metadata."
                )
            return _creative_brief_progress(existing), []
        artifact = await self._begin_artifact(ArtifactCreateCommand(
            namespace=_NAMESPACE,
            kind=_CREATIVE_BRIEF_ARTIFACT,
            owner_id=str(project["id"]),
            run_id=run_id,
            expected_item_count=expected_decision_count + 1,
            metadata=metadata,
        ))
        return _creative_brief_progress(artifact), []

    async def append_creative_brief(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any]]:
        artifact = await self._required_artifact(
            project,
            state,
            _CREATIVE_BRIEF_ARTIFACT,
        )
        metadata = thaw_json_mapping(artifact.metadata)
        items = _required_mapping_batch(
            arguments.get("items"),
            label="items",
            maximum=_CREATIVE_BRIEF_BATCH_LIMIT,
        )
        normalized = tuple(
            _normalize_creative_brief_item(item, metadata=metadata)
            for item in items
        )
        coverage = tuple(
            _creative_brief_item_coverage(item) for item in normalized
        )
        current = await self._append(
            artifact,
            run_id=_required_run_id(state),
            items=normalized,
            coverage=coverage,
            batch_prefix="creative-brief",
        )
        return _creative_brief_progress(current), []

    async def finalize_creative_brief(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any], tuple[DomainEffect, ...]]:
        del arguments
        artifact = await self._required_artifact(
            project,
            state,
            _CREATIVE_BRIEF_ARTIFACT,
            allow_finalized=True,
        )
        metadata = thaw_json_mapping(artifact.metadata)
        expected_coverage = _creative_brief_expected_coverage(metadata)
        if artifact.status is ArtifactStatus.OPEN:
            try:
                artifact = await self._lifecycle.finalize(
                    ArtifactFinalizeCommand(
                        artifact_id=artifact.id,
                        expected_revision=artifact.revision,
                        expected_item_count=artifact.expected_item_count,
                        expected_coverage_keys=expected_coverage,
                        resource_ref=_resource_ref(artifact),
                        write_lease=await self._mutation_lease(
                            artifact,
                            _required_run_id(state),
                        ),
                        complete_work_item=(
                            artifact.scope is ArtifactScope.WORK_ITEM
                        ),
                    )
                )
            except ArtifactValidationError as error:
                raise ScreenplayArtifactInputError(
                    str(error.details.get("reason") or error)
                ) from error
        source_analysis_id = str(
            metadata.get("sourceAnalysisId") or ""
        ).strip()
        source_analysis_content: Mapping[str, Any] | None = None
        if source_analysis_id:
            source_analysis = await self._accepted_source_analysis(project)
            if str(source_analysis["id"]) != source_analysis_id:
                raise ScreenplayArtifactInputError(
                    "The accepted source analysis changed before creative-"
                    "brief finalization."
                )
            source_analysis_content = _json_object(
                source_analysis.get("content_json")
            )
        brief = _assemble_creative_brief(
            metadata,
            _batch_items(await self._repository.list_batches(artifact.id)),
        )
        try:
            normalized_brief = normalize_creative_brief(
                brief,
                project_format=str(metadata.get("projectFormat") or ""),
                source_analysis=source_analysis_content,
            )
        except ValueError as error:
            raise ScreenplayArtifactInputError(str(error)) from error
        content_json = {
            "schemaVersion": 1,
            "generatedBy": "screenplay-agent",
            "documentKind": "creative_brief",
            "creativeBriefArtifactId": artifact.id,
            "artifactRef": artifact.resource_ref,
            "brief": normalized_brief,
        }
        if source_analysis_id:
            content_json["sourceAnalysisId"] = source_analysis_id
        parent_document_ids = [
            str(value)
            for value in metadata.get("parentDocumentIds", [])
            if str(value).strip()
        ]
        return _proposal_result(
            kind="creative_brief",
            title=str(metadata.get("title") or "Agent 创作简报提案"),
            content_json=content_json,
            content_text=None,
            derived_from_ids=parent_document_ids,
        )

    async def begin_structure(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any]]:
        run_id = _required_run_id(state)
        continued = await self._find_for_run(
            project,
            run_id,
            _STRUCTURE_ARTIFACT,
        )
        if continued is not None and continued.scope is ArtifactScope.WORK_ITEM:
            return _structure_progress(continued), []
        brief = await self._accepted_creative_brief(project)
        brief_content = _json_object(brief.get("content_json"))
        structure_kind = (
            "episode_outline"
            if str(project.get("format") or "") in _SERIES_FORMATS
            else "beat_sheet"
        )
        maximum_units = (
            _EPISODE_UNIT_LIMIT
            if structure_kind == "episode_outline"
            else _BEAT_UNIT_LIMIT
        )
        expected_unit_count = _required_count(
            arguments.get("expectedUnitCount"),
            maximum=maximum_units,
            label="expectedUnitCount",
        )
        planned_episode_count = _brief_episode_count(brief_content)
        if (
            structure_kind == "episode_outline"
            and planned_episode_count is not None
            and expected_unit_count != planned_episode_count
        ):
            raise ScreenplayArtifactInputError(
                "expectedUnitCount must match the accepted creative brief's "
                "episodeCount."
            )
        expected_decisions = _brief_structure_decisions(brief_content)
        requires_adaptation_decisions = bool(project.get("source_book_id"))
        if requires_adaptation_decisions and not expected_decisions:
            raise ScreenplayArtifactInputError(
                "Book adaptations require an accepted creative brief with "
                "adaptation decisions before structure design."
            )
        metadata = {
            "title": _title(
                arguments,
                "Agent 分集结构提案"
                if structure_kind == "episode_outline"
                else "Agent 节拍表提案",
            ),
            "structureKind": structure_kind,
            "creativeBriefId": str(brief["id"]),
            "expectedUnitCount": expected_unit_count,
            "expectedDecisions": expected_decisions,
            "requiresAdaptationDecisions": requires_adaptation_decisions,
        }
        existing = await self._find_for_run(
            project,
            run_id,
            _STRUCTURE_ARTIFACT,
        )
        if existing is not None:
            if thaw_json_mapping(existing.metadata) != metadata:
                raise ScreenplayArtifactInputError(
                    "The current run already owns a structure artifact with "
                    "different metadata."
                )
            return _structure_progress(existing), []
        artifact = await self._begin_artifact(ArtifactCreateCommand(
            namespace=_NAMESPACE,
            kind=_STRUCTURE_ARTIFACT,
            owner_id=str(project["id"]),
            run_id=run_id,
            expected_item_count=(
                expected_unit_count + len(expected_decisions)
            ),
            metadata=metadata,
        ))
        return _structure_progress(artifact), []

    async def append_structure(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any]]:
        artifact = await self._required_artifact(
            project,
            state,
            _STRUCTURE_ARTIFACT,
        )
        metadata = thaw_json_mapping(artifact.metadata)
        items = _required_mapping_batch(
            arguments.get("items"),
            label="items",
            maximum=_STRUCTURE_BATCH_LIMIT,
        )
        normalized = tuple(
            _normalize_structure_item(item, metadata=metadata)
            for item in items
        )
        coverage = tuple(
            _structure_item_coverage(item) for item in normalized
        )
        current = await self._append(
            artifact,
            run_id=_required_run_id(state),
            items=normalized,
            coverage=coverage,
            batch_prefix="structure",
        )
        return _structure_progress(current), []

    async def finalize_structure(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any], tuple[DomainEffect, ...]]:
        del arguments
        artifact = await self._required_artifact(
            project,
            state,
            _STRUCTURE_ARTIFACT,
            allow_finalized=True,
        )
        metadata = thaw_json_mapping(artifact.metadata)
        expected_coverage = _structure_expected_coverage(metadata)
        if artifact.status is ArtifactStatus.OPEN:
            try:
                artifact = await self._lifecycle.finalize(
                    ArtifactFinalizeCommand(
                        artifact_id=artifact.id,
                        expected_revision=artifact.revision,
                        expected_item_count=artifact.expected_item_count,
                        expected_coverage_keys=expected_coverage,
                        resource_ref=_resource_ref(artifact),
                        write_lease=await self._mutation_lease(
                            artifact,
                            _required_run_id(state),
                        ),
                        complete_work_item=(
                            artifact.scope is ArtifactScope.WORK_ITEM
                        ),
                    )
                )
            except ArtifactValidationError as error:
                raise ScreenplayArtifactInputError(
                    str(error.details.get("reason") or error)
                ) from error
        brief = await self._accepted_creative_brief(project)
        if str(brief["id"]) != str(metadata.get("creativeBriefId") or ""):
            raise ScreenplayArtifactInputError(
                "The accepted creative brief changed before structure "
                "finalization."
            )
        units, decision_coverage = _assemble_structure_items(
            metadata,
            _batch_items(await self._repository.list_batches(artifact.id)),
        )
        structure_kind = str(metadata.get("structureKind") or "")
        try:
            normalized_units, normalized_coverage = normalize_structure_trace(
                kind=structure_kind,
                units=units,
                decision_coverage=decision_coverage,
                creative_brief=_json_object(brief.get("content_json")),
                require_adaptation_decisions=bool(
                    metadata.get("requiresAdaptationDecisions")
                ),
            )
        except ValueError as error:
            raise ScreenplayArtifactInputError(str(error)) from error
        units_key = (
            "episodes" if structure_kind == "episode_outline" else "beats"
        )
        content_json = {
            "schemaVersion": 1,
            "generatedBy": "screenplay-agent",
            "documentKind": structure_kind,
            "creativeBriefId": str(brief["id"]),
            "structureArtifactId": artifact.id,
            "artifactRef": artifact.resource_ref,
            units_key: normalized_units,
            "decisionCoverage": normalized_coverage,
        }
        return _proposal_result(
            kind=structure_kind,
            title=str(metadata.get("title") or "Agent 结构提案"),
            content_json=content_json,
            content_text=None,
            derived_from_ids=[str(brief["id"])],
        )

    async def begin_scene_list(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any]]:
        run_id = _required_run_id(state)
        continued = await self._find_for_run(
            project,
            run_id,
            _SCENE_LIST_ARTIFACT,
        )
        if continued is not None and continued.scope is ArtifactScope.WORK_ITEM:
            return _artifact_progress(continued), []
        structure = await self._accepted_structure(project)
        expected_count = _required_count(
            arguments.get("expectedSceneCount"),
            maximum=300,
            label="expectedSceneCount",
        )
        title = _title(arguments, "Agent 场景表提案")
        existing = await self._find_for_run(
            project,
            run_id,
            _SCENE_LIST_ARTIFACT,
        )
        if existing is not None:
            metadata = thaw_json_mapping(existing.metadata)
            if (
                str(metadata.get("structureId") or "")
                != str(structure["id"])
                or existing.expected_item_count != expected_count
            ):
                raise ScreenplayArtifactInputError(
                    "The current run already owns a scene-list artifact with "
                    "a different structure or expected count."
                )
            return _artifact_progress(existing), []
        artifact = await self._begin_artifact(ArtifactCreateCommand(
            namespace=_NAMESPACE,
            kind=_SCENE_LIST_ARTIFACT,
            owner_id=str(project["id"]),
            run_id=run_id,
            expected_item_count=expected_count,
            metadata={
                "title": title,
                "structureId": str(structure["id"]),
                "structureKind": str(structure["kind"]),
            },
        ))
        return _artifact_progress(artifact), []

    async def append_scene_list(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any]]:
        artifact = await self._required_artifact(
            project,
            state,
            _SCENE_LIST_ARTIFACT,
        )
        items = _required_mapping_batch(
            arguments.get("scenes"),
            label="scenes",
            maximum=10,
        )
        coverage = tuple(
            _required_text(item.get("id"), "scene id", maximum=100)
            for item in items
        )
        current = await self._append(
            artifact,
            run_id=_required_run_id(state),
            items=items,
            coverage=coverage,
            batch_prefix="scenes",
        )
        return _artifact_progress(current), []

    async def finalize_scene_list(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any], tuple[DomainEffect, ...]]:
        del arguments
        artifact = await self._required_artifact(
            project,
            state,
            _SCENE_LIST_ARTIFACT,
            allow_finalized=True,
        )
        if artifact.status is ArtifactStatus.OPEN:
            try:
                artifact = await self._lifecycle.finalize(
                    ArtifactFinalizeCommand(
                        artifact_id=artifact.id,
                        expected_revision=artifact.revision,
                        expected_item_count=artifact.expected_item_count,
                        resource_ref=_resource_ref(artifact),
                        write_lease=await self._mutation_lease(
                            artifact,
                            _required_run_id(state),
                        ),
                        complete_work_item=(
                            artifact.scope is ArtifactScope.WORK_ITEM
                        ),
                    )
                )
            except ArtifactValidationError as error:
                raise ScreenplayArtifactInputError(
                    str(error.details.get("reason") or error)
                ) from error
        batches = tuple(await self._repository.list_batches(artifact.id))
        metadata = thaw_json_mapping(artifact.metadata)
        structure = await self._accepted_structure(project)
        if str(metadata.get("structureId") or "") != str(structure["id"]):
            raise ScreenplayArtifactInputError(
                "The accepted structure changed before scene-list finalization."
            )
        try:
            scenes = normalize_scene_trace(
                scenes=_batch_items(batches),
                structure_kind=str(structure["kind"]),
                structure_content=_json_object(structure.get("content_json")),
            )
        except ValueError as error:
            raise ScreenplayArtifactInputError(str(error)) from error
        title = str(metadata.get("title") or "Agent 场景表提案")
        content_json = {
            "schemaVersion": 1,
            "generatedBy": "screenplay-agent",
            "documentKind": "scene_list",
            "structureId": str(structure["id"]),
            "artifactRef": artifact.resource_ref,
            "scenes": scenes,
        }
        return _proposal_result(
            kind="scene_list",
            title=title,
            content_json=content_json,
            content_text=None,
            derived_from_ids=[str(structure["id"])],
        )

    async def begin_review(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any]]:
        run_id = _required_run_id(state)
        continued = await self._find_for_run(
            project,
            run_id,
            _REVIEW_ARTIFACT,
        )
        if continued is not None and continued.scope is ArtifactScope.WORK_ITEM:
            return _review_progress(continued), []
        source_state = await _review_source_state(
            self._db,
            str(project["id"]),
        )
        verdict = str(arguments.get("verdict") or "").strip()
        if verdict not in {"ready", "revise", "major_rework"}:
            raise ScreenplayArtifactInputError(
                "verdict must be ready, revise, or major_rework."
            )
        expected_issue_count = _required_non_negative_count(
            arguments.get("expectedIssueCount"),
            maximum=_REVIEW_ISSUE_LIMIT,
            label="expectedIssueCount",
        )
        if verdict == "ready" and expected_issue_count:
            raise ScreenplayArtifactInputError(
                "A ready review cannot declare open issues."
            )
        if verdict != "ready" and expected_issue_count < 1:
            raise ScreenplayArtifactInputError(
                "A revision verdict requires at least one issue."
            )
        metadata = {
            "title": _title(arguments, "Agent 剧本审阅报告"),
            "verdict": verdict,
            "expectedIssueCount": expected_issue_count,
            "reviewedDraftId": str(source_state["draft"]["id"]),
            "allowedSceneIds": source_state["allowedSceneIds"],
            "verificationOfReviewId": str(
                (source_state.get("previousReview") or {}).get("id") or ""
            ),
            "verificationIssueIds": source_state["verificationIssueIds"],
        }
        existing = await self._find_for_run(
            project,
            run_id,
            _REVIEW_ARTIFACT,
        )
        if existing is not None:
            if thaw_json_mapping(existing.metadata) != metadata:
                raise ScreenplayArtifactInputError(
                    "The current run already owns a review artifact with "
                    "different metadata."
                )
            return _review_progress(existing), []
        artifact = await self._begin_artifact(ArtifactCreateCommand(
            namespace=_NAMESPACE,
            kind=_REVIEW_ARTIFACT,
            owner_id=str(project["id"]),
            run_id=run_id,
            expected_item_count=(
                1
                + expected_issue_count
                + len(source_state["verificationIssueIds"])
            ),
            metadata=metadata,
        ))
        return _review_progress(artifact), []

    async def append_review(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any]]:
        artifact = await self._required_artifact(
            project,
            state,
            _REVIEW_ARTIFACT,
        )
        metadata = thaw_json_mapping(artifact.metadata)
        items = _required_mapping_batch(
            arguments.get("items"),
            label="items",
            maximum=_REVIEW_BATCH_LIMIT,
        )
        normalized = tuple(
            _normalize_review_item(item, metadata=metadata)
            for item in items
        )
        coverage = tuple(_review_item_coverage(item) for item in normalized)
        current = await self._append(
            artifact,
            run_id=_required_run_id(state),
            items=normalized,
            coverage=coverage,
            batch_prefix="review",
        )
        return _review_progress(current), []

    async def finalize_review(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any], tuple[DomainEffect, ...]]:
        del arguments
        artifact = await self._required_artifact(
            project,
            state,
            _REVIEW_ARTIFACT,
            allow_finalized=True,
        )
        metadata = thaw_json_mapping(artifact.metadata)
        expected_coverage = _review_expected_coverage(metadata)
        if artifact.status is ArtifactStatus.OPEN:
            try:
                artifact = await self._lifecycle.finalize(
                    ArtifactFinalizeCommand(
                        artifact_id=artifact.id,
                        expected_revision=artifact.revision,
                        expected_item_count=artifact.expected_item_count,
                        expected_coverage_keys=expected_coverage,
                        resource_ref=_resource_ref(artifact),
                        write_lease=await self._mutation_lease(
                            artifact,
                            _required_run_id(state),
                        ),
                        complete_work_item=(
                            artifact.scope is ArtifactScope.WORK_ITEM
                        ),
                    )
                )
            except ArtifactValidationError as error:
                raise ScreenplayArtifactInputError(
                    str(error.details.get("reason") or error)
                ) from error
        draft, content_json = await _validate_and_assemble_review(
            self._db,
            str(project["id"]),
            metadata,
            _batch_items(await self._repository.list_batches(artifact.id)),
        )
        content_json.update({
            "reviewArtifactId": artifact.id,
            "artifactRef": artifact.resource_ref,
        })
        return _proposal_result(
            kind="review",
            title=str(metadata.get("title") or "Agent 剧本审阅报告"),
            content_json=content_json,
            content_text=None,
            derived_from_ids=[str(draft["id"])],
        )

    async def begin_revision(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any]]:
        run_id = _required_run_id(state)
        continued = await self._find_for_run(
            project,
            run_id,
            _REVISION_ARTIFACT,
        )
        if continued is not None and continued.scope is ArtifactScope.WORK_ITEM:
            return _revision_artifact_progress(continued), []
        draft, review, scene_list = await self._revision_inputs(project)
        draft_json = _json_object(draft.get("content_json"))
        review_json = _json_object(review.get("content_json"))
        completed_scene_ids = [
            str(value)
            for value in draft_json.get("completedSceneIds", [])
            if str(value).strip()
        ]
        completed_set = set(completed_scene_ids)
        raw_issues = review_json.get("issues")
        if not isinstance(raw_issues, list) or not raw_issues:
            raise ScreenplayArtifactInputError(
                "The accepted review has no issues to revise."
            )
        review_issue_ids: list[str] = []
        required_scene_ids: list[str] = []
        for issue in raw_issues:
            if not isinstance(issue, Mapping):
                raise ScreenplayArtifactInputError(
                    "The accepted review contains an invalid issue."
                )
            issue_id = _required_text(issue.get("id"), "issue id", maximum=100)
            review_issue_ids.append(issue_id)
            scene_ids = issue.get("sceneIds")
            if not isinstance(scene_ids, list):
                raise ScreenplayArtifactInputError(
                    f"Review issue {issue_id} has no scene ids."
                )
            required_scene_ids.extend(str(value) for value in scene_ids)
        additional_scene_ids = arguments.get("additionalSceneIds") or []
        if not isinstance(additional_scene_ids, list):
            raise ScreenplayArtifactInputError(
                "additionalSceneIds must be an array."
            )
        normalized_additional_scene_ids = [
            _required_text(value, "additional scene id", maximum=100)
            for value in additional_scene_ids
        ]
        expected_scene_ids = list(dict.fromkeys((
            *required_scene_ids,
            *normalized_additional_scene_ids,
        )))
        if (
            not expected_scene_ids
            or not set(expected_scene_ids).issubset(completed_set)
        ):
            raise ScreenplayArtifactInputError(
                "Revision scene ids must belong to the accepted complete draft."
            )
        base_scene_texts = await self._load_scene_texts(str(draft["id"]))
        missing_base = [
            scene_id
            for scene_id in draft_json.get("completedSceneIds", [])
            if str(scene_id) not in base_scene_texts
        ]
        if missing_base:
            raise ScreenplayArtifactInputError(
                "The accepted draft cannot be mapped safely to scene ids: "
                + ", ".join(str(item) for item in missing_base)
            )
        metadata = {
            "title": _title(arguments, "Agent 完整剧本修订稿"),
            "baseDraftId": str(draft["id"]),
            "reviewId": str(review["id"]),
            "sceneListId": str(scene_list["id"]),
            "completedSceneIds": completed_scene_ids,
            "expectedSceneIds": expected_scene_ids,
            "reviewIssueIds": review_issue_ids,
            "revisionSummary": _required_text(
                arguments.get("revisionSummary"),
                "revisionSummary",
                maximum=30_000,
            ),
        }
        existing = await self._find_for_run(
            project,
            run_id,
            _REVISION_ARTIFACT,
        )
        if existing is not None:
            if thaw_json_mapping(existing.metadata) != metadata:
                raise ScreenplayArtifactInputError(
                    "The current run already owns a revision artifact with "
                    "different revision metadata."
                )
            return _revision_artifact_progress(existing), []
        artifact = await self._begin_artifact(ArtifactCreateCommand(
            namespace=_NAMESPACE,
            kind=_REVISION_ARTIFACT,
            owner_id=str(project["id"]),
            run_id=run_id,
            expected_item_count=(
                len(expected_scene_ids) + len(review_issue_ids)
            ),
            metadata=metadata,
        ))
        return _revision_artifact_progress(artifact), []

    async def append_revision(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any]]:
        artifact = await self._required_artifact(
            project,
            state,
            _REVISION_ARTIFACT,
        )
        items = _required_mapping_batch(
            arguments.get("revisedScenes"),
            label="revisedScenes",
            maximum=1,
        )
        normalized = tuple({
            "itemType": "scene_revision",
            "sceneId": _required_text(
                item.get("sceneId"),
                "sceneId",
                maximum=100,
            ),
            "sceneText": _required_text(
                item.get("sceneText"),
                "sceneText",
                maximum=14_000,
            ),
            "executionUpdate": _required_execution_update(item),
        } for item in items)
        coverage = tuple(
            f"scene:{item['sceneId']}" for item in normalized
        )
        current = await self._append(
            artifact,
            run_id=_required_run_id(state),
            items=normalized,
            coverage=coverage,
            batch_prefix="revision",
        )
        return _artifact_progress(current), []

    async def append_revision_resolutions(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any]]:
        artifact = await self._required_artifact(
            project,
            state,
            _REVISION_ARTIFACT,
        )
        items = _required_mapping_batch(
            arguments.get("issueResolutions"),
            label="issueResolutions",
            maximum=8,
        )
        normalized = tuple({
            "itemType": "issue_resolution",
            "issueId": _required_text(
                item.get("issueId"),
                "issueId",
                maximum=100,
            ),
            "status": _required_resolution_status(item.get("status")),
            "resolutionEvidence": _required_text(
                item.get("resolutionEvidence"),
                "resolutionEvidence",
                maximum=2_500,
            ),
        } for item in items)
        coverage = tuple(
            f"issue:{item['issueId']}" for item in normalized
        )
        current = await self._append(
            artifact,
            run_id=_required_run_id(state),
            items=normalized,
            coverage=coverage,
            batch_prefix="resolution",
        )
        return _artifact_progress(current), []

    async def finalize_revision(
        self,
        project: Mapping[str, Any],
        arguments: Mapping[str, Any],
        state: ExecutionState,
    ) -> tuple[dict[str, Any], list[Any], tuple[DomainEffect, ...]]:
        del arguments
        artifact = await self._required_artifact(
            project,
            state,
            _REVISION_ARTIFACT,
            allow_finalized=True,
        )
        metadata = thaw_json_mapping(artifact.metadata)
        expected_scene_ids = tuple(
            str(value)
            for value in metadata.get("expectedSceneIds", [])
            if str(value).strip()
        )
        review_issue_ids = tuple(
            str(value)
            for value in metadata.get("reviewIssueIds", [])
            if str(value).strip()
        )
        expected_coverage = (
            *(f"scene:{value}" for value in expected_scene_ids),
            *(f"issue:{value}" for value in review_issue_ids),
        )
        if artifact.status is ArtifactStatus.OPEN:
            try:
                artifact = await self._lifecycle.finalize(
                    ArtifactFinalizeCommand(
                        artifact_id=artifact.id,
                        expected_revision=artifact.revision,
                        expected_item_count=artifact.expected_item_count,
                        expected_coverage_keys=expected_coverage,
                        resource_ref=_resource_ref(artifact),
                        write_lease=await self._mutation_lease(
                            artifact,
                            _required_run_id(state),
                        ),
                        complete_work_item=(
                            artifact.scope is ArtifactScope.WORK_ITEM
                        ),
                    )
                )
            except ArtifactValidationError as error:
                raise ScreenplayArtifactInputError(
                    str(error.details.get("reason") or error)
                ) from error
        draft, review, scene_list = await self._revision_inputs(project)
        if (
            str(draft["id"]) != str(metadata.get("baseDraftId") or "")
            or str(review["id"]) != str(metadata.get("reviewId") or "")
            or str(scene_list["id"]) != str(metadata.get("sceneListId") or "")
        ):
            raise ScreenplayArtifactInputError(
                "Accepted revision inputs changed before finalization."
            )
        scene_texts = await self._load_scene_texts(str(draft["id"]))
        batches = tuple(await self._repository.list_batches(artifact.id))
        revision_items = _batch_items(batches)
        scene_items = [
            item for item in revision_items
            if item.get("itemType") == "scene_revision"
        ]
        resolution_items = [
            item for item in revision_items
            if item.get("itemType") == "issue_resolution"
        ]
        for item in scene_items:
            scene_texts[str(item["sceneId"])] = str(item["sceneText"])
        try:
            (
                issue_resolutions,
                scene_executions,
                reassessed_scene_ids,
            ) = build_revision_trace(
                scene_list_content=_json_object(scene_list.get("content_json")),
                completed_scene_ids=metadata.get("completedSceneIds"),
                previous_executions=_json_object(draft.get("content_json")).get(
                    "sceneExecutions"
                ),
                review_issues=_json_object(review.get("content_json")).get(
                    "issues"
                ),
                issue_resolutions=[{
                    key: value for key, value in item.items()
                    if key != "itemType"
                } for item in resolution_items],
                execution_updates=[
                    item.get("executionUpdate") for item in scene_items
                ],
            )
        except ValueError as error:
            raise ScreenplayArtifactInputError(str(error)) from error
        completed_scene_ids = [
            str(value)
            for value in metadata.get("completedSceneIds", [])
            if str(value).strip()
        ]
        missing = [
            scene_id
            for scene_id in completed_scene_ids
            if not str(scene_texts.get(scene_id) or "").strip()
        ]
        if missing:
            raise ScreenplayArtifactInputError(
                "Final revision is missing scene text: " + ", ".join(missing)
            )
        content_text = "\n\n".join(
            scene_texts[scene_id].strip()
            for scene_id in completed_scene_ids
        )
        content_json = {
            "schemaVersion": 1,
            "generatedBy": "screenplay-agent",
            "documentKind": "scene_draft",
            "isComplete": True,
            "sceneListId": str(scene_list["id"]),
            "completedSceneIds": completed_scene_ids,
            "sceneExecutions": scene_executions,
            "revisionOf": str(draft["id"]),
            "reviewId": str(review["id"]),
            "revisionArtifactId": artifact.id,
            "artifactRef": artifact.resource_ref,
            "issueResolutions": issue_resolutions,
            "reassessedSceneIds": reassessed_scene_ids,
            "resolvedIssueIds": [
                item["issueId"]
                for item in issue_resolutions
                if item["status"] == "resolved"
            ],
            "partiallyResolvedIssueIds": [
                item["issueId"]
                for item in issue_resolutions
                if item["status"] == "partially_resolved"
            ],
            "revisionSummary": str(metadata.get("revisionSummary") or ""),
        }
        return _proposal_result(
            kind="scene_draft",
            title=str(metadata.get("title") or "Agent 完整剧本修订稿"),
            content_json=content_json,
            content_text=content_text,
            derived_from_ids=[str(draft["id"]), str(review["id"])],
        )

    async def _accepted_structure(
        self,
        project: Mapping[str, Any],
    ) -> dict[str, Any]:
        structure = await self._db.fetch_one(
            "SELECT id, kind, content_json FROM screenplay_documents "
            "WHERE project_id = ? AND kind IN ('beat_sheet', 'episode_outline') "
            "AND status = 'accepted' ORDER BY version DESC LIMIT 1",
            [project["id"]],
        )
        if structure is None:
            raise ScreenplayArtifactInputError(
                "An accepted structure is required before the scene list."
            )
        return structure

    async def _accepted_creative_brief(
        self,
        project: Mapping[str, Any],
    ) -> dict[str, Any]:
        brief = await self._db.fetch_one(
            "SELECT id, content_json FROM screenplay_documents "
            "WHERE project_id = ? AND kind = 'creative_brief' "
            "AND status = 'accepted' ORDER BY version DESC LIMIT 1",
            [project["id"]],
        )
        if brief is None:
            raise ScreenplayArtifactInputError(
                "An accepted creative brief is required before structure "
                "design."
            )
        return brief

    async def _accepted_source_analysis(
        self,
        project: Mapping[str, Any],
    ) -> dict[str, Any]:
        source_analysis = await self._db.fetch_one(
            "SELECT id, content_json FROM screenplay_documents "
            "WHERE project_id = ? AND kind = 'source_analysis' "
            "AND status = 'accepted' ORDER BY version DESC LIMIT 1",
            [project["id"]],
        )
        if source_analysis is None:
            raise ScreenplayArtifactInputError(
                "An accepted source analysis is required before this proposal."
            )
        return source_analysis

    async def _revision_inputs(
        self,
        project: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        draft = await self._db.fetch_one(
            "SELECT id, content_json, content_text, version FROM "
            "screenplay_documents WHERE project_id = ? AND kind = 'scene_draft' "
            "AND status = 'accepted' ORDER BY version DESC LIMIT 1",
            [project["id"]],
        )
        review = await self._db.fetch_one(
            "SELECT id, content_json FROM screenplay_documents "
            "WHERE project_id = ? AND kind = 'review' AND status = 'accepted' "
            "ORDER BY version DESC LIMIT 1",
            [project["id"]],
        )
        scene_list = await self._db.fetch_one(
            "SELECT id, content_json FROM screenplay_documents "
            "WHERE project_id = ? AND kind = 'scene_list' "
            "AND status = 'accepted' ORDER BY version DESC LIMIT 1",
            [project["id"]],
        )
        if draft is None or review is None or scene_list is None:
            raise ScreenplayArtifactInputError(
                "An accepted screenplay review, complete draft, and scene "
                "list are required before revision."
            )
        draft_json = _json_object(draft.get("content_json"))
        review_json = _json_object(review.get("content_json"))
        if (
            draft_json.get("isComplete") is not True
            or str(review_json.get("reviewedDraftId") or "")
            != str(draft["id"])
            or str(review_json.get("verdict") or "") == "ready"
            or str(draft_json.get("sceneListId") or "")
            != str(scene_list["id"])
        ):
            raise ScreenplayArtifactInputError(
                "The accepted review does not apply to the current complete draft."
            )
        return draft, review, scene_list

    async def _find_for_run(
        self,
        project: Mapping[str, Any],
        run_id: str,
        kind: str,
    ) -> ArtifactRecord | None:
        linked = await self._repository.find_linked_for_run(
            namespace=_NAMESPACE,
            kind=kind,
            owner_id=str(project["id"]),
            run_id=run_id,
        )
        if linked is not None:
            return linked
        return await self._repository.find_for_run(
            namespace=_NAMESPACE,
            kind=kind,
            owner_id=str(project["id"]),
            run_id=run_id,
        )

    async def _required_artifact(
        self,
        project: Mapping[str, Any],
        state: ExecutionState,
        kind: str,
        *,
        allow_finalized: bool = False,
    ) -> ArtifactRecord:
        artifact = await self._find_for_run(
            project,
            _required_run_id(state),
            kind,
        )
        if artifact is None:
            raise ScreenplayArtifactInputError(
                "Begin the artifact before appending or finalizing it."
            )
        if artifact.status is ArtifactStatus.ABORTED:
            raise ScreenplayArtifactInputError("The artifact was aborted.")
        if artifact.status is ArtifactStatus.FINALIZED and not allow_finalized:
            raise ScreenplayArtifactInputError(
                "The artifact is already finalized."
            )
        return artifact

    async def _append(
        self,
        artifact: ArtifactRecord,
        *,
        run_id: str,
        items: tuple[Mapping[str, Any], ...],
        coverage: tuple[str, ...],
        batch_prefix: str,
    ) -> ArtifactRecord:
        if artifact.status is not ArtifactStatus.OPEN:
            raise ScreenplayArtifactInputError("The artifact is not open.")
        expected = artifact.expected_item_count
        if (
            expected is not None
            and artifact.committed_item_count + len(items) > expected
        ):
            raise ScreenplayArtifactInputError(
                "The batch exceeds the artifact expected item count."
            )
        existing_batches = tuple(
            await self._repository.list_batches(artifact.id)
        )
        for batch in existing_batches:
            if tuple(batch.coverage_keys) != coverage:
                continue
            existing_items = tuple(
                thaw_json_mapping(item) for item in batch.items
            )
            normalized_items = tuple(dict(item) for item in items)
            if existing_items != normalized_items:
                raise ScreenplayArtifactInputError(
                    "A committed batch reused the same coverage with "
                    "different content."
                )
            return await self._lifecycle.get(artifact.id)
        batch_id = _batch_id(batch_prefix, coverage)
        await self._lifecycle.append(ArtifactAppendCommand(
            artifact_id=artifact.id,
            expected_revision=artifact.revision,
            sequence=artifact.next_sequence,
            batch_id=batch_id,
            idempotency_key=f"{artifact.id}:{batch_id}",
            items=items,
            coverage_keys=coverage,
            write_lease=await self._mutation_lease(artifact, run_id),
        ))
        return await self._lifecycle.get(artifact.id)

    async def _begin_artifact(
        self,
        command: ArtifactCreateCommand,
    ) -> ArtifactRecord:
        started = await self._work_item_artifacts.begin(command)
        return started.artifact

    async def _mutation_lease(
        self,
        artifact: ArtifactRecord,
        run_id: str,
    ) -> ArtifactMutationLease | None:
        if artifact.scope is ArtifactScope.RUN:
            return None
        claim = await self._claims.load_active(artifact.id)
        if claim is None:
            if artifact.work_item_id is None:
                raise ScreenplayArtifactInputError(
                    "The Work Item Artifact is missing its owner."
                )
            claim = await self._claims.acquire(ArtifactWriteClaimCommand(
                artifact_id=artifact.id,
                work_item_id=artifact.work_item_id,
                run_id=run_id,
                expected_revision=artifact.revision,
                lease_duration_ms=300_000,
            ))
        if claim.run_id != run_id:
            raise ScreenplayArtifactInputError(
                "The Artifact is currently owned by another Agent run."
            )
        return ArtifactMutationLease(
            run_id=run_id,
            claim_token=claim.claim_token,
        )

    async def _load_scene_texts(
        self,
        document_id: str,
        *,
        visited: frozenset[str] = frozenset(),
    ) -> dict[str, str]:
        if document_id in visited:
            raise ScreenplayArtifactInputError(
                "The draft revision lineage contains a cycle."
            )
        document = await self._db.fetch_one(
            "SELECT id, project_id, content_json, content_text, version FROM "
            "screenplay_documents WHERE id = ?",
            [document_id],
        )
        if document is None:
            raise ScreenplayArtifactInputError(
                "The revision base draft no longer exists."
            )
        content = _json_object(document.get("content_json"))
        completed = [
            str(value)
            for value in content.get("completedSceneIds", [])
            if str(value).strip()
        ]
        artifact_id = str(content.get("revisionArtifactId") or "").strip()
        revision_of = str(content.get("revisionOf") or "").strip()
        if artifact_id and revision_of:
            result = await self._load_scene_texts(
                revision_of,
                visited=frozenset((*visited, document_id)),
            )
            for item in _batch_items(
                await self._repository.list_batches(artifact_id)
            ):
                if item.get("itemType") != "scene_revision":
                    continue
                result[str(item.get("sceneId") or "")] = str(
                    item.get("sceneText") or ""
                ).strip()
            if all(result.get(scene_id) for scene_id in completed):
                return result
        rolling = await self._rolling_scene_texts(
            project_id=str(document["project_id"]),
            scene_list_id=str(content.get("sceneListId") or ""),
            maximum_version=int(document.get("version") or 0),
        )
        if all(rolling.get(scene_id) for scene_id in completed):
            return rolling
        split = _split_fountain_text(
            str(document.get("content_text") or ""),
            completed,
        )
        if all(split.get(scene_id) for scene_id in completed):
            return split
        return rolling

    async def _rolling_scene_texts(
        self,
        *,
        project_id: str,
        scene_list_id: str,
        maximum_version: int,
    ) -> dict[str, str]:
        rows = await self._db.fetch_all(
            "SELECT content_json, content_text FROM screenplay_documents "
            "WHERE project_id = ? AND kind = 'scene_draft' AND version <= ? "
            "ORDER BY version ASC",
            [project_id, maximum_version],
        )
        result: dict[str, str] = {}
        previous_full = ""
        for row in rows:
            content = _json_object(row.get("content_json"))
            if (
                str(content.get("sceneListId") or "") != scene_list_id
                or content.get("revisionOf")
            ):
                continue
            scene_id = str(content.get("sceneId") or "").strip()
            full_text = str(row.get("content_text") or "").strip()
            if not scene_id or not full_text:
                continue
            if previous_full and full_text.startswith(previous_full):
                scene_text = full_text[len(previous_full):].strip()
            elif not previous_full:
                scene_text = full_text
            else:
                previous_full = full_text
                continue
            if scene_text:
                result[scene_id] = scene_text
            previous_full = full_text
        return result


async def _preflight_revision_finalization(
    db,
    artifact: ArtifactRecord,
    batches: Sequence[ArtifactBatch],
) -> None:
    """Run every fallible revision assembly check before durable finalization."""

    metadata = thaw_json_mapping(artifact.metadata)
    service = ScreenplayArtifactToolService(db)
    draft, review, scene_list = await service._revision_inputs({
        "id": artifact.owner_id,
    })
    if (
        str(draft["id"]) != str(metadata.get("baseDraftId") or "")
        or str(review["id"]) != str(metadata.get("reviewId") or "")
        or str(scene_list["id"]) != str(metadata.get("sceneListId") or "")
    ):
        raise ScreenplayArtifactInputError(
            "Accepted revision inputs changed before finalization."
        )
    scene_texts = await service._load_scene_texts(str(draft["id"]))
    revision_items = _batch_items(batches)
    scene_items = [
        item for item in revision_items
        if item.get("itemType") == "scene_revision"
    ]
    resolution_items = [
        item for item in revision_items
        if item.get("itemType") == "issue_resolution"
    ]
    for item in scene_items:
        scene_texts[str(item["sceneId"])] = str(item["sceneText"])
    build_revision_trace(
        scene_list_content=_json_object(scene_list.get("content_json")),
        completed_scene_ids=metadata.get("completedSceneIds"),
        previous_executions=_json_object(draft.get("content_json")).get(
            "sceneExecutions"
        ),
        review_issues=_json_object(review.get("content_json")).get("issues"),
        issue_resolutions=[{
            key: value for key, value in item.items()
            if key != "itemType"
        } for item in resolution_items],
        execution_updates=[
            item.get("executionUpdate") for item in scene_items
        ],
    )
    missing = [
        str(scene_id)
        for scene_id in metadata.get("completedSceneIds", [])
        if not str(scene_texts.get(str(scene_id)) or "").strip()
    ]
    if missing:
        raise ScreenplayArtifactInputError(
            "Final revision is missing scene text: " + ", ".join(missing)
        )


def _artifact_progress(artifact: ArtifactRecord) -> dict[str, Any]:
    expected = artifact.expected_item_count
    remaining = (
        max(0, expected - artifact.committed_item_count)
        if expected is not None
        else None
    )
    if artifact.status is ArtifactStatus.FINALIZED:
        next_action = "replay_finalization"
    elif remaining == 0:
        next_action = "finalize"
    else:
        next_action = "append_batch"
    return {
        "success": True,
        "artifactId": artifact.id,
        "artifactStatus": artifact.status.value,
        "revision": artifact.revision,
        "committedItemCount": artifact.committed_item_count,
        "expectedItemCount": expected,
        "remainingItemCount": remaining,
        "nextSequence": artifact.next_sequence,
        "nextAction": next_action,
    }


def _revision_artifact_progress(artifact: ArtifactRecord) -> dict[str, Any]:
    progress = _artifact_progress(artifact)
    metadata = thaw_json_mapping(artifact.metadata)
    progress.update({
        "expectedSceneIds": [
            str(value)
            for value in metadata.get("expectedSceneIds", [])
            if str(value).strip()
        ],
        "reviewIssueIds": [
            str(value)
            for value in metadata.get("reviewIssueIds", [])
            if str(value).strip()
        ],
    })
    return progress


def _source_analysis_progress(
    artifact: ArtifactRecord,
    *,
    batches: Sequence[ArtifactBatch] = (),
    discarded: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    progress = _artifact_progress(artifact)
    metadata = thaw_json_mapping(artifact.metadata)
    expected = _source_analysis_counts(metadata)
    occupied = _source_analysis_occupied_indices(batches)
    committed = {
        item_type: len(occupied[item_type])
        for item_type in _SOURCE_ANALYSIS_ITEM_LIMITS
    }
    remaining = {
        item_type: max(0, expected[item_type] - committed[item_type])
        for item_type in _SOURCE_ANALYSIS_ITEM_LIMITS
    }
    next_indices = {
        item_type: next((
            candidate
            for candidate in range(1, expected[item_type] + 1)
            if candidate not in occupied[item_type]
        ), None)
        for item_type in _SOURCE_ANALYSIS_ITEM_LIMITS
    }
    progress.update({
        "expectedItemCounts": expected,
        "committedItemCounts": committed,
        "remainingItemCounts": remaining,
        "nextItemIndices": next_indices,
    })
    if discarded:
        progress["discardedSurplusItemCounts"] = {
            item_type: int(count)
            for item_type, count in discarded.items()
            if int(count) > 0
        }
    return progress


def _creative_brief_progress(artifact: ArtifactRecord) -> dict[str, Any]:
    progress = _artifact_progress(artifact)
    metadata = thaw_json_mapping(artifact.metadata)
    progress.update({
        "expectedDecisionCount": _creative_brief_decision_count(metadata),
        "isBookAdaptation": bool(metadata.get("isBookAdaptation")),
        "targetFormat": str(metadata.get("projectFormat") or ""),
    })
    return progress


def _structure_progress(artifact: ArtifactRecord) -> dict[str, Any]:
    progress = _artifact_progress(artifact)
    metadata = thaw_json_mapping(artifact.metadata)
    progress.update({
        "structureKind": str(metadata.get("structureKind") or ""),
        "expectedUnitCount": int(metadata.get("expectedUnitCount") or 0),
        "expectedDecisionIds": list(_structure_decision_ids(metadata)),
    })
    return progress


def _review_progress(artifact: ArtifactRecord) -> dict[str, Any]:
    progress = _artifact_progress(artifact)
    metadata = thaw_json_mapping(artifact.metadata)
    progress.update({
        "verdict": str(metadata.get("verdict") or ""),
        "expectedIssueCount": _review_issue_count(metadata),
        "reviewedDraftId": str(metadata.get("reviewedDraftId") or ""),
        "allowedSceneIds": list(_review_scene_ids(metadata)),
        "verificationIssueIds": list(
            _review_verification_issue_ids(metadata)
        ),
    })
    return progress


def _proposal_result(
    *,
    kind: str,
    title: str,
    content_json: Mapping[str, Any],
    content_text: str | None,
    derived_from_ids: list[str],
) -> tuple[dict[str, Any], list[Any], tuple[DomainEffect, ...]]:
    rendered_text = (
        str(content_text).strip()
        if content_text is not None
        else render_screenplay_proposal(
            kind=kind,
            title=title,
            content=content_json,
        )
    )
    return (
        {
            "success": True,
            "status": "awaiting_user_review",
            "message": "Proposal delivered for user review.",
        },
        [],
        (DomainEffect(
            type="screenplay.document_proposal",
            payload={
                "kind": kind,
                "title": title,
                "contentJson": dict(content_json),
                "contentText": rendered_text,
                "derivedFromIds": derived_from_ids,
            },
        ),),
    )


def _batch_items(batches: Sequence[ArtifactBatch]) -> list[dict[str, Any]]:
    return [
        thaw_json_mapping(item)
        for batch in batches
        for item in batch.items
    ]


def _batch_id(prefix: str, coverage: tuple[str, ...]) -> str:
    digest = hashlib.sha256(json.dumps(
        list(coverage),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _resource_ref(artifact: ArtifactRecord) -> str:
    return f"artifact://{artifact.namespace}/{artifact.kind}/{artifact.id}"


def _required_run_id(state: ExecutionState) -> str:
    run_id = str(state.run_id or "").strip()
    if not run_id:
        raise ScreenplayArtifactInputError(
            "A persisted Agent run is required for artifact operations."
        )
    return run_id


def _required_mapping_batch(
    value: object,
    *,
    label: str,
    maximum: int,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise ScreenplayArtifactInputError(
            f"{label} must contain between 1 and {maximum} items."
        )
    if not all(isinstance(item, Mapping) for item in value):
        raise ScreenplayArtifactInputError(f"{label} items must be objects.")
    return tuple(dict(item) for item in value)


def _required_source_analysis_counts(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise ScreenplayArtifactInputError(
            "expectedItemCounts must be an object."
        )
    result: dict[str, int] = {}
    for item_type, maximum in _SOURCE_ANALYSIS_ITEM_LIMITS.items():
        raw = value.get(item_type)
        if isinstance(raw, bool):
            raise ScreenplayArtifactInputError(
                f"expectedItemCounts.{item_type} must be an integer."
            )
        try:
            count = int(raw)
        except (TypeError, ValueError):
            raise ScreenplayArtifactInputError(
                f"expectedItemCounts.{item_type} must be an integer."
            ) from None
        minimum = 1 if item_type in _SOURCE_ANALYSIS_REQUIRED_TYPES else 0
        if count < minimum or count > maximum:
            raise ScreenplayArtifactInputError(
                f"expectedItemCounts.{item_type} must be between "
                f"{minimum} and {maximum}."
            )
        result[item_type] = count
    return result


def _source_analysis_counts(metadata: Mapping[str, Any]) -> dict[str, int]:
    return _required_source_analysis_counts(metadata.get("expectedItemCounts"))


def _source_analysis_expected_coverage(
    metadata: Mapping[str, Any],
) -> tuple[str, ...]:
    counts = _source_analysis_counts(metadata)
    return tuple(
        f"{item_type}:{index}"
        for item_type in _SOURCE_ANALYSIS_ITEM_LIMITS
        for index in range(1, counts[item_type] + 1)
    )


def _source_analysis_occupied_indices(
    batches: Sequence[ArtifactBatch],
) -> dict[str, set[int]]:
    """Return the durable per-type cursor owned by the artifact host."""

    occupied = {
        item_type: set() for item_type in _SOURCE_ANALYSIS_ITEM_LIMITS
    }
    for batch in batches:
        for raw_key in batch.coverage_keys:
            item_type, separator, raw_index = str(raw_key).partition(":")
            if not separator or item_type not in occupied:
                continue
            index = _positive_int(raw_index)
            if index is not None:
                occupied[item_type].add(index)
    return occupied


def _source_analysis_evidence_keys(
    source_analysis: Mapping[str, Any],
) -> list[str]:
    analysis = source_analysis.get("analysis")
    if not isinstance(analysis, Mapping):
        return []
    evidence = analysis.get("evidence")
    if not isinstance(evidence, list):
        return []
    return list(dict.fromkeys(
        f"{str(item.get('sourceType') or '').strip()}:"
        f"{str(item.get('sourceId') or '').strip()}"
        for item in evidence
        if isinstance(item, Mapping)
        and str(item.get("sourceType") or "").strip()
        and str(item.get("sourceId") or "").strip()
    ))


def _source_analysis_limitations(
    source_analysis: Mapping[str, Any],
) -> list[str]:
    analysis = source_analysis.get("analysis")
    if not isinstance(analysis, Mapping):
        return []
    coverage = analysis.get("coverage")
    if not isinstance(coverage, Mapping):
        return []
    return _optional_text_list(
        coverage.get("limitations"),
        label="source limitation",
        maximum_items=100,
        maximum_text=4_000,
    )


def _creative_brief_decision_count(
    metadata: Mapping[str, Any],
) -> int:
    return _required_non_negative_count(
        metadata.get("expectedDecisionCount"),
        maximum=_CREATIVE_BRIEF_DECISION_LIMIT,
        label="expectedDecisionCount",
    )


def _creative_brief_evidence_keys(
    metadata: Mapping[str, Any],
) -> tuple[str, ...]:
    raw = metadata.get("allowedEvidenceKeys")
    if not isinstance(raw, list):
        raise ScreenplayArtifactInputError(
            "Creative-brief evidence manifest is invalid."
        )
    return tuple(dict.fromkeys(
        _required_text(value, "evidence key", maximum=500)
        for value in raw
    ))


def _creative_brief_expected_coverage(
    metadata: Mapping[str, Any],
) -> tuple[str, ...]:
    return (
        "brief",
        *(
            f"decision:{index}"
            for index in range(
                1,
                _creative_brief_decision_count(metadata) + 1,
            )
        ),
    )


def _normalize_creative_brief_item(
    item: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    item_type = str(item.get("itemType") or "").strip()
    if item_type == "brief_content":
        normalized: dict[str, Any] = {
            "itemType": item_type,
            "logline": _required_text(
                item.get("logline"),
                "logline",
                maximum=4_000,
            ),
            "coreConflict": _required_text(
                item.get("coreConflict"),
                "coreConflict",
                maximum=6_000,
            ),
            "adaptationPrinciples": _optional_text_list(
                item.get("adaptationPrinciples"),
                label="adaptationPrinciples",
                maximum_items=20,
                maximum_text=2_000,
            ),
            "openQuestions": _optional_text_list(
                item.get("openQuestions"),
                label="openQuestions",
                maximum_items=20,
                maximum_text=2_000,
            ),
        }
        for key, maximum in (
            ("audience", 2_000),
            ("theme", 4_000),
            ("protagonist", 4_000),
        ):
            value = _optional_bounded_text(item.get(key), maximum=maximum)
            if value:
                normalized[key] = value
        format_plan = item.get("formatPlan")
        is_book_adaptation = bool(metadata.get("isBookAdaptation"))
        if format_plan is None and is_book_adaptation:
            raise ScreenplayArtifactInputError(
                "Book adaptations require formatPlan."
            )
        if format_plan is not None:
            normalized["formatPlan"] = _normalize_creative_brief_format_plan(
                format_plan,
                project_format=str(metadata.get("projectFormat") or ""),
            )
        return normalized
    if item_type != "adaptation_decision":
        raise ScreenplayArtifactInputError(
            "itemType must be brief_content or adaptation_decision."
        )
    expected_count = _creative_brief_decision_count(metadata)
    index = _positive_int(item.get("index"))
    if index is None or index > expected_count:
        raise ScreenplayArtifactInputError(
            f"adaptation decision index must be between 1 and "
            f"{expected_count}."
        )
    action = str(item.get("action") or "").strip()
    if action not in ADAPTATION_ACTIONS:
        raise ScreenplayArtifactInputError(
            "adaptation decision action is invalid."
        )
    anchors = item.get("sourceAnchors")
    if not isinstance(anchors, list) or len(anchors) > 30:
        raise ScreenplayArtifactInputError(
            "sourceAnchors must be an array of at most 30 items."
        )
    allowed_evidence = set(_creative_brief_evidence_keys(metadata))
    normalized_anchors: list[dict[str, str]] = []
    seen_anchors: set[str] = set()
    for anchor in anchors:
        if not isinstance(anchor, Mapping):
            raise ScreenplayArtifactInputError(
                "sourceAnchors items must be objects."
            )
        source_type = _required_text(
            anchor.get("sourceType"),
            "sourceType",
            maximum=100,
        )
        source_id = _required_text(
            anchor.get("sourceId"),
            "sourceId",
            maximum=300,
        )
        source_key = f"{source_type}:{source_id}"
        if source_key not in allowed_evidence:
            raise ScreenplayArtifactInputError(
                "Source anchors must belong to the accepted source analysis."
            )
        if source_key not in seen_anchors:
            seen_anchors.add(source_key)
            normalized_anchors.append({
                "sourceType": source_type,
                "sourceId": source_id,
            })
    if action != "invent" and not normalized_anchors:
        raise ScreenplayArtifactInputError(
            "Non-invented adaptation decisions require source anchors."
        )
    return {
        "itemType": item_type,
        "index": index,
        "id": _required_text(item.get("id"), "id", maximum=120),
        "action": action,
        "subject": _required_text(
            item.get("subject"),
            "subject",
            maximum=4_000,
        ),
        "rationale": _required_text(
            item.get("rationale"),
            "rationale",
            maximum=4_000,
        ),
        "screenIntent": _required_text(
            item.get("screenIntent"),
            "screenIntent",
            maximum=4_000,
        ),
        "sourceAnchors": normalized_anchors,
    }


def _normalize_creative_brief_format_plan(
    value: object,
    *,
    project_format: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ScreenplayArtifactInputError("formatPlan must be an object.")
    result: dict[str, Any] = {
        "scopeStrategy": _required_text(
            value.get("scopeStrategy"),
            "formatPlan.scopeStrategy",
            maximum=4_000,
        ),
        "narrativeEndpoint": _required_text(
            value.get("narrativeEndpoint"),
            "formatPlan.narrativeEndpoint",
            maximum=4_000,
        ),
    }
    if project_format in _SERIES_FORMATS:
        result["episodeCount"] = _required_count(
            value.get("episodeCount"),
            maximum=1_000,
            label="formatPlan.episodeCount",
        )
        result["episodeDurationMinutes"] = _required_count(
            value.get("episodeDurationMinutes"),
            maximum=300,
            label="formatPlan.episodeDurationMinutes",
        )
    else:
        result["targetDurationMinutes"] = _required_count(
            value.get("targetDurationMinutes"),
            maximum=600,
            label="formatPlan.targetDurationMinutes",
        )
    return result


def _creative_brief_item_coverage(item: Mapping[str, Any]) -> str:
    if item.get("itemType") == "brief_content":
        return "brief"
    return f"decision:{item['index']}"


def _assemble_creative_brief(
    metadata: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    content_items = [
        item for item in items if item.get("itemType") == "brief_content"
    ]
    if len(content_items) != 1:
        raise ScreenplayArtifactInputError(
            "Creative brief must contain exactly one brief_content item."
        )
    brief = {
        key: value
        for key, value in content_items[0].items()
        if key != "itemType"
    }
    format_plan = brief.get("formatPlan")
    if isinstance(format_plan, Mapping):
        brief["formatPlan"] = {
            "targetFormat": str(metadata.get("projectFormat") or ""),
            **dict(format_plan),
        }
    decision_items = sorted(
        (
            item for item in items
            if item.get("itemType") == "adaptation_decision"
        ),
        key=lambda item: int(item.get("index") or 0),
    )
    if bool(metadata.get("isBookAdaptation")):
        brief["adaptationDecisions"] = [{
            key: value
            for key, value in item.items()
            if key not in {"itemType", "index"}
        } for item in decision_items]
        brief["acknowledgedSourceLimitations"] = [
            str(value)
            for value in metadata.get("sourceLimitations", [])
            if str(value).strip()
        ]
    return brief


def _review_issue_count(metadata: Mapping[str, Any]) -> int:
    return _required_non_negative_count(
        metadata.get("expectedIssueCount"),
        maximum=_REVIEW_ISSUE_LIMIT,
        label="expectedIssueCount",
    )


def _review_verification_issue_ids(
    metadata: Mapping[str, Any],
) -> tuple[str, ...]:
    raw = metadata.get("verificationIssueIds")
    if not isinstance(raw, list):
        raise ScreenplayArtifactInputError(
            "Review verification manifest is invalid."
        )
    result = tuple(
        _required_text(value, "verification issue id", maximum=100)
        for value in raw
    )
    if len(result) != len(set(result)):
        raise ScreenplayArtifactInputError(
            "Review verification manifest contains duplicate issue ids."
        )
    return result


def _review_scene_ids(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    raw = metadata.get("allowedSceneIds")
    if not isinstance(raw, list) or not raw:
        raise ScreenplayArtifactInputError(
            "Review scene manifest is invalid."
        )
    result = tuple(
        _required_text(value, "scene id", maximum=100) for value in raw
    )
    if len(result) != len(set(result)):
        raise ScreenplayArtifactInputError(
            "Review scene manifest contains duplicate scene ids."
        )
    return result


def _review_expected_coverage(
    metadata: Mapping[str, Any],
) -> tuple[str, ...]:
    return (
        "summary",
        *(
            f"issue:{index}"
            for index in range(1, _review_issue_count(metadata) + 1)
        ),
        *(
            f"verification:{index}"
            for index in range(
                1,
                len(_review_verification_issue_ids(metadata)) + 1,
            )
        ),
    )


def _normalize_review_item(
    item: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    item_type = str(item.get("itemType") or "").strip()
    if item_type == "review_summary":
        strengths = item.get("strengths")
        if not isinstance(strengths, list):
            raise ScreenplayArtifactInputError("strengths must be an array.")
        return {
            "itemType": item_type,
            "summary": _required_text(
                item.get("summary"),
                "summary",
                maximum=20_000,
            ),
            "strengths": _optional_text_list(
                strengths,
                label="strengths",
                maximum_items=30,
                maximum_text=3_000,
            ),
        }
    if item_type == "review_issue":
        expected_count = _review_issue_count(metadata)
        index = _positive_int(item.get("index"))
        if index is None or index > expected_count:
            raise ScreenplayArtifactInputError(
                f"review issue index must be between 1 and {expected_count}."
            )
        severity = str(item.get("severity") or "").strip()
        category = str(item.get("category") or "").strip()
        if severity not in REVIEW_SEVERITIES:
            raise ScreenplayArtifactInputError("review severity is invalid.")
        if category not in REVIEW_CATEGORIES:
            raise ScreenplayArtifactInputError("review category is invalid.")
        scene_ids = _required_unique_text_list(
            item.get("sceneIds"),
            label="sceneIds",
            minimum_items=1,
            maximum_items=30,
            maximum_text=100,
        )
        if not set(scene_ids).issubset(set(_review_scene_ids(metadata))):
            raise ScreenplayArtifactInputError(
                "Review issues can only reference completed scenes with "
                "execution records."
            )
        execution_fields = _required_unique_text_list(
            item.get("executionFields"),
            label="executionFields",
            minimum_items=1,
            maximum_items=5,
            maximum_text=100,
        )
        if not set(execution_fields).issubset(REVIEW_EXECUTION_FIELDS):
            raise ScreenplayArtifactInputError(
                "Review issue executionFields are invalid."
            )
        return {
            "itemType": item_type,
            "index": index,
            "id": _required_text(item.get("id"), "id", maximum=100),
            "severity": severity,
            "category": category,
            "sceneIds": scene_ids,
            "executionFields": execution_fields,
            "problem": _required_text(
                item.get("problem"),
                "problem",
                maximum=10_000,
            ),
            "recommendation": _required_text(
                item.get("recommendation"),
                "recommendation",
                maximum=10_000,
            ),
            "acceptanceCriteria": _required_text(
                item.get("acceptanceCriteria"),
                "acceptanceCriteria",
                maximum=10_000,
            ),
        }
    if item_type != "verification_result":
        raise ScreenplayArtifactInputError(
            "itemType must be review_summary, review_issue, or "
            "verification_result."
        )
    issue_ids = _review_verification_issue_ids(metadata)
    index = _positive_int(item.get("index"))
    if index is None or index > len(issue_ids):
        raise ScreenplayArtifactInputError(
            "verification result index is outside the host manifest."
        )
    status = str(item.get("status") or "").strip()
    if status not in REVIEW_VERIFICATION_STATUSES:
        raise ScreenplayArtifactInputError(
            "verification result status is invalid."
        )
    return {
        "itemType": item_type,
        "index": index,
        "status": status,
        "verificationEvidence": _required_text(
            item.get("verificationEvidence"),
            "verificationEvidence",
            maximum=10_000,
        ),
    }


def _review_item_coverage(item: Mapping[str, Any]) -> str:
    item_type = str(item.get("itemType") or "")
    if item_type == "review_summary":
        return "summary"
    if item_type == "review_issue":
        return f"issue:{item['index']}"
    return f"verification:{item['index']}"


async def _review_source_state(db, project_id: str) -> dict[str, Any]:
    draft = await db.fetch_one(
        "SELECT id, content_json FROM screenplay_documents "
        "WHERE project_id = ? AND kind = 'scene_draft' "
        "AND status = 'accepted' ORDER BY version DESC LIMIT 1",
        [project_id],
    )
    if draft is None:
        raise ScreenplayArtifactInputError(
            "An accepted complete scene draft is required for review."
        )
    draft_json = _json_object(draft.get("content_json"))
    if draft_json.get("isComplete") is not True:
        raise ScreenplayArtifactInputError(
            "The accepted scene draft is not marked complete."
        )
    completed_scene_ids = [
        str(value)
        for value in draft_json.get("completedSceneIds", [])
        if str(value).strip()
    ]
    execution_ids = {
        str(item.get("sceneId") or "").strip()
        for item in draft_json.get("sceneExecutions", [])
        if isinstance(item, Mapping)
        and str(item.get("sceneId") or "").strip()
    }
    allowed_scene_ids = [
        scene_id
        for scene_id in completed_scene_ids
        if scene_id in execution_ids
    ]
    if not allowed_scene_ids:
        raise ScreenplayArtifactInputError(
            "The complete draft has no reviewable scene execution records."
        )
    previous_review_id = str(draft_json.get("reviewId") or "").strip()
    revision_of = str(draft_json.get("revisionOf") or "").strip()
    previous_review = None
    previous_review_json: dict[str, Any] = {}
    verification_issue_ids: list[str] = []
    if previous_review_id or revision_of:
        if not previous_review_id or not revision_of:
            raise ScreenplayArtifactInputError(
                "The current revision has incomplete review provenance."
            )
        previous_review = await db.fetch_one(
            "SELECT id, content_json FROM screenplay_documents "
            "WHERE id = ? AND project_id = ? AND kind = 'review'",
            [previous_review_id, project_id],
        )
        previous_review_json = _json_object(
            (previous_review or {}).get("content_json")
        )
        if (
            previous_review is None
            or str(previous_review_json.get("reviewedDraftId") or "")
            != revision_of
        ):
            raise ScreenplayArtifactInputError(
                "The current revision cannot be traced to its prior review."
            )
        previous_issues = previous_review_json.get("issues")
        if not isinstance(previous_issues, list) or not previous_issues:
            raise ScreenplayArtifactInputError(
                "The prior review has no structured issues to verify."
            )
        verification_issue_ids = [
            _required_text(
                issue.get("id") if isinstance(issue, Mapping) else None,
                "prior review issue id",
                maximum=100,
            )
            for issue in previous_issues
        ]
        if len(verification_issue_ids) != len(set(verification_issue_ids)):
            raise ScreenplayArtifactInputError(
                "The prior review contains duplicate issue ids."
            )
    draft["content_json"] = draft_json
    if previous_review is not None:
        previous_review["content_json"] = previous_review_json
    return {
        "draft": draft,
        "allowedSceneIds": allowed_scene_ids,
        "previousReview": previous_review,
        "verificationIssueIds": verification_issue_ids,
    }


async def _validate_and_assemble_review(
    db,
    project_id: str,
    metadata: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_state = await _review_source_state(db, project_id)
    draft = source_state["draft"]
    if str(draft["id"]) != str(metadata.get("reviewedDraftId") or ""):
        raise ScreenplayArtifactInputError(
            "The accepted complete draft changed before review finalization."
        )
    if source_state["allowedSceneIds"] != list(_review_scene_ids(metadata)):
        raise ScreenplayArtifactInputError(
            "The draft scene execution manifest changed before finalization."
        )
    previous_review = source_state.get("previousReview")
    previous_review_id = str((previous_review or {}).get("id") or "")
    if (
        previous_review_id
        != str(metadata.get("verificationOfReviewId") or "")
        or source_state["verificationIssueIds"]
        != list(_review_verification_issue_ids(metadata))
    ):
        raise ScreenplayArtifactInputError(
            "The prior review verification manifest changed before "
            "finalization."
        )
    summary_items = [
        item for item in items if item.get("itemType") == "review_summary"
    ]
    if len(summary_items) != 1:
        raise ScreenplayArtifactInputError(
            "Review artifact must contain exactly one summary item."
        )
    issue_items = sorted(
        (item for item in items if item.get("itemType") == "review_issue"),
        key=lambda item: int(item.get("index") or 0),
    )
    raw_issues = [{
        key: value
        for key, value in item.items()
        if key not in {"itemType", "index"}
    } for item in issue_items]
    draft_json = _json_object(draft.get("content_json"))
    issues = normalize_review_issues(
        issues=raw_issues,
        completed_scene_ids=draft_json.get("completedSceneIds"),
        scene_executions=draft_json.get("sceneExecutions"),
    )
    verification_items = sorted(
        (
            item for item in items
            if item.get("itemType") == "verification_result"
        ),
        key=lambda item: int(item.get("index") or 0),
    )
    verification_results: list[dict[str, Any]] = []
    if previous_review is not None:
        issue_ids = _review_verification_issue_ids(metadata)
        raw_verifications = [{
            "issueId": issue_ids[int(item["index"]) - 1],
            "status": item.get("status"),
            "verificationEvidence": item.get("verificationEvidence"),
        } for item in verification_items]
        previous_review_json = _json_object(previous_review.get("content_json"))
        verification_results = normalize_review_verifications(
            previous_review_issues=previous_review_json.get("issues"),
            issue_resolutions=draft_json.get("issueResolutions"),
            verification_results=raw_verifications,
        )
    elif verification_items:
        raise ScreenplayArtifactInputError(
            "Verification results are only valid when reviewing a revision."
        )
    current_issue_ids = {str(item["id"]) for item in issues}
    failed_verification_ids = {
        item["issueId"]
        for item in verification_results
        if item["status"] != "verified"
    }
    verified_issue_ids = {
        item["issueId"]
        for item in verification_results
        if item["status"] == "verified"
    }
    if not failed_verification_ids.issubset(current_issue_ids):
        raise ScreenplayArtifactInputError(
            "Every failed verification must remain in the current issues."
        )
    if verified_issue_ids & current_issue_ids:
        raise ScreenplayArtifactInputError(
            "Verified prior issues cannot remain open in the current review."
        )
    verdict = str(metadata.get("verdict") or "")
    if verdict == "ready" and issues:
        raise ScreenplayArtifactInputError(
            "A ready review cannot contain open issues."
        )
    if verdict != "ready" and not issues:
        raise ScreenplayArtifactInputError(
            "A revision verdict requires at least one issue."
        )
    if (
        verdict == "ready"
        and any(item["status"] != "verified" for item in verification_results)
    ):
        raise ScreenplayArtifactInputError(
            "A ready rereview requires every prior issue to be verified."
        )
    summary = summary_items[0]
    content_json: dict[str, Any] = {
        "schemaVersion": 1,
        "generatedBy": "screenplay-agent",
        "documentKind": "review",
        "reviewedDraftId": str(draft["id"]),
        "summary": str(summary.get("summary") or ""),
        "strengths": list(summary.get("strengths") or []),
        "issues": issues,
        "verdict": verdict,
    }
    if previous_review is not None:
        content_json.update({
            "verificationOfReviewId": previous_review_id,
            "verificationResults": verification_results,
            "verifiedIssueIds": [
                item["issueId"]
                for item in verification_results
                if item["status"] == "verified"
            ],
            "failedVerificationIssueIds": [
                item["issueId"]
                for item in verification_results
                if item["status"] != "verified"
            ],
        })
    return draft, content_json


def _brief_structure_decisions(
    creative_brief: Mapping[str, Any],
) -> list[dict[str, str]]:
    brief = creative_brief.get("brief")
    if not isinstance(brief, Mapping):
        return []
    raw = brief.get("adaptationDecisions")
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        decision_id = str(item.get("id") or "").strip()
        if not decision_id or decision_id in seen:
            continue
        seen.add(decision_id)
        result.append({
            "id": decision_id,
            "action": str(item.get("action") or "").strip(),
        })
    return result


def _brief_episode_count(creative_brief: Mapping[str, Any]) -> int | None:
    brief = creative_brief.get("brief")
    if not isinstance(brief, Mapping):
        return None
    format_plan = brief.get("formatPlan")
    if not isinstance(format_plan, Mapping):
        return None
    return _positive_int(format_plan.get("episodeCount"))


def _structure_decision_ids(
    metadata: Mapping[str, Any],
) -> tuple[str, ...]:
    raw = metadata.get("expectedDecisions")
    if not isinstance(raw, list):
        raise ScreenplayArtifactInputError(
            "Structure artifact decision manifest is invalid."
        )
    result: list[str] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ScreenplayArtifactInputError(
                "Structure artifact decision manifest is invalid."
            )
        decision_id = _required_text(
            item.get("id"),
            "decision id",
            maximum=120,
        )
        result.append(decision_id)
    if len(result) != len(set(result)):
        raise ScreenplayArtifactInputError(
            "Structure artifact decision manifest contains duplicate ids."
        )
    return tuple(result)


def _structure_expected_coverage(
    metadata: Mapping[str, Any],
) -> tuple[str, ...]:
    expected_unit_count = _required_count(
        metadata.get("expectedUnitCount"),
        maximum=_EPISODE_UNIT_LIMIT,
        label="expectedUnitCount",
    )
    return (
        *(f"unit:{index}" for index in range(1, expected_unit_count + 1)),
        *(f"decision:{value}" for value in _structure_decision_ids(metadata)),
    )


def _normalize_structure_item(
    item: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    item_type = str(item.get("itemType") or "").strip()
    structure_kind = str(metadata.get("structureKind") or "")
    expected_unit_count = _required_count(
        metadata.get("expectedUnitCount"),
        maximum=_EPISODE_UNIT_LIMIT,
        label="expectedUnitCount",
    )
    if item_type == "structure_unit":
        index = _positive_int(item.get("index"))
        if index is None or index > expected_unit_count:
            raise ScreenplayArtifactInputError(
                f"structure unit index must be between 1 and "
                f"{expected_unit_count}."
            )
        return {
            "itemType": item_type,
            "index": index,
            "id": _required_text(item.get("id"), "id", maximum=120),
            "title": _required_text(
                item.get("title"),
                "title",
                maximum=300,
            ),
            "summary": _required_text(
                item.get("summary"),
                "summary",
                maximum=(
                    10_000 if structure_kind == "episode_outline" else 6_000
                ),
            ),
        }
    if item_type != "decision_coverage":
        raise ScreenplayArtifactInputError(
            "itemType must be structure_unit or decision_coverage."
        )
    decision_id = _required_text(
        item.get("decisionId"),
        "decisionId",
        maximum=120,
    )
    if decision_id not in set(_structure_decision_ids(metadata)):
        raise ScreenplayArtifactInputError(
            "decisionId must belong to the accepted creative brief."
        )
    unit_ids = item.get("structureUnitIds")
    if not isinstance(unit_ids, list) or len(unit_ids) > expected_unit_count:
        raise ScreenplayArtifactInputError(
            "structureUnitIds must be an array within the declared unit count."
        )
    normalized_unit_ids = list(dict.fromkeys(
        _required_text(value, "structureUnitId", maximum=120)
        for value in unit_ids
    ))
    return {
        "itemType": item_type,
        "decisionId": decision_id,
        "structureUnitIds": normalized_unit_ids,
        "implementation": _required_text(
            item.get("implementation"),
            "implementation",
            maximum=4_000,
        ),
    }


def _structure_item_coverage(item: Mapping[str, Any]) -> str:
    if item.get("itemType") == "structure_unit":
        return f"unit:{item['index']}"
    return f"decision:{item['decisionId']}"


def _assemble_structure_items(
    metadata: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    structure_kind = str(metadata.get("structureKind") or "")
    if structure_kind not in {"beat_sheet", "episode_outline"}:
        raise ScreenplayArtifactInputError(
            "Structure artifact kind is invalid."
        )
    unit_items = sorted(
        (
            item for item in items
            if item.get("itemType") == "structure_unit"
        ),
        key=lambda item: int(item.get("index") or 0),
    )
    units: list[dict[str, Any]] = []
    for item in unit_items:
        if structure_kind == "beat_sheet":
            units.append({
                "id": item.get("id"),
                "order": item.get("index"),
                "label": item.get("title"),
                "summary": item.get("summary"),
            })
        else:
            units.append({
                "id": item.get("id"),
                "number": item.get("index"),
                "title": item.get("title"),
                "summary": item.get("summary"),
            })
    coverage_by_id = {
        str(item.get("decisionId") or ""): {
            "decisionId": item.get("decisionId"),
            "structureUnitIds": item.get("structureUnitIds"),
            "implementation": item.get("implementation"),
        }
        for item in items
        if item.get("itemType") == "decision_coverage"
    }
    decision_coverage = [
        coverage_by_id[decision_id]
        for decision_id in _structure_decision_ids(metadata)
        if decision_id in coverage_by_id
    ]
    return units, decision_coverage


def _normalize_source_analysis_item(
    item: Mapping[str, Any],
    *,
    index: int,
    counts: Mapping[str, int],
    allowed_sources: set[str],
) -> dict[str, Any]:
    item_type = str(item.get("itemType") or "").strip()
    if item_type not in counts:
        raise ScreenplayArtifactInputError(
            "source-analysis itemType is invalid."
        )
    if index <= 0 or index > counts[item_type]:
        raise ScreenplayArtifactInputError(
            f"{item_type} index must be between 1 and {counts[item_type]}."
        )
    normalized: dict[str, Any] = {
        "itemType": item_type,
        "index": index,
    }
    if item_type == "character":
        normalized.update({
            "name": _required_text(item.get("name"), "name", maximum=300),
            "role": _required_text(item.get("role"), "role", maximum=2_000),
            "goal": _optional_bounded_text(item.get("goal"), maximum=3_000),
            "conflict": _optional_bounded_text(
                item.get("conflict"),
                maximum=3_000,
            ),
        })
    elif item_type == "plot_event":
        normalized.update({
            "event": _required_text(
                item.get("event"),
                "event",
                maximum=6_000,
            ),
            "consequence": _required_text(
                item.get("consequence"),
                "consequence",
                maximum=6_000,
            ),
        })
    elif item_type == "evidence":
        source_type = str(item.get("sourceType") or "").strip()
        source_id = _required_text(
            item.get("sourceId"),
            "sourceId",
            maximum=500,
        )
        source_key = f"{source_type}:{source_id}"
        if source_type not in _VALID_SOURCE_TYPES:
            raise ScreenplayArtifactInputError(
                "evidence sourceType is invalid."
            )
        if source_key not in allowed_sources:
            raise ScreenplayArtifactInputError(
                "Evidence can only cite source material read by the current run."
            )
        normalized.update({
            "sourceType": source_type,
            "sourceId": source_id,
            "claim": _required_text(
                item.get("claim"),
                "claim",
                maximum=4_000,
            ),
        })
    else:
        normalized["text"] = _required_text(
            item.get("text"),
            "text",
            maximum=4_000,
        )
    return normalized


def _group_source_analysis_items(
    items: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped = {
        item_type: [] for item_type in _SOURCE_ANALYSIS_ITEM_LIMITS
    }
    for item in items:
        item_type = str(item.get("itemType") or "")
        if item_type in grouped:
            grouped[item_type].append(dict(item))
    for values in grouped.values():
        values.sort(key=lambda item: int(item.get("index") or 0))
    return grouped


def _optional_text_list(
    value: object,
    *,
    label: str,
    maximum_items: int,
    maximum_text: int,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ScreenplayArtifactInputError(
            f"{label} must be an array of at most {maximum_items} items."
        )
    return [
        _required_text(item, label, maximum=maximum_text)
        for item in value
    ]


def _required_unique_text_list(
    value: object,
    *,
    label: str,
    minimum_items: int,
    maximum_items: int,
    maximum_text: int,
) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) < minimum_items
        or len(value) > maximum_items
    ):
        raise ScreenplayArtifactInputError(
            f"{label} must contain between {minimum_items} and "
            f"{maximum_items} items."
        )
    result = list(dict.fromkeys(
        _required_text(item, label, maximum=maximum_text) for item in value
    ))
    if len(result) != len(value):
        raise ScreenplayArtifactInputError(
            f"{label} cannot contain duplicate values."
        )
    return result


def _optional_bounded_text(value: object, *, maximum: int) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        raise ScreenplayArtifactInputError(
            f"text must contain at most {maximum} characters."
        )
    return text


def _required_count(
    value: object,
    *,
    maximum: int,
    label: str,
) -> int:
    if isinstance(value, bool):
        raise ScreenplayArtifactInputError(f"{label} must be an integer.")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ScreenplayArtifactInputError(
            f"{label} must be an integer."
        ) from error
    if result < 1 or result > maximum:
        raise ScreenplayArtifactInputError(
            f"{label} must be between 1 and {maximum}."
        )
    return result


def _required_non_negative_count(
    value: object,
    *,
    maximum: int,
    label: str,
) -> int:
    if isinstance(value, bool):
        raise ScreenplayArtifactInputError(f"{label} must be an integer.")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ScreenplayArtifactInputError(
            f"{label} must be an integer."
        ) from error
    if result < 0 or result > maximum:
        raise ScreenplayArtifactInputError(
            f"{label} must be between 0 and {maximum}."
        )
    return result


def _required_execution_update(item: Mapping[str, Any]) -> dict[str, Any]:
    execution = item.get("execution")
    if not isinstance(execution, Mapping):
        raise ScreenplayArtifactInputError("execution must be an object.")
    unresolved = execution.get("unresolvedNotes")
    if not isinstance(unresolved, list) or len(unresolved) > 10:
        raise ScreenplayArtifactInputError(
            "execution.unresolvedNotes must be an array of at most 10 items."
        )
    return {
        "sceneId": _required_text(item.get("sceneId"), "sceneId", maximum=100),
        "objectiveResult": _required_text(
            execution.get("objectiveResult"),
            "execution.objectiveResult",
            maximum=2_000,
        ),
        "conflictResult": _required_text(
            execution.get("conflictResult"),
            "execution.conflictResult",
            maximum=2_000,
        ),
        "turnResult": _required_text(
            execution.get("turnResult"),
            "execution.turnResult",
            maximum=2_000,
        ),
        "continuityState": _required_text(
            execution.get("continuityState"),
            "execution.continuityState",
            maximum=2_000,
        ),
        "unresolvedNotes": [
            _required_text(value, "unresolved note", maximum=500)
            for value in unresolved
        ],
    }


def _required_resolution_status(value: object) -> str:
    status = str(value or "").strip()
    if status not in {"resolved", "partially_resolved"}:
        raise ScreenplayArtifactInputError(
            "resolution status must be resolved or partially_resolved."
        )
    return status


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _required_text(value: object, label: str, *, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ScreenplayArtifactInputError(f"{label} is required.")
    if len(text) > maximum:
        raise ScreenplayArtifactInputError(
            f"{label} exceeds {maximum} characters."
        )
    return text


def _title(arguments: Mapping[str, Any], fallback: str) -> str:
    title = str(arguments.get("title") or "").strip()
    return title[:160] if title else fallback


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _split_fountain_text(
    content_text: str,
    scene_ids: Sequence[str],
) -> dict[str, str]:
    text = str(content_text or "").strip()
    ids = [str(value) for value in scene_ids if str(value).strip()]
    if not text or not ids:
        return {}
    if len(ids) == 1:
        return {ids[0]: text}
    matches = list(_FOUNTAIN_HEADING.finditer(text))
    if len(matches) != len(ids):
        return {}
    return {
        scene_id: text[
            matches[index].start(): (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(text)
            )
        ].strip()
        for index, scene_id in enumerate(ids)
    }


__all__ = [
    "ScreenplayArtifactInputError",
    "ScreenplayArtifactToolService",
    "ScreenplayArtifactValidator",
]
