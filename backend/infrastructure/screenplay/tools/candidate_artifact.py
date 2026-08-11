"""Run-scoped candidate Artifact writes for screenplay generation."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from purra.artifacts import (
    ArtifactAppendCommand,
    ArtifactBatch,
    ArtifactCreateCommand,
    ArtifactFinalizeCommand,
    ArtifactLifecycle,
    ArtifactRecord,
    ArtifactStatus,
    ArtifactValidationResult,
)
from purra.artifacts.scope import ArtifactScope
from purra.contracts import ExecutionState
from purra.json_values import thaw_json_mapping

from domains.screenplay_agent.tools.errors import ScreenplayToolInputError
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)


SCREENPLAY_CANDIDATE_NAMESPACE = "purrtypos.screenplay"
SCREENPLAY_CANDIDATE_KIND = "candidate_part"


class _CandidateValidator:
    async def validate_batch(
        self,
        artifact: ArtifactRecord,
        command: ArtifactAppendCommand,
    ) -> ArtifactValidationResult:
        if len(command.items) != 1:
            return ArtifactValidationResult(False, "candidate_part_count_invalid")
        item = thaw_json_mapping(command.items[0])
        payload = item.get("payload")
        content = str(item.get("contentText") or "")
        metadata = thaw_json_mapping(artifact.metadata)
        part_type = str(metadata.get("partType") or "")
        part_key = str(metadata.get("partKey") or "")
        draft_scenes = (
            payload.get("scenes") if isinstance(payload, Mapping) else None
        )
        draft_has_text = bool(
            isinstance(draft_scenes, list)
            and draft_scenes
            and all(
                isinstance(scene, Mapping)
                and str(scene.get("sceneText") or "").strip()
                for scene in draft_scenes
            )
        )
        draft_scene_has_text = bool(
            part_type == "scene"
            and isinstance(payload, Mapping)
            and str(payload.get("sceneId") or "").strip() == part_key
            and str(payload.get("sceneText") or "").strip()
        )
        draft_metadata_complete = bool(
            part_type == "episode_metadata"
            and isinstance(payload, Mapping)
            and str(payload.get("episodeNumber") or "") == part_key
            and str(payload.get("title") or "").strip()
            and str(payload.get("continuitySummary") or "").strip()
        )
        review_dimension_complete = bool(
            part_type == "review_dimension"
            and isinstance(payload, Mapping)
            and ":" in part_key
            and str(payload.get("episodeNumber") or "") == part_key.split(":", 1)[0]
            and str(payload.get("reviewDimension") or "") == part_key.split(":", 1)[1]
            and str(payload.get("title") or "").strip()
            and isinstance(payload.get("contentJson"), Mapping)
            and payload["contentJson"].get("verdict")
            in {"ready", "revise", "major_rework"}
            and isinstance(payload["contentJson"].get("issues"), list)
            and content.strip()
        )
        document_section_complete = bool(
            part_type == "document_section"
            and isinstance(payload, Mapping)
            and str(payload.get("sectionKey") or "") == part_key
            and str(payload.get("title") or "").strip()
            and isinstance(payload.get("contentJson"), Mapping)
            and content.strip()
        )
        if (
            not isinstance(payload, Mapping)
            or not payload
            or (
                not content.strip()
                and not (
                    metadata.get("targetRole") == "screenplayDraft"
                    and (
                        draft_has_text
                        or draft_scene_has_text
                        or draft_metadata_complete
                    )
                )
                and not review_dimension_complete
                and not document_section_complete
            )
        ):
            return ArtifactValidationResult(False, "candidate_part_empty")
        if part_type == "scene" and not draft_scene_has_text:
            return ArtifactValidationResult(False, "candidate_scene_invalid")
        if part_type == "episode_metadata" and not draft_metadata_complete:
            return ArtifactValidationResult(False, "candidate_metadata_invalid")
        if part_type == "review_dimension" and not review_dimension_complete:
            return ArtifactValidationResult(False, "candidate_review_invalid")
        if part_type == "document_section" and not document_section_complete:
            return ArtifactValidationResult(False, "candidate_section_invalid")
        return ArtifactValidationResult(True)

    async def validate_finalization(
        self,
        artifact: ArtifactRecord,
        batches: Sequence[ArtifactBatch],
        command: ArtifactFinalizeCommand,
    ) -> ArtifactValidationResult:
        del artifact, command
        if len(batches) != 1 or len(batches[0].items) != 1:
            return ArtifactValidationResult(False, "candidate_manifest_invalid")
        return ArtifactValidationResult(True)


class ScreenplayCandidateArtifacts:
    def __init__(
        self,
        db,
        *,
        join_ambient_transaction: bool = False,
    ) -> None:
        self._db = db
        self._repository = SqliteArtifactRepository(
            db,
            join_ambient_transaction=join_ambient_transaction,
        )
        self._lifecycle = ArtifactLifecycle(
            self._repository,
            validator=_CandidateValidator(),
        )

    async def write(
        self,
        state: ExecutionState,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        run_id = str(state.run_id or "").strip()
        if not run_id:
            raise RuntimeError("screenplay candidate write requires a Run id")
        return await self._write_bound(
            run_id=run_id,
            scope=state.domain,
            arguments=arguments,
        )

    async def write_host_candidate(
        self,
        *,
        run_id: str,
        scope: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist host-bound model text without routing it through tool JSON."""
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise RuntimeError("screenplay candidate write requires a Run id")
        return await self._write_bound(
            run_id=normalized_run_id,
            scope=scope,
            arguments={"candidate": dict(candidate)},
        )

    async def _write_bound(
        self,
        *,
        run_id: str,
        scope: Mapping[str, Any],
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        project_id = str(scope.get("projectId") or "").strip()
        artifact = await self._repository.find_for_run(
            namespace=SCREENPLAY_CANDIDATE_NAMESPACE,
            kind=SCREENPLAY_CANDIDATE_KIND,
            owner_id=project_id,
            run_id=run_id,
        )
        if artifact is None:
            artifact = await self._lifecycle.begin(ArtifactCreateCommand(
                namespace=SCREENPLAY_CANDIDATE_NAMESPACE,
                kind=SCREENPLAY_CANDIDATE_KIND,
                owner_id=project_id,
                run_id=run_id,
                created_by_run_id=run_id,
                scope=ArtifactScope.RUN,
                expected_item_count=1,
                metadata={
                    "taskId": str(scope.get("taskId") or ""),
                    "unitId": str(scope.get("unitId") or ""),
                    "targetRole": str(scope.get("targetRole") or ""),
                    "partType": str(scope.get("expectedPartType") or ""),
                    "partKey": str(scope.get("expectedPartKey") or ""),
                    "semanticKey": str(
                        scope.get("semanticKey") or scope.get("unitId") or ""
                    ),
                },
            ))
        if artifact.status is ArtifactStatus.FINALIZED:
            return _receipt(artifact, already_written=True)
        batches = tuple(await self._repository.list_batches(artifact.id))
        if batches:
            item = thaw_json_mapping(batches[0].items[0])
            expected = _candidate_item(scope, arguments)
            if _canonical(item) != _canonical(expected):
                raise RuntimeError("screenplay_candidate_part_conflict")
            return _receipt(artifact, already_written=True)
        item = _candidate_item(scope, arguments)
        receipt = await self._lifecycle.append(ArtifactAppendCommand(
            artifact_id=artifact.id,
            expected_revision=artifact.revision,
            sequence=artifact.next_sequence,
            batch_id=str(scope.get("unitId") or "candidate-part"),
            idempotency_key=(
                f"{scope.get('taskId')}:{scope.get('unitId')}:candidate-part"
            ),
            items=(item,),
            coverage_keys=(
                f"{scope.get('expectedPartType')}:{scope.get('expectedPartKey')}",
            ),
        ))
        return {
            "artifactId": artifact.id,
            "status": "open",
            "acceptedParts": receipt.accepted_count,
            "partType": str(scope.get("expectedPartType") or ""),
            "partKey": str(scope.get("expectedPartKey") or ""),
            "alreadyWritten": False,
        }

    async def inspect(self, state: ExecutionState) -> dict[str, Any]:
        run_id = str(state.run_id or "").strip()
        artifact = await self._repository.find_for_run(
            namespace=SCREENPLAY_CANDIDATE_NAMESPACE,
            kind=SCREENPLAY_CANDIDATE_KIND,
            owner_id=str(state.domain.get("projectId") or ""),
            run_id=run_id,
        )
        if artifact is None:
            return {"status": "empty", "acceptedParts": 0}
        batches = tuple(await self._repository.list_batches(artifact.id))
        return {
            **_receipt(artifact, already_written=bool(batches)),
            "acceptedParts": sum(len(batch.items) for batch in batches),
        }

    async def finalize_run(self, run_id: str) -> dict[str, Any]:
        candidate = await self.load_run(run_id)
        artifact = candidate.pop("_artifact")
        batches = candidate.pop("_batches")
        if artifact.status is ArtifactStatus.OPEN:
            artifact = await self._lifecycle.finalize(ArtifactFinalizeCommand(
                artifact_id=artifact.id,
                expected_revision=artifact.revision,
                expected_item_count=1,
                expected_coverage_keys=batches[0].coverage_keys,
                resource_ref=(
                    f"screenplay-candidate://{artifact.owner_id}/{artifact.id}"
                ),
            ))
        return candidate

    async def load_run(self, run_id: str) -> dict[str, Any]:
        normalized = str(run_id or "").strip()
        rows = await self._find_run_artifacts(normalized)
        if len(rows) != 1:
            raise RuntimeError(
                "screenplay_candidate_missing"
                if not rows
                else "screenplay_candidate_ambiguous"
            )
        artifact = rows[0]
        batches = tuple(await self._repository.list_batches(artifact.id))
        if len(batches) != 1 or len(batches[0].items) != 1:
            raise RuntimeError("screenplay_candidate_incomplete")
        item = thaw_json_mapping(batches[0].items[0])
        payload = dict(item.get("payload") or {})
        content_text = str(item.get("contentText") or "")
        if not content_text and thaw_json_mapping(artifact.metadata).get(
            "targetRole"
        ) == "screenplayDraft":
            if str(item.get("partType") or "") == "scene":
                content_text = str(payload.get("sceneText") or "").strip()
            else:
                content_text = "\n\n".join(
                    str(scene.get("sceneText") or "").strip()
                    for scene in payload.get("scenes") or ()
                    if isinstance(scene, Mapping)
                )
        return {
            "artifactId": artifact.id,
            "partType": str(item.get("partType") or ""),
            "partKey": str(item.get("partKey") or ""),
            "payload": payload,
            "contentText": content_text,
            "_artifact": artifact,
            "_batches": batches,
        }

    async def _find_run_artifacts(self, run_id: str) -> tuple[ArtifactRecord, ...]:
        # A Run can create only one screenplay candidate by catalog contract.
        row = await self._db.fetch_all(
            "SELECT * FROM ai_agent_artifacts WHERE namespace = ? AND kind = ? "
            "AND run_id = ? ORDER BY create_time",
            [SCREENPLAY_CANDIDATE_NAMESPACE, SCREENPLAY_CANDIDATE_KIND, run_id],
        )
        artifacts = []
        for item in row:
            loaded = await self._repository.load(str(item["id"]))
            if loaded is not None:
                artifacts.append(loaded)
        return tuple(artifacts)


def _candidate_item(scope, arguments: Mapping[str, Any]) -> dict[str, Any]:
    candidate = arguments.get("candidate")
    if not isinstance(candidate, Mapping):
        raise ScreenplayToolInputError(
            "candidate must be an object.",
            guidance=(
                "Send one candidate object matching the registered "
                "writeScreenplayCandidatePart schema."
            ),
        )
    payload = dict(candidate)
    if str(scope.get("targetRole") or "") == "screenplayDraft":
        part_type = str(scope.get("expectedPartType") or "")
        scenes = payload.get("scenes")
        if part_type == "scene" and not str(payload.get("sceneText") or "").strip():
            raise ScreenplayToolInputError(
                "A scene candidate must contain a non-empty sceneText.",
                guidance="Write only the current scene and include sceneText.",
            )
        if part_type == "episode_metadata" and (
            not str(payload.get("title") or "").strip()
            or not str(payload.get("continuitySummary") or "").strip()
        ):
            raise ScreenplayToolInputError(
                "Episode metadata requires title and continuitySummary.",
                guidance="Write only the compact episode metadata object.",
            )
        if part_type not in {"scene", "episode_metadata"} and not isinstance(
            scenes, list
        ):
            raise ScreenplayToolInputError(
                "A screenplayDraft candidate must contain a scenes array.",
                guidance=(
                    "Provide every required scene in candidate.scenes and include "
                    "a non-empty sceneText for each scene."
                ),
            )
        content = ""
    else:
        content = str(payload.pop("contentText", ""))
    return {
        "partType": str(scope.get("expectedPartType") or ""),
        "partKey": str(scope.get("expectedPartKey") or ""),
        "payload": payload,
        "contentText": content,
    }


def _receipt(artifact: ArtifactRecord, *, already_written: bool) -> dict[str, Any]:
    metadata = thaw_json_mapping(artifact.metadata)
    return {
        "artifactId": artifact.id,
        "status": artifact.status.value,
        "partType": str(metadata.get("partType") or ""),
        "partKey": str(metadata.get("partKey") or ""),
        "alreadyWritten": already_written,
    }


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


__all__ = [
    "SCREENPLAY_CANDIDATE_KIND",
    "SCREENPLAY_CANDIDATE_NAMESPACE",
    "ScreenplayCandidateArtifacts",
]
