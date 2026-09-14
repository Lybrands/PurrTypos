"""Durable Unit executor for the replacement Novel Analysis recipe."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from agents.novel_analysis.access_contract import (
    analysis_model_tool_names,
    analysis_unit_read_guidance,
)
from agents.novel_analysis.attempt_artifact import (
    NovelAnalysisAttemptArtifactStore,
)
from agents.novel_analysis.domain import NovelAnalysisRequestScope
from agents.novel_analysis.recipe import (
    AnalysisSegment,
    AnalysisUnitKind,
    NOVEL_ANALYSIS_REPLACEMENT_SCHEMA_VERSION,
)
from agents.novel_analysis.source_model import (
    NovelAnalysisSourceScopeError,
    SqliteNovelAnalysisSourceRepository,
)
from agents.novel_analysis.unit_schema import validate_model_unit_output
from purra.cancellation import raise_if_stopped
from purra.errors import ModelGatewayError
from purra.json_values import canonical_json_digest
from purra.long_tasks import DurableUnitExecutionContext, LongTaskUnitResult
from purra.recovery import FailureCategory, FailureScope, FailureSignal


_MODEL_OUTPUT_CODES = frozenset({
    "missing_required_tool_call",
    "novel_analysis_model_output_invalid",
    "novel_analysis_evidence_invalid",
    "novel_analysis_result_not_submitted",
    "model_output_truncated",
    "tool_call_truncated",
})
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
_INTERRUPTED_ATTEMPT_CODES = frozenset({
    "execution_interrupted",
    "execution_recovery_after_restart",
})


class NovelAnalysisModelUnitRunner(Protocol):
    async def run(
        self,
        *,
        kind: AnalysisUnitKind,
        scope: NovelAnalysisRequestScope,
        dependency_payloads: tuple[dict[str, object], ...],
        observations: tuple[dict[str, object], ...],
        enabled_tool_names: tuple[str, ...],
        instruction: str,
        context: DurableUnitExecutionContext,
        signal=None,
    ) -> Mapping[str, object]: ...


class NovelAnalysisReplacementUnitExecutor:
    def __init__(self, db, *, model_runner: NovelAnalysisModelUnitRunner | None) -> None:
        self._source = SqliteNovelAnalysisSourceRepository(db)
        self._artifacts = NovelAnalysisAttemptArtifactStore(db)
        self._model_runner = model_runner

    async def execute(
        self,
        context: DurableUnitExecutionContext,
        signal=None,
    ) -> LongTaskUnitResult:
        raise_if_stopped(signal)
        kind = AnalysisUnitKind(str(context.unit.metadata.get("unitKind") or ""))
        scope = _scope_from_task(context.task.metadata)
        await self._source.validate_scope(scope)
        dependencies = []
        for reference in context.dependency_outputs.values():
            dependencies.append(await self._load_output(reference))
        dependencies = tuple(dependencies)
        payload = await self._execute_kind(
            kind,
            scope=scope,
            unit_metadata=context.unit.metadata,
            dependencies=dependencies,
            context=context,
            signal=signal,
        )
        raise_if_stopped(signal)
        operation_id = (
            f"{context.task.id}:{context.unit.id}:{context.unit.attempt}"
        )
        receipt = await self._artifacts.commit(
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
                "schemaVersion": NOVEL_ANALYSIS_REPLACEMENT_SCHEMA_VERSION,
                "unitKind": kind.value,
                "operationId": operation_id,
                "artifactReplayed": receipt.replayed,
            },
            metadata={
                "artifactId": receipt.artifact_id,
                **(
                    {"finalResponse": receipt.resource_ref}
                    if kind is AnalysisUnitKind.REVIEW
                    else {}
                ),
            },
        )

    def classify_failure(self, error: Exception) -> FailureSignal:
        code = str(getattr(error, "code", "") or type(error).__name__)[:240]
        if code in _MODEL_OUTPUT_CODES:
            return FailureSignal(
                category=FailureCategory.MODEL_OUTPUT_INVALID,
                code=code,
                retryable=True,
            )
        if isinstance(error, ModelGatewayError):
            if code in _TRANSIENT_PROVIDER_CODES or error.retryable:
                return FailureSignal(
                    category=FailureCategory.TRANSIENT_PROVIDER,
                    code=code,
                    retryable=error.retryable,
                    scope=(
                        FailureScope.SYSTEMIC
                        if code in _TRANSIENT_PROVIDER_CODES
                        else FailureScope.LOCAL
                    ),
                )
            if code in {
                "provider_bad_request",
                "provider_reasoning_context_invalid",
                "unsupported_model_feature",
            }:
                return FailureSignal(
                    category=FailureCategory.PROTOCOL_INCOMPATIBLE,
                    code=code,
                    retryable=False,
                    scope=FailureScope.SYSTEMIC,
                )
            if code in {
                "provider_authentication_failed",
                "provider_insufficient_balance",
            }:
                return FailureSignal(
                    category=FailureCategory.PERMANENT_EXTERNAL,
                    code=code,
                    retryable=False,
                    scope=FailureScope.SYSTEMIC,
                )
        return FailureSignal(
            category=FailureCategory.BUSINESS_INVARIANT,
            code=code,
            retryable=False,
        )

    async def _execute_kind(
        self,
        kind: AnalysisUnitKind,
        *,
        scope: NovelAnalysisRequestScope,
        unit_metadata: Mapping[str, object],
        dependencies: tuple[dict[str, object], ...],
        context: DurableUnitExecutionContext,
        signal,
    ) -> dict[str, object]:
        if kind is AnalysisUnitKind.EXTRACT:
            segment = _extract_segment(scope, unit_metadata)
            result = await self._run_model(
                kind,
                scope=NovelAnalysisRequestScope(
                    source_revision_id=scope.source_revision_id,
                    command_id=scope.command_id,
                    segments=(segment,),
                ),
                dependencies=(),
                observations=(),
                context=context,
                signal=signal,
            )
            await self._validate_evidence(result, scope, allowed_segments={segment.id})
            return result
        if kind is AnalysisUnitKind.NORMALIZE:
            observations = _dependency_observations(dependencies)
            allowed_ids = frozenset(
                str(item["observationId"]) for item in observations
            )
            result = await self._run_model(
                kind,
                scope=scope,
                dependencies=dependencies,
                observations=observations,
                context=context,
                allowed_observation_ids=allowed_ids,
                signal=signal,
            )
            await self._validate_evidence(result, scope)
            expected_segments = {
                segment_id
                for dependency in dependencies
                for segment_id in _referenced_segment_ids(dependency)
            }
            if _referenced_segment_ids(result) != expected_segments:
                raise ModelGatewayError(
                    "normalize output changed source segment coverage",
                    code="novel_analysis_model_output_invalid",
                    retryable=True,
                )
            return result
        if kind is AnalysisUnitKind.VALIDATE_EVIDENCE:
            upstream = _one_dependency(kind, dependencies)
            evidence = await self._validate_evidence(upstream, scope)
            return {
                **upstream,
                "kind": kind.value,
                "evidenceIndex": evidence,
            }
        if kind in {
            AnalysisUnitKind.OVERVIEW,
            AnalysisUnitKind.DISTILL_TECHNIQUE,
        }:
            upstream = _one_dependency(kind, dependencies)
            observations = tuple(
                _observation_directory_item(item)
                for item in upstream.get("observations") or ()
            )
            result = await self._run_model(
                kind,
                scope=scope,
                dependencies=dependencies,
                observations=observations,
                context=context,
                allowed_observation_ids=frozenset(
                    str(item["observationId"]) for item in observations
                ),
                signal=signal,
            )
            if kind is AnalysisUnitKind.OVERVIEW:
                await self._validate_evidence(result, scope)
            return result
        if kind is AnalysisUnitKind.COVERAGE:
            return await self._coverage(scope, dependencies)
        if kind is AnalysisUnitKind.REVIEW:
            coverage = _one_dependency(kind, dependencies)
            if coverage.get("coverageComplete") is not True:
                raise ValueError("analysis review requires complete source coverage")
            overview = coverage.get("storyOverview")
            if not isinstance(overview, Mapping):
                raise ValueError("analysis review requires storyOverview")
            return {
                "schemaVersion": NOVEL_ANALYSIS_REPLACEMENT_SCHEMA_VERSION,
                "kind": kind.value,
                "sourceRevisionId": scope.source_revision_id,
                "facts": list(coverage.get("facts") or ()),
                "observations": list(coverage.get("observations") or ()),
                "storyOverview": dict(overview),
                "techniqueResult": coverage.get("techniqueResult"),
                "coverageReport": dict(coverage.get("coverageReport") or {}),
                "evidenceIndex": list(coverage.get("evidenceIndex") or ()),
                "reviewStatus": "pending_review",
            }
        raise ValueError(f"unsupported analysis Unit kind: {kind.value}")

    async def _run_model(
        self,
        kind,
        *,
        scope,
        dependencies,
        observations,
        context,
        allowed_observation_ids=frozenset(),
        signal,
    ) -> dict[str, object]:
        raw = await self._recover_interrupted_submission(context)
        if raw is None and self._model_runner is None:
            raise RuntimeError("replacement analysis model runner is unavailable")
        if raw is None:
            raw = await self._model_runner.run(
                kind=kind,
                scope=scope,
                dependency_payloads=dependencies,
                observations=observations,
                enabled_tool_names=analysis_model_tool_names(kind),
                instruction=analysis_unit_read_guidance(kind),
                context=context,
                signal=signal,
            )
        submitted = dict(raw)
        if set(("schemaVersion", "kind")).issubset(submitted):
            if (
                submitted.pop("schemaVersion")
                != NOVEL_ANALYSIS_REPLACEMENT_SCHEMA_VERSION
                or submitted.pop("kind") != kind.value
            ):
                raise ModelGatewayError(
                    "analysis submitted result identity is invalid",
                    code="novel_analysis_model_output_invalid",
                    retryable=True,
                )
            for fact in submitted.get("facts") or ():
                if isinstance(fact, dict):
                    fact.pop("factId", None)
            for observation in submitted.get("observations") or ():
                if isinstance(observation, dict):
                    observation.pop("observationId", None)
            technique = submitted.get("techniqueResult")
            if (
                isinstance(technique, dict)
                and technique.get("status") == "empty"
            ):
                technique.pop("techniques", None)
        return validate_model_unit_output(
            kind,
            submitted,
            allowed_observation_ids=allowed_observation_ids,
        )

    async def _recover_interrupted_submission(self, context):
        if (
            context.unit.attempt < 2
            or str(context.unit.error_code or "") not in _INTERRUPTED_ATTEMPT_CODES
        ):
            return None
        previous_attempt = context.unit.attempt - 1
        return await self._artifacts.try_load_operation_payload(
            task_id=context.task.id,
            operation_id=(
                f"{context.task.id}:{context.unit.id}:{previous_attempt}"
            ),
        )

    async def _validate_evidence(
        self,
        payload: Mapping[str, object],
        scope: NovelAnalysisRequestScope,
        *,
        allowed_segments: set[str] | None = None,
    ) -> list[dict[str, object]]:
        references = _evidence_references(payload)
        resolved = []
        seen = set()
        for reference in references:
            segment_id = str(reference["segmentId"])
            if allowed_segments is not None and segment_id not in allowed_segments:
                raise ModelGatewayError(
                    "analysis output cites a segment outside this Unit",
                    code="novel_analysis_evidence_invalid",
                    retryable=True,
                )
            key = (segment_id, str(reference["sourceSpanId"]))
            if key in seen:
                continue
            seen.add(key)
            try:
                resolved.append(await self._source.resolve_source_span(
                    scope,
                    segment_id=key[0],
                    source_span_id=key[1],
                ))
            except NovelAnalysisSourceScopeError as error:
                if error.code != "analysis_source_span_invalid":
                    raise
                raise ModelGatewayError(
                    str(error),
                    code="novel_analysis_evidence_invalid",
                    retryable=True,
                ) from error
        if not resolved:
            raise ModelGatewayError(
                "analysis output has no resolvable source evidence",
                code="novel_analysis_evidence_invalid",
                retryable=True,
            )
        return resolved

    async def _coverage(
        self,
        scope: NovelAnalysisRequestScope,
        dependencies: tuple[dict[str, object], ...],
    ) -> dict[str, object]:
        if len(dependencies) != 3:
            raise ValueError("coverage Unit requires evidence, overview, and technique")
        evidence_payload = next(
            (item for item in dependencies if item.get("kind") == "validate_evidence"),
            None,
        )
        overview_payload = next(
            (item for item in dependencies if item.get("kind") == "overview"),
            None,
        )
        technique_payload = next(
            (item for item in dependencies if item.get("kind") == "distill_technique"),
            None,
        )
        if not all((evidence_payload, overview_payload, technique_payload)):
            raise ValueError("coverage Unit dependencies have invalid kinds")
        combined = {
            "facts": list(evidence_payload.get("facts") or ()),
            "observations": list(evidence_payload.get("observations") or ()),
            "storyOverview": overview_payload.get("storyOverview"),
        }
        resolved = await self._validate_evidence(combined, scope)
        covered = {str(item["segmentId"]) for item in resolved}
        expected = {item.id for item in scope.segments}
        return {
            "schemaVersion": NOVEL_ANALYSIS_REPLACEMENT_SCHEMA_VERSION,
            "kind": AnalysisUnitKind.COVERAGE.value,
            **combined,
            "techniqueResult": technique_payload.get("techniqueResult"),
            "evidenceIndex": resolved,
            "coverageComplete": covered == expected,
            "coverageReport": {
                "expectedSegmentIds": sorted(expected),
                "coveredSegmentIds": sorted(covered),
                "missingSegmentIds": sorted(expected - covered),
                "evidenceSpanCount": len(resolved),
            },
        }

    async def _load_output(self, reference: str) -> dict[str, object]:
        prefix = "novel-analysis-v1://"
        if not str(reference).startswith(prefix):
            raise ValueError("analysis dependency output ref is invalid")
        return await self._artifacts.load_payload(str(reference)[len(prefix):])


def _scope_from_task(metadata: Mapping[str, object]) -> NovelAnalysisRequestScope:
    return NovelAnalysisRequestScope.from_mapping({
        "sourceRevisionId": metadata.get("sourceRevisionId"),
        "commandId": metadata.get("commandId"),
        "segments": list(metadata.get("segments") or ()),
    })


def _extract_segment(
    scope: NovelAnalysisRequestScope,
    metadata: Mapping[str, object],
) -> AnalysisSegment:
    raw = metadata.get("segment")
    if not isinstance(raw, Mapping):
        raise ValueError("extract Unit segment metadata is unavailable")
    segment_id = str(raw.get("id") or "")
    segment = next((item for item in scope.segments if item.id == segment_id), None)
    if segment is None or segment.to_mapping() != dict(raw):
        raise ValueError("extract Unit segment conflicts with task scope")
    return segment


def _dependency_observations(
    dependencies: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    if not dependencies:
        raise ValueError("normalize Unit requires dependency outputs")
    result = tuple(
        _observation_directory_item(item)
        for dependency in dependencies
        for item in dependency.get("observations") or ()
    )
    ids = tuple(str(item.get("observationId") or "") for item in result)
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("normalize dependency observations are invalid")
    return result


def _observation_directory_item(value) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("analysis dependency observation is invalid")
    required = (
        "observationId", "cardKind", "title", "bodyMarkdown", "evidenceRefs",
    )
    if not set(required).issubset(value):
        raise ValueError("analysis dependency observation is incomplete")
    return {key: value[key] for key in required}


def _one_dependency(
    kind: AnalysisUnitKind,
    dependencies: tuple[dict[str, object], ...],
) -> dict[str, object]:
    if len(dependencies) != 1:
        raise ValueError(f"{kind.value} Unit requires one dependency")
    return dependencies[0]


def _evidence_references(payload: Mapping[str, object]):
    for fact in payload.get("facts") or ():
        yield from fact.get("evidenceRefs") or ()
    for observation in payload.get("observations") or ():
        yield from observation.get("evidenceRefs") or ()
    overview = payload.get("storyOverview")
    if isinstance(overview, Mapping):
        yield from overview.get("evidenceRefs") or ()


def _referenced_segment_ids(payload: Mapping[str, object]) -> set[str]:
    return {
        str(reference.get("segmentId") or "")
        for reference in _evidence_references(payload)
        if isinstance(reference, Mapping)
    }


__all__ = [
    "NovelAnalysisModelUnitRunner",
    "NovelAnalysisReplacementUnitExecutor",
]
