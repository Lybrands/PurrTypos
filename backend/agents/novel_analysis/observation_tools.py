"""Bounded observation directory used by replacement normalize operations."""

from __future__ import annotations

import json

from purra.cancellation import raise_if_stopped
from purra.contracts import (
    ToolDataContract,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from purra.ports import ToolRegistration
from purra.tools import InMemoryToolCatalog


NOVEL_ANALYSIS_OBSERVATIONS_STATE_KEY = "novelAnalysisObservations"
_MAX_READ_ITEMS = 20
_MAX_READ_CHARACTERS = 32_000


def validate_observation_directory(value) -> tuple[dict[str, object], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("analysis observation directory must be a list")
    result = []
    seen = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {
            "observationId",
            "cardKind",
            "title",
            "bodyMarkdown",
            "evidenceRefs",
        }:
            raise ValueError("analysis observation shape is invalid")
        observation_id = _text(raw["observationId"], "observationId", 500)
        if observation_id in seen:
            raise ValueError("analysis observation ids must be unique")
        seen.add(observation_id)
        evidence = raw["evidenceRefs"]
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("analysis observation requires evidenceRefs")
        normalized_evidence = []
        for reference in evidence:
            if not isinstance(reference, dict) or set(reference) != {
                "segmentId",
                "sourceSpanId",
            }:
                raise ValueError("analysis observation evidence ref is invalid")
            normalized_evidence.append({
                "segmentId": _text(reference["segmentId"], "segmentId", 500),
                "sourceSpanId": _text(
                    reference["sourceSpanId"], "sourceSpanId", 100
                ),
            })
        result.append({
            "observationId": observation_id,
            "cardKind": _text(raw["cardKind"], "cardKind", 100),
            "title": _text(raw["title"], "title", 500),
            "bodyMarkdown": _text(raw["bodyMarkdown"], "bodyMarkdown", 8_000),
            "evidenceRefs": normalized_evidence,
        })
    return tuple(result)


def build_novel_analysis_observation_tool_catalog() -> InMemoryToolCatalog:
    async def list_observations(state, arguments, signal=None):
        raise_if_stopped(signal)
        try:
            items = validate_observation_directory(
                state.domain.get(NOVEL_ANALYSIS_OBSERVATIONS_STATE_KEY)
            )
            offset = int(arguments.get("offset", 0))
            limit = int(arguments.get("limit", 50))
            page = items[offset:offset + limit]
            return _result({
                "total": len(items),
                "items": [{
                    "observationId": item["observationId"],
                    "cardKind": item["cardKind"],
                    "title": item["title"],
                    "evidenceCount": len(item["evidenceRefs"]),
                } for item in page],
                "nextOffset": offset + limit if offset + limit < len(items) else None,
            })
        except ValueError as error:
            return _invalid(error)

    async def read_observations(state, arguments, signal=None):
        raise_if_stopped(signal)
        try:
            items = validate_observation_directory(
                state.domain.get(NOVEL_ANALYSIS_OBSERVATIONS_STATE_KEY)
            )
            requested = arguments.get("observationIds")
            if not isinstance(requested, list) or not requested:
                raise ValueError("observationIds must be a non-empty list")
            if len(requested) > _MAX_READ_ITEMS:
                raise ValueError("too many observations requested")
            if len(requested) != len(set(requested)):
                raise ValueError("observationIds must be unique")
            by_id = {item["observationId"]: item for item in items}
            if any(item not in by_id for item in requested):
                raise ValueError("observation is outside the normalize input")
            selected = [by_id[item] for item in requested]
            if sum(len(str(item["bodyMarkdown"])) for item in selected) > _MAX_READ_CHARACTERS:
                raise ValueError("observation read exceeds the character budget")
            return _result({"items": selected})
        except ValueError as error:
            return _invalid(error)

    registrations = (
        _registration(
            "listAnalysisObservations",
            "分页列出当前 normalize 输入中的 observation ID、标题与证据数量。",
            "查看分析观察目录",
            {
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            (),
            list_observations,
        ),
        _registration(
            "readAnalysisObservations",
            "按 ID 读取当前 normalize 输入中的 observation 正文与不可改写的来源 handle。",
            "读取分析观察",
            {
                "observationIds": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 500},
                    "minItems": 1,
                    "maxItems": _MAX_READ_ITEMS,
                    "uniqueItems": True,
                },
            },
            ("observationIds",),
            read_observations,
        ),
    )
    return InMemoryToolCatalog(registrations)


def _registration(name, description, display_name, properties, required, handler):
    return ToolRegistration(
        schema=ToolSchema(
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
            display_names={"zh-CN": display_name, "en": name},
        ),
        handler=handler,
        policy=ToolPolicy(ToolExecutionMode.READ, display_name, ToolRiskLevel.READ),
        concurrency_safe=True,
        data_contract=ToolDataContract(
            model_owned_paths=tuple(properties),
            host_bound_paths=(NOVEL_ANALYSIS_OBSERVATIONS_STATE_KEY,),
        ),
        operation_display_params=(
            lambda state, arguments, call, *, label=display_name, tool_name=name: {
                "displayNames": {"zh-CN": label, "en": tool_name},
            }
        ),
    )


def _result(payload) -> ToolHandlerResult:
    return ToolHandlerResult(
        content=json.dumps(payload, ensure_ascii=False, allow_nan=False),
    )


def _invalid(error: ValueError) -> ToolHandlerResult:
    return ToolHandlerResult(
        content=json.dumps({
            "success": False,
            "code": "tool_input_invalid",
            "error": str(error),
        }, ensure_ascii=False),
        error_code="tool_input_invalid",
    )


def _text(value, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} is invalid")
    return normalized


__all__ = [
    "NOVEL_ANALYSIS_OBSERVATIONS_STATE_KEY",
    "build_novel_analysis_observation_tool_catalog",
    "validate_observation_directory",
]
