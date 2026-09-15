"""Read-only public projection for finalized replacement review Artifacts."""

from __future__ import annotations

from collections.abc import Mapping

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


NOVEL_ANALYSIS_REVIEW_CONTRACT = "purrtypos.novel_analysis.review.v2"
NOVEL_ANALYSIS_REVIEW_REF_PREFIX = "novel-analysis://"


class NovelAnalysisReviewProjectionError(ValueError):
    code = "novel_analysis_review_projection_invalid"


class NovelAnalysisReviewProjection:
    """Expose only the settled review winner."""

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
            "schemaVersion", "kind", "childRunId", "skillArtifactId", "skillDigest", "coverageArtifactId",
            "coverageDigest", "synthesisArtifactId", "synthesisDigest",
            "summaryMarkdown", "facts", "craftCards", "techniqueResult",
        }
        if set(payload) != expected or payload.get("schemaVersion") != 3:
            raise NovelAnalysisReviewProjectionError(
                "scalable review Artifact payload is invalid"
            )
        summary = _required_text(payload.get("summaryMarkdown"), "summaryMarkdown")
        facts = _mapping_list(payload.get("facts"), "facts")
        cards = _mapping_list(payload.get("craftCards"), "craftCards")
        if not facts:
            raise NovelAnalysisReviewProjectionError("scalable review facts are empty")
        section_ids = await self._section_ids(task_owner_id)
        if not section_ids:
            raise NovelAnalysisReviewProjectionError(
                "scalable review source sections no longer resolve"
            )
        return {
            "analysisSchemaVersion": 2,
            "artifactContract": NOVEL_ANALYSIS_REVIEW_CONTRACT,
            "artifactId": artifact_id,
            "artifactRef": NOVEL_ANALYSIS_REVIEW_REF_PREFIX + artifact_id,
            "artifactKind": "novel_analysis_scalable_review",
            "createdByRunId": created_by_run_id,
            "taskId": task_id,
            "sourceRevisionId": task_owner_id,
            "sectionIds": section_ids,
            "facts": facts,
            "craftCards": cards,
            "storyOverview": {"summaryMarkdown": summary},
            "techniqueResult": payload.get("techniqueResult"),
            "conflicts": [],
            "reviewStatus": "pending",
        }

    async def _section_ids(self, revision_id: str) -> list[str]:
        rows = await self._db.fetch_all(
            "SELECT id FROM novel_source_sections "
            "WHERE revision_id = ? ORDER BY ordinal",
            [revision_id],
        )
        return [str(row["id"]) for row in rows]


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


def _mapping_list(value, name: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise NovelAnalysisReviewProjectionError(f"{name} is invalid")
    return [dict(item) for item in value]


def _required_text(value, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise NovelAnalysisReviewProjectionError(f"{name} is invalid")
    return normalized


__all__ = [
    "NOVEL_ANALYSIS_REVIEW_CONTRACT",
    "NOVEL_ANALYSIS_REVIEW_REF_PREFIX",
    "NovelAnalysisReviewProjection",
    "NovelAnalysisReviewProjectionError",
]
