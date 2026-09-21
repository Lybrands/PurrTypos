"""Deterministic coverage gate for scalable whole-book analysis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.reduce_execution import _artifact_id, _validate_dependency_payload
from purra.cancellation import raise_if_stopped
from purra.json_values import canonical_json_digest
from purra.long_tasks import LongTaskUnitResult
from purra.recovery import FailureCategory, FailureSignal


COVERAGE_OUTPUT_SCHEMA_VERSION = 1


class ScalableCoverageError(ValueError):
    code = "novel_analysis_coverage_invalid"


class ScalableCoverageUnitExecutor:
    """Prove Synthesis lineage before any review/publication is allowed."""

    def __init__(self, db) -> None:
        self._store = NovelAnalysisAttemptArtifactStore(db)

    async def execute(self, context, signal=None) -> LongTaskUnitResult:
        raise_if_stopped(signal)
        metadata = context.unit.metadata
        if str(metadata.get("unitKind") or "") != "coverage":
            raise ScalableCoverageError("Coverage executor received another Unit kind")
        dependencies = tuple(context.unit.dependencies)
        if len(dependencies) != 1 or set(context.dependency_outputs) != set(dependencies):
            raise ScalableCoverageError("Coverage dependency contract is invalid")
        expected_slices = _text_tuple(metadata.get("expectedSliceIds"), "slice")
        expected_passes = _text_tuple(metadata.get("expectedPassIds"), "pass")
        quality_checks = _text_tuple(metadata.get("qualityChecks"), "quality check")
        if metadata.get("deterministic") is not True:
            raise ScalableCoverageError("Coverage Unit must be deterministic")

        synthesis_id = _artifact_id(context.dependency_outputs[dependencies[0]])
        synthesis = await self._store.load_payload(synthesis_id)
        _validate_synthesis(synthesis, expected_slices, expected_passes)
        pass_roots = []
        for pass_id, artifact_id in zip(
            expected_passes, synthesis["inputArtifactIds"], strict=True
        ):
            payload = await self._store.load_payload(artifact_id)
            try:
                lineage = _validate_dependency_payload(payload, pass_id=pass_id)
            except ValueError as error:
                raise ScalableCoverageError("Coverage pass root is invalid") from error
            if lineage != expected_slices:
                raise ScalableCoverageError("Coverage pass root does not cover every slice")
            pass_roots.append({
                "passId": pass_id,
                "artifactId": artifact_id,
                "payloadDigest": canonical_json_digest(payload),
                "coveredSliceIds": list(lineage),
            })

        operation_id = f"{context.task.id}:{context.unit.id}:{context.unit.attempt}"
        payload = {
            "schemaVersion": COVERAGE_OUTPUT_SCHEMA_VERSION,
            "kind": "coverage",
            "synthesisArtifactId": synthesis_id,
            "synthesisDigest": canonical_json_digest(synthesis),
            "expectedSliceIds": list(expected_slices),
            "expectedPassIds": list(expected_passes),
            "passRoots": pass_roots,
            "qualityChecks": list(quality_checks),
            "summaryMarkdownPresent": True,
            "covered": True,
        }
        recovery = await self._store.try_load_execution_payload(
            task_id=context.task.id,
            unit_id=context.unit.id,
            attempt=context.unit.attempt,
            error_code=getattr(context.unit, "error_code", None),
        )
        recovered = recovery[0] if recovery is not None else None
        recovered_operation_id = recovery[1] if recovery is not None else None
        if recovered is not None and canonical_json_digest(recovered) != canonical_json_digest(payload):
            raise ScalableCoverageError("Coverage attempt Artifact identity conflict")
        receipt = await self._store.commit(
            task_id=context.task.id,
            unit_id=context.unit.id,
            attempt=context.unit.attempt,
            operation_id=operation_id,
            run_id=context.run_id,
            payload=payload,
        )
        return LongTaskUnitResult(
            output_ref=receipt.resource_ref,
            artifact_digest=canonical_json_digest(payload),
            validation_receipt={
                "schemaVersion": COVERAGE_OUTPUT_SCHEMA_VERSION,
                "unitKind": "coverage",
                "operationId": operation_id,
                "coveredSliceCount": len(expected_slices),
                "coveredPassCount": len(expected_passes),
                "artifactReplayed": receipt.replayed,
                **(
                    {"recoveredOperationId": recovered_operation_id}
                    if recovered_operation_id is not None
                    and recovered_operation_id != operation_id
                    else {}
                ),
            },
            metadata={
                "artifactId": receipt.artifact_id,
                "synthesisArtifactId": synthesis_id,
            },
        )

    def classify_failure(self, error):
        return FailureSignal(
            category=FailureCategory.BUSINESS_INVARIANT,
            code=str(getattr(error, "code", "") or type(error).__name__)[:240],
            retryable=False,
        )


def _text_tuple(value, label):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ScalableCoverageError(f"Coverage {label} list is invalid")
    items = tuple(str(item or "").strip() for item in value)
    if not items or any(not item for item in items) or len(set(items)) != len(items):
        raise ScalableCoverageError(f"Coverage {label} list is invalid")
    return items


def _validate_synthesis(synthesis: Mapping[str, object], expected_slices, expected_passes):
    required = {
        "schemaVersion", "kind", "childRunId", "inputArtifactIds",
        "coveredSliceIds", "summaryMarkdown", "facts", "craftCards",
    }
    if not isinstance(synthesis, Mapping) or set(synthesis) != required:
        raise ScalableCoverageError("Coverage Synthesis Artifact shape is invalid")
    input_ids = synthesis.get("inputArtifactIds")
    facts = synthesis.get("facts")
    if (
        synthesis.get("schemaVersion") != 3
        or synthesis.get("kind") != "synthesize"
        or synthesis.get("coveredSliceIds") != list(expected_slices)
        or not isinstance(input_ids, list)
        or len(input_ids) != len(expected_passes)
        or len(set(input_ids)) != len(input_ids)
        or not str(synthesis.get("summaryMarkdown") or "").strip()
        or not isinstance(facts, list)
        or not facts
        or not isinstance(synthesis.get("craftCards"), list)
    ):
        raise ScalableCoverageError("Coverage Synthesis Artifact is incomplete")


__all__ = ["COVERAGE_OUTPUT_SCHEMA_VERSION", "ScalableCoverageError", "ScalableCoverageUnitExecutor"]
