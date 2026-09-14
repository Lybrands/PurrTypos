"""Validated durable submission tool for replacement analysis model Units."""

from __future__ import annotations

import json
from collections.abc import Mapping

from agents.novel_analysis.attempt_artifact import NovelAnalysisAttemptArtifactStore
from agents.novel_analysis.domain import (
    NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS,
    NovelAnalysisRequestScope,
)
from agents.novel_analysis.observation_tools import (
    NOVEL_ANALYSIS_OBSERVATIONS_STATE_KEY,
    validate_observation_directory,
)
from agents.novel_analysis.recipe import AnalysisUnitKind
from agents.novel_analysis.source_model import (
    NovelAnalysisSourceScopeError,
    SqliteNovelAnalysisSourceRepository,
)
from agents.novel_analysis.source_tools import NOVEL_ANALYSIS_SOURCE_SCOPE_STATE_KEY
from agents.novel_analysis.unit_schema import (
    NovelAnalysisModelOutputError,
    validate_model_unit_output,
)
from purra.cancellation import raise_if_stopped
from purra.contracts import (
    ToolDataContract,
    ToolEffectState,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from purra.ports import ToolRegistration
from purra.tools import InMemoryToolCatalog


SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT = "submitNovelAnalysisUnitResult"


def build_novel_analysis_submission_tool_catalog(db) -> InMemoryToolCatalog:
    source = SqliteNovelAnalysisSourceRepository(db)
    artifacts = NovelAnalysisAttemptArtifactStore(db)

    async def submit(state, arguments, signal=None):
        raise_if_stopped(signal)
        try:
            scope = NovelAnalysisRequestScope.from_mapping(
                state.domain.get(NOVEL_ANALYSIS_SOURCE_SCOPE_STATE_KEY)
            )
            kind = AnalysisUnitKind(str(state.domain.get("unitKind") or ""))
            if kind not in _MODEL_UNIT_KINDS:
                raise ValueError("host-only analysis Unit cannot submit a model result")
            binding = state.domain.get("operationBinding")
            operation_id = str(state.domain.get("operationScopeId") or "")
            if not isinstance(binding, Mapping):
                raise ValueError("analysis Operation binding is unavailable")
            task_id = _text(binding.get("taskId"), "taskId")
            unit_id = _text(binding.get("unitId"), "unitId")
            attempt = binding.get("unitAttempt")
            if type(attempt) is not int or attempt < 0:
                raise ValueError("analysis Unit attempt is invalid")
            if operation_id != f"{task_id}:{unit_id}:{attempt}":
                raise ValueError("analysis Operation identity is invalid")
            if not state.run_id:
                raise ValueError("analysis Operation owner Run is unavailable")
            observations = validate_observation_directory(
                state.domain.get(NOVEL_ANALYSIS_OBSERVATIONS_STATE_KEY)
            )
            allowed_observation_ids = frozenset(
                str(item["observationId"]) for item in observations
            )
            result = validate_model_unit_output(
                kind,
                arguments.get("result"),
                allowed_observation_ids=allowed_observation_ids,
            )
            await _validate_evidence(source, scope, kind, result)
            receipt = await artifacts.commit(
                task_id=task_id,
                unit_id=unit_id,
                attempt=attempt,
                operation_id=operation_id,
                run_id=state.run_id,
                payload=result,
            )
            raise_if_stopped(signal)
            return ToolHandlerResult(
                content=json.dumps({
                    "artifactRef": receipt.resource_ref,
                    "replayed": receipt.replayed,
                }, ensure_ascii=False, allow_nan=False),
                effect_state=ToolEffectState.COMMITTED,
            )
        except (NovelAnalysisModelOutputError, NovelAnalysisSourceScopeError, ValueError) as error:
            return ToolHandlerResult(
                content=json.dumps({
                    "success": False,
                    "code": "tool_input_invalid",
                    "error": str(error),
                }, ensure_ascii=False),
                error_code="tool_input_invalid",
                effect_state=ToolEffectState.NOT_STARTED,
            )

    registration = ToolRegistration(
        schema=ToolSchema(
            name=SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT,
            description=(
                "Validate and durably submit the complete result for the current "
                "analysis Unit. This does not publish or modify the source book."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "result": {
                        "type": "object",
                        "description": (
                            "The complete result matching the current Unit schema "
                            "described in the host instruction."
                        ),
                        **_submission_result_schema(),
                    },
                },
                "required": ["result"],
                "additionalProperties": False,
            },
            display_names={"zh-CN": "提交分析单元结果", "en": "Submit analysis unit result"},
        ),
        handler=submit,
        policy=ToolPolicy(
            ToolExecutionMode.PROPOSE,
            "提交分析单元结果",
            ToolRiskLevel.WRITE,
        ),
        concurrency_safe=False,
        host_managed_durability=True,
        cancellation_linearizable=True,
        max_argument_chars=100_000,
        data_contract=ToolDataContract(
            model_owned_paths=("result",),
            host_bound_paths=(
                NOVEL_ANALYSIS_SOURCE_SCOPE_STATE_KEY,
                NOVEL_ANALYSIS_OBSERVATIONS_STATE_KEY,
                "operationScopeId",
                "operationBinding",
            ),
        ),
        operation_display_params=lambda state, arguments, call: {
            "displayNames": {
                "zh-CN": "提交分析单元结果",
                "en": "Submit analysis unit result",
            },
        },
    )
    return InMemoryToolCatalog((registration,))


def _submission_result_schema() -> dict[str, object]:
    evidence_ref = {
        "type": "object",
        "properties": {
            "segmentId": {"type": "string"},
            "sourceSpanId": {"type": "string"},
        },
        "required": ["segmentId", "sourceSpanId"],
        "additionalProperties": False,
    }
    evidence_refs = {
        "type": "array",
        "items": evidence_ref,
        "minItems": 1,
    }
    return {
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "factKind": {
                            "type": "string",
                            "enum": sorted(NOVEL_ANALYSIS_PUBLISHABLE_FACT_KINDS),
                        },
                        "subjectKey": {"type": "string"},
                        "predicate": {"type": "string"},
                        "value": {},
                        "evidenceRefs": evidence_refs,
                    },
                    "required": [
                        "factKind", "subjectKey", "predicate", "value",
                        "evidenceRefs",
                    ],
                    "additionalProperties": False,
                },
            },
            "observations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "cardKind": {"type": "string"},
                        "title": {"type": "string"},
                        "bodyMarkdown": {"type": "string"},
                        "evidenceRefs": evidence_refs,
                        "mergedObservationIds": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                    },
                    "required": [
                        "cardKind", "title", "bodyMarkdown", "evidenceRefs",
                    ],
                    "additionalProperties": False,
                },
            },
            "storyOverview": {
                "type": "object",
                "properties": {
                    "summaryMarkdown": {"type": "string"},
                    "evidenceRefs": evidence_refs,
                },
                "required": ["summaryMarkdown", "evidenceRefs"],
                "additionalProperties": False,
            },
            "techniqueResult": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["generated", "empty"]},
                    "reason": {"type": "string"},
                    "techniques": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "bodyMarkdown": {"type": "string"},
                                "observationIds": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "minItems": 1,
                                },
                            },
                            "required": [
                                "title", "bodyMarkdown", "observationIds",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["status"],
                "additionalProperties": False,
            },
        },
        "oneOf": [
            {"required": ["facts", "observations"]},
            {"required": ["storyOverview"]},
            {"required": ["techniqueResult"]},
        ],
        "additionalProperties": False,
    }


_MODEL_UNIT_KINDS = frozenset({
    AnalysisUnitKind.EXTRACT,
    AnalysisUnitKind.NORMALIZE,
    AnalysisUnitKind.OVERVIEW,
    AnalysisUnitKind.DISTILL_TECHNIQUE,
})


async def _validate_evidence(source, scope, kind, payload) -> None:
    if kind is AnalysisUnitKind.DISTILL_TECHNIQUE:
        return
    references = tuple(_evidence_references(payload))
    if not references:
        raise NovelAnalysisModelOutputError(
            "analysis output has no source evidence"
        )
    for reference in references:
        await source.resolve_source_span(
            scope,
            segment_id=str(reference.get("segmentId") or ""),
            source_span_id=str(reference.get("sourceSpanId") or ""),
        )


def _evidence_references(payload):
    for fact in payload.get("facts") or ():
        yield from fact.get("evidenceRefs") or ()
    for observation in payload.get("observations") or ():
        yield from observation.get("evidenceRefs") or ()
    overview = payload.get("storyOverview")
    if isinstance(overview, Mapping):
        yield from overview.get("evidenceRefs") or ()


def _text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is invalid")
    return value.strip()


__all__ = [
    "SUBMIT_NOVEL_ANALYSIS_UNIT_RESULT",
    "build_novel_analysis_submission_tool_catalog",
]
