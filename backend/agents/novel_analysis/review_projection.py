"""Read-only public projection for finalized replacement review Artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256

from agents.novel_analysis.attempt_artifact import (
    NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_KIND,
    NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE,
    NovelAnalysisAttemptArtifactStore,
)
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from purra.artifacts import ArtifactStatus
from purra.json_values import thaw_json_mapping


NOVEL_ANALYSIS_REVIEW_CONTRACT = "purrtypos.novel_analysis.review.v1"
NOVEL_ANALYSIS_REVIEW_REF_PREFIX = "novel-analysis-v1://"


class NovelAnalysisReviewProjectionError(ValueError):
    code = "novel_analysis_review_projection_invalid"


class NovelAnalysisReviewProjection:
    """Expose only the settled review winner; never rewrite a legacy Artifact."""

    def __init__(self, db) -> None:
        self._db = db
        self._artifacts = SqliteArtifactRepository(db)
        self._attempts = NovelAnalysisAttemptArtifactStore(db)

    async def load(self, reference_or_id: str) -> dict[str, object]:
        artifact_id = _artifact_id(reference_or_id)
        artifact = await self._artifacts.load(artifact_id)
        if (
            artifact is None
            or artifact.status is not ArtifactStatus.FINALIZED
            or artifact.namespace != NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_NAMESPACE
            or artifact.kind != NOVEL_ANALYSIS_ATTEMPT_ARTIFACT_KIND
            or artifact.schema_version != 1
        ):
            raise NovelAnalysisReviewProjectionError(
                "replacement review Artifact is unavailable"
            )
        metadata = thaw_json_mapping(artifact.metadata)
        task_id = str(metadata.get("taskId") or "")
        unit_id = str(metadata.get("unitId") or "")
        attempt = metadata.get("attempt")
        operation_id = str(metadata.get("operationId") or "")
        if (
            not task_id
            or unit_id != "review:artifact"
            or type(attempt) is not int
            or attempt < 1
            or operation_id != f"{task_id}:{unit_id}:{attempt}"
            or artifact.owner_id != task_id
            or artifact.owner_ref.kind != "operation"
            or artifact.owner_ref.id != operation_id
        ):
            raise NovelAnalysisReviewProjectionError(
                "replacement review Artifact identity is invalid"
            )
        task = await self._db.fetch_one(
            "SELECT namespace, owner_id FROM ai_agent_long_tasks WHERE id = ?",
            [task_id],
        )
        winner = await self._db.fetch_one(
            "SELECT status, output_ref FROM ai_agent_long_task_units "
            "WHERE task_id = ? AND unit_id = ?",
            [task_id, unit_id],
        )
        resource_ref = NOVEL_ANALYSIS_REVIEW_REF_PREFIX + artifact_id
        if (
            task is None
            or task.get("namespace") != "purrtypos.novel_analysis"
            or winner is None
            or winner.get("status") != "completed"
            or winner.get("output_ref") != resource_ref
        ):
            raise NovelAnalysisReviewProjectionError(
                "replacement review Artifact is not the settled Unit winner"
            )
        payload = await self._attempts.load_payload(artifact_id)
        return await self._project(
            artifact_id=artifact_id,
            created_by_run_id=artifact.created_by_run_id,
            task_id=task_id,
            task_owner_id=str(task["owner_id"]),
            payload=payload,
        )

    async def _project(
        self,
        *,
        artifact_id: str,
        created_by_run_id: str,
        task_id: str,
        task_owner_id: str,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        expected = {
            "schemaVersion",
            "kind",
            "sourceRevisionId",
            "facts",
            "observations",
            "storyOverview",
            "techniqueResult",
            "coverageReport",
            "evidenceIndex",
            "reviewStatus",
        }
        if set(payload) != expected or (
            payload.get("schemaVersion") != 1
            or payload.get("kind") != "review"
            or payload.get("reviewStatus") != "pending_review"
        ):
            raise NovelAnalysisReviewProjectionError(
                "replacement review Artifact payload is invalid"
            )
        revision_id = str(payload.get("sourceRevisionId") or "")
        if not revision_id or revision_id != task_owner_id:
            raise NovelAnalysisReviewProjectionError(
                "replacement review Artifact source scope changed"
            )
        facts = _mapping_list(payload.get("facts"), "facts")
        observations = _mapping_list(payload.get("observations"), "observations")
        evidence_index = _mapping_list(payload.get("evidenceIndex"), "evidenceIndex")
        coverage = _mapping(payload.get("coverageReport"), "coverageReport")
        if coverage.get("missingSegmentIds") != []:
            raise NovelAnalysisReviewProjectionError(
                "replacement review Artifact has incomplete coverage"
            )
        evidence_by_ref = {}
        section_ids = []
        for item in evidence_index:
            key = (
                str(item.get("segmentId") or ""),
                str(item.get("sourceSpanId") or ""),
            )
            section_id = str(item.get("sectionId") or "")
            text = item.get("text")
            start = item.get("startCharacter")
            end = item.get("endCharacter")
            ordinal = item.get("sectionOrdinal")
            if (
                not all(key)
                or key in evidence_by_ref
                or not section_id
                or not isinstance(text, str)
                or not text
                or type(start) is not int
                or type(end) is not int
                or end <= start
                or type(ordinal) is not int
            ):
                raise NovelAnalysisReviewProjectionError(
                    "replacement review evidence index is invalid"
                )
            evidence_by_ref[key] = {
                "referenceKind": "quote",
                "sectionId": section_id,
                "excerpt": text,
                "segmentStartCharacter": start,
                "segmentEndCharacter": end,
                "sectionOrdinal": ordinal,
                "locator": {"start": start, "end": end},
                "excerptDigest": "sha256:" + sha256(text.encode("utf-8")).hexdigest(),
            }
            section_ids.append(section_id)
        titles = await self._section_titles(revision_id, tuple(dict.fromkeys(section_ids)))
        if set(titles) != set(section_ids):
            raise NovelAnalysisReviewProjectionError(
                "replacement review evidence section no longer resolves"
            )
        for evidence in evidence_by_ref.values():
            evidence["sectionTitle"] = titles[evidence["sectionId"]]

        projected_facts = []
        for fact in facts:
            projected_facts.append({
                "claimNature": "fact",
                "id": _required_text(fact.get("factId"), "factId"),
                "factKind": _required_text(fact.get("factKind"), "factKind"),
                "subjectKey": _required_text(fact.get("subjectKey"), "subjectKey"),
                "predicate": _required_text(fact.get("predicate"), "predicate"),
                "value": fact.get("value"),
                "lifecycleStatus": "active",
                "evidence": _project_evidence(fact.get("evidenceRefs"), evidence_by_ref),
            })
        projected_cards = []
        for observation in observations:
            projected_cards.append({
                "id": _required_text(
                    observation.get("observationId"), "observationId"
                ),
                "cardKind": _required_text(
                    observation.get("cardKind"), "cardKind"
                ),
                "title": _required_text(observation.get("title"), "title"),
                "bodyMarkdown": _required_text(
                    observation.get("bodyMarkdown"), "bodyMarkdown"
                ),
                "evidence": _project_evidence(
                    observation.get("evidenceRefs"), evidence_by_ref
                ),
            })
        overview = _mapping(payload.get("storyOverview"), "storyOverview")
        story_overview = {
            "summaryMarkdown": _required_text(
                overview.get("summaryMarkdown"), "summaryMarkdown"
            ),
            "evidence": _project_evidence(
                overview.get("evidenceRefs"), evidence_by_ref
            ),
        }
        return {
            "analysisSchemaVersion": 1,
            "artifactContract": NOVEL_ANALYSIS_REVIEW_CONTRACT,
            "artifactId": artifact_id,
            "artifactRef": NOVEL_ANALYSIS_REVIEW_REF_PREFIX + artifact_id,
            "artifactKind": "novel_analysis_review",
            "createdByRunId": created_by_run_id,
            "taskId": task_id,
            "sourceRevisionId": revision_id,
            "sectionIds": list(dict.fromkeys(section_ids)),
            "facts": projected_facts,
            "craftCards": projected_cards,
            "storyOverview": story_overview,
            "analysisTechniqueResult": payload.get("techniqueResult"),
            "coverage": coverage,
            "conflicts": [],
            "reviewStatus": "pending",
            "publicationSupported": True,
        }

    async def _section_titles(
        self,
        revision_id: str,
        section_ids: tuple[str, ...],
    ) -> dict[str, str]:
        if not section_ids:
            return {}
        placeholders = ",".join("?" for _ in section_ids)
        rows = await self._db.fetch_all(
            "SELECT id, title FROM novel_source_sections "
            f"WHERE revision_id = ? AND id IN ({placeholders})",
            [revision_id, *section_ids],
        )
        return {str(row["id"]): str(row.get("title") or "") for row in rows}


def _artifact_id(reference_or_id: str) -> str:
    value = str(reference_or_id or "").strip()
    artifact_id = (
        value.removeprefix(NOVEL_ANALYSIS_REVIEW_REF_PREFIX)
        if value.startswith(NOVEL_ANALYSIS_REVIEW_REF_PREFIX)
        else value
    )
    if not artifact_id or "://" in artifact_id:
        raise NovelAnalysisReviewProjectionError(
            "replacement review Artifact reference is invalid"
        )
    return artifact_id


def _mapping(value, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise NovelAnalysisReviewProjectionError(f"{name} is invalid")
    return dict(value)


def _mapping_list(value, name: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise NovelAnalysisReviewProjectionError(f"{name} is invalid")
    return [dict(item) for item in value]


def _required_text(value, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise NovelAnalysisReviewProjectionError(f"{name} is invalid")
    return normalized


def _project_evidence(value, evidence_by_ref) -> list[dict[str, object]]:
    references = _mapping_list(value, "evidenceRefs")
    if not references:
        raise NovelAnalysisReviewProjectionError("evidenceRefs is empty")
    result = []
    for reference in references:
        key = (
            str(reference.get("segmentId") or ""),
            str(reference.get("sourceSpanId") or ""),
        )
        evidence = evidence_by_ref.get(key)
        if evidence is None:
            raise NovelAnalysisReviewProjectionError(
                "replacement review evidence reference is unresolved"
            )
        result.append(dict(evidence))
    return result


__all__ = [
    "NOVEL_ANALYSIS_REVIEW_CONTRACT",
    "NOVEL_ANALYSIS_REVIEW_REF_PREFIX",
    "NovelAnalysisReviewProjection",
    "NovelAnalysisReviewProjectionError",
]
