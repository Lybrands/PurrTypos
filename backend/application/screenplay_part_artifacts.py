"""Artifact-only persistence and reads for screenplay Manifest Parts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from purra.artifacts import (
    ArtifactAppendCommand,
    ArtifactCreateCommand,
    ArtifactFinalizeCommand,
    ArtifactLifecycle,
    ArtifactStatus,
)
from purra.artifacts.scope import ArtifactScope
from purra.json_values import freeze_json_mapping, thaw_json_mapping

from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from infrastructure.screenplay.tools.candidate_artifact import (
    SCREENPLAY_CANDIDATE_KIND,
    SCREENPLAY_CANDIDATE_NAMESPACE,
)


SCREENPLAY_PART_ARTIFACT_KIND = "manifest_part"
SCREENPLAY_PART_REF_PREFIX = "screenplay-part-artifact://"


@dataclass(frozen=True, slots=True)
class ValidatedPartArtifactRef:
    artifact_id: str
    run_id: str
    semantic_key: str
    content_digest: str
    validation_receipt: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("artifact_id", "run_id", "semantic_key", "content_digest"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"validated Part Artifact {name} is required")
            object.__setattr__(self, name, value)
        receipt = freeze_json_mapping(self.validation_receipt)
        if receipt.get("valid") is not True:
            raise ValueError("validated Part Artifact receipt must be valid")
        object.__setattr__(self, "validation_receipt", receipt)

    @property
    def output_ref(self) -> str:
        return f"{SCREENPLAY_PART_REF_PREFIX}{self.artifact_id}"


class ScreenplayPartArtifactQuery:
    def __init__(self, db) -> None:
        self._db = db
        self._repository = SqliteArtifactRepository(db)
        self._lifecycle = ArtifactLifecycle(self._repository)

    async def require(
        self,
        ref: ValidatedPartArtifactRef | str,
    ) -> Mapping[str, Any]:
        expected = ref if isinstance(ref, ValidatedPartArtifactRef) else None
        artifact_id = (
            expected.artifact_id
            if expected is not None
            else _artifact_id_from_ref(str(ref or ""))
        )
        artifact = await self._repository.load(artifact_id)
        if artifact is None:
            raise LookupError("screenplay Part Artifact does not exist")
        if artifact.status is not ArtifactStatus.FINALIZED:
            raise RuntimeError("screenplay Part Artifact is not finalized")
        batches = tuple(await self._repository.list_batches(artifact.id))
        if len(batches) != 1 or len(batches[0].items) != 1:
            raise RuntimeError("screenplay Part Artifact is incomplete")
        batch = batches[0]
        metadata = thaw_json_mapping(artifact.metadata)
        semantic_key = str(
            metadata.get("semanticKey") or metadata.get("unitId") or ""
        ).strip()
        run_id = str(artifact.run_id or artifact.created_by_run_id or "").strip()
        receipt = {
            "valid": True,
            "artifactId": artifact.id,
            "artifactRevision": artifact.revision,
            "semanticKey": semantic_key,
            "contentDigest": batch.content_digest,
            "runId": run_id,
        }
        actual = ValidatedPartArtifactRef(
            artifact_id=artifact.id,
            run_id=run_id,
            semantic_key=semantic_key,
            content_digest=batch.content_digest,
            validation_receipt=receipt,
        )
        if expected is not None and (
            expected.artifact_id != actual.artifact_id
            or expected.run_id != actual.run_id
            or expected.semantic_key != actual.semantic_key
            or expected.content_digest != actual.content_digest
        ):
            raise RuntimeError("screenplay Part Artifact reference conflicts")
        item = thaw_json_mapping(batch.items[0])
        payload = item.get("payload")
        if not isinstance(payload, Mapping):
            raise RuntimeError("screenplay Part Artifact payload is invalid")
        if artifact.kind == SCREENPLAY_CANDIDATE_KIND:
            return {
                **dict(payload),
                "contentText": str(item.get("contentText") or ""),
                "artifactId": artifact.id,
                "runId": run_id,
                "artifactDigest": batch.content_digest,
                "validationReceipt": receipt,
            }
        return {
            **dict(payload),
            "artifactId": artifact.id,
            "runId": run_id,
            "artifactDigest": batch.content_digest,
            "validationReceipt": receipt,
        }

    async def validated_ref(
        self,
        *,
        artifact_id: str,
        run_id: str,
        semantic_key: str,
    ) -> ValidatedPartArtifactRef:
        artifact = await self._repository.load(str(artifact_id or "").strip())
        if artifact is None or artifact.status is not ArtifactStatus.FINALIZED:
            raise RuntimeError("screenplay candidate Artifact is not finalized")
        if str(artifact.run_id or "") != str(run_id or ""):
            raise RuntimeError("screenplay candidate Artifact Run does not match")
        batches = tuple(await self._repository.list_batches(artifact.id))
        if len(batches) != 1 or len(batches[0].items) != 1:
            raise RuntimeError("screenplay candidate Artifact is incomplete")
        metadata = thaw_json_mapping(artifact.metadata)
        stored_key = str(
            metadata.get("semanticKey") or metadata.get("unitId") or ""
        )
        if stored_key != semantic_key:
            raise RuntimeError("screenplay candidate semantic key does not match")
        receipt = {
            "valid": True,
            "artifactId": artifact.id,
            "artifactRevision": artifact.revision,
            "semanticKey": semantic_key,
            "contentDigest": batches[0].content_digest,
            "runId": str(run_id),
        }
        return ValidatedPartArtifactRef(
            artifact_id=artifact.id,
            run_id=str(run_id),
            semantic_key=semantic_key,
            content_digest=batches[0].content_digest,
            validation_receipt=receipt,
        )

    async def write_host_part(
        self,
        *,
        project_id: str,
        task_id: str,
        unit_id: str,
        semantic_key: str,
        part_kind: str,
        output: Mapping[str, Any],
    ) -> ValidatedPartArtifactRef:
        run_id = f"screenplay-host:{task_id}:{unit_id}"
        artifact = await self._repository.find_for_run(
            namespace=SCREENPLAY_CANDIDATE_NAMESPACE,
            kind=SCREENPLAY_PART_ARTIFACT_KIND,
            owner_id=project_id,
            run_id=run_id,
        )
        if artifact is None:
            artifact = await self._lifecycle.begin(ArtifactCreateCommand(
                namespace=SCREENPLAY_CANDIDATE_NAMESPACE,
                kind=SCREENPLAY_PART_ARTIFACT_KIND,
                owner_id=project_id,
                run_id=run_id,
                created_by_run_id=run_id,
                scope=ArtifactScope.RUN,
                expected_item_count=1,
                metadata={
                    "taskId": task_id,
                    "unitId": unit_id,
                    "semanticKey": semantic_key,
                    "partKind": part_kind,
                },
            ))
        item = {"payload": dict(output)}
        batches = tuple(await self._repository.list_batches(artifact.id))
        if batches:
            if _canonical(thaw_json_mapping(batches[0].items[0])) != _canonical(item):
                raise RuntimeError("screenplay Part Artifact content conflicts")
        elif artifact.status is ArtifactStatus.OPEN:
            await self._lifecycle.append(ArtifactAppendCommand(
                artifact_id=artifact.id,
                expected_revision=artifact.revision,
                sequence=artifact.next_sequence,
                batch_id=unit_id,
                idempotency_key=f"{task_id}:{unit_id}:host-part",
                items=(item,),
                coverage_keys=(semantic_key,),
            ))
            artifact = await self._repository.load(artifact.id)
            if artifact is None:
                raise RuntimeError("screenplay Part Artifact disappeared")
            batches = tuple(await self._repository.list_batches(artifact.id))
        if artifact.status is ArtifactStatus.OPEN:
            artifact = await self._lifecycle.finalize(ArtifactFinalizeCommand(
                artifact_id=artifact.id,
                expected_revision=artifact.revision,
                expected_item_count=1,
                expected_coverage_keys=(semantic_key,),
                resource_ref=f"{SCREENPLAY_PART_REF_PREFIX}{artifact.id}",
            ))
        return await self.validated_ref(
            artifact_id=artifact.id,
            run_id=run_id,
            semantic_key=semantic_key,
        )

    async def require_unit(
        self,
        task_id: str,
        unit_id: str,
    ) -> Mapping[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT output_ref, artifact_digest, validation_receipt_json "
            "FROM ai_agent_long_task_units WHERE task_id = ? AND unit_id = ? "
            "AND status = 'completed'",
            [task_id, unit_id],
        )
        if row is None or not str(row.get("output_ref") or "").strip():
            return None
        receipt = _mapping(row.get("validation_receipt_json"))
        ref = ValidatedPartArtifactRef(
            artifact_id=_artifact_id_from_ref(str(row["output_ref"])),
            run_id=str(receipt.get("runId") or ""),
            semantic_key=str(receipt.get("semanticKey") or unit_id),
            content_digest=str(row.get("artifact_digest") or ""),
            validation_receipt=receipt,
        )
        return await self.require(ref)

    async def list_task_outputs(self, task_id: str) -> dict[str, Mapping[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT unit_id, output_ref FROM ai_agent_long_task_units "
            "WHERE task_id = ? AND status = 'completed' AND output_ref IS NOT NULL "
            "ORDER BY position",
            [task_id],
        )
        result: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            result[str(row["unit_id"])] = await self.require(str(row["output_ref"]))
        return result


def _artifact_id_from_ref(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized.startswith(SCREENPLAY_PART_REF_PREFIX):
        raise ValueError("screenplay unit output is not an Artifact reference")
    artifact_id = normalized.removeprefix(SCREENPLAY_PART_REF_PREFIX).strip()
    if not artifact_id:
        raise ValueError("screenplay Part Artifact id is missing")
    return artifact_id


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


__all__ = [
    "SCREENPLAY_PART_ARTIFACT_KIND",
    "SCREENPLAY_PART_REF_PREFIX",
    "ScreenplayPartArtifactQuery",
    "ValidatedPartArtifactRef",
]
