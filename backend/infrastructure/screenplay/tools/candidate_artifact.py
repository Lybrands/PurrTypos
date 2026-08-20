"""Run-scoped candidate Artifact writes for screenplay generation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from purra.artifacts import (
    ArtifactAppendCommand,
    ArtifactBatch,
    ArtifactCreateCommand,
    ArtifactFinalizeCommand,
    ArtifactLifecycle,
    ArtifactMutationLease,
    ArtifactOwnerRef,
    ArtifactRecord,
    ArtifactStatus,
    ArtifactValidationResult,
)
from purra.artifacts.continuity import ArtifactWriteClaimCommand
from purra.contracts import ExecutionState
from purra.json_values import thaw_json_mapping

from domains.screenplay_agent.tools.errors import ScreenplayToolInputError
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from infrastructure.persistence.sqlite_artifact_claim_repository import (
    SqliteArtifactClaimRepository,
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
        candidate_normalizer: (
            Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]
            | None
        ) = None,
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
        self._claims = SqliteArtifactClaimRepository(db)
        self._candidate_normalizer = candidate_normalizer

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
        item = self._normalize_item(
            scope,
            _candidate_item(scope, arguments),
            artifact_id="",
        )
        owner_ref = ArtifactOwnerRef(kind="agent_run", id=run_id)
        artifact = await self._repository.find_for_owner(
            namespace=SCREENPLAY_CANDIDATE_NAMESPACE,
            kind=SCREENPLAY_CANDIDATE_KIND,
            owner_id=project_id,
            owner_ref=owner_ref,
        )
        if artifact is None:
            validation_contract = scope.get("candidateValidation")
            turn_id = (
                await self._candidate_turn_id(run_id)
                if isinstance(validation_contract, Mapping)
                else ""
            )
            artifact = await self._lifecycle.begin(ArtifactCreateCommand(
                namespace=SCREENPLAY_CANDIDATE_NAMESPACE,
                kind=SCREENPLAY_CANDIDATE_KIND,
                owner_id=project_id,
                owner_ref=owner_ref,
                created_by_run_id=run_id,
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
                    "turnId": turn_id,
                    "candidateValidationDigest": (
                        _digest(validation_contract)
                        if isinstance(validation_contract, Mapping)
                        else ""
                    ),
                },
            ))
        if artifact.status is ArtifactStatus.FINALIZED:
            return _receipt(artifact, already_written=True)
        batches = tuple(await self._repository.list_batches(artifact.id))
        if batches:
            stored = thaw_json_mapping(batches[0].items[0])
            if _canonical(stored) != _canonical(item):
                raise RuntimeError("screenplay_candidate_part_conflict")
            return _receipt(artifact, already_written=True)
        write_lease = await self._write_lease(artifact, run_id)
        receipt = await self._lifecycle.append(ArtifactAppendCommand(
            artifact_id=artifact.id,
            expected_revision=artifact.revision,
            sequence=artifact.next_sequence,
            batch_id=str(scope.get("unitId") or "candidate-part"),
            idempotency_key=(
                f"{scope.get('taskId')}:{scope.get('unitId')}:candidate-part"
            ),
            items=(item,),
            write_lease=write_lease,
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

    async def validate_run(
        self,
        *,
        run_id: str,
        scope: Mapping[str, Any],
    ) -> dict[str, Any]:
        candidate = await self.load_run(run_id)
        artifact = candidate["_artifact"]
        batches = candidate["_batches"]
        metadata = thaw_json_mapping(artifact.metadata)
        expected_metadata = {
            "taskId": str(scope.get("taskId") or ""),
            "unitId": str(scope.get("unitId") or ""),
            "targetRole": str(scope.get("targetRole") or ""),
            "partType": str(scope.get("expectedPartType") or ""),
            "partKey": str(scope.get("expectedPartKey") or ""),
        }
        validation_contract = scope.get("candidateValidation")
        if isinstance(validation_contract, Mapping):
            expected_metadata.update({
                "turnId": await self._candidate_turn_id(run_id),
                "candidateValidationDigest": _digest(validation_contract),
            })
        if (
            artifact.created_by_run_id != run_id
            or {
                key: str(metadata.get(key) or "")
                for key in expected_metadata
            }
            != expected_metadata
            or len(batches) != 1
            or len(batches[0].items) != 1
        ):
            raise ValueError("candidate Artifact scope conflicts with its Run")
        stored = thaw_json_mapping(batches[0].items[0])
        normalized = self._normalize_item(
            scope,
            stored,
            artifact_id=artifact.id,
        )
        if _canonical(stored) != _canonical(normalized):
            raise ValueError("candidate Artifact is not task-normalized")
        return candidate

    async def _candidate_turn_id(self, run_id: str) -> str:
        rows = await self._db.fetch_all(
            "SELECT turn_id, source, kind, channel, visibility, "
            "output_stream_id, invocation_id FROM ai_agent_run_events "
            "WHERE source_event_key = ?",
            [f"run:{run_id}:running"],
        )
        if (
            len(rows) != 1
            or rows[0].get("source") != "runtime"
            or rows[0].get("kind") != "run.lifecycle"
            or rows[0].get("channel") != "lifecycle"
            or rows[0].get("visibility") != "public"
            or rows[0].get("output_stream_id") is not None
            or rows[0].get("invocation_id") is not None
            or not str(rows[0].get("turn_id") or "").strip()
        ):
            raise ValueError("candidate Artifact turn identity is invalid")
        return str(rows[0]["turn_id"])

    def _normalize_item(
        self,
        scope: Mapping[str, Any],
        item: Mapping[str, Any],
        *,
        artifact_id: str,
    ) -> dict[str, Any]:
        contract = scope.get("candidateValidation")
        if contract is None:
            return dict(item)
        if not isinstance(contract, Mapping) or self._candidate_normalizer is None:
            raise RuntimeError("candidate validation contract is unavailable")
        _require_validation_scope(scope, contract)
        public = _public_candidate(scope, item, artifact_id=artifact_id)
        try:
            first = dict(self._candidate_normalizer(
                _json_clone(contract),
                _json_clone(public),
            ))
            second = dict(self._candidate_normalizer(
                _json_clone(contract),
                _json_clone(public),
            ))
        except (TypeError, ValueError) as error:
            raise ScreenplayToolInputError(
                "The screenplay candidate does not match its task contract.",
                guidance=(
                    "Correct the candidate using the registered task identity "
                    "and content constraints, then retry once."
                ),
                details={"validationCode": "candidate_task_invalid"},
            ) from error
        if _canonical(first) != _canonical(second):
            raise ValueError("candidate normalizer is not deterministic")
        return _normalized_candidate_item(scope, first)

    async def inspect(self, state: ExecutionState) -> dict[str, Any]:
        run_id = str(state.run_id or "").strip()
        artifact = await self._repository.find_for_owner(
            namespace=SCREENPLAY_CANDIDATE_NAMESPACE,
            kind=SCREENPLAY_CANDIDATE_KIND,
            owner_id=str(state.domain.get("projectId") or ""),
            owner_ref=ArtifactOwnerRef(kind="agent_run", id=run_id),
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
            write_lease = await self._write_lease(artifact, run_id)
            artifact = await self._lifecycle.finalize(ArtifactFinalizeCommand(
                artifact_id=artifact.id,
                expected_revision=artifact.revision,
                write_lease=write_lease,
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
            "AND owner_ref_kind = 'agent_run' AND owner_ref_id = ? "
            "ORDER BY create_time",
            [SCREENPLAY_CANDIDATE_NAMESPACE, SCREENPLAY_CANDIDATE_KIND, run_id],
        )
        artifacts = []
        for item in row:
            loaded = await self._repository.load(str(item["id"]))
            if loaded is not None:
                artifacts.append(loaded)
        return tuple(artifacts)

    async def _write_lease(
        self,
        artifact: ArtifactRecord,
        run_id: str,
    ) -> ArtifactMutationLease:
        claim = await self._claims.acquire(ArtifactWriteClaimCommand(
            artifact_id=artifact.id,
            run_id=run_id,
            expected_revision=artifact.revision,
            lease_duration_ms=300_000,
        ))
        return ArtifactMutationLease(
            run_id=claim.run_id,
            claim_token=claim.claim_token,
        )


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


def _public_candidate(
    scope: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    artifact_id: str,
) -> dict[str, Any]:
    payload = thaw_json_mapping(item.get("payload"))
    content_text = str(item.get("contentText") or "")
    if not content_text and str(scope.get("targetRole") or "") == (
        "screenplayDraft"
    ):
        if str(item.get("partType") or "") == "scene":
            content_text = str(payload.get("sceneText") or "").strip()
        else:
            content_text = "\n\n".join(
                str(scene.get("sceneText") or "").strip()
                for scene in payload.get("scenes") or ()
                if isinstance(scene, Mapping)
            )
    return {
        "artifactId": artifact_id,
        "partType": str(item.get("partType") or ""),
        "partKey": str(item.get("partKey") or ""),
        "payload": payload,
        "contentText": content_text,
    }


def _normalized_candidate_item(
    scope: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    payload = candidate.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("normalized candidate payload is missing")
    content_text = str(candidate.get("contentText") or "")
    if str(scope.get("targetRole") or "") == "screenplayDraft":
        content_text = ""
    return {
        "partType": str(scope.get("expectedPartType") or ""),
        "partKey": str(scope.get("expectedPartKey") or ""),
        "payload": dict(payload),
        "contentText": content_text,
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


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _json_clone(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(_canonical(value))


def _require_validation_scope(
    scope: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> None:
    kind = str(contract.get("kind") or "")
    expected_type = str(scope.get("expectedPartType") or "")
    expected_key = str(scope.get("expectedPartKey") or "")
    compatible = {
        "generic": True,
        "scene": (
            expected_type == "scene"
            and expected_key == str(contract.get("expectedSceneId") or "")
        ),
        "episode_metadata": (
            expected_type == "episode_metadata"
            and expected_key == str(contract.get("episodeNumber") or "")
        ),
        "review_dimension": (
            expected_type == "review_dimension"
            and expected_key
            == (
                f"{contract.get('episodeNumber')}:"
                f"{contract.get('dimension')}"
            )
        ),
        "document_section": (
            expected_type == "document_section"
            and expected_key == str(contract.get("sectionKey") or "")
        ),
        "scene_list_fragment": (
            expected_type == "document_section"
            and expected_key == f"episode-{contract.get('episodeNumber')}"
        ),
    }.get(kind, False)
    if not compatible:
        raise ScreenplayToolInputError(
            "The candidate validation contract conflicts with its task scope.",
            guidance="Use the host-bound candidate contract for this exact task unit.",
            details={"validationCode": "candidate_scope_invalid"},
        )


__all__ = [
    "SCREENPLAY_CANDIDATE_KIND",
    "SCREENPLAY_CANDIDATE_NAMESPACE",
    "ScreenplayCandidateArtifacts",
]
