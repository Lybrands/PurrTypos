"""Durable Unit executor for Screenplay replacement Parts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import json
from typing import Protocol

from agents.screenplay.access_contract import (
    screenplay_part_tool_guidance,
    screenplay_part_tool_names,
)
from agents.screenplay.access_receipts import (
    ScreenplayAccessKind,
    ScreenplayOperationAccessReceiptStore,
    screenplay_access_receipt_digest,
)
from agents.screenplay.candidate_artifact import ScreenplayCandidateArtifactStore
from agents.screenplay.capture_artifact import ScreenplayCaptureArtifactStore
from agents.screenplay.host_result_artifact import (
    SCREENPLAY_HOST_RESULT_REF_PREFIX,
    ScreenplayHostResultArtifactStore,
)
from agents.screenplay.revision_projector import (
    ScreenplayReplacementRevisionProjector,
)
from agents.screenplay.candidate_artifact import SCREENPLAY_CANDIDATE_REF_PREFIX
from agents.screenplay.capture_artifact import SCREENPLAY_CAPTURE_REF_PREFIX
from agents.screenplay.contracts import (
    ScreenplayPartCompletion,
    ScreenplayPartKind,
    ScreenplayPartOperationScope,
    ScreenplaySourceItemRef,
    screenplay_part_definition,
)
from agents.screenplay.output_contract import (
    ScreenplayPartOutputError,
    ScreenplayPartOutputEvidence,
    validate_screenplay_part_output,
)
from agents.screenplay.usage import ScreenplayOperationUsageStore
from infrastructure.persistence.sqlite_long_task_repository import (
    SqliteLongTaskRepository,
)
from purra.cancellation import raise_if_stopped
from purra.errors import ModelGatewayError
from purra.json_values import canonical_json_digest
from purra.long_tasks import DurableUnitExecutionContext, LongTaskUnitResult
from purra.recovery import FailureCategory, FailureScope, FailureSignal


_TRANSIENT_PROVIDER_CODES = frozenset({
    "model_gateway_error",
    "provider_circuit_open",
    "provider_capacity_limited",
    "provider_rate_limited",
    "provider_unavailable",
    "upstream_stream_interrupted",
    "model_activity_deadline_exceeded",
    "model_progress_deadline_exceeded",
    "model_invocation_deadline_exceeded",
})
_MODEL_OUTPUT_CODES = frozenset({
    "missing_required_tool_call",
    "model_output_truncated",
    "tool_call_truncated",
    "screenplay_candidate_missing",
    "screenplay_candidate_invalid",
    "screenplay_structured_output_invalid",
    "response_constraint_violation",
})
_INTERRUPTED_ATTEMPT_CODES = frozenset({
    "execution_interrupted",
    "execution_recovery_after_restart",
})


class ScreenplayModelPartRunner(Protocol):
    async def run(
        self,
        *,
        scope: ScreenplayPartOperationScope,
        enabled_tool_names: tuple[str, ...],
        instruction: str,
        context: DurableUnitExecutionContext,
        signal=None,
    ) -> ScreenplayPartOutputEvidence: ...


class ScreenplayReplacementUnitExecutor:
    def __init__(
        self,
        db,
        *,
        model_runner: ScreenplayModelPartRunner | None,
        long_task_repository=None,
    ) -> None:
        self._db = db
        self._artifacts = ScreenplayCandidateArtifactStore(db)
        self._captures = ScreenplayCaptureArtifactStore(db)
        self._host_results = ScreenplayHostResultArtifactStore(db)
        self._access_receipts = ScreenplayOperationAccessReceiptStore(db)
        self._usage = ScreenplayOperationUsageStore(db)
        self._revision_projector = ScreenplayReplacementRevisionProjector(db)
        self._model_runner = model_runner
        self._long_tasks = long_task_repository or SqliteLongTaskRepository(db)

    async def execute(
        self,
        context: DurableUnitExecutionContext,
        signal=None,
    ) -> LongTaskUnitResult:
        raise_if_stopped(signal)
        scope = _operation_scope(context)
        definition = screenplay_part_definition(scope.part_kind)
        if definition.completion is ScreenplayPartCompletion.HOST_ONLY:
            return await self._execute_host_only(scope, context, signal)
        if definition.completion not in {
            ScreenplayPartCompletion.CANDIDATE_TOOL,
            ScreenplayPartCompletion.HOST_CAPTURE,
        }:
            raise ValueError(
                "Screenplay replacement executor does not yet support this completion"
            )
        evidence = await self._recover_interrupted_output(scope, context)
        if evidence is None:
            if self._model_runner is None:
                raise RuntimeError("Screenplay replacement model runner is unavailable")
            evidence = await self._model_runner.run(
                scope=scope,
                enabled_tool_names=screenplay_part_tool_names(scope.part_kind),
                instruction=screenplay_part_tool_guidance(scope.part_kind),
                context=context,
                signal=signal,
            )
        validated = validate_screenplay_part_output(scope.part_kind, evidence)
        usage_receipt = await self._usage.record_from_events(
            scope=scope,
            root_run_id=context.run_id,
            metadata=(
                {"recoveredOutput": True}
                if evidence is not None and scope.attempt > 1
                and str(context.unit.error_code or "") in _INTERRUPTED_ATTEMPT_CODES
                else None
            ),
        )
        if definition.completion is ScreenplayPartCompletion.HOST_CAPTURE:
            raise_if_stopped(signal)
            artifact_id, output_ref, _ = await self._captures.commit(
                scope=scope,
                run_id=context.run_id,
                payload=validated["hostCapture"],
            )
            payload = validated["hostCapture"]
            raise_if_stopped(signal)
            access_receipt = await self._access_receipt_fields(scope)
            return LongTaskUnitResult(
                output_ref=output_ref,
                artifact_digest=canonical_json_digest(payload),
                validation_receipt={
                    "schemaVersion": 1,
                    "partKind": scope.part_kind.value,
                    "operationScopeId": scope.operation_scope_id,
                    "completion": "host_capture",
                    "operationUsage": usage_receipt.to_mapping(),
                    **access_receipt,
                },
                metadata={"artifactId": artifact_id},
            )
        try:
            payload, output_ref = await self._artifacts.require_scope_artifact(
                scope,
                str(validated["candidateArtifactId"]),
            )
        except ValueError as error:
            raise ScreenplayPartOutputError(
                "screenplay_candidate_invalid",
                retryable=True,
            ) from error
        raise_if_stopped(signal)
        access_receipt = await self._access_receipt_fields(scope)
        return LongTaskUnitResult(
            output_ref=output_ref,
            artifact_digest=canonical_json_digest(payload),
            validation_receipt={
                "schemaVersion": 1,
                "partKind": scope.part_kind.value,
                "operationScopeId": scope.operation_scope_id,
                "completion": "candidate_tool",
                "operationUsage": usage_receipt.to_mapping(),
                **access_receipt,
            },
            metadata={"artifactId": validated["candidateArtifactId"]},
        )

    async def _execute_host_only(self, scope, context, signal):
        dependencies = await self._settled_dependencies(scope, context)
        if not dependencies:
            raise ValueError("Screenplay host-only Part requires settled dependencies")
        refs = [item["outputRef"] for item in dependencies]
        if scope.part_kind is ScreenplayPartKind.HOST_PROJECTION:
            usage_settlement = await self._usage.settle_task_roots(
                scope.task_id,
                self._long_tasks,
            )
            projected = await self._revision_projector.project(
                scope=scope,
                run_id=context.run_id,
                operation=str(context.task.metadata.get("operation") or ""),
                dependencies=dependencies,
            )
            result = {
                "projectionStatus": "committed",
                "targetRole": scope.target_role,
                "settledOutputRefs": refs,
                "operationUsage": usage_settlement,
                **projected,
            }
        elif scope.part_kind is ScreenplayPartKind.VALIDATION:
            projections = [
                item["payload"].get("result", {})
                for item in dependencies
                if item["payload"].get("partKind") == "host_projection"
                and isinstance(item["payload"].get("result"), Mapping)
            ]
            if (
                len(projections) != 1
                or projections[0].get("projectionStatus") != "committed"
                or not str(projections[0].get("revisionId") or "").strip()
            ):
                raise ValueError(
                    "Screenplay validation requires a committed Revision projection"
                )
            result = {
                "valid": True,
                "revisionId": str(projections[0]["revisionId"]),
                "validatedOutputRefs": refs,
            }
        elif scope.part_kind is ScreenplayPartKind.FINAL_RESPONSE:
            validations = [
                item["payload"].get("result", {})
                for item in dependencies
                if isinstance(item["payload"].get("result"), Mapping)
                and item["payload"].get("partKind") == "validation"
                and item["payload"].get("result", {}).get("valid") is True
            ]
            if len(validations) != 1 or not str(
                validations[0].get("revisionId") or ""
            ).strip():
                raise ValueError("Screenplay final response requires a valid receipt")
            result = {
                "status": "completed",
                "targetRole": scope.target_role,
                "revisionId": str(validations[0]["revisionId"]),
                "settledOutputRefs": refs,
            }
        else:
            raise ValueError("unsupported Screenplay host-only Part")
        raise_if_stopped(signal)
        artifact_id, output_ref, _ = await self._host_results.commit(
            scope=scope,
            run_id=context.run_id,
            result=result,
            dependencies=tuple({
                "partKey": item["partKey"],
                "outputRef": item["outputRef"],
                "contentDigest": item["contentDigest"],
            } for item in dependencies),
        )
        raise_if_stopped(signal)
        access_receipt = await self._access_receipt_fields(scope)
        return LongTaskUnitResult(
            output_ref=output_ref,
            artifact_digest=canonical_json_digest(result),
            validation_receipt={
                "schemaVersion": 1,
                "partKind": scope.part_kind.value,
                "operationScopeId": scope.operation_scope_id,
                "completion": "host_only",
                **access_receipt,
            },
            metadata={
                "artifactId": artifact_id,
                **({"finalResponse": output_ref}
                   if scope.part_kind is ScreenplayPartKind.FINAL_RESPONSE else {}),
            },
        )

    async def _settled_dependencies(self, scope, context):
        keys = scope.dependency_part_keys
        if not keys:
            return ()
        marks = ",".join("?" for _ in keys)
        rows = await self._db.fetch_all(
            "SELECT semantic_key, status, output_ref, metadata_json "
            "FROM ai_agent_long_task_units "
            f"WHERE task_id = ? AND semantic_key IN ({marks})",
            [scope.task_id, *keys],
        )
        by_key = {str(row["semantic_key"]): row for row in rows}
        if set(by_key) != set(keys):
            raise ValueError("Screenplay settled dependency scope is incomplete")
        context_refs = {str(value) for value in context.dependency_outputs.values()}
        result = []
        for key in keys:
            row = by_key[key]
            output_ref = str(row.get("output_ref") or "")
            if row.get("status") != "completed" or output_ref not in context_refs:
                raise ValueError("Screenplay dependency is not the settled winner")
            payload = await self._load_dependency(scope, output_ref)
            metadata = json.loads(str(row.get("metadata_json") or "{}"))
            if not isinstance(metadata, Mapping):
                raise ValueError("Screenplay dependency metadata is invalid")
            await self._access_receipts.record(
                scope=scope,
                run_id=context.run_id,
                access_kind=ScreenplayAccessKind.DEPENDENCY,
                resource_ref=output_ref,
                content_digest=canonical_json_digest(payload),
                metadata={"partKey": key},
            )
            result.append({
                "partKey": key,
                "outputRef": output_ref,
                "contentDigest": canonical_json_digest(payload),
                "payload": payload,
                "partKind": str(metadata.get("partKind") or ""),
                "episodeNumber": metadata.get("episodeNumber"),
            })
        if context_refs != {item["outputRef"] for item in result}:
            raise ValueError("Screenplay dependency outputs exceed declared scope")
        return tuple(result)

    async def _access_receipt_fields(self, scope):
        receipts = await self._access_receipts.list_for_scope(scope)
        return {
            "accessReceiptDigest": screenplay_access_receipt_digest(receipts),
            "accessReceipts": [item.to_mapping() for item in receipts],
        }

    async def _load_dependency(self, scope, output_ref):
        if output_ref.startswith(SCREENPLAY_CANDIDATE_REF_PREFIX):
            return await self._artifacts.load_dependency(
                project_id=scope.project_id, task_id=scope.task_id,
                output_ref=output_ref,
            )
        if output_ref.startswith(SCREENPLAY_CAPTURE_REF_PREFIX):
            return await self._captures.load_dependency(
                project_id=scope.project_id, task_id=scope.task_id,
                output_ref=output_ref,
            )
        if output_ref.startswith(SCREENPLAY_HOST_RESULT_REF_PREFIX):
            return await self._host_results.load_dependency(
                project_id=scope.project_id, task_id=scope.task_id,
                output_ref=output_ref,
            )
        raise ValueError("Screenplay dependency output type is unsupported")

    def classify_failure(self, error: Exception) -> FailureSignal:
        code = str(getattr(error, "code", "") or type(error).__name__)[:240]
        if isinstance(error, ScreenplayPartOutputError):
            return FailureSignal(
                category=(
                    FailureCategory.MODEL_OUTPUT_INVALID
                    if error.retryable
                    else FailureCategory.BUSINESS_INVARIANT
                ),
                code=error.code,
                retryable=error.retryable,
            )
        if isinstance(error, ModelGatewayError):
            if code in _MODEL_OUTPUT_CODES:
                return FailureSignal(
                    category=FailureCategory.MODEL_OUTPUT_INVALID,
                    code=code,
                    retryable=True,
                )
            if error.retryable or code in _TRANSIENT_PROVIDER_CODES:
                return FailureSignal(
                    category=FailureCategory.TRANSIENT_PROVIDER,
                    code=code,
                    retryable=True,
                    scope=FailureScope.SYSTEMIC,
                )
            return FailureSignal(
                category=FailureCategory.PROTOCOL_INCOMPATIBLE,
                code=code,
                retryable=False,
                scope=FailureScope.SYSTEMIC,
            )
        return FailureSignal(
            category=FailureCategory.BUSINESS_INVARIANT,
            code=code,
            retryable=False,
        )

    async def _recover_interrupted_output(self, scope, context):
        if (
            scope.attempt < 2
            or str(context.unit.error_code or "") not in _INTERRUPTED_ATTEMPT_CODES
        ):
            return None
        previous = replace(scope, attempt=scope.attempt - 1)
        if screenplay_part_definition(scope.part_kind).completion is ScreenplayPartCompletion.HOST_CAPTURE:
            captured = await self._captures.try_load(previous)
            if captured is None:
                return None
            _, _, payload = captured
            await self._copy_access_receipts(previous, scope, context.run_id)
            return ScreenplayPartOutputEvidence(host_capture=payload)
        payload = await self._artifacts.try_load_scope_payload(previous)
        if payload is None:
            return None
        receipt = await self._artifacts.commit(
            scope=scope,
            run_id=context.run_id,
            payload=payload,
        )
        await self._copy_access_receipts(previous, scope, context.run_id)
        return ScreenplayPartOutputEvidence(
            candidate_artifact_id=receipt.artifact_id
        )

    async def _copy_access_receipts(self, previous, current, run_id):
        for receipt in await self._access_receipts.list_for_scope(previous):
            await self._access_receipts.record(
                scope=current,
                run_id=run_id,
                access_kind=receipt.access_kind,
                resource_ref=receipt.resource_ref,
                content_digest=receipt.content_digest,
                metadata={
                    **dict(receipt.metadata),
                    "recoveredFromOperationScopeId": previous.operation_scope_id,
                },
            )


def _operation_scope(context) -> ScreenplayPartOperationScope:
    metadata = context.unit.metadata
    task_metadata = context.task.metadata
    if not isinstance(metadata, Mapping) or not isinstance(task_metadata, Mapping):
        raise ValueError("Screenplay replacement Unit metadata is invalid")
    source_items = metadata.get("sourceItems") or ()
    return ScreenplayPartOperationScope(
        project_id=str(task_metadata.get("projectId") or ""),
        task_id=context.task.id,
        unit_id=context.unit.id,
        attempt=context.unit.attempt,
        part_kind=ScreenplayPartKind(str(metadata.get("partKind") or "")),
        part_key=str(metadata.get("semanticKey") or ""),
        target_role=str(task_metadata.get("targetRole") or ""),
        source_revision_refs=tuple(metadata.get("sourceRevisionRefs") or ()),
        deliverable_revision_scope=metadata.get("deliverableRevisionScope") or {},
        dependency_part_keys=tuple(metadata.get("dependencyPartKeys") or ()),
        source_book_id=metadata.get("sourceBookId"),
        source_items=tuple(
            ScreenplaySourceItemRef.from_mapping(item) for item in source_items
        ),
        episode_number=metadata.get("episodeNumber"),
        scene_id=metadata.get("sceneId"),
    )


__all__ = [
    "ScreenplayModelPartRunner",
    "ScreenplayReplacementUnitExecutor",
]
