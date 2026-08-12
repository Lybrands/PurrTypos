"""Atomic commit boundary for one completed screenplay Operation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from application.screenplay_part_artifacts import (
    ScreenplayPartArtifactQuery,
    ValidatedPartArtifactRef,
)
from domains.screenplay_agent.operation import ScreenplayOperationRecord
from infrastructure.persistence.sqlite_screenplay_operation_repository import (
    SqliteScreenplayOperationRepository,
)
from infrastructure.persistence.sqlite_screenplay_v2_repository import (
    SqliteScreenplayV2Repository,
)
from purra.normalization import required_text


class ScreenplayCandidateAssemblerPort(Protocol):
    async def assemble(
        self,
        operation: ScreenplayOperationRecord,
        candidate_parts: Sequence[Mapping[str, Any]],
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class ScreenplayOperationFinalizationCommand:
    operation_id: str
    expected_manifest_digest: str
    candidate_part_refs: tuple[ValidatedPartArtifactRef, ...]
    final_response_ref: ValidatedPartArtifactRef

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation_id", required_text(
            self.operation_id,
            "screenplay finalization operation id",
        ))
        object.__setattr__(self, "expected_manifest_digest", required_text(
            self.expected_manifest_digest,
            "screenplay finalization manifest digest",
        ))
        refs = tuple(self.candidate_part_refs)
        if not refs or not all(
            isinstance(ref, ValidatedPartArtifactRef) for ref in refs
        ):
            raise ValueError("screenplay finalization candidate refs are required")
        object.__setattr__(self, "candidate_part_refs", refs)
        if not isinstance(self.final_response_ref, ValidatedPartArtifactRef):
            raise TypeError("screenplay finalization response ref is invalid")


@dataclass(frozen=True, slots=True)
class ScreenplayOperationFinalizationReceipt:
    id: str
    operation_id: str
    revision_id: str
    assistant_content_digest: str

    def __post_init__(self) -> None:
        for name in (
            "id",
            "operation_id",
            "revision_id",
            "assistant_content_digest",
        ):
            object.__setattr__(self, name, required_text(
                getattr(self, name),
                f"screenplay finalization receipt {name}",
            ))


class SqliteScreenplayOperationFinalizer:
    def __init__(
        self,
        db,
        *,
        candidate_assembler: ScreenplayCandidateAssemblerPort,
    ) -> None:
        self._db = db
        self._operations = SqliteScreenplayOperationRepository(db)
        self._parts = ScreenplayPartArtifactQuery(db)
        self._revisions = SqliteScreenplayV2Repository(db)
        self._candidate_assembler = candidate_assembler

    async def finalize(
        self,
        command: ScreenplayOperationFinalizationCommand,
    ) -> ScreenplayOperationFinalizationReceipt:
        request_digest = _command_digest(command)
        command_id = f"operation:finalize:{command.operation_id}"
        async with self._db.transaction(cancellation_linearizable=True):
            operation = await self._operations.load(command.operation_id)
            if operation is None:
                raise LookupError("screenplay Operation does not exist")
            if operation.manifest_digest != command.expected_manifest_digest:
                raise ValueError("screenplay finalization manifest digest conflicts")
            replay = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_operation_commands "
                "WHERE command_id = ? AND command_type = 'finalize'",
                [command_id],
            )
            if replay is not None:
                if (
                    str(replay["operation_id"]) != operation.id
                    or str(replay["request_digest"]) != request_digest
                ):
                    raise ValueError("screenplay finalization command conflicts")
                return _receipt(_object(replay.get("response_json")))
            if operation.status.value != "running" or not operation.long_task_id:
                raise ValueError("screenplay Operation is not ready to finalize")
            if operation.cancel_requested_at_ms is not None:
                raise ValueError(
                    "screenplay Operation cancel was requested before finalization"
                )

            expected_candidate_keys, expected_response_key = _expected_part_keys(
                operation
            )
            actual_candidate_keys = tuple(
                ref.semantic_key for ref in command.candidate_part_refs
            )
            if actual_candidate_keys != expected_candidate_keys:
                raise ValueError("screenplay finalization candidate Parts are incomplete")
            if command.final_response_ref.semantic_key != expected_response_key:
                raise ValueError("screenplay finalization response Part conflicts")
            candidate_parts = []
            for ref in command.candidate_part_refs:
                candidate_parts.append(await self._parts.require(ref))
            response = await self._parts.require(command.final_response_ref)
            assistant_content = str(response.get("finalResponse") or "").strip()
            if not assistant_content:
                raise ValueError("screenplay final response Artifact is empty")
            publication = await self._candidate_assembler.assemble(
                operation,
                tuple(candidate_parts),
            )
            turn = await self._db.fetch_one(
                "SELECT * FROM screenplay_agent_turns WHERE id = ? "
                "AND project_id = ? AND session_id = ?",
                [operation.turn_id, operation.project_id, operation.session_id],
            )
            if turn is None or str(turn.get("status") or "") != "running":
                raise ValueError("screenplay Turn is not ready to finalize")

            published = await self._revisions.publish_screenplay_agent_task_candidate(
                task_id=operation.long_task_id,
                project_id=operation.project_id,
                target_role=operation.target_role,
                proposal_kind=str(publication.proposal_kind),
                title=str(publication.title),
                content_json=publication.content_json,
                content_text=str(publication.content_text),
                planner_run_id=str(turn.get("planner_run_id") or "").strip() or None,
                finalizing_run_id=publication.finalizing_run_id,
                base_revision_id=publication.base_revision_id,
                source_run_ids=publication.source_run_ids,
            )
            revision_id = str(published.get("revisionId") or "").strip()
            if not revision_id:
                raise RuntimeError("screenplay finalization published no Revision")
            assistant_digest = _sha256(assistant_content)
            receipt_id = f"spafinal_{request_digest[:32]}"

            await self._db.execute(
                "UPDATE screenplay_agent_turns SET status = 'completed', "
                "assistant_content = ?, execution_owner_id = NULL, "
                "lease_expires_at_ms = NULL, heartbeat_at_ms = NULL, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [assistant_content, operation.turn_id],
            )
            await self._db.execute(
                "UPDATE screenplay_agent_operations SET status = 'succeeded', "
                "result_revision_id = ?, finalization_receipt_id = ?, "
                "error_json = NULL, revision = revision + 1, "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [revision_id, receipt_id, operation.id],
            )
            receipt = ScreenplayOperationFinalizationReceipt(
                id=receipt_id,
                operation_id=operation.id,
                revision_id=revision_id,
                assistant_content_digest=assistant_digest,
            )
            await self._db.execute(
                "INSERT INTO screenplay_agent_operation_commands "
                "(command_id, operation_id, command_type, request_digest, "
                "receipt_id, response_json) VALUES (?, ?, 'finalize', ?, ?, ?)",
                [
                    command_id,
                    operation.id,
                    request_digest,
                    receipt.id,
                    _dump({
                        "id": receipt.id,
                        "operationId": receipt.operation_id,
                        "revisionId": receipt.revision_id,
                        "assistantContentDigest": receipt.assistant_content_digest,
                    }),
                ],
            )
            return receipt


def _expected_part_keys(
    operation: ScreenplayOperationRecord,
) -> tuple[tuple[str, ...], str]:
    requirements = dict(operation.requirements_json)
    recipe = requirements.get("recipe")
    steps = recipe.get("steps") if isinstance(recipe, Mapping) else None
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
        raise ValueError("screenplay Operation recipe is missing")
    candidates = tuple(
        str(step.get("id") or "")
        for step in steps
        if isinstance(step, Mapping)
        and str(step.get("kind") or "") == "validate_manifest_part"
    )
    responses = tuple(
        str(step.get("id") or "")
        for step in steps
        if isinstance(step, Mapping)
        and str(step.get("kind") or "") == "compose_final_response"
    )
    if not candidates or len(responses) != 1 or any(not value for value in candidates):
        raise ValueError("screenplay Operation finalization Parts are invalid")
    return candidates, responses[0]


def _command_digest(command: ScreenplayOperationFinalizationCommand) -> str:
    return _sha256(_dump({
        "operationId": command.operation_id,
        "manifestDigest": command.expected_manifest_digest,
        "candidatePartRefs": [
            _ref_mapping(ref) for ref in command.candidate_part_refs
        ],
        "finalResponseRef": _ref_mapping(command.final_response_ref),
    }))


def _ref_mapping(ref: ValidatedPartArtifactRef) -> dict[str, Any]:
    return {
        "artifactId": ref.artifact_id,
        "runId": ref.run_id,
        "semanticKey": ref.semantic_key,
        "contentDigest": ref.content_digest,
        "validationReceipt": dict(ref.validation_receipt),
    }


def _receipt(value: Mapping[str, Any]) -> ScreenplayOperationFinalizationReceipt:
    return ScreenplayOperationFinalizationReceipt(
        id=str(value.get("id") or ""),
        operation_id=str(value.get("operationId") or ""),
        revision_id=str(value.get("revisionId") or ""),
        assistant_content_digest=str(value.get("assistantContentDigest") or ""),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _dump(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


__all__ = [
    "ScreenplayOperationFinalizationCommand",
    "ScreenplayOperationFinalizationReceipt",
    "SqliteScreenplayOperationFinalizer",
]
